"""Tests for config loading and time-window logic."""

import os
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from booker.config_loader import load_course, load_prefs, TimeWindow
from booker.ezlinks_booker import _parse_time_to_minutes, EZLinksBooker, TeeTimeSlot


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("time_str,expected_minutes", [
    ("7:00 AM", 7 * 60),
    ("7:00AM", 7 * 60),
    ("07:00", 7 * 60),
    ("7:14 AM", 7 * 60 + 14),
    ("12:00 PM", 12 * 60),
    ("12:00 AM", 0),
    ("1:30 PM", 13 * 60 + 30),
    ("11:59 PM", 23 * 60 + 59),
])
def test_parse_time_to_minutes(time_str: str, expected_minutes: int) -> None:
    assert _parse_time_to_minutes(time_str) == expected_minutes


# ---------------------------------------------------------------------------
# Time window matching
# ---------------------------------------------------------------------------

def _slot(time_str: str) -> TeeTimeSlot:
    return TeeTimeSlot(time_str=time_str, element_index=0)


@pytest.mark.parametrize("time_str,earliest,latest,expected", [
    ("7:00 AM", "07:00", "09:00", True),
    ("7:14 AM", "07:00", "09:00", True),
    ("9:00 AM", "07:00", "09:00", True),
    ("9:01 AM", "07:00", "09:00", False),
    ("6:59 AM", "07:00", "09:00", False),
    ("10:30 AM", "09:00", "11:00", True),
    ("12:00 PM", "07:00", "09:00", False),
])
def test_in_window(time_str: str, earliest: str, latest: str, expected: bool) -> None:
    assert EZLinksBooker._in_window(time_str, earliest, latest) is expected


# ---------------------------------------------------------------------------
# Slot selection
# ---------------------------------------------------------------------------

def _make_booker(earliest: str = "07:00", latest: str = "09:00") -> EZLinksBooker:
    from booker.config_loader import BookingPrefs, CourseConfig, TimeWindow

    course = CourseConfig(
        name="Test Course",
        booking_url="https://example.com",
        requires_login=False,
        selectors={},
    )
    prefs = BookingPrefs(
        course="test",
        players=2,
        preferred_window=TimeWindow(earliest=earliest, latest=latest),
        fallback_windows=[TimeWindow(earliest="09:00", latest="11:00")],
        days_ahead=7,
        dry_run=True,
        target_date=date.today() + timedelta(days=7),
    )
    return EZLinksBooker(course=course, prefs=prefs)


def test_select_best_slot_preferred() -> None:
    booker = _make_booker("07:00", "09:00")
    slots = [
        TeeTimeSlot("6:00 AM", 0),
        TeeTimeSlot("7:14 AM", 1),
        TeeTimeSlot("8:00 AM", 2),
        TeeTimeSlot("10:00 AM", 3),
    ]
    best = booker._select_best_slot(slots)
    assert best is not None
    assert best.time_str == "7:14 AM"


def test_select_best_slot_fallback() -> None:
    booker = _make_booker("07:00", "07:30")
    slots = [
        TeeTimeSlot("9:00 AM", 0),
        TeeTimeSlot("9:30 AM", 1),
        TeeTimeSlot("10:00 AM", 2),
    ]
    best = booker._select_best_slot(slots)
    assert best is not None
    assert best.time_str == "9:00 AM"  # fallback window 09:00–11:00


def test_select_best_slot_none_available() -> None:
    booker = _make_booker("07:00", "07:30")
    slots = [TeeTimeSlot("1:00 PM", 0), TeeTimeSlot("2:00 PM", 1)]
    best = booker._select_best_slot(slots)
    assert best is None


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def test_load_course_marine_park() -> None:
    course = load_course("marine_park")
    assert course.name == "Marine Park Golf Course"
    assert "ezlinksgolf.com" in course.booking_url
    assert course.requires_login is True


def test_load_course_unknown_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        load_course("nonexistent_course")


def test_load_prefs_defaults() -> None:
    prefs = load_prefs()
    assert prefs.course == "marine_park"
    assert prefs.players == 2
    assert prefs.days_ahead == 7
    assert prefs.dry_run is False
    assert prefs.target_date == date.today() + timedelta(days=7)


def test_load_prefs_env_override() -> None:
    with patch.dict(os.environ, {"PLAYERS": "4", "DRY_RUN": "true"}):
        prefs = load_prefs()
    assert prefs.players == 4
    assert prefs.dry_run is True


def test_load_prefs_date_override() -> None:
    prefs = load_prefs(date_override="2026-07-04")
    assert str(prefs.target_date) == "2026-07-04"
