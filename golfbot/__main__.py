"""CLI entry point. Subcommands: run | mock | scrape."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

from dotenv import load_dotenv

from golfbot import bot as botmod
from golfbot import mock_source
from golfbot.config import load as load_config
from golfbot.config import resolve_telegram_secrets

DATA_DIR = Path("data")
STATE_PATH = DATA_DIR / "state.json"
BOOKINGS_PATH = DATA_DIR / "bookings.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="golfbot")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", help="Start Telegram listener (long-polling)")

    mock = sub.add_parser("mock", help="Inject a synthetic tee time (P1 testing)")
    mock.add_argument("--course", required=True, help="course key, e.g. roy_kizer")
    mock.add_argument("--date", required=True, help="YYYY-MM-DD")
    mock.add_argument("--time", required=True, help="HH:MM (24-hour)")
    mock.add_argument("--players", type=int, default=4)
    mock.add_argument("--grade", choices=["A", "B", "C"], default="A")

    scrape = sub.add_parser("scrape", help="One-shot real scrape (P2)")
    scrape.add_argument(
        "--date",
        help="YYYY-MM-DD for a single date; omit to sweep the configured horizon",
    )
    scrape.add_argument(
        "--course",
        help="course key (defaults to every course in config)",
    )
    scrape.add_argument(
        "--players",
        type=int,
        default=None,
        help="override per-date min_players (default: derived from availability)",
    )
    scrape.add_argument(
        "--raw",
        action="store_true",
        help="show unfiltered API output (skip day/time/grade filters + Policy B)",
    )

    args = parser.parse_args(argv)

    if args.cmd == "run":
        return _cmd_run()
    if args.cmd == "mock":
        return _cmd_mock(args)
    if args.cmd == "scrape":
        return _cmd_scrape(args)
    return 0


def _cmd_run() -> int:
    load_dotenv()
    try:
        cfg = load_config()
        token, chat_id = resolve_telegram_secrets(cfg)
    except (RuntimeError, ValueError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.INFO)

    ctx = botmod.BotContext(
        cfg=cfg,
        state_path=STATE_PATH,
        bookings_path=BOOKINGS_PATH,
        chat_id=chat_id,
    )

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    from golfbot import scanner
    from golfbot.providers.golfatx import GolfATXProvider
    from golfbot.providers.golfnow import GolfNowProvider

    providers = {"golfnow": GolfNowProvider(), "golfatx": GolfATXProvider()}

    async def post_init(app):
        scheduler = AsyncIOScheduler(timezone=cfg.tz)

        async def _job(force: bool = False):
            job = scheduler.get_job("scan")
            next_run = job.next_run_time if job else None
            logging.getLogger("golfbot.scheduler").info(
                "scan job firing (force=%s, next fire after this: %s)",
                force, next_run,
            )
            await scanner.scan_and_notify(
                cfg=cfg,
                providers=providers,
                state_path=STATE_PATH,
                bot=app.bot,
                chat_id=chat_id,
                next_run_at=next_run,
                force=force,
            )

        # Skip the immediate startup scan if we just scanned recently —
        # avoids redundant API hits across dev restarts.
        from golfbot import store as _store
        state = _store.load_state(STATE_PATH)
        last_poll_str = state.get("last_poll_at")
        skip_initial = False
        if last_poll_str:
            try:
                last_poll = datetime.fromisoformat(last_poll_str)
                age = datetime.now(cfg.tz) - last_poll
                if 0 <= age.total_seconds() < 10 * 60:
                    skip_initial = True
                    print(
                        f"Skipping initial scan — last poll was "
                        f"{int(age.total_seconds() // 60)} min ago",
                        file=sys.stderr,
                    )
            except (ValueError, TypeError):
                pass

        first_fire = (
            None if skip_initial
            else datetime.now(cfg.tz) + timedelta(seconds=10)
        )
        scheduler.add_job(
            _job,
            trigger=IntervalTrigger(
                minutes=cfg.polling.default_interval_minutes,
                jitter=cfg.polling.jitter_minutes * 60,
            ),
            id="scan",
            next_run_time=first_fire,
            # None = always fire eventually, no matter how late (e.g. macOS slept).
            # With coalesce=True, multiple missed fires collapse into one.
            misfire_grace_time=None,
            coalesce=True,
        )
        scheduler.start()
        app.bot_data["scheduler"] = scheduler
        app.bot_data["providers"] = providers
        app.bot_data["scan_job"] = _job

    async def post_shutdown(app):
        sched = app.bot_data.get("scheduler")
        if sched:
            sched.shutdown(wait=False)

    app = botmod.build_app(token, ctx, post_init=post_init, post_shutdown=post_shutdown)
    print(f"Starting golfbot — chat_id={chat_id}. Ctrl-C to stop.")
    app.run_polling()
    return 0


def _cmd_scrape(args: argparse.Namespace) -> int:
    """Scrape provider(s) for tee times and print results.

    Default: full configured horizon, filter+grade+Policy B, show preview
    of what would notify. `--raw` shows unfiltered API output (debug mode).
    """
    load_dotenv()
    try:
        cfg = load_config()
    except (RuntimeError, ValueError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    # Resolve the date set to scrape.
    if args.date:
        try:
            dates = [date.fromisoformat(args.date)]
        except ValueError:
            print(f"Bad --date {args.date!r}: use YYYY-MM-DD", file=sys.stderr)
            return 1
    else:
        # Default sweep: today + start_offset_days through end of horizon.
        from golfbot.horizon import current_window
        today = datetime.now(cfg.tz).date()
        start, end = current_window(
            today=today,
            start_offset_days=cfg.search.start_offset_days,
            horizon_days=cfg.search.horizon_days,
        )
        dates = []
        d = start
        while d <= end:
            dates.append(d)
            d = d + timedelta(days=1)

    # Resolve courses.
    if args.course:
        c = cfg.course_by_key(args.course)
        if c is None:
            known = ", ".join(x.key for x in cfg.courses)
            print(f"Unknown --course {args.course!r}; known: {known}", file=sys.stderr)
            return 1
        courses = [c]
    else:
        courses = list(cfg.courses)

    # Group by provider.
    by_provider: dict[str, list] = {}
    for c in courses:
        by_provider.setdefault(c.provider, []).append(c)

    from golfbot.providers.golfatx import GolfATXProvider
    from golfbot.providers.golfnow import GolfNowProvider
    providers = {"golfnow": GolfNowProvider(), "golfatx": GolfATXProvider()}

    # Set up live INFO logging so the user sees progress.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    print(
        f"Scraping {len(courses)} course(s) × {len(dates)} date(s) "
        f"(players>={args.players}). Ctrl-C to stop.",
        file=sys.stderr,
    )
    for provider_name, owned in by_provider.items():
        if provider_name not in providers:
            print(
                f"  ! provider {provider_name!r} not implemented yet — skipping: "
                f"{', '.join(c.key for c in owned)}",
                file=sys.stderr,
            )

    raw_players = args.players if args.players is not None else 3

    if args.raw:
        # Raw mode bypasses the pipeline — we need the unfiltered RawSlot list.
        async def run_raw() -> list:
            all_slots = []
            for provider_name, owned in by_provider.items():
                prov = providers.get(provider_name)
                if prov is None:
                    continue
                for d in dates:
                    all_slots.extend(await prov.fetch_slots(owned, d, raw_players))
            return all_slots

        raw_slots = asyncio.run(run_raw())
        _print_raw(raw_slots, dates, raw_players)
        return 0

    # Filtered preview: scope cfg.courses to the requested course (if any) and
    # delegate to the same scanner.run_scan used by the scheduled job.
    from golfbot import availability as avail_mod
    from golfbot import scanner, store
    scoped_cfg = cfg.model_copy(update={"courses": courses})

    if args.players is not None:
        # --players overrides the dynamic count for every date.
        matches = asyncio.run(
            scanner.run_scan(
                scoped_cfg, providers, dates,
                availability=None,
                fallback_min_players=args.players,
            )
        )
    else:
        # Default: load availability from state and let scanner compute
        # per-date min_players. Members without a real telegram_user_id
        # are excluded — so if only Colby is registered, this queries with 1.
        availability = avail_mod.load_availability(store.load_state(STATE_PATH))
        matches = asyncio.run(
            scanner.run_scan(scoped_cfg, providers, dates, availability=availability)
        )

    # We don't have the raw counts here (run_scan filters internally), but the
    # per-fetch logs already show them. Show the funnel summary using what we know.
    days = ", ".join(d[:3].capitalize() for d in cfg.search.days_of_week)
    win = cfg.time_windows.acceptable
    print("\n" + "─" * 70, file=sys.stderr)
    print("Funnel:", file=sys.stderr)
    print(
        f"  after filters + Policy B: {len(matches):>4} match(es)  "
        f"(days={days}, window={win.start.strftime('%H:%M')}-"
        f"{win.end.strftime('%H:%M')}, "
        f"grade≥{cfg.grading.notify_min_grade})",
        file=sys.stderr,
    )
    print("─" * 70, file=sys.stderr)

    if not matches:
        print("\nNo notifiable matches.")
        return 0

    print(f"\nWould notify on {len(matches)} match(es):\n")
    for m in sorted(matches, key=lambda x: (x.raw.tee_date, x.raw.tee_time)):
        dow = m.raw.tee_date.strftime("%a")
        price = f"${m.raw.price_usd:.2f}" if m.raw.price_usd is not None else "—"
        print(
            f"  {dow} {m.raw.tee_date} {m.raw.tee_time.strftime('%H:%M')}  "
            f"Grade {m.grade}  {m.course_display:18} "
            f"≥{m.raw.players_available}p  {price:>7}  {m.raw.booking_url}"
        )
    return 0


def _print_raw(slots: list, dates: list, players: int) -> None:
    if not slots:
        print(f"\nNo slots returned across {len(dates)} date(s) (players>={players}).")
        return
    print(f"\nFound {len(slots)} raw slot(s) across {len(dates)} date(s) (players>={players}):")
    for s in sorted(slots, key=lambda x: (x.tee_date, x.course_key, x.tee_time)):
        price = f"${s.price_usd:.2f}" if s.price_usd is not None else "—"
        print(
            f"  {s.tee_date} {s.tee_time.strftime('%H:%M')}  "
            f"{s.course_key:18} {s.holes}h  ≥{s.players_available}p  {price:>7}  "
            f"[{s.provider}]  {s.booking_url}"
        )


def _print_preview(raw, graded, best, cfg, dates, players: int) -> None:
    """The funnel view + final notifiable matches."""
    days = ", ".join(d[:3].capitalize() for d in cfg.search.days_of_week)
    ideal = cfg.time_windows.acceptable
    print("\n" + "─" * 70, file=sys.stderr)
    print("Funnel:", file=sys.stderr)
    print(f"  fetched      : {len(raw):>4} raw slot(s)", file=sys.stderr)
    print(
        f"  after filters: {len(graded):>4}  "
        f"(days={days}, window={ideal.start.strftime('%H:%M')}-"
        f"{ideal.end.strftime('%H:%M')}, "
        f"grade≥{cfg.grading.notify_min_grade})",
        file=sys.stderr,
    )
    print(
        f"  Policy B     : {len(best):>4}  (best per course-date)",
        file=sys.stderr,
    )
    print("─" * 70, file=sys.stderr)

    if not best:
        print("\nNo notifiable matches.")
        return

    print(f"\nWould notify on {len(best)} match(es):\n")
    for m in sorted(best, key=lambda x: (x.raw.tee_date, x.raw.tee_time)):
        dow = m.raw.tee_date.strftime("%a")
        price = f"${m.raw.price_usd:.2f}" if m.raw.price_usd is not None else "—"
        print(
            f"  {dow} {m.raw.tee_date} {m.raw.tee_time.strftime('%H:%M')}  "
            f"Grade {m.grade}  {m.course_display:18} "
            f"≥{m.raw.players_available}p  {price:>7}  {m.raw.booking_url}"
        )


def _cmd_mock(args: argparse.Namespace) -> int:
    load_dotenv()
    try:
        cfg = load_config()
        token, chat_id = resolve_telegram_secrets(cfg)
    except (RuntimeError, ValueError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    try:
        tee_date = date.fromisoformat(args.date)
    except ValueError:
        print(f"Bad --date {args.date!r}: use YYYY-MM-DD", file=sys.stderr)
        return 1
    try:
        hh, mm = args.time.split(":")
        tee_time = time(int(hh), int(mm))
    except (ValueError, AttributeError):
        print(f"Bad --time {args.time!r}: use HH:MM (24-hour)", file=sys.stderr)
        return 1

    try:
        slot, msg_id = asyncio.run(mock_source.inject(
            cfg=cfg,
            bot_token=token,
            chat_id=chat_id,
            state_path=STATE_PATH,
            course_key=args.course,
            tee_date=tee_date,
            tee_time=tee_time,
            players=args.players,
            grade=args.grade,
        ))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(
        f"Sent {args.grade}-grade mock tee time: "
        f"{slot.course_key} {slot.tee_date} {slot.tee_time.strftime('%H:%M')} "
        f"({slot.players_open} players). message_id={msg_id}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
