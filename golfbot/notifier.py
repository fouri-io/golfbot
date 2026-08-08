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

import random
from datetime import date, datetime, time

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from golfbot.config import Config
from golfbot.models import TeeTimeSlot

_DEFAULT_RNG = random.Random()

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
# Snark                                                                       #
# --------------------------------------------------------------------------- #

# Used when `alerts.headlines` is empty or missing, so an alert never fails
# for want of copy (docs/SPEC.md > Snark).
FALLBACK_HEADLINE = "Gold Star — book it"

# "Never repeat the immediately previous headline" is in-memory only; a
# restart legitimately forgets. Tests pass their own state dict so they
# don't share this one.
_HEADLINE_STATE: dict[str, str | None] = {"last": None}


def pick_headline(
    cfg: Config,
    rng: random.Random | None = None,
    state: dict[str, str | None] | None = None,
) -> str:
    """Pick a random alert headline, never the immediately-previous one.

    `{name}` in a headline is replaced with a random roster member, so
    "Quick — before {name} sees it" becomes "Quick — before Steve sees it".

    The no-repeat check is on the *template*, before substitution — else
    the same line would recur with a different name and read as a repeat.
    """
    rng = rng or _DEFAULT_RNG
    state = _HEADLINE_STATE if state is None else state

    pool = list(cfg.alerts.headlines) or [FALLBACK_HEADLINE]

    template = rng.choice(pool)
    # A one-line pool can't avoid repeating itself; don't spin forever.
    if len(pool) > 1:
        while template == state.get("last"):
            template = rng.choice(pool)
    state["last"] = template

    if "{name}" in template:
        names = [m.name for m in cfg.group.members]
        template = template.replace("{name}", rng.choice(names)) if names else template
    return template


# --------------------------------------------------------------------------- #
# Pure renderers                                                              #
# --------------------------------------------------------------------------- #


def render_open(slot: TeeTimeSlot, course_display: str, headline: str) -> str:
    """Render a Gold Star alert (docs/SPEC.md > Gold Star alert).

        🎯 Book it now, dumbass

        Jimmy Clay · Fri May 22
        7:40 AM · 3 spots open

    Pure: the caller supplies the headline (see `pick_headline`) so this
    stays deterministic and testable. The 🔗 Book it link is a keyboard
    button, not part of the text — see `build_keyboard_open`.
    """
    import html as _html

    spots = slot.spots_open
    noun = "spot" if spots == 1 else "spots"
    return "\n".join([
        f"🎯 {_html.escape(headline)}",
        "",
        f"{_html.escape(course_display)} · {_fmt_date(slot.tee_date)}",
        f"{_fmt_time(slot.tee_time)} · {spots} {noun} open",
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
    last_alert_line = _stamp_line(
        "📨 Last alert", state.get("last_alert_at"), now,
        empty="— (none yet)",
    )
    next_scan_line = _stamp_line(
        "⏭ Next scan", state.get("next_run_at"), now,
        empty="— (not scheduled)",
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
    lines.append(next_scan_line)
    lines.append(last_alert_line)
    lines.append(f"🔔 Notifications: {'OFF (paused)' if paused else 'ON'}")
    return "\n".join(lines)


_FULL_MAX_TIMES_PER_COURSE = 10
_TELEGRAM_TEXT_LIMIT = 4096


def render_full_listing(
    slots: list,    # list[RawSlot]; importing the type would create a cycle
    cfg: Config,
    run_at: datetime,
    weather: dict[str, dict] | None = None,
) -> str:
    """Render every slot in `slots`, grouped by date then course.

    This is the on-demand firehose (docs/SPEC.md > /full): all configured
    courses, all times, not just the premium window and not just the
    all-star set.

    Each (course, date) cell shows the count + up to
    `_FULL_MAX_TIMES_PER_COURSE` earliest times. When a forecast is cached
    it is appended to each date heading. Output is truncated to fit in a
    single Telegram message (4096-char limit) with a note when cut.
    """
    import html as _html
    from collections import defaultdict

    weather = weather or {}
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
        forecast = _forecast_suffix(d.isoformat(), weather)
        body_lines.append(f"<b>{dow} {date_str}</b> ({day_total}){forecast}")
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
    headline: str,
) -> int:
    """Send a Gold Star alert. Returns the Telegram message_id.

    Nothing edits this message afterwards — a re-alert is a fresh message
    with the new spot count (docs/SPEC.md > Re-alert semantics).
    """
    msg = await bot.send_message(
        chat_id=chat_id,
        text=render_open(slot, course_display, headline),
        reply_markup=build_keyboard_open(slot),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    return msg.message_id


# --------------------------------------------------------------------------- #
# Digest model — used by the scheduled scanner                                #
# --------------------------------------------------------------------------- #


def _forecast_suffix(d_iso: str, weather: dict[str, dict]) -> str:
    """e.g. '  ⛅ 87°/66° · Rain 12%', or '' when no forecast is cached.

    Appended to a /full date heading (docs/SPEC.md > Weather).
    """
    w = weather.get(d_iso)
    if not w:
        return ""
    emoji = _weather_emoji_from_dict(w)
    tmax = int(round(float(w.get("tmax", 0))))
    tmin = int(round(float(w.get("tmin", 0))))
    rain = int(w.get("rain_pct", 0))
    return f"  {emoji} {tmax}°/{tmin}° · Rain {rain}%"


def _weather_emoji_from_dict(w: dict | None) -> str:
    if not w:
        return ""
    from golfbot.weather import emoji_for
    return emoji_for(w.get("code"))
