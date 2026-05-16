"""Telegram message rendering + send/edit.

The pure renderers (`render_*` and `build_keyboard_*`) take only what they
need — slot, display strings, member names — and have no Telegram-runtime
dependencies aside from `InlineKeyboardMarkup` types. The async wrappers
at the bottom call the bot.

Callback-data format: ``"{action}:{slot_id}"`` where action is one of
``yes``, ``no``, ``book``, ``skip``, ``pause``, ``undo``.

See SPEC.md > Notification mocks.
"""
from __future__ import annotations

from datetime import date, datetime, time

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from golfbot.config import Config
from golfbot.models import TeeTimeSlot

# --------------------------------------------------------------------------- #
# Formatting helpers                                                          #
# --------------------------------------------------------------------------- #


def _fmt_date(d: date) -> str:
    """e.g. 'Sat May 23' (no leading zero on the day)."""
    return d.strftime("%a %b ") + str(d.day)


def _fmt_time(t: time) -> str:
    """e.g. '8:00 AM' (no leading zero on the hour)."""
    s = t.strftime("%I:%M %p")
    return s[1:] if s.startswith("0") else s


def _fmt_clock(dt: datetime) -> str:
    """e.g. '2:14 PM'."""
    return _fmt_time(dt.time())


# --------------------------------------------------------------------------- #
# Pure renderers                                                              #
# --------------------------------------------------------------------------- #


def render_open(slot: TeeTimeSlot, course_display: str, all_member_names: list[str]) -> str:
    """Render an OPEN slot notification (initial or with running tally)."""
    lines = [
        f"🏌️ Tee Time Found — Grade {slot.grade}",
        "",
        f"{course_display} · {_fmt_date(slot.tee_date)}",
        f"{_fmt_time(slot.tee_time)} · {slot.players_open} players · {slot.holes} holes",
        "",
        "👥 Availability:",
    ]
    lines.extend(_format_tally(slot.votes, all_member_names))
    return "\n".join(lines)


def render_booked(
    slot: TeeTimeSlot,
    course_display: str,
    booked_by: str,
    booked_at: datetime,
) -> str:
    """Render the BOOKED state."""
    yes_names = sorted(n for n, v in slot.votes.items() if v.vote == "yes")
    no_names = sorted(n for n, v in slot.votes.items() if v.vote == "no")

    lines = [
        "🏌️ BOOKED ✅",
        "",
        f"{course_display} · {_fmt_date(slot.tee_date)}",
        f"{_fmt_time(slot.tee_time)} · {slot.players_open} players · {slot.holes} holes",
        "",
        "👥 Final roster:",
        f"✅ Yes: {', '.join(yes_names) if yes_names else '—'}",
        f"❌ No:  {', '.join(no_names) if no_names else '—'}",
        "",
        f"Booked by {booked_by} at {_fmt_clock(booked_at)}",
        f"Notifications paused through {_fmt_date(slot.tee_date)}.",
    ]
    return "\n".join(lines)


def render_expired(slot: TeeTimeSlot, course_display: str) -> str:
    """Render the auto-archived expired state."""
    return (
        f"⌛ Expired — {course_display} tee time was "
        f"{_fmt_date(slot.tee_date)}, {_fmt_time(slot.tee_time)}"
    )


def render_skipped(slot: TeeTimeSlot, course_display: str) -> str:
    """Render the admin-skipped state."""
    return (
        f"🚫 Skipped — {course_display} · "
        f"{_fmt_date(slot.tee_date)}, {_fmt_time(slot.tee_time)}"
    )


def render_status(state: dict, cfg: Config, today: date) -> str:
    """Render `/status` text."""
    from golfbot.horizon import current_window

    course_names = ", ".join(c.display for c in cfg.courses)

    booked_through = (
        date.fromisoformat(state["horizon_override_until"])
        if state.get("horizon_override_until") else None
    )
    start, end = current_window(
        today=today,
        start_offset_days=cfg.search.start_offset_days,
        horizon_days=cfg.search.horizon_days,
        booked_through=booked_through,
    )

    days = ", ".join(d.capitalize()[:3] for d in cfg.search.days_of_week)
    ideal = cfg.time_windows.ideal
    accept = cfg.time_windows.acceptable

    from golfbot import bookings as bookings_mod
    bookings = bookings_mod.load_bookings(state)
    booking_summary = _bookings_summary(bookings) if bookings else _active_booking_summary(state)
    paused = bool(state.get("paused"))

    return "\n".join([
        f"📡 Watching: {course_names}",
        f"🗓  Horizon: {_fmt_date(start)} → {_fmt_date(end)} ({cfg.search.horizon_days} days)",
        f"🎯 Days: {days}",
        f"⏰ Ideal: {_fmt_time(ideal.start)}–{_fmt_time(ideal.end)}"
        f" · Acceptable: {_fmt_time(accept.start)}–{_fmt_time(accept.end)}",
        f"📌 Bookings: {booking_summary}",
        f"🔔 Notifications: {'OFF (paused)' if paused else 'ON'}",
    ])


def _bookings_summary(bookings: dict[date, dict]) -> str:
    parts = []
    for d in sorted(bookings.keys()):
        b = bookings[d]
        tee_time = time.fromisoformat(b["tee_time"])
        parts.append(
            f"{d.strftime('%a')} {d.month}/{d.day} {_fmt_time(tee_time)} {b['course_display']}"
        )
    return "; ".join(parts)


def _active_booking_summary(state: dict) -> str:
    """Find the most recent booked slot in state and summarize it.

    Returns '— (none)' if there isn't one.
    """
    booked = [s for s in state.get("tee_times", []) if s.get("status") == "booked"]
    if not booked:
        return "— (none)"
    s = booked[-1]
    d = date.fromisoformat(s["tee_date"])
    t = time.fromisoformat(s["tee_time"])
    return f"{s['course_key']} · {_fmt_date(d)}, {_fmt_time(t)}"


def _format_tally(votes: dict, all_member_names: list[str]) -> list[str]:
    """The 'Availability' block."""
    yes = sorted(n for n, v in votes.items() if v.vote == "yes")
    no = sorted(n for n, v in votes.items() if v.vote == "no")
    waiting = sorted(set(all_member_names) - set(votes.keys()))
    return [
        f"✅ Yes ({len(yes)}): {', '.join(yes) if yes else '—'}",
        f"❌ No ({len(no)}):  {', '.join(no) if no else '—'}",
        f"⏳ Waiting:   {', '.join(waiting) if waiting else '—'}",
    ]


# --------------------------------------------------------------------------- #
# Keyboard builders                                                           #
# --------------------------------------------------------------------------- #


def build_keyboard_open(slot: TeeTimeSlot) -> InlineKeyboardMarkup:
    """Three rows: URL link, vote buttons, admin action buttons."""
    sid = slot.id
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Open booking page", url=slot.booking_url)],
        [
            InlineKeyboardButton("✅ Yes", callback_data=f"yes:{sid}"),
            InlineKeyboardButton("❌ No", callback_data=f"no:{sid}"),
        ],
        [
            InlineKeyboardButton("📖 Booked it", callback_data=f"book:{sid}"),
            InlineKeyboardButton("🚫 Skip", callback_data=f"skip:{sid}"),
            InlineKeyboardButton("🔕 Pause", callback_data=f"pause:{sid}"),
        ],
    ])


def build_keyboard_booked(slot: TeeTimeSlot) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↩️ Undo", callback_data=f"undo:{slot.id}")],
    ])


# --------------------------------------------------------------------------- #
# Async Telegram API wrappers                                                 #
# --------------------------------------------------------------------------- #


async def send_new_slot(
    bot: Bot,
    chat_id: int,
    slot: TeeTimeSlot,
    course_display: str,
    all_member_names: list[str],
) -> int:
    """Send the initial OPEN-state message. Returns the Telegram message_id."""
    msg = await bot.send_message(
        chat_id=chat_id,
        text=render_open(slot, course_display, all_member_names),
        reply_markup=build_keyboard_open(slot),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    return msg.message_id


async def update_tally(
    bot: Bot,
    chat_id: int,
    slot: TeeTimeSlot,
    course_display: str,
    all_member_names: list[str],
) -> None:
    """Edit an OPEN-state message in place to reflect updated votes."""
    assert slot.message_id is not None, "slot must have message_id to update"
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=slot.message_id,
        text=render_open(slot, course_display, all_member_names),
        reply_markup=build_keyboard_open(slot),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def mark_booked(
    bot: Bot,
    chat_id: int,
    slot: TeeTimeSlot,
    course_display: str,
    booked_by: str,
    booked_at: datetime,
) -> None:
    """Edit the message into BOOKED form (keeps only the Undo button)."""
    assert slot.message_id is not None
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=slot.message_id,
        text=render_booked(slot, course_display, booked_by, booked_at),
        reply_markup=build_keyboard_booked(slot),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def mark_expired(
    bot: Bot,
    chat_id: int,
    slot: TeeTimeSlot,
    course_display: str,
) -> None:
    """Edit the message into the ⌛ Expired state. Strips all buttons."""
    assert slot.message_id is not None
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=slot.message_id,
        text=render_expired(slot, course_display),
        reply_markup=None,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# --------------------------------------------------------------------------- #
# Digest model — used by the scheduled scanner                                #
# --------------------------------------------------------------------------- #


def render_digest(
    matches: list[dict],
    run_at: datetime,
    next_run_at: datetime | None,
    cfg: Config,
    bookings: dict[date, dict] | None = None,
) -> str:
    """Render the per-scan digest message as Telegram HTML.

    `matches` is a list of plain dicts (see `scanner.match_to_dict`).
    `bookings` (optional) is a {date: booking_record} map — when present,
    a `📌 BOOKED` section is shown at the top and matches that correspond
    to existing bookings are hidden from the "available" list.
    """
    from golfbot import bookings as bookings_mod

    bookings = bookings or {}
    horizon = cfg.search.horizon_days
    title = f"🏌️ <b>Tee Times</b> — {_fmt_clock(run_at)}"

    # Visible matches = those NOT already booked.
    visible = [m for m in matches if not bookings_mod.match_is_booked(m, bookings)]

    sections: list[str] = [title]

    if bookings:
        sections.append("")
        sections.append("<b>📌 BOOKED</b>")
        for booking_date in sorted(bookings.keys()):
            sections.append(_render_booking_line(bookings[booking_date]))

    if visible:
        sorted_visible = sorted(visible, key=lambda m: (m["tee_date"], m["tee_time"]))
        sections.append("")
        sections.append(
            f"{len(visible)} available match{'es' if len(visible) != 1 else ''} "
            f"(next {horizon} days):"
        )
        sections.append("")
        for i, m in enumerate(sorted_visible, 1):
            sections.append(f"<b>{i}.</b> " + _render_digest_line(m))
    elif not bookings:
        sections.append("")
        sections.append(f"No matches in the next {horizon} days.")
        sections.append(f"Watching {len(cfg.courses)} course(s).")
    else:
        sections.append("")
        sections.append("No other available matches.")

    sections.append("")
    sections.append(_render_footer(run_at, next_run_at))
    return "\n".join(sections)


def _render_booking_line(b: dict) -> str:
    """A single bullet in the BOOKED section."""
    import html as _html
    tee_date = date.fromisoformat(b["tee_date"])
    tee_time = time.fromisoformat(b["tee_time"])
    dow = tee_date.strftime("%a")
    d = f"{tee_date.month}/{tee_date.day}"
    course = _html.escape(b["course_display"])
    roster = _format_roster(
        b.get("members_in") or [],
        b.get("members_out") or [],
    )
    line = f"• <b>{dow} {d}</b> · <b>{_fmt_time(tee_time)}</b> · <b>{course}</b>"
    if roster:
        line += f" · {roster}"
    return line


def build_digest_keyboard(
    matches: list[dict],
    bookings: dict[date, dict] | None = None,
) -> "InlineKeyboardMarkup":
    """Compute the inline keyboard for a digest message.

    Layout:
      • Up to 2 cancel buttons per row for each existing booking.
      • Up to 3 confirm `✓ #N` buttons per row for each visible match.
      • Match numbers must align with the numbering in `render_digest`.
    """
    from golfbot import bookings as bookings_mod

    bookings = bookings or {}
    rows: list[list[InlineKeyboardButton]] = []

    # Cancel buttons (one per booking, sorted by date, 2 per row).
    if bookings:
        cancel_btns: list[InlineKeyboardButton] = []
        for booking_date in sorted(bookings.keys()):
            short = f"{booking_date.strftime('%a')} {booking_date.month}/{booking_date.day}"
            cancel_btns.append(InlineKeyboardButton(
                f"↩️ Cancel {short}",
                callback_data=f"cx:{booking_date.isoformat()}",
            ))
        for i in range(0, len(cancel_btns), 2):
            rows.append(cancel_btns[i:i + 2])

    # Confirm buttons (one per visible match, sorted, 3 per row).
    visible = [m for m in matches if not bookings_mod.match_is_booked(m, bookings)]
    if visible:
        sorted_visible = sorted(visible, key=lambda m: (m["tee_date"], m["tee_time"]))
        confirm_btns: list[InlineKeyboardButton] = []
        for i, m in enumerate(sorted_visible, 1):
            hhmm = m["tee_time"].replace(":", "")[:4]
            confirm_btns.append(InlineKeyboardButton(
                f"✓ #{i}",
                callback_data=f"cn:{m['course_key']}:{m['tee_date']}:{hhmm}",
            ))
        for i in range(0, len(confirm_btns), 3):
            rows.append(confirm_btns[i:i + 3])

    return InlineKeyboardMarkup(rows)


def _render_digest_line(m: dict) -> str:
    """One line per match. Format:
    'A · Mon 5/18 · 7:30 AM · Roy Kizer · 3 open · Colby+Ed (Steve out) · $45 · <a>book</a>'."""
    import html as _html
    tee_date = date.fromisoformat(m["tee_date"])
    tee_time = time.fromisoformat(m["tee_time"])

    dow = tee_date.strftime("%a")
    d = f"{tee_date.month}/{tee_date.day}"
    t = _fmt_time(tee_time)

    grade = m["grade"]
    course = _html.escape(m["course_display"])
    players = m["players_available"]
    price = m.get("price_usd")
    price_str = f"${price:.0f}" if price else None

    parts = [
        f"<b>{grade}</b>",
        f"{dow} {d}",
        t,
        course,
        f"{players} open",
    ]
    roster_str = _format_roster(m.get("members_in") or [], m.get("members_out") or [])
    if roster_str:
        parts.append(roster_str)
    if price_str:
        parts.append(price_str)
    parts.append(f'<a href="{_html.escape(m["booking_url"], quote=True)}">book</a>')
    return " · ".join(parts)


def _format_roster(members_in: list[str], members_out: list[str]) -> str:
    """e.g. 'Colby+Ed (Steve out)' or 'Colby' or ''."""
    if not members_in and not members_out:
        return ""
    in_part = "+".join(members_in) if members_in else "—"
    if members_out:
        return f"{in_part} ({', '.join(members_out)} out)"
    return in_part


def _render_footer(run_at: datetime, next_run_at: datetime | None) -> str:
    if next_run_at:
        delta = next_run_at - run_at
        mins = max(0, int(delta.total_seconds() // 60))
        if mins >= 60:
            human = f"~{mins // 60}h"
        else:
            human = f"~{mins}m"
        return f"<i>Next scan: {human}</i>  ·  /tee · /pause · /help"
    return "<i>Updated just now</i>  ·  /tee · /pause · /help"


async def send_digest(
    bot: Bot,
    chat_id: int,
    matches: list[dict],
    run_at: datetime,
    next_run_at: datetime | None,
    cfg: Config,
    bookings: dict[date, dict] | None = None,
) -> int:
    """Send the digest message; return Telegram message_id."""
    msg = await bot.send_message(
        chat_id=chat_id,
        text=render_digest(matches, run_at, next_run_at, cfg, bookings=bookings),
        reply_markup=build_digest_keyboard(matches, bookings),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    return msg.message_id


async def mark_skipped_msg(
    bot: Bot,
    chat_id: int,
    slot: TeeTimeSlot,
    course_display: str,
) -> None:
    """Edit the message into the 🚫 Skipped state. Strips all buttons."""
    assert slot.message_id is not None
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=slot.message_id,
        text=render_skipped(slot, course_display),
        reply_markup=None,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
