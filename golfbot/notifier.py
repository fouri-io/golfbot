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


def _humanize_delta(delta_seconds: int) -> str:
    """e.g. 'just now', '5m ago' (when given positive delta from past),
    or 'in 55m' (when given negative delta to a future event)."""
    if -60 < delta_seconds < 60:
        return "just now"
    is_past = delta_seconds > 0
    sec = abs(delta_seconds)
    mins = sec // 60
    if mins < 60:
        body = f"{mins}m"
    else:
        hours, rem = divmod(mins, 60)
        body = f"{hours}h" if rem == 0 else f"{hours}h {rem}m"
    return f"{body} ago" if is_past else f"in {body}"


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

    now = datetime.now(cfg.tz)
    last_scan_line = _stamp_line(
        "🔁 Last scan", state.get("last_poll_at"), now,
    )
    last_digest_line = _stamp_line(
        "📨 Last digest", state.get("last_digest_at"), now,
        empty="— (none yet)",
    )

    return "\n".join([
        f"📡 Watching: {course_names}",
        f"🗓  Horizon: {_fmt_date(start)} → {_fmt_date(end)} ({cfg.search.horizon_days} days)",
        f"🎯 Days: {days}",
        f"⏰ Ideal: {_fmt_time(ideal.start)}–{_fmt_time(ideal.end)}"
        f" · Acceptable: {_fmt_time(accept.start)}–{_fmt_time(accept.end)}",
        f"📌 Bookings: {booking_summary}",
        last_scan_line,
        last_digest_line,
        f"🔔 Notifications: {'OFF (paused)' if paused else 'ON'}",
    ])


_FULL_MAX_TIMES_PER_COURSE = 10
_TELEGRAM_TEXT_LIMIT = 4096


def render_full_listing(
    slots: list,    # list[RawSlot]; importing the type would create a cycle
    cfg: Config,
    run_at: datetime,
) -> str:
    """Render every slot in `slots`, grouped by date then course.

    Each (course, date) cell shows the count + up to
    `_FULL_MAX_TIMES_PER_COURSE` earliest times. Output is truncated to fit
    in a single Telegram message (4096-char limit) with a note when cut.
    """
    import html as _html
    from collections import defaultdict

    course_display: dict[str, str] = {c.key: c.display for c in cfg.courses}

    # date -> course_key -> list[RawSlot]
    by_date: dict = defaultdict(lambda: defaultdict(list))
    for s in slots:
        by_date[s.tee_date][s.course_key].append(s)

    header = [
        f"🏌️ <b>All Slots</b> — {_fmt_clock(run_at)}",
        f"<i>{len(cfg.courses)} courses · "
        f"{', '.join(d[:3].capitalize() for d in cfg.search.days_of_week)} · 18-hole</i>",
        "",
    ]
    body_lines: list[str] = []
    total = 0

    for d in sorted(by_date.keys()):
        course_slots = by_date[d]
        day_total = sum(len(ss) for ss in course_slots.values())
        if day_total == 0:
            continue
        total += day_total
        dow = d.strftime("%a")
        date_str = f"{d.month}/{d.day}"
        body_lines.append(f"<b>{dow} {date_str}</b> ({day_total})")
        for course_key in sorted(
            course_slots.keys(),
            key=lambda k: (-len(course_slots[k]), course_display.get(k, k)),
        ):
            ss = sorted(course_slots[course_key], key=lambda s: s.tee_time)
            display = _html.escape(course_display.get(course_key, course_key))
            shown = [s.tee_time.strftime("%H:%M") for s in ss[:_FULL_MAX_TIMES_PER_COURSE]]
            extra = len(ss) - len(shown)
            times_str = ", ".join(shown)
            if extra > 0:
                times_str += f"... +{extra} more"
            body_lines.append(f"  {display} ({len(ss)}): {times_str}")
        body_lines.append("")

    if total == 0:
        return "\n".join(header + ["No slots available across configured days."])

    footer = [f"<i>{total} total slots</i>"]

    # Truncate if over Telegram's limit. Drop trailing day groups first.
    full = "\n".join(header + body_lines + footer)
    if len(full) <= _TELEGRAM_TEXT_LIMIT:
        return full

    # Iteratively pop the last day until we fit.
    while body_lines and len("\n".join(header + body_lines + footer)) > _TELEGRAM_TEXT_LIMIT - 80:
        # Pop until we hit a date heading (last day's group)
        while body_lines and not body_lines[-1].startswith("<b>"):
            body_lines.pop()
        if body_lines:
            body_lines.pop()  # the date heading itself
    footer = [f"<i>{total} total slots — output truncated to fit Telegram limit</i>"]
    return "\n".join(header + body_lines + footer)


def _stamp_line(label: str, iso_value: str | None, now: datetime, empty: str = "— never") -> str:
    """Format e.g. '🔁 Last scan: 5m ago (12:35 PM)' or '— never' if missing."""
    if not iso_value:
        return f"{label}: {empty}"
    try:
        when = datetime.fromisoformat(iso_value)
    except (ValueError, TypeError):
        return f"{label}: {empty}"
    delta_seconds = int((now - when).total_seconds())
    rel = _humanize_delta(delta_seconds)
    return f"{label}: {rel} ({_fmt_clock(when)})"


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
    """Footer: relative time since the scan, relative time to next scan."""
    now = datetime.now(run_at.tzinfo) if run_at.tzinfo else datetime.now()
    last = _humanize_delta(int((now - run_at).total_seconds()))
    if next_run_at:
        next_delta = int((now - next_run_at).total_seconds())
        nxt = _humanize_delta(next_delta)
        return f"<i>Last scan: {last} · Next: {nxt}</i>  ·  /tee · /pause · /help"
    return f"<i>Last scan: {last}</i>  ·  /tee · /pause · /help"


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
