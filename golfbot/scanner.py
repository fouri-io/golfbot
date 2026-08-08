"""Scrape pipeline orchestrator.

`run_scan` is the shared core used by both the `golfbot scrape` CLI and
the scheduled `golfbot run` job. Given config + provider registry + a
list of dates, it runs every provider, normalizes/filters/grades,
applies the Gold Star rule, and returns a `list[Match]`.

`scan_and_notify` is the scheduler entrypoint — it wraps `run_scan`, folds
each Gold Star into the `state.tee_times` ledger, and pushes one alert per
slot that the re-alert rule says is worth announcing (first sighting, or
more open spots than last announced). Stable availability produces silence.

It also caches the full raw scan in `state.json` so `/full` costs no API
calls.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from telegram import Bot
from telegram.error import RetryAfter
from telegram.error import TimedOut as TelegramTimedOut

from golfbot import actions, notifier, store
from golfbot import weather as weather_mod
from golfbot.config import Config
from golfbot.horizon import current_window
from golfbot.models import TeeTimeSlot, make_slot_id
from golfbot.pipeline import Match, gold_star_slots
from golfbot.providers.base import Provider, RawSlot

log = logging.getLogger(__name__)

# Telegram allows roughly one message per second to a given chat. A scan that
# finds several Gold Stars at once must pace itself or the tail of the burst
# sits queued on Telegram's side until our read timeout fires.
_ALERT_SEND_INTERVAL_SECONDS = 1.0
_ALERT_SEND_ATTEMPTS = 3
_ALERT_RETRY_BACKOFF_SECONDS = 2.0


async def run_full_scan(
    cfg: Config,
    providers: dict[str, Provider],
    dates: list[date],
    min_players: int = 1,
) -> list[RawSlot]:
    """Fetch raw slots across all configured courses + dates with no filters.

    Used by the `/full` Telegram command — caller renders the output
    directly without going through the Gold Star rule.
    """
    by_provider: dict[str, list] = {}
    for c in cfg.courses:
        by_provider.setdefault(c.provider, []).append(c)

    raw: list[RawSlot] = []
    for provider_name, courses in by_provider.items():
        prov = providers.get(provider_name)
        if prov is None:
            log.warning(
                "run_full_scan: provider %r not registered — skipping %d course(s)",
                provider_name, len(courses),
            )
            continue
        for d in dates:
            slots = await prov.fetch_slots(courses, d, min_players)
            raw.extend(slots)
    return raw


async def run_scan(
    cfg: Config,
    providers: dict[str, Provider],
    dates: list[date],
    min_players: int = 1,
    prefetched: list[RawSlot] | None = None,
) -> list[Match]:
    """Run providers + the Gold Star rule. Returns qualifying matches.

    v2: no availability layer. Every date in the horizon is scanned and the
    only player constraint is the rule's own `spots >= 1`.

    `prefetched` lets the caller pass in already-fetched RawSlots (used by
    `scan_and_notify` which caches the raw scan to make /full free).
    When None, this fetches fresh.
    """
    if prefetched is not None:
        raw: list[RawSlot] = list(prefetched)
    else:
        raw = await run_full_scan(cfg, providers, dates, min_players=min_players)

    return gold_star_slots(raw, cfg)


async def scan_and_notify(
    cfg: Config,
    providers: dict[str, Provider],
    state_path: Path,
    bot: Bot,
    chat_id: int,
    next_run_at: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Scheduled-run entrypoint. Polls, folds Gold Stars into the ledger,
    and sends one alert per slot that is due.

    Returns a summary dict for inspection/tests — this is NOT the persisted
    shape. State is written to `state.json` per docs/SPEC.md > Data model:
    raw slots cached under `raw_slots` so /full costs no API calls, and the
    ledger under `tee_times`.
    """
    now = datetime.now(cfg.tz)

    # Respect quiet hours unless the caller explicitly forced (e.g. /scan).
    if not force and cfg.polling.active_window is not None:
        win = cfg.polling.active_window
        current = now.time()
        if not (win.start <= current < win.end):
            log.info(
                "scan: outside active window %s-%s; skipping",
                win.start.strftime("%H:%M"), win.end.strftime("%H:%M"),
            )
            return {"skipped": "outside active window", "matches": [], "alerts_sent": 0}

    today = now.date()
    start, end = current_window(
        today=today,
        start_offset_days=cfg.search.start_offset_days,
        horizon_days=cfg.search.horizon_days,
    )
    dates: list[date] = []
    d = start
    while d <= end:
        dates.append(d)
        d = d + timedelta(days=1)

    state = store.load_state(state_path)
    state.setdefault("tee_times", [])
    pruned = actions.prune_past_slots(state, today)
    if pruned:
        log.info("scan: pruned %d past slot(s) from the ledger", pruned)
    log.info(
        "scan: %d course(s) x %d date(s), %d all-star",
        len(cfg.courses), len(dates), len(cfg.all_star_courses()),
    )

    # Fetch raw slots for every date in horizon at the lowest practical
    # min_players so /full has full coverage; the Gold Star rule is applied
    # client-side on top of the cached raw slots.
    raw_slots = await run_full_scan(cfg, providers, dates, min_players=1)
    raw_dicts = [s.to_dict() for s in raw_slots]

    matches = await run_scan(cfg, providers, dates, prefetched=raw_slots)

    state["last_poll_at"] = now.isoformat()
    paused = bool(state.get("paused"))

    # Refresh weather cache if configured and stale.
    if cfg.weather is not None and cfg.weather.enabled:
        fetched_at, _ = weather_mod.load_cache(state)
        if not weather_mod.is_fresh(fetched_at, now, cfg.weather.cache_hours):
            try:
                days = await weather_mod.fetch_forecast(
                    cfg.weather.latitude,
                    cfg.weather.longitude,
                    cfg.timezone,
                )
                weather_mod.save_cache(state, now, days)
                log.info("weather: refreshed forecast (%d days)", len(days))
            except Exception:
                log.warning("weather: fetch failed; using existing cache if any", exc_info=True)

    current_dicts = [match_to_dict(m) for m in matches]
    # Cached so /full costs no API calls (docs/SPEC.md > Data model).
    state["raw_slots"] = raw_dicts
    state["next_run_at"] = next_run_at.isoformat() if next_run_at else None

    # Fold every Gold Star into the ledger, then alert on the ones the
    # re-alert rule says are worth a push (docs/SPEC.md > Re-alert semantics).
    # Dedup lives entirely in the ledger — there is no digest-level compare.
    due: list[dict[str, Any]] = []
    for m in matches:
        slot_dict = _match_to_slot_dict(m, now)
        slot_in_state, _is_new = actions.upsert_slot(state, slot_dict, now)
        if actions.should_alert(slot_in_state):
            due.append(slot_in_state)

    sent = 0
    if paused:
        if due:
            log.info("scan: %d slot(s) due to alert but notifications are paused", len(due))
    else:
        for i, slot_in_state in enumerate(due):
            # Telegram rate-limits per-chat sends to roughly 1/sec. Pace the
            # burst rather than letting the server queue it and time us out.
            if i:
                await asyncio.sleep(_ALERT_SEND_INTERVAL_SECONDS)
            if not await _send_alert(bot, chat_id, cfg, slot_in_state):
                continue
            actions.record_alert(slot_in_state, now)
            state["last_alert_at"] = now.isoformat()
            sent += 1
        if sent:
            log.info("scan: sent %d Gold Star alert(s)", sent)
        if len(due) != sent:
            log.warning(
                "scan: %d of %d alert(s) failed to send; they stay unstamped "
                "and will be retried on the next scan",
                len(due) - sent, len(due),
            )

    await store.save_state(state_path, state)
    return {
        "run_at": now.isoformat(),
        "matches": current_dicts,
        "raw_slots": raw_dicts,
        "alerts_sent": sent,
    }


async def _send_alert(
    bot: Bot,
    chat_id: int,
    cfg: Config,
    slot_in_state: dict[str, Any],
) -> bool:
    """Send one Gold Star alert, retrying briefly on a timeout.

    Returns True if the send is believed to have succeeded — only then does
    the caller stamp the ledger.

    A `TimedOut` means we never saw Telegram's response, NOT that the message
    failed to arrive. Retrying can therefore duplicate a message that did land.
    We accept that: a duplicate is a minor annoyance, a silently dropped Gold
    Star defeats the point of the tool. Exhausting the retries leaves the slot
    unstamped so the next scan tries again.
    """
    slot = TeeTimeSlot.from_dict(slot_in_state)
    course_display = _course_display(cfg, slot.course_key)

    for attempt in range(1, _ALERT_SEND_ATTEMPTS + 1):
        try:
            await notifier.send_new_slot(
                bot=bot,
                chat_id=chat_id,
                slot=slot,
                course_display=course_display,
                headline=notifier.pick_headline(cfg),
            )
            return True
        except TelegramTimedOut:
            if attempt < _ALERT_SEND_ATTEMPTS:
                log.warning(
                    "scan: alert for %s timed out (attempt %d/%d) — retrying",
                    slot.id, attempt, _ALERT_SEND_ATTEMPTS,
                )
                await asyncio.sleep(_ALERT_RETRY_BACKOFF_SECONDS * attempt)
                continue
            log.error(
                "scan: alert for %s timed out after %d attempt(s); it may or "
                "may not have been delivered",
                slot.id, _ALERT_SEND_ATTEMPTS,
            )
            return False
        except RetryAfter as e:
            # Flood control: Telegram tells us exactly how long to wait.
            wait = float(getattr(e, "retry_after", _ALERT_RETRY_BACKOFF_SECONDS))
            log.warning("scan: flood control on %s — waiting %.0fs", slot.id, wait)
            await asyncio.sleep(wait)
            continue
        except Exception:
            log.exception("scan: failed to send alert for %s", slot.id)
            return False
    return False


def _course_display(cfg: Config, course_key: str) -> str:
    c = cfg.course_by_key(course_key)
    return c.display if c else course_key


def _match_to_slot_dict(m: Match, now: datetime) -> dict[str, Any]:
    """Build a ledger entry from a Gold Star match."""
    return TeeTimeSlot(
        id=make_slot_id(m.raw.course_key, m.raw.tee_date, m.raw.tee_time),
        course_key=m.raw.course_key,
        tee_date=m.raw.tee_date,
        tee_time=m.raw.tee_time,
        spots_open=m.raw.players_available,
        holes=m.raw.holes,
        booking_url=m.raw.booking_url,
        first_seen_at=now,
        last_seen_at=now,
    ).to_dict()


def match_to_dict(m: Match) -> dict[str, Any]:
    """Serialize a Match for state.json."""
    return {
        "course_key": m.raw.course_key,
        "course_display": m.course_display,
        "tee_date": m.raw.tee_date.isoformat(),
        "tee_time": m.raw.tee_time.isoformat(),
        "players_available": m.raw.players_available,
        "holes": m.raw.holes,
        "booking_url": m.raw.booking_url,
        "price_usd": m.raw.price_usd,
        "provider": m.raw.provider,
    }


def _weather_dict_for_render(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Renderer-friendly weather: {iso_date_str: WeatherDay.to_dict()}."""
    _, days = weather_mod.load_cache(state)
    return {d.isoformat(): wd.to_dict() for d, wd in days.items()}
