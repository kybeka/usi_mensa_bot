import os
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from campus import CAMPUS_INFO, get_menu_url, get_display_name, DEFAULT_CAMPUS
from menu_fetcher import (
    DayMenu,
    ScrapeMeta,
    escape_html,
    format_day_menu,
    format_week_menu,
    get_day_menus_with_meta,
    get_day_menu_with_meta,
    has_any_menu_cards,
    remaining_weekdays,
)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
TIMEZONE = os.getenv('TIMEZONE', 'Europe/Zurich')
GITHUB_EVENT_NAME = os.getenv('GITHUB_EVENT_NAME', '').strip()
SEND_HOUR_LOCAL = int(os.getenv('SEND_HOUR_LOCAL', '10'))
FETCH_RETRIES = int(os.getenv('FETCH_RETRIES', '3'))
FETCH_RETRY_BACKOFF_SECONDS = float(os.getenv('FETCH_RETRY_BACKOFF_SECONDS', '3'))


def telegram_api(method: str, *, params=None, json_body=None):
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError('Missing TELEGRAM_BOT_TOKEN.')
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}'
    response = requests.post(url, params=params, json=json_body, timeout=45)
    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise RuntimeError(f'Telegram API HTTP {response.status_code} for {method}: {detail}')
    data = response.json()
    if not data.get('ok'):
        raise RuntimeError(f'Telegram API error for {method}: {data}')
    return data['result']


def get_day_menu_with_retry(target_date: date, menu_url: str):
    last_exc = None
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            return get_day_menu_with_meta(target_date, menu_url)
        except Exception as exc:
            last_exc = exc
            if attempt == FETCH_RETRIES:
                break
            wait_s = FETCH_RETRY_BACKOFF_SECONDS * attempt
            print(f'WARN: Menu fetch attempt {attempt}/{FETCH_RETRIES} failed: {exc}. Retrying in {wait_s:.1f}s...')
            time.sleep(wait_s)
    raise RuntimeError(f'Failed to fetch menu after {FETCH_RETRIES} attempts: {last_exc}')


def build_fallback_message(hall_name: str, target_date: date, menu_url: str, reason: str) -> str:
    header = (
        f"🍽️ <b>{escape_html(hall_name)} — "
        f"{target_date.strftime('%A, %d %b %Y')}</b>\n"
        f"<a href=\"{menu_url}\">Open menu page</a>"
    )
    return header + f"\n\nAutomatic parsing failed ({escape_html(reason)}). Please check the official menu page."


def decide_message_type(menu_cards_count: int, meta: ScrapeMeta) -> str:
    if menu_cards_count > 0:
        return 'real'
    # If no section was found at all, treat as closed/holiday/no-menu day.
    if not meta.section_extracted:
        return 'skip_no_menu'
    # Section exists but yielded zero cards: parsing likely broke.
    return 'fallback_parse_failure'


def build_week_preview_message(today: date, today_menu: DayMenu, menu_url: str) -> str | None:
    week_dates = remaining_weekdays(today)
    if not week_dates:
        return None

    day_menus = [today_menu]
    remaining_dates = week_dates[1:]
    try:
        day_menus.extend(day_menu for day_menu, _meta in get_day_menus_with_meta(remaining_dates, menu_url))
    except Exception as exc:
        print(f"WARN: Could not fetch weekly preview batch error={exc}")
        day_menus.extend(
            DayMenu(
                target_date=target_date.isoformat(),
                weekday_name=target_date.strftime('%A'),
                cards=[],
                raw_section=[],
            )
            for target_date in remaining_dates
        )

    if not has_any_menu_cards(day_menus):
        return None
    return format_week_menu(day_menus, menu_url)


def main() -> int:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError('TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required for channel job.')

    now_local = datetime.now(ZoneInfo(TIMEZONE))
    today = now_local.date()
    # Keep two UTC cron triggers for DST, but send only in the configured local hour.
    if GITHUB_EVENT_NAME != 'workflow_dispatch' and now_local.hour != SEND_HOUR_LOCAL:
        print(
            f'Skip: local time is {now_local.strftime("%H:%M")} in {TIMEZONE}; '
            f'waiting for {SEND_HOUR_LOCAL:02d}:00 window.'
        )
        return 0

    # Menus are identical across campuses; send one combined message.
    campuses_display = " / ".join(get_display_name(key) for key in CAMPUS_INFO.keys())
    menu_url = get_menu_url(DEFAULT_CAMPUS)
    print(f"JOB target_date={today.isoformat()} menu_url={menu_url}")
    try:
        menu, meta = get_day_menu_with_retry(today, menu_url)
    except Exception as exc:
        text = build_fallback_message(campuses_display, today, menu_url, reason='fetch_error')
        print(f"OUTCOME type=fallback_fetch_error target_date={today.isoformat()} error={exc}")
        telegram_api('sendMessage', json_body={
            'chat_id': str(TELEGRAM_CHAT_ID),
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True,
        })
        return 0

    message_type = decide_message_type(len(menu.cards), meta)
    if message_type == 'skip_no_menu':
        print(f"OUTCOME type=skip_no_menu target_date={today.isoformat()} parsed_cards={len(menu.cards)} section_extracted={meta.section_extracted}")
        return 0

    if message_type == 'fallback_parse_failure':
        text = build_fallback_message(campuses_display, today, menu_url, reason='parse_error')
    else:
        label = today.strftime('%A')
        text = format_day_menu(menu, label, menu_url, hall_name=campuses_display)

    if today.weekday() == 0 and message_type == 'real':
        weekly_text = build_week_preview_message(today, menu, menu_url)
        if weekly_text:
            telegram_api('sendMessage', json_body={
                'chat_id': str(TELEGRAM_CHAT_ID),
                'text': weekly_text,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True,
            })
            print(f"OUTCOME type=weekly_preview target_date={today.isoformat()}")
        else:
            print(f"OUTCOME type=skip_weekly_preview target_date={today.isoformat()}")

    telegram_api('sendMessage', json_body={
        'chat_id': str(TELEGRAM_CHAT_ID),
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    })
    print(f"OUTCOME type={message_type} target_date={today.isoformat()} parsed_cards={len(menu.cards)}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
