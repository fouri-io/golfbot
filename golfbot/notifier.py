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

    start, end = current_window(
        today=today,
        start_offset_days=cfg.search.start_offset_days,
        horizon_days=cfg.search.horizon_days,
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

    lines = [
        f"📡 Watching: {course_names}",
        f"🗓  Horizon: {_fmt_date(start)} → {_fmt_date(end)} ({cfg.search.horizon_days} days)",
        f"🎯 Days: {days}",
        f"⏰ Ideal: {_fmt_time(ideal.start)}–{_fmt_time(ideal.end)}"
        f" · Acceptable: {_fmt_time(accept.start)}–{_fmt_time(accept.end)}",
        f"📌 Bookings: {booking_summary}",
    ]
    aw = cfg.polling.active_window
    if aw is not None:
        cur = now.time()
        in_window = aw.start <= cur < aw.end
        win_str = f"{_fmt_time(aw.start)}–{_fmt_time(aw.end)}"
        if in_window:
            lines.append(f"☀️ Active hours: {win_str} (in window)")
        else:
            lines.append(f"🌙 Quiet hours: outside {win_str} — scheduled scans paused")
    lines.append(last_scan_line)
    lines.append(last_digest_line)
    lines.append(f"🔔 Notifications: {'OFF (paused)' if paused else 'ON'}")
    return "\n".join(lines)


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
    weather: dict[str, dict] | None = None,
) -> str:
    """Telegram-HTML digest. Merged-list layout.

    The available matches AND existing bookings are surfaced as a single
    keyboard list (in `build_digest_keyboard`) with status icons (✅/—)
    inline on each row. The text portion shows only the title, counts,
    and a per-date forecast block — no separate BOOKED section.
    """
    bookings = bookings or {}
    weather = weather or {}
    horizon = cfg.search.horizon_days

    merged = _merge_rows_for_display(matches, bookings)

    title = f"🏌️ <b>Tee Times</b> — {_fmt_clock(run_at)}"

    counts: list[str] = []
    if bookings:
        n = len(bookings)
        counts.append(f"{n} booking{'s' if n != 1 else ''}")
    n = len(merged)
    counts.append(f"{n} slot{'s' if n != 1 else ''}")
    subtitle = " · ".join(counts)

    sections: list[str] = [title, f"<i>{subtitle}</i>"]

    if merged:
        sorted_merged = sorted(merged, key=lambda x: (x["tee_date"], x["tee_time"]))
        # Forecast block — one line per unique date, with weather + roster.
        seen_dates: list[str] = []
        roster_by_date: dict[str, tuple[list[str], list[str]]] = {}
        for row in sorted_merged:
            if row["tee_date"] not in seen_dates:
                seen_dates.append(row["tee_date"])
                roster_by_date[row["tee_date"]] = (
                    row.get("members_in") or [],
                    row.get("members_out") or [],
                )

        sections.append("")
        sections.append("<b>📅 Forecast</b>")
        for d_iso in seen_dates:
            members_in, members_out = roster_by_date[d_iso]
            sections.append(_render_forecast_line(d_iso, weather, members_in, members_out))
    else:
        sections.append("")
        sections.append(f"No matches in the next {horizon} days.")
        sections.append(f"Watching {len(cfg.courses)} course(s).")

    sections.append("")
    sections.append(_render_footer(run_at, next_run_at))
    return "\n".join(sections)


def _merge_rows_for_display(
    matches: list[dict],
    bookings: dict[date, dict],
) -> list[dict]:
    """Merge matches + bookings into a single deduped list.

    When a slot exists in both `matches` and `bookings`, the booking
    record wins — it has the authoritative committed roster (the booker
    was forced into `members_in` at confirmation time).

    Bookings whose slot isn't represented in the current scan still get
    a ghost row — so the user always sees their bookings, even if the
    provider stopped returning that slot.
    """
    by_key: dict[tuple, dict] = {}
    for m in matches:
        key = (m["course_key"], m["tee_date"], m["tee_time"])
        by_key[key] = m
    for _d, b in bookings.items():
        key = (b["course_key"], b["tee_date"], b["tee_time"])
        by_key[key] = b   # overlay — booking wins
    return list(by_key.values())


def _render_booking_v2(b: dict, weather: dict[str, dict], cfg: Config | None = None) -> str:
    """e.g. '• Mon 5/18 ☀️ · 7:30 AM · Roy Kizer · Colby'"""
    import html as _html
    tee_date = date.fromisoformat(b["tee_date"])
    tee_time = time.fromisoformat(b["tee_time"])
    dow = tee_date.strftime("%a")
    d = f"{tee_date.month}/{tee_date.day}"
    w = weather.get(b["tee_date"])
    emoji = _weather_emoji_from_dict(w)
    course = _html.escape(_resolve_display(b, cfg))
    roster = _format_roster(b.get("members_in") or [], b.get("members_out") or [])

    parts = [f"<b>{dow} {d}</b>"]
    if emoji:
        parts.append(emoji)
    parts.append(_fmt_time(tee_time))
    parts.append(course)
    if roster:
        parts.append(roster)
    return "• " + " · ".join(parts)


def _resolve_display(d: dict, cfg: Config | None) -> str:
    """Return the course display name, preferring the current config so
    edits to `display` in config.yaml take effect immediately on the
    next render — even if `state.last_scan` still has stale strings."""
    if cfg is not None:
        c = cfg.course_by_key(d.get("course_key", ""))
        if c is not None:
            return c.display
    return d.get("course_display", "")


def _render_forecast_line(
    d_iso: str,
    weather: dict[str, dict],
    members_in: list[str],
    members_out: list[str],
) -> str:
    """e.g. 'Wed 5/20  ⛅ 87°/66°  Rain 12%  ·  Colby+Steve (Ed out)'"""
    d = date.fromisoformat(d_iso)
    dow = d.strftime("%a")
    date_str = f"{d.month}/{d.day}"
    w = weather.get(d_iso)

    parts = [f"<b>{dow} {date_str}</b>"]
    if w:
        emoji = _weather_emoji_from_dict(w)
        tmax = int(round(float(w.get("tmax", 0))))
        tmin = int(round(float(w.get("tmin", 0))))
        rain = int(w.get("rain_pct", 0))
        parts.append(f"{emoji} {tmax}°/{tmin}°")
        parts.append(f"Rain {rain}%")

    roster = _format_roster(members_in, members_out)
    if roster:
        parts.append(roster)
    return " · ".join(parts)


def _weather_emoji_from_dict(w: dict | None) -> str:
    if not w:
        return ""
    from golfbot.weather import emoji_for
    return emoji_for(w.get("code"))


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
    cfg: Config | None = None,
) -> "InlineKeyboardMarkup":
    """Inline keyboard for the digest.

    Layout:
      • One full-width URL button per slot. Status emoji prefix (`✅` if
        booked, `—` if not) carries the at-a-glance state indicator.
      • Compact toggle grid at the bottom, 4 per row. Each button label
        reflects the action it'll take: `✓ #N` to confirm an unbooked
        slot, `↩️ #N` to cancel a booked one.
      • Numbering and order match `render_digest` — sorted (date, time).
    """
    from golfbot import bookings as bookings_mod

    bookings = bookings or {}
    merged = _merge_rows_for_display(matches, bookings)
    sorted_merged = sorted(merged, key=lambda x: (x["tee_date"], x["tee_time"]))

    rows: list[list[InlineKeyboardButton]] = []

    # URL info rows with status prefix.
    for i, row in enumerate(sorted_merged, 1):
        is_booked = bookings_mod.match_is_booked(row, bookings)
        status = "✅" if is_booked else "—"
        rows.append([
            InlineKeyboardButton(
                _row_button_text(i, row, status, cfg, is_booked=is_booked),
                url=row["booking_url"],
            ),
        ])

    # Compact toggle grid.
    if sorted_merged:
        toggle_btns: list[InlineKeyboardButton] = []
        for i, row in enumerate(sorted_merged, 1):
            hhmm = row["tee_time"].replace(":", "")[:4]
            is_booked = bookings_mod.match_is_booked(row, bookings)
            label = f"↩️ #{i}" if is_booked else f"✓ #{i}"
            toggle_btns.append(InlineKeyboardButton(
                label,
                callback_data=f"tb:{row['course_key']}:{row['tee_date']}:{hhmm}",
            ))
        per_row = 4
        for j in range(0, len(toggle_btns), per_row):
            rows.append(toggle_btns[j:j + per_row])

    return InlineKeyboardMarkup(rows)


def _row_button_text(
    idx: int,
    row: dict,
    status: str,
    cfg: Config | None = None,
    is_booked: bool = False,
) -> str:
    """e.g. '— 2. Wed Jimmy Clay 7:30A · 3 open · $25' for unbooked,
    or '✅ 1. Mon Roy Kizer 7:30A · Colby+Steve · $25' for booked.

    The "3 open" segment is replaced with the committed roster on booked
    rows — slot count is moot once you've claimed the slot, but who's
    going matters.
    """
    tee_date = date.fromisoformat(row["tee_date"])
    tee_time = time.fromisoformat(row["tee_time"])
    dow = tee_date.strftime("%a")
    parts = [
        f"{status} {idx}.",
        dow,
        _resolve_display(row, cfg),
        _short_time(tee_time),
    ]
    if is_booked:
        roster = "+".join(row.get("members_in") or [])
        if roster:
            parts.append(f"· {roster}")
    else:
        parts.append(f"· {row['players_available']} open")
    price = row.get("price_usd")
    if price:
        parts.append(f"· ${int(round(float(price)))}")
    return " ".join(parts)


def _short_time(t: time) -> str:
    """Compact AM/PM, e.g. '7:30A' or '12:30P'. Saves chars on buttons."""
    h = t.hour % 12 or 12
    am_pm = "A" if t.hour < 12 else "P"
    return f"{h}:{t.minute:02d}{am_pm}"


def _booking_button_text(b: dict, cfg: Config | None = None) -> str:
    """e.g. '📌 Mon 5/18 7:30A Roy Kizer'."""
    tee_date = date.fromisoformat(b["tee_date"])
    tee_time = time.fromisoformat(b["tee_time"])
    dow = tee_date.strftime("%a")
    d = f"{tee_date.month}/{tee_date.day}"
    return f"📌 {dow} {d} {_short_time(tee_time)} {_resolve_display(b, cfg)}"


def _match_button_text(idx: int, m: dict, cfg: Config | None = None) -> str:
    """e.g. '1. Wed Riverside 7:30A · 3 open · $45'."""
    tee_date = date.fromisoformat(m["tee_date"])
    tee_time = time.fromisoformat(m["tee_time"])
    dow = tee_date.strftime("%a")
    parts = [
        f"{idx}.",
        dow,
        _resolve_display(m, cfg),
        _short_time(tee_time),
        f"· {m['players_available']} open",
    ]
    price = m.get("price_usd")
    if price:
        parts.append(f"· ${int(round(float(price)))}")
    return " ".join(parts)


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
    weather: dict[str, dict] | None = None,
) -> int:
    """Send the digest message; return Telegram message_id."""
    msg = await bot.send_message(
        chat_id=chat_id,
        text=render_digest(
            matches, run_at, next_run_at, cfg,
            bookings=bookings, weather=weather,
        ),
        reply_markup=build_digest_keyboard(matches, bookings, cfg=cfg),
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
