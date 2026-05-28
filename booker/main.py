"""
Entry point for the tee time booking bot.

Usage:
    python -m booker.main [--course COURSE] [--date YYYY-MM-DD] [--dry-run]

All options can also be set via environment variables (see .env.example).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config_loader import load_course, load_prefs
from .ezlinks_booker import EZLinksBooker
from .logger import get_logger
from .notifier import send_notification

load_dotenv()
log = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Golf tee time booking bot")
    p.add_argument("--course", help="Course key from courses.yaml (default: marine_park)")
    p.add_argument("--date", help="Target date YYYY-MM-DD (default: today + days_ahead)")
    p.add_argument("--dry-run", action="store_true", help="Log times without booking")
    p.add_argument("--headed", action="store_true", help="Run browser in non-headless mode")
    return p.parse_args()


async def _main() -> int:
    args = _parse_args()

    try:
        prefs = load_prefs(
            course_override=args.course,
            date_override=args.date,
        )
        if args.dry_run:
            prefs = prefs.model_copy(update={"dry_run": True})

        course = load_course(prefs.course)
    except Exception as exc:
        log.error("Config error: %s", exc)
        return 1

    log.info(
        "Booking: course=%s date=%s players=%d dry_run=%s",
        course.name,
        prefs.target_date,
        prefs.players,
        prefs.dry_run,
    )

    booker = EZLinksBooker(course=course, prefs=prefs, headless=not args.headed)
    result = await booker.book()

    if result.success:
        log.info(
            "Booking complete! Time=%s Confirmation=%s",
            result.booked_time,
            result.confirmation_number,
        )
    else:
        log.error("Booking failed: %s", result.error_message)

    send_notification(result)

    return 0 if result.success else 1


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
