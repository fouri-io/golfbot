"""GolfATX (City of Austin WebTrac) provider.

Hits the public WebTrac search at txaustinweb.myvscloud.com. The site is
behind Cloudflare, so we use `curl_cffi` to mimic a real Firefox TLS
fingerprint — plain `httpx` gets blocked.

Two-step request:
1. GET the search page once to harvest a session cookie + CSRF token.
2. GET the search with that token + course/date/player params.

One search returns ALL Austin muni courses for the date (we pass
`secondarycode=""` to query everything), so a 7-day horizon = 7 requests
total. We filter to our roster client-side.

Player availability is explicit on WebTrac (the "Open Slots" column) —
no ">=N" approximation needed.
"""
from __future__ import annotations

import logging
import re
import time as _time_mod
from dataclasses import dataclass
from datetime import date, time

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from golfbot.config import Course
from golfbot.providers.base import RawSlot

log = logging.getLogger(__name__)

_BASE_URL = "https://txaustinweb.myvscloud.com"
_SEARCH_PATH = "/webtrac/web/search.html"
_IMPERSONATE = "firefox135"

# WebTrac's internal course code → display name in the response table.
# Verified by inspecting the <select id="secondarycode"> on the search form.
WEBTRAC_NAME_BY_CODE: dict[str, str] = {
    "1": "Jimmy Clay Golf Course",
    "2": "Roy Kizer Golf Course",
    "3": "Morris Williams Golf Course",
    "4": "Lions Municipal Golf Course",
    "5": "Hancock Golf Course",
}


@dataclass
class GolfATXProvider:
    name: str = "golfatx"
    timeout_seconds: float = 30.0

    async def fetch_slots(
        self,
        courses: list[Course],
        target_date: date,
        min_players: int,
    ) -> list[RawSlot]:
        owned = [c for c in courses if c.provider == self.name]
        if not owned:
            return []

        # WebTrac display name -> our internal course key
        name_to_key: dict[str, str] = {}
        for c in owned:
            code = str(c.provider_id)
            webtrac_name = WEBTRAC_NAME_BY_CODE.get(code)
            if webtrac_name is None:
                log.warning(
                    "course %r has unknown GolfATX code %r; skipping",
                    c.key, c.provider_id,
                )
                continue
            name_to_key[webtrac_name] = c.key

        if not name_to_key:
            return []

        log.info(
            "golfatx fetch: %d course(s) for %s ...",
            len(name_to_key), target_date,
        )
        started = _time_mod.monotonic()

        try:
            async with AsyncSession(impersonate=_IMPERSONATE, timeout=self.timeout_seconds) as session:
                token = await _harvest_csrf_token(session)
                html = await _do_search(session, token, target_date, min_players)
        except RequestException as e:
            log.warning("GolfATX fetch failed for %s: %s", target_date, e)
            return []
        except RuntimeError as e:
            log.warning("GolfATX fetch failed for %s: %s", target_date, e)
            return []

        slots = parse_results(html, name_to_key, target_date)
        elapsed = _time_mod.monotonic() - started
        log.info(
            "golfatx fetch: %s -> %d slot(s) in %.2fs",
            target_date, len(slots), elapsed,
        )
        return slots


# --------------------------------------------------------------------------- #
# Internals — exposed for tests                                                #
# --------------------------------------------------------------------------- #


async def _harvest_csrf_token(session: AsyncSession) -> str:
    """Hit the search form to get a fresh CSRF token + session cookies."""
    r = await session.get(f"{_BASE_URL}{_SEARCH_PATH}?module=GR")
    r.raise_for_status()
    m = re.search(r'name="_csrf_token"\s+value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("Could not extract _csrf_token from WebTrac search form")
    return m.group(1)


async def _do_search(
    session: AsyncSession,
    token: str,
    target_date: date,
    min_players: int,
) -> str:
    """Run the all-courses search with the given parameters. Returns the
    raw HTML response body."""
    params = build_search_params(token, target_date, min_players)
    r = await session.get(f"{_BASE_URL}{_SEARCH_PATH}", params=params)
    r.raise_for_status()
    return r.text


def build_search_params(token: str, target_date: date, min_players: int) -> dict[str, str]:
    """Build the query-string parameters for a WebTrac search.

    `begintime=12:00 am` ensures we get all slots from start of day —
    WebTrac would otherwise hide earlier results.
    """
    return {
        "Action": "Start",
        "SubAction": "",
        "_csrf_token": token,
        "secondarycode": "",                          # all courses
        "begintime": "12:00 am",                      # full day
        "begindate": target_date.strftime("%m/%d/%Y"),
        "numberofplayers": str(min_players),
        "numberofholes": "18",
        "search": "yes",
        "page": "1",
        "module": "GR",
        "multiselectlist_value": "",
        "grwebsearch_buttonsearch": "yes",
    }


def parse_results(
    html: str,
    name_to_key: dict[str, str],
    target_date: date,
) -> list[RawSlot]:
    """Parse WebTrac result HTML into RawSlots, filtered to our roster.

    `name_to_key` maps WebTrac course display names to our internal keys.
    Rows whose course isn't in the map are silently skipped.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="grwebsearch_output_table")
    if table is None:
        return []
    tbody = table.find("tbody")
    if tbody is None:
        return []

    out: list[RawSlot] = []
    for tr in tbody.find_all("tr"):
        slot = _parse_row(tr, name_to_key, target_date)
        if slot is not None:
            out.append(slot)
    return out


def _parse_row(tr, name_to_key: dict[str, str], target_date: date) -> RawSlot | None:
    cells = {td.get("data-title"): td for td in tr.find_all("td")}

    course_cell = cells.get("Course")
    time_cell = cells.get("Time")
    slots_cell = cells.get("Open Slots")
    action_cell = cells.get("Item Action")
    if not (course_cell and time_cell and slots_cell and action_cell):
        return None

    course_name = course_cell.get_text(strip=True)
    course_key = name_to_key.get(course_name)
    if course_key is None:
        return None  # not in our roster

    tee_time = _parse_clock(time_cell.get_text(strip=True))
    if tee_time is None:
        return None

    try:
        open_slots = int(slots_cell.get_text(strip=True))
    except (ValueError, TypeError):
        return None
    if open_slots <= 0:
        return None

    link = action_cell.find("a", href=True)
    if not link:
        return None
    # Bookable rows have class="button success ..." with a real addtocart URL.
    # Future rows outside Austin's release window (5-7 days depending on day
    # of week) have class="button error ...", href="#", text "Unavailable",
    # and a tooltip listing the release rules. We surface only bookable ones.
    classes = link.get("class") or []
    if "success" not in classes:
        return None
    booking_url = link["href"]
    if not booking_url.startswith("http"):
        return None

    return RawSlot(
        course_key=course_key,
        tee_date=target_date,
        tee_time=tee_time,
        players_available=open_slots,
        holes=18,
        booking_url=booking_url,
        provider="golfatx",
        price_usd=None,    # WebTrac doesn't expose price in search results
    )


def _parse_clock(s: str) -> time | None:
    """Parse strings like ' 8:00 am', '3:01 pm'."""
    s = s.strip().lower()
    m = re.match(r"(\d{1,2}):(\d{2})\s*(am|pm)$", s)
    if not m:
        return None
    h, mi, mer = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (1 <= h <= 12 and 0 <= mi < 60):
        return None
    if mer == "pm" and h != 12:
        h += 12
    elif mer == "am" and h == 12:
        h = 0
    return time(h, mi)
