"""Load and validate course configs and booking preferences."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator

CONFIG_DIR = Path(__file__).parent.parent / "config"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CredentialsConfig(BaseModel):
    email_env: str
    password_env: str

    def resolve(self) -> tuple[str, str]:
        email = os.environ.get(self.email_env, "")
        password = os.environ.get(self.password_env, "")
        return email, password


class PaymentConfig(BaseModel):
    card_number_env: str
    card_expiry_env: str
    card_cvv_env: str
    card_zip_env: str
    card_name_env: str

    def resolve(self) -> dict[str, str]:
        return {
            "card_number": os.environ.get(self.card_number_env, ""),
            "card_expiry": os.environ.get(self.card_expiry_env, ""),
            "card_cvv": os.environ.get(self.card_cvv_env, ""),
            "card_zip": os.environ.get(self.card_zip_env, ""),
            "card_name": os.environ.get(self.card_name_env, ""),
        }


class CourseConfig(BaseModel):
    name: str
    booking_url: str
    requires_login: bool = True
    credentials: CredentialsConfig | None = None
    payment: PaymentConfig | None = None
    selectors: dict[str, str] = {}


class TimeWindow(BaseModel):
    earliest: str  # "HH:MM"
    latest: str    # "HH:MM"

    @field_validator("earliest", "latest")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise ValueError(f"Time must be HH:MM format, got: {v!r}")
        return v


class BookingPrefs(BaseModel):
    course: str
    players: int
    preferred_window: TimeWindow
    fallback_windows: list[TimeWindow]
    days_ahead: int
    dry_run: bool
    target_date: date

    @field_validator("players")
    @classmethod
    def players_range(cls, v: int) -> int:
        if not 1 <= v <= 4:
            raise ValueError("players must be between 1 and 4")
        return v


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def load_course(course_name: str | None = None) -> CourseConfig:
    data = _load_yaml(CONFIG_DIR / "courses.yaml")
    name = course_name or os.environ.get("COURSE", "marine_park")
    courses = data.get("courses", {})
    if name not in courses:
        available = list(courses.keys())
        raise ValueError(f"Course {name!r} not found. Available: {available}")
    return CourseConfig(**courses[name])


def load_prefs(
    course_override: str | None = None,
    date_override: str | None = None,
) -> BookingPrefs:
    data = _load_yaml(CONFIG_DIR / "booking_prefs.yaml")
    defaults = data.get("defaults", {})

    # Environment variable overrides
    course = course_override or os.environ.get("COURSE") or defaults.get("course", "marine_park")
    players = int(os.environ.get("PLAYERS") or defaults.get("players", 2))
    days_ahead = int(os.environ.get("DAYS_AHEAD") or defaults.get("days_ahead", 7))
    dry_run = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes") or defaults.get("dry_run", False)

    earliest = os.environ.get("EARLIEST_TIME") or defaults["preferred_window"]["earliest"]
    latest = os.environ.get("LATEST_TIME") or defaults["preferred_window"]["latest"]

    raw_date = date_override or os.environ.get("TARGET_DATE")
    if raw_date:
        target_date = date.fromisoformat(raw_date)
    else:
        target_date = date.today() + timedelta(days=days_ahead)

    fallbacks = [TimeWindow(**w) for w in defaults.get("fallback_windows", [])]

    return BookingPrefs(
        course=course,
        players=players,
        preferred_window=TimeWindow(earliest=earliest, latest=latest),
        fallback_windows=fallbacks,
        days_ahead=days_ahead,
        dry_run=dry_run,
        target_date=target_date,
    )
