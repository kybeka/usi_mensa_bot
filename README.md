<p align="center">
  <img src="img/tg_bot_pfp.png" alt="USI Mensa Telegram Bot avatar" width="200">
</p>

# USI Mensa Telegram Bot

[![Send channel menu](https://github.com/kybeka/usi_campus_menu_bot/actions/workflows/send-channel.yml/badge.svg)](https://github.com/kybeka/usi_campus_menu_bot/actions/workflows/send-channel.yml)
![Python](https://img.shields.io/badge/python-3.12-blue)
![Playwright](https://img.shields.io/badge/scraper-Playwright-2ea44f)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
[![Telegram](https://img.shields.io/badge/Telegram-@usi__mensa-26A5E4?logo=telegram&logoColor=white)](https://t.me/usi_mensa)
![Vibe-coded](https://img.shields.io/badge/vibe--coded-yes-ff69b4)

Posts the daily USI mensa menu to [@usi_mensa](https://t.me/usi_mensa) around 10:00 Europe/Zurich.

Menus are currently treated as identical across the configured campuses, so the bot sends one combined channel message. On Mondays, it first sends a week-at-a-glance preview for Monday-Friday, marked as tentative because the official menu may still change.

## Disclaimer

This project is unofficial and was created independently. It is not affiliated with, endorsed by, or maintained by the Universita della Svizzera italiana (USI), SV Group, or the USI mensa.

## Features

- Scrapes the official SV Gastronomie menu page with Playwright.
- Posts Telegram messages using Telegram Bot API HTML formatting.
- Sends a daily combined menu for all configured campuses.
- Sends a Monday-only weekly preview before the daily message.
- Clicks the real date tabs when collecting weekly menus.
- Uses retry/backoff for transient scrape failures.
- Falls back to the official menu link when scraping breaks.
- Runs from GitHub Actions on a weekday schedule, with a Zurich local-time gate for DST safety.

## Example Weekly Preview

```text
USI Mensa - Week at a glance
Tentative, may change. Check the official menu page for updates.

Day        Menu
---------- ------------------------------------------------------------
Mon 20 Apr Autentico: Cordon bleu di pollo; Pasta: Penne alla norma; Giardino: Piccata di tofu alla crema di datterini
Tue 21 Apr Autentico: Spezzatino di maiale ai funghi; Pasta: Fusilli chiles en nogada; Giardino: Tomino del boscaiolo
Wed 22 Apr Autentico: Polpette di carne al pomodoro; Pasta: Pasta con seppioline; Giardino: Frittata pomodorini e mozzarella
```

## How It Works

- [channel_job.py](channel_job.py) is the scheduled entry point. It checks runtime gates, fetches menu data, builds Telegram messages, and sends them.
- [menu_fetcher.py](menu_fetcher.py) handles Playwright page loading, day-tab clicks, text extraction, parsing, and message formatting.
- [campus.py](campus.py) stores the campus display names and menu URLs.
- [.github/workflows/send-channel.yml](.github/workflows/send-channel.yml) installs dependencies, installs Chromium, and runs the job.

## Schedule

The workflow starts at `08:00` and `09:00` UTC on weekdays. Those two cron entries cover 10:00 in Europe/Zurich across daylight saving time changes.

Only one scheduled run sends a message: [channel_job.py](channel_job.py) checks the actual Zurich local hour and exits early unless it is the configured send hour. Manual `workflow_dispatch` runs bypass that time gate for testing.

## Message Behavior

Daily message:
- Sent Monday-Friday when the current day menu parses successfully.
- Contains all parsed menu cards and prices.
- Uses one combined campus heading.

Monday weekly preview:
- Sent only on Mondays.
- Sent before the normal daily message.
- Sent only if Monday's normal daily menu parsed successfully.
- Reuses Monday's already-fetched menu.
- Scrapes Tuesday-Friday in one shared browser session by clicking each date tab.
- Formats the preview as a Telegram-safe monospace table.
- Includes a "Tentative, may change" note.

Failure handling:
- No day section found: skip, likely closed/holiday/no menu published.
- Day section found but zero parsed cards: send a fallback status message with the official link.
- Scrape/fetch error after retries: send a fallback status message with the official link.
- Weekly preview batch issue: log the error and continue to the normal daily message.

## Local Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... TIMEZONE=Europe/Zurich \
python channel_job.py
```

Manual local runs will send to Telegram if valid secrets are present.

## Deployment

1. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as GitHub Actions secrets.
2. Make sure the Telegram bot is allowed to post in the target channel.
3. Keep the workflow enabled.
4. Use `workflow_dispatch` for a manual smoke test when needed.

The workflow has a `15` minute timeout so a stuck browser install or scrape cannot run indefinitely.

## Updating Campuses

Edit [campus.py](campus.py) to add, remove, or rename campuses. The current channel message assumes menus are identical across campuses and scrapes only `DEFAULT_CAMPUS`.

## Public Repo Notes

- Do not commit `.env`; it is ignored.
- Keep Telegram credentials in GitHub Actions secrets.
- The scraper depends on the live SV Gastronomie website. If the site changes its tab markup or text structure, the scrape may need adjustment.

## License

MIT License. See [LICENSE](LICENSE).
