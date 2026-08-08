"""Tests for golfbot.scanner — Gold Star ledger + alert loop.

No network: providers are fakes, Telegram is a fake, and weather is
disabled on the test config so nothing reaches Open-Meteo.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from golfbot import store
from golfbot.config import load
from golfbot.horizon import current_window
from golfbot.pipeline import Match, is_desired_day
from golfbot.providers.base import RawSlot
from golfbot.scanner import match_to_dict, scan_and_notify

REPO_ROOT = Path(__file__).resolve().parent.parent

IN_WINDOW = time(7, 40)      # config premium_window is 07:20-08:00
OUT_OF_WINDOW = time(14, 0)


@pytest.fixture
def cfg():
    """Repo config with weather disabled — tests must not hit Open-Meteo."""
    return load(REPO_ROOT / "config.yaml").model_copy(update={"weather": None})


def _horizon_dates(cfg) -> list[date]:
    start, end = current_window(
        today=datetime.now(cfg.tz).date(),
        start_offset_days=cfg.search.start_offset_days,
        horizon_days=cfg.search.horizon_days,
    )
    out, d = [], start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


@pytest.fixture
def weekday(cfg) -> date:
    """A scannable weekday inside the live horizon."""
    for d in _horizon_dates(cfg):
        if is_desired_day(d, cfg.search.days_of_week):
            return d
    pytest.skip("no configured weekday falls in the current horizon")


@pytest.fixture
def weekend(cfg) -> date:
    for d in _horizon_dates(cfg):
        if not is_desired_day(d, cfg.search.days_of_week):
            return d
    pytest.skip("no weekend day falls in the current horizon")


def _raw(d: date, course="roy_kizer", t=IN_WINDOW, spots=3) -> RawSlot:
    return RawSlot(
        course_key=course,
        tee_date=d,
        tee_time=t,
        players_available=spots,
        holes=18,
        booking_url="https://example.com/book/" + course,
        provider="golfatx",
        price_usd=None,
    )


class FakeBot:
    """Records send_message calls instead of touching Telegram."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append({"chat_id": chat_id, "text": text, **kw})
        message_id = 100 + len(self.sent)
        return type("_Msg", (), {"message_id": message_id})()


class FakeProvider:
    def __init__(self, slots: list[RawSlot]):
        self._slots = slots

    async def fetch_slots(self, courses, target_date, min_players):
        owned = {c.key for c in courses}
        return [
            s for s in self._slots
            if s.tee_date == target_date and s.course_key in owned
        ]


async def _scan(cfg, state_path, slots, bot=None):
    bot = bot or FakeBot()
    providers = {"golfatx": FakeProvider(slots), "golfnow": FakeProvider(slots)}
    result = await scan_and_notify(
        cfg=cfg, providers=providers, state_path=state_path,
        bot=bot, chat_id=-1, force=True,
    )
    return bot, result


# ---------- match_to_dict ----------


def test_match_to_dict_drops_retired_keys():
    d = match_to_dict(Match(raw=_raw(date(2026, 5, 18)), course_display="Roy Kizer"))
    assert d["course_key"] == "roy_kizer"
    assert d["course_display"] == "Roy Kizer"
    assert d["tee_time"] == "07:40:00"
    assert d["players_available"] == 3
    for gone in ("grade", "course_tier", "members_in", "members_out"):
        assert gone not in d


# ---------- the alert loop (docs/SPEC.md > Re-alert semantics) ----------


async def test_first_sighting_alerts(cfg, tmp_path, weekday):
    bot, result = await _scan(cfg, tmp_path / "state.json", [_raw(weekday)])
    assert len(bot.sent) == 1
    assert bot.sent[0]["text"].startswith("🎯 ")
    assert "Roy Kizer" in bot.sent[0]["text"]
    assert "3 spots open" in bot.sent[0]["text"]
    assert len(result["matches"]) == 1


async def test_unchanged_slot_does_not_realert(cfg, tmp_path, weekday):
    p = tmp_path / "state.json"
    slots = [_raw(weekday)]
    bot1, _ = await _scan(cfg, p, slots)
    bot2, _ = await _scan(cfg, p, slots)
    assert len(bot1.sent) == 1
    assert bot2.sent == [], "an unchanged slot must stay silent"


async def test_more_spots_triggers_realert(cfg, tmp_path, weekday):
    p = tmp_path / "state.json"
    bot1, _ = await _scan(cfg, p, [_raw(weekday, spots=2)])
    bot2, _ = await _scan(cfg, p, [_raw(weekday, spots=4)])
    assert len(bot1.sent) == 1
    assert len(bot2.sent) == 1, "more open spots is a better opportunity"
    assert "4 spots open" in bot2.sent[0]["text"]


async def test_fewer_spots_stays_silent(cfg, tmp_path, weekday):
    p = tmp_path / "state.json"
    await _scan(cfg, p, [_raw(weekday, spots=4)])
    bot2, _ = await _scan(cfg, p, [_raw(weekday, spots=1)])
    assert bot2.sent == []


async def test_ledger_records_alert_state(cfg, tmp_path, weekday):
    p = tmp_path / "state.json"
    await _scan(cfg, p, [_raw(weekday, spots=3)])
    ledger = store.load_state(p)["tee_times"]
    assert len(ledger) == 1
    entry = ledger[0]
    assert entry["id"] == f"roy_kizer:{weekday.isoformat()}:0740"
    assert entry["spots_open"] == 3
    assert entry["last_alerted_spots"] == 3
    assert entry["last_alerted_at"] is not None


async def test_slot_id_has_no_spot_component(cfg, tmp_path, weekday):
    """Spot count changes must land on the SAME ledger row, not a new one."""
    p = tmp_path / "state.json"
    await _scan(cfg, p, [_raw(weekday, spots=2)])
    await _scan(cfg, p, [_raw(weekday, spots=4)])
    assert len(store.load_state(p)["tee_times"]) == 1


# ---------- what must never alert ----------


async def test_paused_suppresses_alerts(cfg, tmp_path, weekday):
    p = tmp_path / "state.json"
    state = store.default_state()
    state["paused"] = True
    await store.save_state(p, state)

    bot, _ = await _scan(cfg, p, [_raw(weekday)])
    assert bot.sent == []


async def test_paused_does_not_stamp_the_ledger(cfg, tmp_path, weekday):
    """A slot muted by /pause must alert once /resume lands."""
    p = tmp_path / "state.json"
    state = store.default_state()
    state["paused"] = True
    await store.save_state(p, state)
    await _scan(cfg, p, [_raw(weekday)])

    state = store.load_state(p)
    state["paused"] = False
    await store.save_state(p, state)

    bot, _ = await _scan(cfg, p, [_raw(weekday)])
    assert len(bot.sent) == 1


async def test_non_all_star_course_never_alerts(cfg, tmp_path, weekday):
    bot, result = await _scan(cfg, tmp_path / "state.json", [_raw(weekday, course="lions")])
    assert bot.sent == []
    assert result["matches"] == []


async def test_weekend_never_alerts(cfg, tmp_path, weekend):
    bot, result = await _scan(cfg, tmp_path / "state.json", [_raw(weekend)])
    assert bot.sent == []
    assert result["matches"] == []


async def test_outside_premium_window_never_alerts(cfg, tmp_path, weekday):
    bot, result = await _scan(cfg, tmp_path / "state.json", [_raw(weekday, t=OUT_OF_WINDOW)])
    assert bot.sent == []
    assert result["matches"] == []


# ---------- /full cache ----------


async def test_scan_caches_all_raw_slots_including_non_all_star(cfg, tmp_path, weekday):
    """Scan all, alert on four: /full needs every slot, not just winners."""
    _, result = await _scan(cfg, tmp_path / "state.json", [
        _raw(weekday),
        _raw(weekday, course="lions"),
        _raw(weekday, t=OUT_OF_WINDOW),
    ])
    cached = {(s["course_key"], s["tee_time"]) for s in result["raw_slots"]}
    assert ("lions", "07:40:00") in cached
    assert ("roy_kizer", "14:00:00") in cached


async def test_raw_slots_persist_at_top_level(cfg, tmp_path, weekday):
    """docs/SPEC.md > Data model puts raw_slots at the root of state.json —
    /full reads it from there, and there is no `last_scan` wrapper."""
    p = tmp_path / "state.json"
    await _scan(cfg, p, [_raw(weekday)])
    state = store.load_state(p)
    assert isinstance(state["raw_slots"], list)
    assert state["raw_slots"], "cache must be populated for /full"
    assert "last_scan" not in state


async def test_scan_records_poll_and_next_run_stamps(cfg, tmp_path, weekday):
    p = tmp_path / "state.json"
    providers = {"golfatx": FakeProvider([_raw(weekday)]), "golfnow": FakeProvider([])}
    next_run = datetime.now(cfg.tz) + timedelta(minutes=60)
    await scan_and_notify(
        cfg=cfg, providers=providers, state_path=p, bot=FakeBot(),
        chat_id=-1, force=True, next_run_at=next_run,
    )
    state = store.load_state(p)
    assert state["last_poll_at"] is not None
    assert state["next_run_at"] == next_run.isoformat()
    assert state["last_alert_at"] is not None
