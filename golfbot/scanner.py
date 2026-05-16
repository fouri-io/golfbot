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
from golfbot.config import Config
from golfbot.horizon import current_window
from golfbot.pipeline import Match, apply_policy_b, filter_and_grade
from golfbot.providers.base import Provider, RawSlot

log = logging.getLogger(__name__)


async def run_scan(
    cfg: Config,
    providers: dict[str, Provider],
    dates: list[date],
    availability: dict[str, avail_mod.AvailabilityRecord] | None = None,
    fallback_min_players: int = 1,
) -> list[Match]:
    """Run providers + pipeline. Returns Policy-B-filtered matches annotated
    with per-date roster.

    Per date the scanner consults `availability` to decide:
      - Skip the date if admin is out (and `cfg.group.admin_required`).
      - Otherwise query providers with `players_to_search_for(date)`.

    `availability` may be None — in that case every date is scanned with
    `fallback_min_players` (used by `scrape --raw` and tests that don't
    care about availability).
    """
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

    raw: list[RawSlot] = []
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
) -> dict[str, Any]:
    """Scheduled-run entrypoint. Polls, dedups vs last scan, sends digest
    if changed. Returns the new last_scan dict for inspection/tests.
    """
    now = datetime.now(cfg.tz)
    today = now.date()
    start, end = current_window(
        today=today,
        start_offset_days=cfg.search.start_offset_days,
        horizon_days=cfg.search.horizon_days,
        booked_through=None,
    )
    dates: list[date] = []
    d = start
    while d <= end:
        dates.append(d)
        d = d + timedelta(days=1)

    availability = avail_mod.load_availability(store.load_state(state_path))
    log.info(
        "scan: %d course(s) x %d date(s), %d registered member(s)",
        len(cfg.courses), len(dates), len(avail_mod.registered_members(cfg)),
    )
    matches = await run_scan(cfg, providers, dates, availability=availability)

    state = store.load_state(state_path)
    state["last_poll_at"] = now.isoformat()
    paused = bool(state.get("paused"))

    # Compare against previous match set
    current_dicts = [match_to_dict(m) for m in matches]
    prev_dicts = (state.get("last_scan") or {}).get("matches", [])
    if _signature(current_dicts) == _signature(prev_dicts):
        log.info("scan: no change since previous scan (%d match(es))", len(matches))
        # Still update last_poll_at so /status reflects fresh activity.
        state.setdefault("last_scan", {})["run_at"] = now.isoformat()
        await store.save_state(state_path, state)
        return state["last_scan"]

    # Match set changed — record + (maybe) notify
    last_scan: dict[str, Any] = {
        "run_at": now.isoformat(),
        "matches": current_dicts,
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
            msg_id = await notifier.send_digest(
                bot=bot,
                chat_id=chat_id,
                matches=current_dicts,
                run_at=now,
                next_run_at=next_run_at,
                cfg=cfg,
                bookings=bookings,
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
