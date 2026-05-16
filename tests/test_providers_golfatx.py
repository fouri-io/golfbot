"""Tests for the GolfATX provider's parsing (no network)."""
from __future__ import annotations

from datetime import date, time
from pathlib import Path

from golfbot.providers.golfatx import (
    WEBTRAC_NAME_BY_CODE,
    _parse_clock,
    build_search_params,
    parse_results,
)

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "golfatx_roy_kizer_2026-05-19.html"
)


def _load_fixture() -> str:
    return FIXTURE.read_text()


# ---------- _parse_clock ----------


def test_parse_clock_morning():
    assert _parse_clock("8:00 am") == time(8, 0)
    assert _parse_clock(" 7:30 am") == time(7, 30)


def test_parse_clock_pm():
    assert _parse_clock(" 2:21 pm") == time(14, 21)
    assert _parse_clock("12:30 pm") == time(12, 30)


def test_parse_clock_midnight_and_noon():
    assert _parse_clock("12:00 am") == time(0, 0)
    assert _parse_clock("12:00 pm") == time(12, 0)


def test_parse_clock_invalid():
    assert _parse_clock("nonsense") is None
    assert _parse_clock("25:00 am") is None
    assert _parse_clock("8:99 am") is None
    assert _parse_clock("") is None


# ---------- build_search_params ----------


def test_build_search_params():
    params = build_search_params("CSRF123", date(2026, 5, 19), 3)
    assert params["_csrf_token"] == "CSRF123"
    assert params["begindate"] == "05/19/2026"
    assert params["numberofplayers"] == "3"
    assert params["numberofholes"] == "18"
    assert params["begintime"] == "12:00 am"
    assert params["secondarycode"] == ""    # all courses
    assert params["module"] == "GR"
    assert params["search"] == "yes"


# ---------- parse_results ----------


def test_parse_results_extracts_roy_kizer_rows():
    name_to_key = {"Roy Kizer Golf Course": "roy_kizer"}
    slots = parse_results(_load_fixture(), name_to_key, date(2026, 5, 19))
    assert len(slots) == 3
    assert all(s.course_key == "roy_kizer" for s in slots)
    assert all(s.tee_date == date(2026, 5, 19) for s in slots)
    assert all(s.holes == 18 for s in slots)
    assert all(s.provider == "golfatx" for s in slots)


def test_parse_results_first_row_fields():
    name_to_key = {"Roy Kizer Golf Course": "roy_kizer"}
    slots = parse_results(_load_fixture(), name_to_key, date(2026, 5, 19))
    s = slots[0]
    assert s.tee_time == time(14, 21)
    assert s.players_available == 2
    assert s.booking_url.startswith(
        "https://txaustinweb.myvscloud.com/webtrac/web/addtocart.html"
    )
    assert "GRFMIDList=280637905" in s.booking_url
    assert s.price_usd is None


def test_parse_results_filters_unknown_courses():
    """Slots for courses not in name_to_key should be skipped."""
    slots = parse_results(_load_fixture(), {}, date(2026, 5, 19))
    assert slots == []


def test_parse_results_handles_empty_html():
    assert parse_results("", {}, date(2026, 5, 19)) == []
    assert parse_results("<html></html>", {}, date(2026, 5, 19)) == []


def test_parse_results_handles_open_slots_count_correctly():
    """Rows 1-3 of fixture (bookable): 2, 3, 3 open. The 4th row is
    unavailable and must not be returned."""
    name_to_key = {"Roy Kizer Golf Course": "roy_kizer"}
    slots = parse_results(_load_fixture(), name_to_key, date(2026, 5, 19))
    assert [s.players_available for s in slots] == [2, 3, 3]


def test_parse_results_filters_unavailable_rows():
    """Fixture's 4th row has class='button error' + href='#' — out of
    booking window. Parser must skip it even though Open Slots=4."""
    name_to_key = {"Roy Kizer Golf Course": "roy_kizer"}
    slots = parse_results(_load_fixture(), name_to_key, date(2026, 5, 19))
    # All returned slots have real booking URLs.
    for s in slots:
        assert s.booking_url.startswith(
            "https://txaustinweb.myvscloud.com/webtrac/web/addtocart.html"
        )
    # The 4th-row time (6:31 AM) is the unavailable one and should be absent.
    assert all(s.tee_time != time(6, 31) for s in slots)


def test_parse_results_filters_error_class_button():
    """Synthetic row with class='button error' must be skipped."""
    html = """
    <table id="grwebsearch_output_table"><tbody>
    <tr>
      <td class="label-cell" data-title="Course">Roy Kizer Golf Course</td>
      <td class="label-cell" data-title="Date">05/30/2026</td>
      <td class="label-cell" data-title="Time"> 7:00 am</td>
      <td class="label-cell" data-title="Status"></td>
      <td class="label-cell" data-title="Open Slots">4</td>
      <td class="button-cell" data-title="Item Action">
        <a class="button error cart-button" href="#">Unavailable</a>
      </td>
    </tr>
    </tbody></table>
    """
    name_to_key = {"Roy Kizer Golf Course": "roy_kizer"}
    assert parse_results(html, name_to_key, date(2026, 5, 30)) == []


def test_parse_results_filters_placeholder_href():
    """Even if a row's class happens to be 'success' (defensive), a href
    that's not an http URL must be rejected."""
    html = """
    <table id="grwebsearch_output_table"><tbody>
    <tr>
      <td class="label-cell" data-title="Course">Roy Kizer Golf Course</td>
      <td class="label-cell" data-title="Date">05/19/2026</td>
      <td class="label-cell" data-title="Time"> 8:00 am</td>
      <td class="label-cell" data-title="Status"></td>
      <td class="label-cell" data-title="Open Slots">2</td>
      <td class="button-cell" data-title="Item Action">
        <a class="button success cart-button" href="#">Add To Cart</a>
      </td>
    </tr>
    </tbody></table>
    """
    name_to_key = {"Roy Kizer Golf Course": "roy_kizer"}
    assert parse_results(html, name_to_key, date(2026, 5, 19)) == []


# ---------- WEBTRAC_NAME_BY_CODE constant ----------


def test_webtrac_name_map_complete():
    """All 5 Austin muni courses should be in the lookup."""
    assert set(WEBTRAC_NAME_BY_CODE.keys()) == {"1", "2", "3", "4", "5"}
    assert "Jimmy Clay" in WEBTRAC_NAME_BY_CODE["1"]
    assert "Roy Kizer" in WEBTRAC_NAME_BY_CODE["2"]
    assert "Morris Williams" in WEBTRAC_NAME_BY_CODE["3"]
    assert "Lions" in WEBTRAC_NAME_BY_CODE["4"]
    assert "Hancock" in WEBTRAC_NAME_BY_CODE["5"]
