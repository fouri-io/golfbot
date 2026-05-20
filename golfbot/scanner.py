"""Scrape pipeline orchestrator.

`run_scan` is the shared core used by both the `golfbot scrape` CLI and
the scheduled `golfbot run` job. Given config + provider registry + a
list of dates, it runs every provider, normalizes/filters/grades,
applies Policy B, and returns a `list[Match]`.

`scan_and_notify` is the scheduler entrypoint — it wraps `run_scan`,
compares the result against the previously-stored scan, and sends a
new Telegram digest only when the match set changes (no spam on stable
availability). On any change it persists the result to `state.json`
so `/tee` can re-render it.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from telegram import Bot

from dataclasses import replace

from golfbot import availability as avail_mod
from golfbot import bookings as bookings_mod
from golfbot import notifier, store
from golfbot import weather as weather_mod
from golfbot.config import Config
from golfbot.horizon import current_window
from golfbot.pipeline import Match, apply_policy_b, filter_and_grade
from golfbot.providers.base import Provider, RawSlot

log = logging.getLogger(__name__)


async def run_full_scan(
    cfg: Config,
    providers: dict[str, Provider],
    dates: list[date],
    min_players: int = 2,
) -> list[RawSlot]:
    """Fetch raw slots across all configured courses + dates with no filters.

    Used by the `/full` Telegram command — caller renders the output
    directly without going through grading / Policy B / availability gates.
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
    availability: dict[str, avail_mod.AvailabilityRecord] | None = None,
    fallback_min_players: int = 1,
    prefetched: list[RawSlot] | None = None,
) -> list[Match]:
    """Run providers + pipeline. Returns Policy-B-filtered matches annotated
    with per-date roster.

    Per date the scanner consults `availability` to decide:
      - Skip the date if admin is out (and `cfg.group.admin_required`).
      - Otherwise filter slots to those with `players_available >=` the
        count of available registered members.

    `prefetched` lets the caller pass in already-fetched RawSlots (used by
    `scan_and_notify` which caches the raw scan to make /full free).
    When None, this fetches fresh.
    """
    if prefetched is not None:
        raw: list[RawSlot] = list(prefetched)
    else:
        by_provider: dict[str, list] = {}
        for c in cfg.courses:
            by_provider.setdefault(c.provider, []).append(c)

        # Decide per-date queries.
        scan_plan: list[tuple[date, int]] = []
        for d in dates:
            if availability is None:
                scan_plan.append((d, fallback_min_players))
                continue
            if not avail_mod.date_should_be_scanned(d, cfg, availability):
                log.info("scan: admin out for %s — skipping date", d)
                continue
            n = avail_mod.players_to_search_for(d, cfg, availability)
            scan_plan.append((d, n))

        raw = []
        for provider_name, courses in by_provider.items():
            prov = providers.get(provider_name)
            if prov is None:
                log.warning(
                    "scanner: provider %r not registered — skipping %d course(s)",
                    provider_name, len(courses),
                )
                continue
            for d, min_players in scan_plan:
                slots = await prov.fetch_slots(courses, d, min_players)
                raw.extend(slots)

    # Apply availability filters client-side. When using `prefetched`,
    # raw may include dates where admin is out; drop those here.
    if availability is not None:
        raw = [s for s in raw if avail_mod.date_should_be_scanned(s.tee_date, cfg, availability)]
        # Also: require slot.players_available >= group size needed
        raw = [
            s for s in raw
            if s.players_available >= avail_mod.players_to_search_for(s.tee_date, cfg, availability)
        ]

    graded = filter_and_grade(raw, cfg)
    best = apply_policy_b(graded)

    # Annotate each match with the per-date roster (if availability known).
    if availability is None:
        return best
    annotated: list[Match] = []
    for m in best:
        members_in = avail_mod.available_members_on(m.raw.tee_date, cfg, availability)
        members_out = avail_mod.out_members_on(m.raw.tee_date, cfg, availability)
        annotated.append(replace(
            m,
            members_in=tuple(members_in),
            members_out=tuple(members_out),
        ))
    return annotated


async def scan_and_notify(
    cfg: Config,
    providers: dict[str, Provider],
    state_path: Path,
    bot: Bot,
    chat_id: int,
    next_run_at: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Scheduled-run entrypoint. Polls, dedups vs last scan, sends digest
    if changed. Returns the new last_scan dict for inspection/tests.

    Fetches RAW slots for every date in horizon (no per-fetch availability
    skip) and caches them in state.last_scan.raw_slots. This makes /full
    free of fresh API calls. Filtering for the digest happens client-side
    on top of the cached raw slots.
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
            return (
                store.load_state(state_path).get("last_scan") or {}
            )

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
    availability = avail_mod.load_availability(state)
    log.info(
        "scan: %d course(s) x %d date(s), %d registered member(s)",
        len(cfg.courses), len(dates), len(avail_mod.registered_members(cfg)),
    )

    # Fetch raw slots for every date in horizon. We always use the lowest
    # practical min_players so /full has full coverage; per-date roster
    # filtering happens client-side below.
    raw_slots = await run_full_scan(cfg, providers, dates, min_players=2)
    raw_dicts = [s.to_dict() for s in raw_slots]

    # Now apply the filter pipeline on top of the raw cache.
    matches = await run_scan(
        cfg, providers, dates, availability=availability,
        prefetched=raw_slots,
    )

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

    # Compare against previous match set
    current_dicts = [match_to_dict(m) for m in matches]
    prev_dicts = (state.get("last_scan") or {}).get("matches", [])
    if _signature(current_dicts) == _signature(prev_dicts):
        log.info("scan: no change since previous scan (%d match(es))", len(matches))
        # Update run_at + raw cache so /full and /status reflect fresh activity.
        existing_scan = state.setdefault("last_scan", {})
        existing_scan["run_at"] = now.isoformat()
        existing_scan["raw_slots"] = raw_dicts
        await store.save_state(state_path, state)
        return state["last_scan"]

    # Match set changed — record + (maybe) notify
    last_scan: dict[str, Any] = {
        "run_at": now.isoformat(),
        "matches": current_dicts,
        "raw_slots": raw_dicts,
        "next_run_at": next_run_at.isoformat() if next_run_at else None,
        "telegram_message_id": None,
    }

    if paused:
        log.info(
            "scan: %d new/changed match(es) but notifications are paused",
            len(matches),
        )
    else:
        try:
            bookings = bookings_mod.load_bookings(state)
            weather_dict = _weather_dict_for_render(state)
            msg_id = await notifier.send_digest(
                bot=bot,
                chat_id=chat_id,
                matches=current_dicts,
                run_at=now,
                next_run_at=next_run_at,
                cfg=cfg,
                bookings=bookings,
                weather=weather_dict,
            )
            last_scan["telegram_message_id"] = msg_id
            state["last_digest_at"] = now.isoformat()
            log.info(
                "scan: sent digest (%d match(es), message_id=%s)",
                len(matches), msg_id,
            )
        except Exception:
            log.exception("scan: failed to send digest")

    state["last_scan"] = last_scan
    await store.save_state(state_path, state)
    return last_scan


def match_to_dict(m: Match) -> dict[str, Any]:
    """Serialize a Match for state.json."""
    return {
        "course_key": m.raw.course_key,
        "course_display": m.course_display,
        "course_tier": m.course_tier,
        "tee_date": m.raw.tee_date.isoformat(),
        "tee_time": m.raw.tee_time.isoformat(),
        "grade": m.grade,
        "players_available": m.raw.players_available,
        "holes": m.raw.holes,
        "booking_url": m.raw.booking_url,
        "price_usd": m.raw.price_usd,
        "provider": m.raw.provider,
        "members_in": list(m.members_in),
        "members_out": list(m.members_out),
    }


def _weather_dict_for_render(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Renderer-friendly weather: {iso_date_str: WeatherDay.to_dict()}."""
    _, days = weather_mod.load_cache(state)
    return {d.isoformat(): wd.to_dict() for d, wd in days.items()}


def _signature(match_dicts: list[dict[str, Any]]) -> frozenset[tuple]:
    """Stable identity for a set of matches — used to decide if scan changed.

    Includes slot identity plus per-date roster, so an `/out` from a member
    causes a re-notification with the updated roster.
    """
    return frozenset(
        (
            m["course_key"], m["tee_date"], m["tee_time"], m["players_available"],
            tuple(m.get("members_in") or ()),
            tuple(m.get("members_out") or ()),
        )
        for m in match_dicts
    )
