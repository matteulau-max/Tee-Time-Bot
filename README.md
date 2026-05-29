# Tee Time Bot

Automated golf tee time booking bot for EZLinks-powered courses (e.g. Marine Park Golf Course, Brooklyn NY). Runs on a schedule via GitHub Actions and sends email notifications on success or failure.

## How it works

1. GitHub Actions triggers the bot at midnight ET (when slots are released).
2. The bot opens a headless Chromium browser and navigates to the EZLinks booking page.
3. It searches for tee times 7 days out, finds the best slot in your preferred time window, completes the booking including login and payment, and emails you the confirmation number.

## Supported courses

| Key | Course | URL |
|-----|--------|-----|
| `marine_park` | Marine Park Golf Course, Brooklyn NY | [book](https://marineparkridepp.ezlinksgolf.com/index.html#!/search) |

Add new courses by editing `config/courses.yaml` — no core code changes needed.

## Setup

### 1. Clone and install

```bash
git clone https://github.com/matteulau-max/Tee-Time-Bot
cd Tee-Time-Bot
pip install -r requirements.txt
playwright install chromium --with-deps
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Test with a dry run

```bash
python -m booker.main --dry-run
```

This runs the full Playwright flow but stops before clicking "Book". It logs all available tee times and saves screenshots to `screenshots/`.

### 4. Set up a self-hosted runner

EZLinks blocks all cloud/datacenter IPs. GitHub's hosted runners use Azure IPs
and will be blocked too. A **self-hosted runner** runs the workflow on your own
machine using your home IP.

> Your machine needs to be on and the runner service running at midnight for the
> cron to fire. Put your laptop on a charger and disable sleep, or leave a
> desktop on overnight.

**One-time setup (≈5 minutes):**

1. Go to your GitHub repo → **Settings → Actions → Runners → New self-hosted runner**
2. Choose your OS (macOS or Linux) and follow the download/configure steps shown — they look like:
   ```bash
   mkdir actions-runner && cd actions-runner
   # Download the runner package (GitHub shows the exact URL for your OS)
   curl -o actions-runner.tar.gz -L https://github.com/actions/runner/releases/download/...
   tar xzf actions-runner.tar.gz
   ./config.sh --url https://github.com/matteulau-max/Tee-Time-Bot --token YOUR_TOKEN
   ```
3. Install it as a **background service** so it starts on boot and wakes for scheduled runs:
   ```bash
   # macOS / Linux
   sudo ./svc.sh install
   sudo ./svc.sh start
   ```
4. Verify it's online: GitHub repo → **Settings → Actions → Runners** — you should see your machine listed as **Idle**.

Add the following **Secrets** and **Variables** to your repository
(`Settings → Secrets and variables → Actions`):

#### Secrets (sensitive — never logged)

| Secret | Description |
|--------|-------------|
| `MARINE_PARK_EMAIL` | Your EZLinks account email |
| `MARINE_PARK_PASSWORD` | Your EZLinks account password |
| `PAYMENT_CARD_NUMBER` | Credit card number |
| `PAYMENT_CARD_EXPIRY` | Expiry date (MM/YY) |
| `PAYMENT_CARD_CVV` | CVV / security code |
| `PAYMENT_CARD_ZIP` | Billing ZIP code |
| `PAYMENT_CARD_NAME` | Cardholder name (must match EZLinks account) |
| `SMTP_FROM` | Gmail address used to send notifications |
| `SMTP_PASSWORD` | Gmail [App Password](https://myaccount.google.com/apppasswords) |

#### Variables (non-sensitive configuration)

| Variable | Default | Description |
|----------|---------|-------------|
| `COURSE` | `marine_park` | Course key |
| `PLAYERS` | `2` | Number of players |
| `EARLIEST_TIME` | `07:00` | Start of preferred window (24h) |
| `LATEST_TIME` | `09:00` | End of preferred window (24h) |
| `NOTIFY_EMAIL` | — | Email address to receive alerts |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server |
| `DRY_RUN` | `false` | Set `true` to disable actual booking |

### 6. Manual trigger

Go to **Actions → Book Tee Time → Run workflow** to trigger manually with optional overrides for course, date, and dry-run mode.

## Adding a new course

Edit `config/courses.yaml`:

```yaml
courses:
  my_new_course:
    name: "My Golf Course"
    booking_url: "https://mycourse.ezlinksgolf.com/index.html#!/search"
    requires_login: true
    credentials:
      email_env: "MY_COURSE_EMAIL"
      password_env: "MY_COURSE_PASSWORD"
    payment:
      card_number_env: "PAYMENT_CARD_NUMBER"
      card_expiry_env: "PAYMENT_CARD_EXPIRY"
      card_cvv_env: "PAYMENT_CARD_CVV"
      card_zip_env: "PAYMENT_CARD_ZIP"
      card_name_env: "PAYMENT_CARD_NAME"
    selectors: {}
```

If the new course uses non-standard HTML selectors, override them in `selectors: {}`. Available keys are documented in `booker/ezlinks_booker.py` → `DEFAULT_SELECTORS`.

## Anti-bot notes

| Signal | Mitigation |
|--------|-----------|
| 403 on plain HTTP | Playwright real browser bypasses header/TLS checks |
| `navigator.webdriver` | Patched by `playwright-stealth` |
| Rate limiting | Random 0.5–2s human-like delays between interactions |
| CAPTCHA | Bot detects reCAPTCHA/hCaptcha, halts, and sends failure email |
| Session timeout | Entire flow completes in < 5 minutes (well under 20-minute timeout) |

## Debugging

On failure, GitHub Actions uploads:
- `screenshots/` — PNG at each step of the flow
- `tee_bot.log` — full structured log

Run locally with `--headed` to watch the browser in real time:

```bash
python -m booker.main --headed --dry-run
```

## Project structure

```
booker/
  main.py             Entry point
  config_loader.py    Load courses.yaml + booking_prefs.yaml, pydantic models
  ezlinks_booker.py   Playwright automation for EZLinks SPAs
  notifier.py         SMTP email notifications
  logger.py           Logging setup
config/
  courses.yaml        Course registry
  booking_prefs.yaml  Default booking preferences
.github/workflows/
  book_tee_time.yml   Cron schedule + manual trigger
tests/
  test_config_loader.py  Unit tests
```
