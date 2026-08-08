"""Telegram message rendering + send.

The pure renderers (`render_*`) take only what they need — slot, display
strings — and have no Telegram-runtime dependencies aside from
`InlineKeyboardMarkup` types. The async wrappers at the bottom call the bot.

v2: there are no callback buttons. The only button anywhere is a URL button
linking to the provider's booking page, which produces no callback data —
voting, booking and tally rendering are gone
(docs/decisions/0006-gold-star-pivot.md).

See docs/SPEC.md > Notifications.
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


def render_open(slot: TeeTimeSlot, course_display: str) -> str:
    """Render a Gold Star alert.

    Slice 4 rewrites this to the SPEC v2 format (randomized snark headline
    + factual body + 🔗 Book it). For now it carries the v2 fields with no
    vote tally.
    """
    return "\n".join([
        "🎯 Gold Star",
        "",
        f"{course_display} · {_fmt_date(slot.tee_date)}",
        f"{_fmt_time(slot.tee_time)} · {slot.spots_open} spots open",
    ])


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
    premium = cfg.premium_window
    all_star = ", ".join(c.display for c in cfg.all_star_courses())
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
        f"⭐ All-star: {all_star}",
        f"🗓  Horizon: {_fmt_date(start)} → {_fmt_date(end)} ({cfg.search.horizon_days} days)",
        f"🎯 Days: {days}",
        f"⏰ Premium: {_fmt_time(premium.start)}–{_fmt_time(premium.end)}",
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


# --------------------------------------------------------------------------- #
# Keyboard builders                                                           #
# --------------------------------------------------------------------------- #


def build_keyboard_open(slot: TeeTimeSlot) -> InlineKeyboardMarkup:
    """A single URL button — the only button v2 has anywhere.

    A URL button produces no callback data, which is why the bot registers
    no callback handlers at all (docs/SPEC.md > Gold Star alert).
    """
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Book it", url=slot.booking_url)],
    ])


# --------------------------------------------------------------------------- #
# Async Telegram API wrappers                                                 #
# --------------------------------------------------------------------------- #


async def send_new_slot(
    bot: Bot,
    chat_id: int,
    slot: TeeTimeSlot,
    course_display: str,
) -> int:
    """Send a Gold Star alert. Returns the Telegram message_id.

    Nothing edits this message afterwards — a re-alert is a fresh message.
    """
    msg = await bot.send_message(
        chat_id=chat_id,
        text=render_open(slot, course_display),
        reply_markup=build_keyboard_open(slot),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    return msg.message_id


# --------------------------------------------------------------------------- #
# Digest model — used by the scheduled scanner                                #
# --------------------------------------------------------------------------- #


def render_digest(
    matches: list[dict],
    run_at: datetime,
    next_run_at: datetime | None,
    cfg: Config,
    weather: dict[str, dict] | None = None,
) -> str:
    """Telegram-HTML listing of the current Gold Star matches.

    v2: no bookings overlay and no roster — the bot tracks neither.
    """
    weather = weather or {}
    horizon = cfg.search.horizon_days

    title = f"🏌️ <b>Tee Times</b> — {_fmt_clock(run_at)}"
    n = len(matches)
    subtitle = f"{n} slot{'s' if n != 1 else ''}"

    sections: list[str] = [title, f"<i>{subtitle}</i>"]

    if matches:
        sorted_matches = sorted(matches, key=lambda x: (x["tee_date"], x["tee_time"]))
        sections.append("")
        for m in sorted_matches:
            sections.append(_render_digest_line(m))

        seen_dates: list[str] = []
        for row in sorted_matches:
            if row["tee_date"] not in seen_dates:
                seen_dates.append(row["tee_date"])
        sections.append("")
        sections.append("<b>📅 Forecast</b>")
        for d_iso in seen_dates:
            sections.append(_render_forecast_line(d_iso, weather))
    else:
        sections.append("")
        sections.append(f"No matches in the next {horizon} days.")
        sections.append(f"Watching {len(cfg.courses)} course(s).")

    sections.append("")
    sections.append(_render_footer(run_at, next_run_at))
    return "\n".join(sections)


def _resolve_display(d: dict, cfg: Config | None) -> str:
    """Return the course display name, preferring the current config so
    edits to `display` in config.yaml take effect immediately on the
    next render — even if `state.last_scan` still has stale strings."""
    if cfg is not None:
        c = cfg.course_by_key(d.get("course_key", ""))
        if c is not None:
            return c.display
    return d.get("course_display", "")


def _render_forecast_line(d_iso: str, weather: dict[str, dict]) -> str:
    """e.g. 'Wed 5/20 · ⛅ 87°/66° · Rain 12%'"""
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
    return " · ".join(parts)


def _weather_emoji_from_dict(w: dict | None) -> str:
    if not w:
        return ""
    from golfbot.weather import emoji_for
    return emoji_for(w.get("code"))


def _short_time(t: time) -> str:
    """Compact AM/PM, e.g. '7:30A' or '12:30P'. Saves chars on buttons."""
    h = t.hour % 12 or 12
    am_pm = "A" if t.hour < 12 else "P"
    return f"{h}:{t.minute:02d}{am_pm}"


def _render_digest_line(m: dict) -> str:
    """One line per match. Format:
    'Mon 5/18 · 7:30 AM · Roy Kizer · 3 open · Colby+Ed (Steve out) · $45 · <a>book</a>'.

    v2: no grade badge — everything here already cleared the Gold Star bar,
    so a per-row quality marker carries no information."""
    import html as _html
    tee_date = date.fromisoformat(m["tee_date"])
    tee_time = time.fromisoformat(m["tee_time"])

    dow = tee_date.strftime("%a")
    d = f"{tee_date.month}/{tee_date.day}"
    t = _fmt_time(tee_time)

    course = _html.escape(m["course_display"])
    players = m["players_available"]
    price = m.get("price_usd")
    price_str = f"${price:.0f}" if price else None

    parts = [
        f"{dow} {d}",
        t,
        course,
        f"{players} open",
    ]
    if price_str:
        parts.append(price_str)
    parts.append(f'<a href="{_html.escape(m["booking_url"], quote=True)}">book</a>')
    return " · ".join(parts)


def _render_footer(run_at: datetime, next_run_at: datetime | None) -> str:
    """Footer: relative time since the scan, relative time to next scan."""
    now = datetime.now(run_at.tzinfo) if run_at.tzinfo else datetime.now()
    last = _humanize_delta(int((now - run_at).total_seconds()))
    if next_run_at:
        next_delta = int((now - next_run_at).total_seconds())
        nxt = _humanize_delta(next_delta)
        return f"<i>Last scan: {last} · Next: {nxt}</i>  ·  /full · /pause · /help"
    return f"<i>Last scan: {last}</i>  ·  /full · /pause · /help"


async def send_digest(
    bot: Bot,
    chat_id: int,
    matches: list[dict],
    run_at: datetime,
    next_run_at: datetime | None,
    cfg: Config,
    weather: dict[str, dict] | None = None,
) -> int:
    """Send the digest message; return Telegram message_id.

    No keyboard — the per-row booking links live in the message text.
    """
    msg = await bot.send_message(
        chat_id=chat_id,
        text=render_digest(matches, run_at, next_run_at, cfg, weather=weather),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    return msg.message_id
