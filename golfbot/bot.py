"""Telegram bot wiring: command handlers, callback handlers, application builder.

The pure state mutations live in `actions.py`; pure rendering lives in
`notifier.py`. This module is the glue: it authorizes callers, loads/saves
state through `store.py`, calls into actions, then asks the notifier to
refresh the Telegram message.

Cross-process safety: a separate `golfbot mock` invocation also writes to
state.json. Because writes are atomic-rename and reads are on-demand
(no in-memory cache), the running bot always sees fresh state on each
callback / command.

See SPEC.md > Telegram commands.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from golfbot import actions, notifier, store
from golfbot.config import Config
from golfbot.models import TeeTimeSlot

log = logging.getLogger(__name__)

_ADMIN_ACTIONS = {"book", "skip", "pause", "undo"}

# Sibling project's update/deploy script, relayed by /garmin. Default is
# resolved relative to this repo so it works regardless of the bot's launch cwd
# or which user's home dir the tree lives under:
#   .../dev/golfbot/golfbot/bot.py -> .../dev/garmin-golf/update.sh
# Override with the GARMIN_UPDATE_SCRIPT env var (absolute path) when the two
# projects aren't siblings.
_DEFAULT_GARMIN_UPDATE_SCRIPT = (
    Path(__file__).resolve().parent.parent.parent / "garmin-golf" / "update.sh"
)


def _garmin_script_path() -> Path:
    """Path to the garmin-golf update script: GARMIN_UPDATE_SCRIPT env override
    if set, else the sibling-project default."""
    override = os.environ.get("GARMIN_UPDATE_SCRIPT", "").strip()
    if override:
        return Path(override).expanduser()
    return _DEFAULT_GARMIN_UPDATE_SCRIPT


@dataclass
class BotContext:
    """Everything handlers need at runtime — stashed in `app.bot_data['ctx']`."""

    cfg: Config
    state_path: Path
    bookings_path: Path
    chat_id: int

    @property
    def tz(self) -> ZoneInfo:
        return self.cfg.tz

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()

    def member_name_for(self, user_id: int) -> str | None:
        for m in self.cfg.group.members:
            if m.telegram_user_id and m.telegram_user_id == user_id:
                return m.name
        return None

    def is_admin(self, user_id: int) -> bool:
        return self.member_name_for(user_id) == self.cfg.group.admin

    def course_display(self, key: str) -> str:
        c = self.cfg.course_by_key(key)
        return c.display if c else key

    def member_names(self) -> list[str]:
        return [m.name for m in self.cfg.group.members]


def build_app(
    token: str,
    ctx: BotContext,
    post_init=None,
    post_shutdown=None,
) -> Application:
    """Construct the Application with all handlers wired. Caller starts it
    (`app.run_polling()` blocks; `app.initialize()` + `start()` for finer
    control).

    `post_init` / `post_shutdown` are optional async callbacks PTB invokes
    around the app lifecycle — used by `_cmd_run` to attach the scheduler.
    """
    builder = ApplicationBuilder().token(token)
    if post_init is not None:
        builder = builder.post_init(post_init)
    if post_shutdown is not None:
        builder = builder.post_shutdown(post_shutdown)
    app = builder.build()
    app.bot_data["ctx"] = ctx

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("tee", cmd_tee))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("avail", cmd_avail))
    app.add_handler(CommandHandler("out", cmd_out))
    app.add_handler(CommandHandler("in", cmd_in))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("full", cmd_full))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("unbook", cmd_unbook))
    app.add_handler(CommandHandler("courses", cmd_courses))
    app.add_handler(CommandHandler("garmin", cmd_garmin))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    # Booking callbacks (unified toggle, plus legacy cn/cx for old messages).
    app.add_handler(CallbackQueryHandler(cb_toggle, pattern=r"^tb:"))
    app.add_handler(CallbackQueryHandler(cb_confirm, pattern=r"^cn:"))
    app.add_handler(CallbackQueryHandler(cb_cancel, pattern=r"^cx:"))
    # Availability grid callbacks (weekly pattern + legacy date-toggle).
    app.add_handler(CallbackQueryHandler(cb_avail_toggle_weekly, pattern=r"^aw:"))
    app.add_handler(CallbackQueryHandler(cb_avail_toggle, pattern=r"^av:"))
    app.add_handler(CallbackQueryHandler(cb_noop, pattern=r"^noop$"))
    # Fallback: legacy per-slot voting (mock command).
    app.add_handler(CallbackQueryHandler(handle_callback))

    return app


def _ctx(context: ContextTypes.DEFAULT_TYPE) -> BotContext:
    return context.bot_data["ctx"]


# --------------------------------------------------------------------------- #
# Slash commands                                                              #
# --------------------------------------------------------------------------- #


_HELP_TEXT = (
    "golfbot commands:\n"
    "\n"
    "Tee times\n"
    "/tee       — last scan's filtered matches + bookings\n"
    "/full      — every slot in horizon, no filters (admin, slow)\n"
    "/scan      — trigger a fresh filtered scan now (admin)\n"
    "  In the digest, tap ✓ #N to confirm a booking (after booking externally),\n"
    "  and tap ↩️ Cancel to undo it.\n"
    "\n"
    "Availability\n"
    "/avail     — tap-to-toggle 7-day grid (recommended)\n"
    "/out  <date> [date ...]  — mark yourself OUT via text\n"
    "/in   <date> [date ...]  — mark yourself back IN via text\n"
    "  dates: mon, tue, wed... or today/tomorrow or 2026-05-20 or 5/20\n"
    "\n"
    "Status\n"
    "/status    — current bookings, horizon, pause flag, last poll\n"
    "/courses   — list courses being scanned\n"
    "\n"
    "Garmin\n"
    "/garmin    — sync rounds + deploy golf dashboard (admin)\n"
    "\n"
    "Notifications\n"
    "/pause     — mute auto-scan notifications\n"
    "/resume    — unmute\n"
    "\n"
    "Setup\n"
    "/whoami    — your Telegram user ID\n"
    "/help      — this message"
)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(_HELP_TEXT)


async def cmd_tee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-render the most recent scan's digest with current bookings + weather."""
    from datetime import datetime as _dt

    from golfbot import bookings as bookings_mod
    from golfbot import notifier as _notifier
    from golfbot import scanner as _scanner

    if update.message is None:
        return
    ctx = _ctx(context)
    state = store.load_state(ctx.state_path)
    last = state.get("last_scan")
    if not last:
        await update.message.reply_text(
            "No scan has run yet. The bot polls on a schedule — first scan will fire shortly."
        )
        return
    run_at = _dt.fromisoformat(last["run_at"])
    next_run_iso = last.get("next_run_at")
    next_run_at = _dt.fromisoformat(next_run_iso) if next_run_iso else None
    matches = last.get("matches", [])
    bookings = bookings_mod.load_bookings(state)
    weather = _scanner._weather_dict_for_render(state)
    text = _notifier.render_digest(
        matches=matches,
        run_at=run_at,
        next_run_at=next_run_at,
        cfg=ctx.cfg,
        bookings=bookings,
        weather=weather,
    )
    keyboard = _notifier.build_digest_keyboard(matches, bookings, cfg=ctx.cfg)
    await update.message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def cmd_avail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post an interactive availability grid: one row per date, one
    toggle button per registered member. Tap your own name to flip
    in/out. Taps from others get politely rejected."""
    from golfbot import availability as avail_mod

    if update.message is None:
        return
    ctx = _ctx(context)
    state = store.load_state(ctx.state_path)
    availability = avail_mod.load_availability(state)
    members = avail_mod.registered_members(ctx.cfg)
    if not members:
        await update.message.reply_text(
            "No members registered yet. DM the bot /whoami to get your ID, "
            "then ask the admin to add it to config.yaml."
        )
        return

    text, keyboard = build_avail_grid(ctx.cfg, availability, ctx.today())
    await update.message.reply_text(
        text, reply_markup=keyboard, parse_mode=ParseMode.HTML,
    )


def build_avail_grid(cfg, availability, today=None):
    """Build the (text, InlineKeyboardMarkup) pair for the weekly-pattern grid.

    Layout: one row per weekday (Mon..Sun). First button on each row is a
    no-op weekday label; remaining buttons are one per registered member,
    showing whether that member is IN (✅) or OUT (❌) on that weekday.
    Tapping toggles the member's weekly pattern.

    `today` arg is unused (kept for backward compat / tests).
    """
    from golfbot import availability as avail_mod

    members = avail_mod.registered_members(cfg)

    text = (
        "🗓 <b>Weekly Availability</b>\n"
        "Tap your name to toggle in/out for that weekday.\n"
        "<i>Per-date one-offs: /out 5/20 · /in 5/20</i>"
    )

    weekday_labels = [
        ("Mon", 0), ("Tue", 1), ("Wed", 2), ("Thu", 3),
        ("Fri", 4), ("Sat", 5), ("Sun", 6),
    ]

    rows = []
    for label, idx in weekday_labels:
        row = [InlineKeyboardButton(label, callback_data="noop")]
        for member in members:
            rec = availability.get(member)
            if rec is None:
                # default record: weekend off
                is_in = idx not in {5, 6}
            else:
                is_in = idx not in rec.out_weekdays
            icon = "✅" if is_in else "❌"
            row.append(InlineKeyboardButton(
                f"{icon} {member}",
                callback_data=f"aw:{member}:{idx}",
            ))
        rows.append(row)

    return text, InlineKeyboardMarkup(rows)


async def cb_avail_toggle(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle a tap on an availability button. Owner-only — taps on
    someone else's name get an alert."""
    from datetime import date as _date

    from golfbot import availability as avail_mod

    q = update.callback_query
    if q is None or q.data is None or q.from_user is None:
        return

    try:
        _prefix, name, date_iso = q.data.split(":", 2)
    except ValueError:
        await q.answer("Bad availability data.", show_alert=True)
        return

    ctx = _ctx(context)
    caller_name = ctx.member_name_for(q.from_user.id)
    if caller_name != name:
        if caller_name is None:
            await q.answer("You're not on the roster.", show_alert=True)
        else:
            await q.answer(f"Only {name} can toggle {name}'s availability.", show_alert=True)
        return

    try:
        target_date = _date.fromisoformat(date_iso)
    except ValueError:
        await q.answer("Bad date in callback.", show_alert=True)
        return

    state = store.load_state(ctx.state_path)
    availability = avail_mod.load_availability(state)
    if avail_mod.is_available(name, target_date, availability):
        avail_mod.set_out(name, [target_date], availability)
        new_label = "OUT"
    else:
        avail_mod.set_in(name, [target_date], availability)
        new_label = "IN"
    avail_mod.save_availability(state, availability)
    await store.save_state(ctx.state_path, state)

    _, keyboard = build_avail_grid(ctx.cfg, availability, ctx.today())
    await q.edit_message_reply_markup(reply_markup=keyboard)
    short = f"{target_date.strftime('%a')} {target_date.month}/{target_date.day}"
    await q.answer(f"{name} {new_label} for {short}")


async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Acknowledge no-op (date label) taps silently."""
    if update.callback_query:
        await update.callback_query.answer()


async def cb_avail_toggle_weekly(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Tap a weekly-pattern button. Toggles `out_weekdays` for the member."""
    from golfbot import availability as avail_mod

    q = update.callback_query
    if q is None or q.data is None or q.from_user is None:
        return

    try:
        _prefix, name, weekday_str = q.data.split(":", 2)
        weekday = int(weekday_str)
    except (ValueError, IndexError):
        await q.answer("Bad availability data.", show_alert=True)
        return
    if not 0 <= weekday <= 6:
        await q.answer("Bad weekday.", show_alert=True)
        return

    ctx = _ctx(context)
    caller_name = ctx.member_name_for(q.from_user.id)
    if caller_name != name:
        if caller_name is None:
            await q.answer("You're not on the roster.", show_alert=True)
        else:
            await q.answer(
                f"Only {name} can toggle {name}'s schedule.",
                show_alert=True,
            )
        return

    state = store.load_state(ctx.state_path)
    availability = avail_mod.load_availability(state)
    is_out_now = avail_mod.toggle_weekday(name, weekday, availability)
    avail_mod.save_availability(state, availability)
    await store.save_state(ctx.state_path, state)

    weekday_label = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][weekday]
    _, keyboard = build_avail_grid(ctx.cfg, availability)
    await q.edit_message_reply_markup(reply_markup=keyboard)
    await q.answer(f"{name} {'OUT' if is_out_now else 'IN'} on {weekday_label}s")


async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tap `✓ #N` on a digest match → record the booking."""
    from datetime import datetime as _dt

    from golfbot import bookings as bookings_mod
    from golfbot import notifier as _notifier

    q = update.callback_query
    if q is None or q.data is None or q.from_user is None:
        return

    try:
        _prefix, course_key, tee_date_iso, hhmm = q.data.split(":", 3)
    except ValueError:
        await q.answer("Bad confirm data.", show_alert=True)
        return

    ctx = _ctx(context)
    if not ctx.is_admin(q.from_user.id):
        await q.answer("Admin only.", show_alert=True)
        return

    state = store.load_state(ctx.state_path)
    last = state.get("last_scan") or {}
    matches = last.get("matches", [])

    target_match = None
    for m in matches:
        if (
            m.get("course_key") == course_key
            and m.get("tee_date") == tee_date_iso
            and m.get("tee_time", "")[:5].replace(":", "") == hhmm
        ):
            target_match = m
            break

    if target_match is None:
        await q.answer(
            "Match isn't in the last scan anymore. Try /scan to refresh.",
            show_alert=True,
        )
        return

    bookings = bookings_mod.load_bookings(state)
    booked_by = ctx.member_name_for(q.from_user.id) or ctx.cfg.group.admin
    bookings_mod.add_booking(bookings, target_match, booked_by, ctx.now())
    bookings_mod.save_bookings(state, bookings)
    await store.save_state(ctx.state_path, state)

    await _refresh_digest_message(q, ctx, state, last, bookings, matches)
    await q.answer(
        f"✅ Booked: {target_match['course_display']} "
        f"{target_match['tee_date']} {target_match['tee_time'][:5]}"
    )


async def cb_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tap `✓ #N` / `↩️ #N` on a digest row — toggles booked status.

    If the slot is currently booked, cancels it. If not, confirms it
    (replacing any existing booking on the same date — one per date).
    """
    from datetime import date as _date

    from golfbot import bookings as bookings_mod

    q = update.callback_query
    if q is None or q.data is None or q.from_user is None:
        return

    try:
        _prefix, course_key, tee_date_iso, hhmm = q.data.split(":", 3)
    except ValueError:
        await q.answer("Bad toggle data.", show_alert=True)
        return

    ctx = _ctx(context)
    if not ctx.is_admin(q.from_user.id):
        await q.answer("Admin only.", show_alert=True)
        return

    try:
        target_date = _date.fromisoformat(tee_date_iso)
    except ValueError:
        await q.answer("Bad date.", show_alert=True)
        return

    state = store.load_state(ctx.state_path)
    bookings = bookings_mod.load_bookings(state)

    existing = bookings.get(target_date)
    same_slot = (
        existing is not None
        and existing.get("course_key") == course_key
        and (existing.get("tee_time", "")[:5].replace(":", "") == hhmm)
    )

    if same_slot:
        # Cancel the booking for this slot.
        removed = bookings_mod.cancel_booking(bookings, target_date)
        bookings_mod.save_bookings(state, bookings)
        await store.save_state(ctx.state_path, state)
        last = state.get("last_scan") or {}
        matches = last.get("matches", [])
        await _refresh_digest_message(q, ctx, state, last, bookings, matches)
        course = (removed or {}).get("course_display", "")
        await q.answer(f"↩️ Cancelled: {course} {tee_date_iso}".strip())
        return

    # Find the match in last_scan to confirm.
    last = state.get("last_scan") or {}
    matches = last.get("matches", [])
    target_match = None
    for m in matches:
        if (
            m.get("course_key") == course_key
            and m.get("tee_date") == tee_date_iso
            and m.get("tee_time", "")[:5].replace(":", "") == hhmm
        ):
            target_match = m
            break
    if target_match is None:
        await q.answer(
            "Match isn't in the last scan anymore. Try /scan to refresh.",
            show_alert=True,
        )
        return

    booked_by = ctx.member_name_for(q.from_user.id) or ctx.cfg.group.admin
    bookings_mod.add_booking(bookings, target_match, booked_by, ctx.now())
    bookings_mod.save_bookings(state, bookings)
    await store.save_state(ctx.state_path, state)
    await _refresh_digest_message(q, ctx, state, last, bookings, matches)
    await q.answer(
        f"✅ Booked: {target_match['course_display']} "
        f"{target_match['tee_date']} {target_match['tee_time'][:5]}"
    )


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tap `↩️ Cancel <date>` on a booking → remove it."""
    from datetime import date as _date

    from golfbot import bookings as bookings_mod

    q = update.callback_query
    if q is None or q.data is None or q.from_user is None:
        return

    try:
        _prefix, date_iso = q.data.split(":", 1)
    except ValueError:
        await q.answer("Bad cancel data.", show_alert=True)
        return

    ctx = _ctx(context)
    if not ctx.is_admin(q.from_user.id):
        await q.answer("Admin only.", show_alert=True)
        return

    try:
        target_date = _date.fromisoformat(date_iso)
    except ValueError:
        await q.answer("Bad date in callback.", show_alert=True)
        return

    state = store.load_state(ctx.state_path)
    bookings = bookings_mod.load_bookings(state)
    removed = bookings_mod.cancel_booking(bookings, target_date)
    bookings_mod.save_bookings(state, bookings)
    await store.save_state(ctx.state_path, state)

    last = state.get("last_scan") or {}
    matches = last.get("matches", [])
    await _refresh_digest_message(q, ctx, state, last, bookings, matches)

    course = (removed or {}).get("course_display", "")
    await q.answer(f"↩️ Cancelled: {course} {target_date.isoformat()}".strip())


async def _refresh_digest_message(q, ctx, state, last, bookings, matches) -> None:
    """Edit the message backing the callback to reflect new bookings state."""
    from datetime import datetime as _dt

    from telegram.constants import ParseMode

    from golfbot import notifier as _notifier
    from golfbot import scanner as _scanner

    run_at_iso = last.get("run_at")
    if run_at_iso:
        run_at = _dt.fromisoformat(run_at_iso)
    else:
        run_at = ctx.now()
    next_run_iso = last.get("next_run_at")
    next_run_at = _dt.fromisoformat(next_run_iso) if next_run_iso else None
    weather = _scanner._weather_dict_for_render(state)
    text = _notifier.render_digest(
        matches=matches,
        run_at=run_at,
        next_run_at=next_run_at,
        cfg=ctx.cfg,
        bookings=bookings,
        weather=weather,
    )
    keyboard = _notifier.build_digest_keyboard(matches, bookings, cfg=ctx.cfg)
    try:
        await q.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        # Common: "message is not modified" if the rendered text didn't change.
        pass


async def cmd_out(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cmd_avail_mutation(update, context, mark_out=True)


async def cmd_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cmd_avail_mutation(update, context, mark_out=False)


async def _cmd_avail_mutation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    mark_out: bool,
) -> None:
    from golfbot import availability as avail_mod

    if update.message is None or update.effective_user is None:
        return
    ctx = _ctx(context)
    name = ctx.member_name_for(update.effective_user.id)
    if name is None:
        await update.message.reply_text(
            "You're not on the roster yet. DM the bot /whoami to get your ID, "
            "then ask the admin to add it to config.yaml."
        )
        return

    args = list(context.args or [])
    if not args:
        cmd = "out" if mark_out else "in"
        await update.message.reply_text(
            f"Usage: /{cmd} <date> [date2 ...]\n"
            "Examples: /out wed · /out mon tue thu · /out 2026-05-20 · /out 5/20"
        )
        return

    today = ctx.today()
    parsed: list = []
    bad: list[str] = []
    for arg in args:
        d = avail_mod.parse_date_arg(arg, today)
        if d is None:
            bad.append(arg)
        else:
            parsed.append(d)
    if bad:
        await update.message.reply_text(
            f"Didn't recognize: {', '.join(bad)}\n"
            "Try: mon/tue/wed/.../sun, today, tomorrow, 2026-05-20, or 5/20"
        )
        return

    state = store.load_state(ctx.state_path)
    availability = avail_mod.load_availability(state)
    if mark_out:
        avail_mod.set_out(name, parsed, availability)
        verb, icon = "OUT", "❌"
    else:
        avail_mod.set_in(name, parsed, availability)
        verb, icon = "IN", "✅"
    avail_mod.save_availability(state, availability)
    await store.save_state(ctx.state_path, state)

    dates_str = ", ".join(d.strftime("%a %-m/%-d") for d in parsed)
    await update.message.reply_text(f"{icon} {name} marked {verb} for: {dates_str}")


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply with the caller's Telegram user id (used for onboarding)."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    await update.message.reply_text(
        f"Your Telegram user ID is {user.id}.\n"
        "Send this to the admin to be added as a roster member."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    ctx = _ctx(context)
    state = store.load_state(ctx.state_path)
    await update.message.reply_text(
        notifier.render_status(state, ctx.cfg, ctx.today())
    )


async def cmd_courses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    ctx = _ctx(context)
    lines = ["Watched courses:"]
    for c in ctx.cfg.courses:
        lines.append(f"• {c.display} (tier {c.tier})")
    await update.message.reply_text("\n".join(lines))


async def cmd_garmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: run the sibling garmin-golf update/deploy script and relay
    its one-line summary.

    `../garmin-golf/update.sh` syncs new rounds, runs the AI coach, rebuilds +
    deploys the dashboard, and prints a Telegram-friendly summary as its final
    stdout line (everything verbose goes to its own log). We run it off the
    event loop and echo back that last line (equivalent to `tail -1`).
    """
    import asyncio

    if update.message is None or update.effective_user is None:
        return
    ctx = _ctx(context)
    if not ctx.is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return

    script = _garmin_script_path()
    if not script.exists():
        await update.message.reply_text(f"Update script not found: {script}")
        return

    caller = ctx.member_name_for(update.effective_user.id) or update.effective_user.id
    log.info("garmin: update triggered by %s — running %s", caller, script)
    placeholder = await update.message.reply_text("🔄 Running The Turn update…")
    try:
        proc = await asyncio.create_subprocess_exec(
            str(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=600,
        )
    except TimeoutError:
        log.warning("garmin: update timed out after 600s")
        await placeholder.edit_text("⚠️ Garmin update timed out after 10 min.")
        return
    except Exception:
        log.exception("garmin: failed to run update script")
        await placeholder.edit_text("⚠️ Failed to run update — see bot logs.")
        return

    stdout = stdout_b.decode("utf-8", errors="replace")
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if lines:
        summary = lines[-1].strip()
        log.info("garmin: update done (exit %s) — %s", proc.returncode, summary)
    else:
        # No stdout summary — surface stderr tail / exit code so failures
        # aren't silent.
        err = stderr_b.decode("utf-8", errors="replace").strip()
        err_tail = err.splitlines()[-1] if err else ""
        summary = (
            f"⚠️ Update produced no summary (exit {proc.returncode})"
            + (f": {err_tail}" if err_tail else "")
        )
        log.warning(
            "garmin: update produced no stdout summary (exit %s); stderr tail: %s",
            proc.returncode, err_tail or "(none)",
        )
    await placeholder.edit_text(summary)


async def cmd_full(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: show every available 18-hole slot in horizon, no filters.

    Reads from the cached raw slots stored on the last scheduled scan, so
    no fresh API calls are made. Falls back to a live fetch only when the
    cache is missing (first run, or after a state.json wipe).
    """
    from telegram.constants import ParseMode

    from golfbot import notifier as _notifier
    from golfbot.providers.base import RawSlot

    if update.message is None or update.effective_user is None:
        return
    ctx = _ctx(context)
    if not ctx.is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return

    state = store.load_state(ctx.state_path)
    last = state.get("last_scan") or {}
    raw_dicts = last.get("raw_slots")

    if raw_dicts:
        run_at_iso = last.get("run_at")
        from datetime import datetime as _dt
        run_at = _dt.fromisoformat(run_at_iso) if run_at_iso else ctx.now()
        slots = [RawSlot.from_dict(d) for d in raw_dicts]
        text = _notifier.render_full_listing(slots, ctx.cfg, run_at)
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    # No cache yet — fall back to a fresh fetch (one-time cost; the next
    # scheduled scan populates the cache for future /full calls).
    from datetime import timedelta as _timedelta

    from golfbot import scanner as _scanner
    from golfbot.availability import _DAY_INDEX
    from golfbot.horizon import current_window

    providers = context.application.bot_data.get("providers")
    if providers is None:
        await update.message.reply_text(
            "Providers aren't initialized. Are you on `golfbot run`?"
        )
        return

    placeholder = await update.message.reply_text(
        "🔄 No cached scan yet — fetching now, ~30-60s. Future /full calls "
        "will be instant once the scheduled scan populates the cache."
    )

    today = ctx.today()
    start, end = current_window(
        today=today,
        start_offset_days=ctx.cfg.search.start_offset_days,
        horizon_days=ctx.cfg.search.horizon_days,
    )
    days_set = {_DAY_INDEX[name] for name in ctx.cfg.search.days_of_week}
    dates: list = []
    d = start
    while d <= end:
        if d.weekday() in days_set:
            dates.append(d)
        d = d + _timedelta(days=1)

    try:
        slots = await _scanner.run_full_scan(ctx.cfg, providers, dates, min_players=2)
    except Exception as e:
        await placeholder.edit_text(f"Full scan failed: {e}")
        return

    slots = [s for s in slots if s.holes == 18]
    text = _notifier.render_full_listing(slots, ctx.cfg, ctx.now())
    try:
        await placeholder.edit_text(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        )
    except Exception as e:
        await placeholder.edit_text(f"Failed to render listing: {e}")


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: trigger an immediate scan. Reuses the same job the
    scheduler runs, so dedup + digest behavior are identical."""
    if update.message is None or update.effective_user is None:
        return
    ctx = _ctx(context)
    if not ctx.is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return

    scan_job = context.application.bot_data.get("scan_job")
    if scan_job is None:
        await update.message.reply_text(
            "Scanner isn't running. Are you on the long-running bot (`golfbot run`)?"
        )
        return

    await update.message.reply_text("🔄 Scanning now…")
    try:
        await scan_job(force=True)
    except Exception as e:
        await update.message.reply_text(f"Scan failed: {e}")
        return
    # If a digest fired, the user has already seen it (separate message).
    # Otherwise, point them at /tee for the current cached list.
    await update.message.reply_text(
        "✅ Scan complete. /tee to see current matches."
    )


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    ctx = _ctx(context)
    if not ctx.is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return
    state = store.load_state(ctx.state_path)
    actions.set_paused(state, True, ctx.now())
    await store.save_state(ctx.state_path, state)
    await update.message.reply_text("🔕 Notifications paused.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    ctx = _ctx(context)
    if not ctx.is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return
    state = store.load_state(ctx.state_path)
    actions.set_paused(state, False, None)
    await store.save_state(ctx.state_path, state)
    await update.message.reply_text("🔔 Notifications resumed.")


async def cmd_unbook(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Roll back the most recent booking lock."""
    if update.message is None or update.effective_user is None:
        return
    ctx = _ctx(context)
    if not ctx.is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return
    state = store.load_state(ctx.state_path)
    booked = [s for s in state["tee_times"] if s.get("status") == "booked"]
    if not booked:
        await update.message.reply_text("No active booking to undo.")
        return
    slot_dict = booked[-1]
    _, undo_record = actions.undo_booking(state, slot_dict["id"], ctx.now())
    store.append_booking(ctx.bookings_path, undo_record)
    await store.save_state(ctx.state_path, state)
    slot = TeeTimeSlot.from_dict(slot_dict)
    if slot.message_id is not None:
        await notifier.update_tally(
            context.bot, ctx.chat_id, slot,
            ctx.course_display(slot.course_key), ctx.member_names(),
        )
    await update.message.reply_text(
        f"↩️ Undid booking: {ctx.course_display(slot.course_key)} · "
        f"{slot.tee_date.isoformat()} {slot.tee_time.strftime('%H:%M')}"
    )


# --------------------------------------------------------------------------- #
# Callback queries (inline button taps)                                       #
# --------------------------------------------------------------------------- #


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or q.data is None or q.from_user is None:
        return

    parts = q.data.split(":", 1)
    if len(parts) != 2:
        await q.answer("Bad callback data.", show_alert=True)
        return
    action, slot_id = parts

    ctx = _ctx(context)
    member = ctx.member_name_for(q.from_user.id)
    if member is None:
        await q.answer("You are not on the roster.", show_alert=True)
        return
    if action in _ADMIN_ACTIONS and member != ctx.cfg.group.admin:
        await q.answer("Admin only.", show_alert=True)
        return

    state = store.load_state(ctx.state_path)

    # The "pause" action is a global toggle — doesn't change the slot.
    if action == "pause":
        actions.set_paused(state, True, ctx.now())
        await store.save_state(ctx.state_path, state)
        await q.answer("🔕 Notifications paused.")
        return

    # All other actions mutate the slot. Catch ActionError so we can give
    # the user a clean alert rather than a stack trace in the logs.
    try:
        if action == "yes":
            actions.record_vote(state, slot_id, member, "yes", ctx.now())
            confirmation = "✅ Vote recorded."
        elif action == "no":
            actions.record_vote(state, slot_id, member, "no", ctx.now())
            confirmation = "❌ Out for the day."
        elif action == "skip":
            actions.mark_skipped(state, slot_id)
            confirmation = "🚫 Slot skipped."
        elif action == "book":
            _, booking = actions.mark_booked(state, slot_id, member, ctx.now())
            store.append_booking(ctx.bookings_path, booking)
            confirmation = "📖 Booked."
        elif action == "undo":
            _, undo_record = actions.undo_booking(state, slot_id, ctx.now())
            store.append_booking(ctx.bookings_path, undo_record)
            confirmation = "↩️ Undone."
        else:
            await q.answer(f"Unknown action: {action}", show_alert=True)
            return
    except actions.ActionError as e:
        await q.answer(str(e), show_alert=True)
        return

    await store.save_state(ctx.state_path, state)

    # Re-render the message according to the slot's new status.
    slot_dict = actions.find_slot(state, slot_id)
    slot = TeeTimeSlot.from_dict(slot_dict)
    course_display = ctx.course_display(slot.course_key)

    if slot.status == "open":
        await notifier.update_tally(
            context.bot, ctx.chat_id, slot, course_display, ctx.member_names(),
        )
    elif slot.status == "booked":
        booked_at = datetime.fromisoformat(slot_dict["booked_at"])
        await notifier.mark_booked(
            context.bot, ctx.chat_id, slot, course_display, member, booked_at,
        )
    elif slot.status == "skipped":
        await notifier.mark_skipped_msg(
            context.bot, ctx.chat_id, slot, course_display,
        )

    await q.answer(confirmation)
