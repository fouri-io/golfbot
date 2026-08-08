# 0002 — `curl_cffi` to reach WebTrac through Cloudflare

**Status:** Accepted

## Context

The City of Austin muni courses publish availability through WebTrac at
`txaustinweb.myvscloud.com`. The site is public — no login, no paywall — but it
sits behind Cloudflare, which fingerprints the TLS handshake. Requests from
plain `httpx` are blocked regardless of headers, because the block is on the
TLS/JA3 signature, not on anything in the HTTP layer.

## Decision

Use `curl_cffi` for the GolfATX provider, impersonating a real Firefox TLS
fingerprint. `httpx` stays in use for GolfNow and Open-Meteo, which have no
such gate.

The GolfATX fetch is two steps: GET the search page to harvest a session cookie
and CSRF token, then GET the search itself with those plus the course/date/player
parameters.

## Consequences

Good:

- Access to the muni courses, which are the whole point of the project —
  they're the cheap tee times.
- One request per **date** rather than per course: WebTrac returns every Austin
  muni course in a single search (`secondarycode=""`), so a 7-day horizon costs
  7 requests and we filter to our roster client-side.

Bad:

- A second HTTP stack to keep working, with a compiled dependency.
- The impersonation target is a moving one. If Cloudflare tightens, the fix is
  to bump the impersonated browser profile — that's the first thing to try when
  GolfATX starts returning zero rows.
- Two-step token harvesting means a markup change on the search page breaks the
  fetch before the search even runs. Distinguish "token scrape failed" from
  "search returned nothing" when debugging.

## Notes

This is scraping a public page at a low, human rate (a handful of requests per
hour for personal use). Keep it that way: the polling interval and the
`active_window` in `config.yaml` exist partly to keep this defensible. Don't
add parallelism or shrink the interval to seconds.
