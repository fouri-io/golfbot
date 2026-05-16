"""Synthetic tee-time injector for P1.

Builds a TeeTimeSlot from CLI args, upserts it into state.json, sends the
initial Telegram notification, and persists the message_id. The running
`golfbot run` process serves any button taps that follow.

See SPEC.md > Phasing > P1.
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
    players: int,
    grade: str,
    now: datetime,
) -> TeeTimeSlot:
    """Pure constructor — no I/O. Useful for testing in isolation."""
    course = cfg.course_by_key(course_key)
    if course is None:
        known = ", ".join(c.key for c in cfg.courses)
        raise ValueError(f"unknown course {course_key!r}; known: {known}")
    return TeeTimeSlot(
        id=make_slot_id(course_key, tee_date, tee_time, players),
        course_key=course_key,
        tee_date=tee_date,
        tee_time=tee_time,
        players_open=players,
        holes=cfg.search.holes,
        grade=grade,
        booking_url=_MOCK_BOOKING_URL,
        first_seen_at=now,
        last_seen_at=now,
        status="open",
    )


async def inject(
    cfg: Config,
    bot_token: str,
    chat_id: int,
    state_path: Path,
    course_key: str,
    tee_date: date,
    tee_time: time,
    players: int,
    grade: str,
) -> tuple[TeeTimeSlot, int]:
    """Inject + send. Returns (slot, telegram_message_id)."""
    now = datetime.now(cfg.tz)
    slot = build_mock_slot(cfg, course_key, tee_date, tee_time, players, grade, now)

    state = store.load_state(state_path)
    slot_in_state, _is_new = actions.upsert_slot(state, slot.to_dict(), now)

    bot = Bot(token=bot_token)
    async with bot:
        member_names = [m.name for m in cfg.group.members]
        course_display = cfg.course_by_key(course_key).display  # type: ignore[union-attr]
        message_id = await notifier.send_new_slot(
            bot, chat_id, slot, course_display, member_names,
        )
    slot_in_state["message_id"] = message_id
    await store.save_state(state_path, state)
    return slot, message_id
