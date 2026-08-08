"""Telegram bot wiring: command handlers and the application builder.

The pure state mutations live in `actions.py`; pure rendering lives in
`notifier.py`. This module is the glue: it authorizes callers, loads/saves
state through `store.py`, calls into actions, then asks the notifier to
render.

Cross-process safety: a separate `golfbot mock` invocation also writes to
state.json. Because writes are atomic-rename and reads are on-demand
(no in-memory cache), the running bot always sees fresh state on each
command.

v2: no callback handlers. The group votes and books offline, so the only
button anywhere is a URL link (docs/decisions/0006-gold-star-pivot.md).

See docs/SPEC.md > Telegram commands.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from golfbot import actions, notifier, store
from golfbot.config import Config

log = logging.getLogger(__name__)

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
    app.add_handler(CommandHandler("full", cmd_full))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("courses", cmd_courses))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("garmin", cmd_garmin))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    # No CallbackQueryHandlers: v2's only button is a URL button, which
    # produces no callback data (docs/SPEC.md > Gold Star alert).

    return app


def _ctx(context: ContextTypes.DEFAULT_TYPE) -> BotContext:
    return context.bot_data["ctx"]


# --------------------------------------------------------------------------- #
# Slash commands                                                              #
# --------------------------------------------------------------------------- #


_HELP_TEXT = (
    "golfbot — Gold Star scanner\n"
    "\n"
    "You get pinged only when an all-star course opens at a premium\n"
    "weekday time. Booking happens offline; the bot just watches.\n"
    "\n"
    "Tee times\n"
    "/full      — every open slot in horizon, all courses, all times\n"
    "/scan      — force a scan right now\n"
    "\n"
    "Status\n"
    "/status    — all-star set, premium window, last/next scan\n"
    "/courses   — all scanned courses (⭐ = can alert)\n"
    "\n"
    "Garmin\n"
    "/garmin    — sync rounds + deploy golf dashboard (admin)\n"
    "\n"
    "Notifications\n"
    "/pause     — mute alerts (admin)\n"
    "/resume    — unmute (admin)\n"
    "\n"
    "Setup\n"
    "/whoami    — your Telegram user ID\n"
    "/help      — this message"
)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(_HELP_TEXT)


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
    # Every course is scanned; ⭐ marks the ones that can fire an alert.
    lines = ["Watched courses (⭐ = all-star, can alert):"]
    for c in ctx.cfg.courses:
        lines.append(f"• {'⭐ ' if c.all_star else ''}{c.display}")
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

    from golfbot import notifier as _notifier
    from golfbot.providers.base import RawSlot

    if update.message is None:
        return
    ctx = _ctx(context)

    state = store.load_state(ctx.state_path)
    raw_dicts = state.get("raw_slots")

    if raw_dicts:
        run_at_iso = state.get("last_poll_at")
        from datetime import datetime as _dt
        run_at = _dt.fromisoformat(run_at_iso) if run_at_iso else ctx.now()
        slots = [RawSlot.from_dict(d) for d in raw_dicts]
        from golfbot import scanner as _scanner
        text = _notifier.render_full_listing(
            slots, ctx.cfg, run_at, weather=_scanner._weather_dict_for_render(state),
        )
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
    from golfbot.horizon import current_window
    from golfbot.pipeline import is_desired_day

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
    dates: list = []
    d = start
    while d <= end:
        if is_desired_day(d, ctx.cfg.search.days_of_week):
            dates.append(d)
        d = d + _timedelta(days=1)

    try:
        slots = await _scanner.run_full_scan(ctx.cfg, providers, dates, min_players=1)
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
    """Trigger an immediate scan. Reuses the same job the scheduler runs,
    so dedup + alert behavior are identical."""
    if update.message is None:
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
    # If an alert fired, the group has already seen it (separate message).
    await update.message.reply_text(
        "✅ Scan complete. /full to see everything that's open."
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
