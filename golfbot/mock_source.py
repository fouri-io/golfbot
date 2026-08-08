"""Synthetic Gold Star injector — for exercising the alert without scraping.

Builds a TeeTimeSlot from CLI args, upserts it into the state ledger, and
sends the alert. Used to check the alert renders correctly without waiting
for a real cancellation to appear.

Slice 4 rewrites the alert format itself; this module just feeds it.

See docs/SPEC.md > Notifications.
"""
from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

from telegram import Bot

from golfbot import actions, notifier, store
from golfbot.config import Config
from golfbot.models import TeeTimeSlot, make_slot_id

# Placeholder URL used for mocked slots — Telegram rejects invalid URLs in
# inline URL buttons, so it has to be something real-looking.
_MOCK_BOOKING_URL = "https://txaustinweb.myvscloud.com/webtrac/web/search.html"


def build_mock_slot(
    cfg: Config,
    course_key: str,
    tee_date: date,
    tee_time: time,
    spots: int,
    now: datetime,
) -> TeeTimeSlot:
    """Pure constructor — no I/O. Useful for testing in isolation."""
    course = cfg.course_by_key(course_key)
    if course is None:
        known = ", ".join(c.key for c in cfg.courses)
        raise ValueError(f"unknown course {course_key!r}; known: {known}")
    return TeeTimeSlot(
        id=make_slot_id(course_key, tee_date, tee_time),
        course_key=course_key,
        tee_date=tee_date,
        tee_time=tee_time,
        spots_open=spots,
        holes=cfg.search.holes,
        booking_url=_MOCK_BOOKING_URL,
        first_seen_at=now,
        last_seen_at=now,
    )


async def inject(
    cfg: Config,
    bot_token: str,
    chat_id: int,
    state_path: Path,
    course_key: str,
    tee_date: date,
    tee_time: time,
    spots: int,
) -> tuple[TeeTimeSlot, int]:
    """Inject + send. Returns (slot, telegram_message_id)."""
    now = datetime.now(cfg.tz)
    slot = build_mock_slot(cfg, course_key, tee_date, tee_time, spots, now)

    state = store.load_state(state_path)
    state.setdefault("tee_times", [])
    slot_in_state, _is_new = actions.upsert_slot(state, slot.to_dict(), now)

    bot = Bot(token=bot_token)
    async with bot:
        course_display = cfg.course_by_key(course_key).display  # type: ignore[union-attr]
        message_id = await notifier.send_new_slot(bot, chat_id, slot, course_display)

    actions.record_alert(slot_in_state, now)
    await store.save_state(state_path, state)
    return slot, message_id
