"""
Playwright automation for EZLinks golf booking sites.

EZLinks is an AngularJS SPA. All selectors below are based on the standard
EZLinks booking UI. Override them per-course via courses.yaml → selectors.

ANTI-BOT NOTES:
  - EZLinks returns 403 to plain HTTP clients; Playwright's real browser bypasses this.
  - playwright-stealth patches navigator.webdriver and other headless signals.
  - Random delays simulate human interaction cadence.
  - CAPTCHA: if detected, the bot halts and sends a failure notification rather
    than silently completing a wrong booking.
"""

from __future__ import annotations

import asyncio
import os
import random
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

try:
    from playwright_stealth import stealth_async
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False

from .config_loader import BookingPrefs, CourseConfig
from .logger import get_logger
from .notifier import BookingResult

log = get_logger(__name__)

SCREENSHOTS_DIR = Path("screenshots")
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Default EZLinks selectors
# These match the standard EZLinks AngularJS booking UI.
# Override any key via courses.yaml → selectors.
# ---------------------------------------------------------------------------
DEFAULT_SELECTORS: dict[str, str] = {
    # Search form
    "date_input": "input[placeholder*='Date'], input[id*='date'], input[ng-model*='date']",
    "players_select": "select[ng-model*='player'], select[id*='player'], .player-select select",
    "search_button": "button[ng-click*='search'], button:has-text('Search'), button:has-text('Find Tee Times')",
    # Results
    "results_container": ".tee-times, .teetime-list, [ng-repeat*='teeTime'], .search-results",
    "tee_time_card": ".tee-time-item, .teetime-card, [ng-repeat*='teeTime'] > *, .time-slot",
    "time_label": ".tee-time, .time, [class*='time']",
    "book_button": "button:has-text('Book'), button:has-text('Reserve'), a:has-text('Book')",
    # Login modal
    "login_email": "input[type='email'], input[name='email'], input[placeholder*='Email']",
    "login_password": "input[type='password'], input[name='password']",
    "login_submit": "button[type='submit'], button:has-text('Log In'), button:has-text('Sign In')",
    # Payment form
    "payment_card_number": "input[name*='card'], input[placeholder*='Card Number'], input[id*='cardNumber']",
    "payment_expiry": "input[placeholder*='Expir'], input[name*='expir'], input[id*='expiry']",
    "payment_cvv": "input[placeholder*='CVV'], input[name*='cvv'], input[id*='cvv'], input[placeholder*='CVC']",
    "payment_zip": "input[placeholder*='Zip'], input[name*='zip'], input[id*='zip']",
    "payment_name": "input[placeholder*='Name'], input[name*='name'], input[id*='cardName']",
    "payment_submit": "button:has-text('Confirm'), button:has-text('Pay'), button:has-text('Complete')",
    # Confirmation
    "confirmation_number": ".confirmation-number, [class*='confirm'] .number, [id*='confirmation']",
    # CAPTCHA detection
    "captcha_iframe": "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[title*='reCAPTCHA']",
}


@dataclass
class TeeTimeSlot:
    time_str: str          # e.g. "7:14 AM"
    element_index: int
    price: str = ""
    available_players: int = 4


@dataclass
class EZLinksBooker:
    course: CourseConfig
    prefs: BookingPrefs
    headless: bool = True
    _selectors: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._selectors = {**DEFAULT_SELECTORS, **self.course.selectors}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def book(self) -> BookingResult:
        async with async_playwright() as pw:
            browser, context, page = await self._launch_browser(pw)
            try:
                return await self._run_booking_flow(page)
            except Exception as exc:
                log.exception("Unhandled error during booking flow")
                await self._screenshot(page, "error_unhandled")
                return BookingResult(
                    success=False,
                    course_name=self.course.name,
                    target_date=str(self.prefs.target_date),
                    error_message=str(exc),
                )
            finally:
                await browser.close()

    # ------------------------------------------------------------------
    # Browser setup
    # ------------------------------------------------------------------

    async def _launch_browser(
        self, pw: Playwright
    ) -> tuple[Browser, BrowserContext, Page]:
        log.info("Launching Chromium (headless=%s)", self.headless)
        browser = await pw.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/New_York",
        )
        page = await context.new_page()
        if STEALTH_AVAILABLE:
            await stealth_async(page)
            log.debug("playwright-stealth applied")
        else:
            log.warning(
                "playwright-stealth not installed; bot may be fingerprinted as headless"
            )
        return browser, context, page

    # ------------------------------------------------------------------
    # Booking flow
    # ------------------------------------------------------------------

    async def _run_booking_flow(self, page: Page) -> BookingResult:
        target_date = self.prefs.target_date
        log.info(
            "Starting booking flow: course=%s date=%s players=%d dry_run=%s",
            self.course.name,
            target_date,
            self.prefs.players,
            self.prefs.dry_run,
        )

        await self._navigate_to_search(page)
        await self._check_for_captcha(page)
        await self._set_search_params(page, target_date)
        await self._check_for_captcha(page)

        slots = await self._collect_tee_times(page)
        if not slots:
            return BookingResult(
                success=False,
                course_name=self.course.name,
                target_date=str(target_date),
                error_message="No tee times found for this date.",
            )

        log.info("Found %d tee time(s):", len(slots))
        for s in slots:
            log.info("  %s  %s  (%d players avail)", s.time_str, s.price, s.available_players)

        best = self._select_best_slot(slots)
        if best is None:
            available_strs = [s.time_str for s in slots]
            log.warning("No slot matches preferences. Available: %s", available_strs)
            return BookingResult(
                success=False,
                course_name=self.course.name,
                target_date=str(target_date),
                error_message="No available slot in preferred or fallback windows.",
                available_times=available_strs,
            )

        log.info("Selected slot: %s", best.time_str)

        if self.prefs.dry_run:
            log.info("DRY RUN — not completing booking.")
            return BookingResult(
                success=True,
                course_name=self.course.name,
                target_date=str(target_date),
                booked_time=best.time_str,
                players=self.prefs.players,
                confirmation_number="DRY-RUN",
                available_times=[s.time_str for s in slots],
            )

        await self._click_book_button(page, best)
        await self._check_for_captcha(page)
        await self._handle_login(page)
        await self._handle_payment(page)
        confirmation = await self._capture_confirmation(page)

        return BookingResult(
            success=True,
            course_name=self.course.name,
            target_date=str(target_date),
            booked_time=best.time_str,
            players=self.prefs.players,
            confirmation_number=confirmation,
            available_times=[s.time_str for s in slots],
        )

    # ------------------------------------------------------------------
    # Step 1: Navigate
    # ------------------------------------------------------------------

    async def _navigate_to_search(self, page: Page) -> None:
        log.info("Navigating to %s", self.course.booking_url)
        await page.goto(self.course.booking_url, wait_until="domcontentloaded", timeout=30_000)
        # AngularJS apps need extra time to bootstrap
        await self._human_delay(2, 4)
        await self._screenshot(page, "01_loaded")
        log.debug("Page loaded: %s", page.url)

    # ------------------------------------------------------------------
    # Step 2: Set search parameters
    # ------------------------------------------------------------------

    async def _set_search_params(self, page: Page, target_date: date) -> None:
        date_str = target_date.strftime("%-m/%-d/%Y")  # e.g. "6/4/2026"
        log.info("Setting date=%s players=%d", date_str, self.prefs.players)

        await self._fill_date(page, date_str)
        await self._human_delay(0.5, 1.5)
        await self._set_players(page)
        await self._human_delay(0.5, 1.5)
        await self._click_search(page)
        await self._human_delay(1, 2)
        await self._screenshot(page, "02_search_submitted")

    async def _fill_date(self, page: Page, date_str: str) -> None:
        sel = self._selectors["date_input"]
        try:
            date_el = page.locator(sel).first
            await date_el.wait_for(state="visible", timeout=10_000)
            await date_el.triple_click()
            await self._human_delay(0.2, 0.5)
            await date_el.fill(date_str)
            log.debug("Date field filled: %s", date_str)
        except PlaywrightTimeoutError:
            log.warning("Date input not found with selector %r, trying click-on-calendar approach", sel)
            await self._screenshot(page, "date_field_not_found")
            raise RuntimeError(
                f"Could not find date input field. Selector: {sel!r}. "
                "Check screenshots and update courses.yaml → selectors → date_input."
            )

    async def _set_players(self, page: Page) -> None:
        sel = self._selectors["players_select"]
        try:
            select_el = page.locator(sel).first
            await select_el.wait_for(state="visible", timeout=8_000)
            await select_el.select_option(str(self.prefs.players))
            log.debug("Players set to %d", self.prefs.players)
        except PlaywrightTimeoutError:
            log.warning("Players dropdown not found (%r), skipping", sel)

    async def _click_search(self, page: Page) -> None:
        sel = self._selectors["search_button"]
        btn = page.locator(sel).first
        await btn.wait_for(state="visible", timeout=8_000)
        await btn.click()
        log.debug("Search button clicked")

    # ------------------------------------------------------------------
    # Step 3: Collect results
    # ------------------------------------------------------------------

    async def _collect_tee_times(self, page: Page) -> list[TeeTimeSlot]:
        log.info("Waiting for tee time results…")
        results_sel = self._selectors["results_container"]
        card_sel = self._selectors["tee_time_card"]

        try:
            await page.wait_for_selector(results_sel, state="visible", timeout=20_000)
        except PlaywrightTimeoutError:
            log.warning("Results container not found (%r)", results_sel)
            await self._screenshot(page, "03_no_results")
            # Try the card selector directly as a fallback
            try:
                await page.wait_for_selector(card_sel, state="visible", timeout=10_000)
            except PlaywrightTimeoutError:
                return []

        await self._screenshot(page, "03_results")
        await self._human_delay(0.5, 1.0)

        cards = await page.locator(card_sel).all()
        slots: list[TeeTimeSlot] = []

        for idx, card in enumerate(cards):
            try:
                time_el = card.locator(self._selectors["time_label"]).first
                time_str = (await time_el.inner_text()).strip()
                if not time_str:
                    continue

                price_str = ""
                try:
                    price_el = card.locator("[class*='price'], .rate, .cost").first
                    price_str = (await price_el.inner_text()).strip()
                except Exception:
                    pass

                slots.append(TeeTimeSlot(time_str=time_str, element_index=idx, price=price_str))
            except Exception as exc:
                log.debug("Skipping card %d: %s", idx, exc)

        return slots

    # ------------------------------------------------------------------
    # Step 4: Select best slot
    # ------------------------------------------------------------------

    def _select_best_slot(self, slots: list[TeeTimeSlot]) -> TeeTimeSlot | None:
        windows = [self.prefs.preferred_window] + list(self.prefs.fallback_windows)
        for window in windows:
            candidates = [s for s in slots if self._in_window(s.time_str, window.earliest, window.latest)]
            if candidates:
                log.debug(
                    "Window %s-%s: %d candidate(s)", window.earliest, window.latest, len(candidates)
                )
                return candidates[0]
        return None

    @staticmethod
    def _in_window(time_str: str, earliest: str, latest: str) -> bool:
        try:
            slot_minutes = _parse_time_to_minutes(time_str)
            early_minutes = _parse_time_to_minutes(earliest)
            late_minutes = _parse_time_to_minutes(latest)
            return early_minutes <= slot_minutes <= late_minutes
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Step 5: Click Book
    # ------------------------------------------------------------------

    async def _click_book_button(self, page: Page, slot: TeeTimeSlot) -> None:
        card_sel = self._selectors["tee_time_card"]
        book_sel = self._selectors["book_button"]

        cards = await page.locator(card_sel).all()
        if slot.element_index >= len(cards):
            raise RuntimeError(f"Card index {slot.element_index} out of range ({len(cards)} cards)")

        target_card = cards[slot.element_index]
        book_btn = target_card.locator(book_sel).first
        await book_btn.wait_for(state="visible", timeout=8_000)
        await self._human_delay(0.5, 1.5)
        await book_btn.click()
        log.info("Clicked book for slot: %s", slot.time_str)
        await self._human_delay(1, 3)
        await self._screenshot(page, "04_after_book_click")

    # ------------------------------------------------------------------
    # Step 6: Login
    # ------------------------------------------------------------------

    async def _handle_login(self, page: Page) -> None:
        if not self.course.requires_login or not self.course.credentials:
            return

        email, password = self.course.credentials.resolve()
        if not email or not password:
            log.warning("Login credentials not set in environment — skipping login step")
            return

        email_sel = self._selectors["login_email"]
        try:
            await page.wait_for_selector(email_sel, state="visible", timeout=8_000)
        except PlaywrightTimeoutError:
            log.debug("No login form visible, assuming already authenticated")
            return

        log.info("Login form detected, entering credentials")
        await self._screenshot(page, "05_login_form")

        email_field = page.locator(email_sel).first
        await email_field.fill(email)
        await self._human_delay(0.3, 0.8)

        pw_field = page.locator(self._selectors["login_password"]).first
        await pw_field.fill(password)
        await self._human_delay(0.3, 0.8)

        submit = page.locator(self._selectors["login_submit"]).first
        await submit.click()
        log.info("Login submitted")
        await self._human_delay(2, 4)
        await self._screenshot(page, "06_after_login")

    # ------------------------------------------------------------------
    # Step 7: Payment
    # ------------------------------------------------------------------

    async def _handle_payment(self, page: Page) -> None:
        if not self.course.payment:
            log.debug("No payment config — skipping payment step")
            return

        card_num_sel = self._selectors["payment_card_number"]
        try:
            await page.wait_for_selector(card_num_sel, state="visible", timeout=10_000)
        except PlaywrightTimeoutError:
            log.debug("No payment form visible, skipping")
            return

        log.info("Payment form detected, entering card details")
        await self._screenshot(page, "07_payment_form")
        payment = self.course.payment.resolve()

        async def fill(sel_key: str, value: str) -> None:
            if not value:
                log.warning("Payment field %r is empty — check secrets", sel_key)
                return
            field = page.locator(self._selectors[sel_key]).first
            await field.wait_for(state="visible", timeout=5_000)
            await field.fill(value)
            await self._human_delay(0.2, 0.5)

        await fill("payment_card_number", payment["card_number"])
        await fill("payment_expiry", payment["card_expiry"])
        await fill("payment_cvv", payment["card_cvv"])
        await fill("payment_zip", payment["card_zip"])
        await fill("payment_name", payment["card_name"])

        await self._screenshot(page, "08_payment_filled")
        submit = page.locator(self._selectors["payment_submit"]).first
        await submit.wait_for(state="visible", timeout=5_000)
        await self._human_delay(0.5, 1.5)
        await submit.click()
        log.info("Payment submitted")
        await self._human_delay(3, 6)
        await self._screenshot(page, "09_after_payment")

    # ------------------------------------------------------------------
    # Step 8: Capture confirmation
    # ------------------------------------------------------------------

    async def _capture_confirmation(self, page: Page) -> str | None:
        confirm_sel = self._selectors["confirmation_number"]
        try:
            el = await page.wait_for_selector(confirm_sel, timeout=15_000)
            if el:
                text = (await el.inner_text()).strip()
                log.info("Confirmation number: %s", text)
                await self._screenshot(page, "10_confirmation")
                return text
        except PlaywrightTimeoutError:
            log.warning("Confirmation number element not found (%r)", confirm_sel)
            # Try extracting from page title or any visible text containing digits
            try:
                body_text = await page.inner_text("body")
                for line in body_text.splitlines():
                    if "confirmation" in line.lower() and any(c.isdigit() for c in line):
                        log.info("Extracted confirmation from body: %s", line.strip())
                        return line.strip()
            except Exception:
                pass
        await self._screenshot(page, "10_confirmation_not_found")
        return None

    # ------------------------------------------------------------------
    # Anti-bot: CAPTCHA detection
    # ------------------------------------------------------------------

    async def _check_for_captcha(self, page: Page) -> None:
        captcha_sel = self._selectors["captcha_iframe"]
        try:
            el = page.locator(captcha_sel).first
            if await el.is_visible():
                await self._screenshot(page, "captcha_detected")
                raise RuntimeError(
                    "CAPTCHA detected on the page. Manual intervention required. "
                    "See screenshots/captcha_detected.png"
                )
        except PlaywrightTimeoutError:
            pass
        except RuntimeError:
            raise
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    async def _screenshot(self, page: Page, label: str) -> None:
        path = SCREENSHOTS_DIR / f"{label}.png"
        try:
            await page.screenshot(path=str(path), full_page=False)
            log.debug("Screenshot saved: %s", path)
        except Exception as exc:
            log.debug("Could not save screenshot %s: %s", path, exc)

    @staticmethod
    async def _human_delay(min_s: float = 0.5, max_s: float = 2.0) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))


# ---------------------------------------------------------------------------
# Time parsing helpers
# ---------------------------------------------------------------------------

def _parse_time_to_minutes(time_str: str) -> int:
    """
    Parse a time string to total minutes from midnight.
    Accepts: "7:14 AM", "07:14", "07:14:00", "7:14AM"
    """
    t = time_str.strip().upper().replace(" ", "")
    is_pm = t.endswith("PM")
    is_am = t.endswith("AM")
    t = t.replace("AM", "").replace("PM", "")
    parts = t.split(":")
    hours = int(parts[0])
    minutes = int(parts[1]) if len(parts) > 1 else 0
    if is_pm and hours != 12:
        hours += 12
    if is_am and hours == 12:
        hours = 0
    return hours * 60 + minutes
