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
    format_day_menu_discord,
    format_week_menu,
    format_week_menu_discord,
    get_day_menus_with_meta,
    get_day_menu_with_meta,
    remaining_weekdays,
)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')
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


def discord_webhook_send(payload: dict) -> None:
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError('Missing DISCORD_WEBHOOK_URL.')
    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=45)
    if response.status_code not in (200, 204):
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise RuntimeError(f'Discord webhook HTTP {response.status_code}: {detail}')


def build_fallback_message(hall_name: str, target_date: date, menu_url: str, reason: str) -> str:
    header = (
        f"🍽️ <b>{escape_html(hall_name)} — "
        f"{target_date.strftime('%A, %d %b %Y')}</b>\n"
        f"<a href=\"{menu_url}\">Open menu page</a>"
    )
    return header + f"\n\nAutomatic parsing failed ({escape_html(reason)}). Please check the official menu page."


def build_fallback_discord(hall_name: str, target_date: date, menu_url: str, reason: str) -> dict:
    return {
        "embeds": [{
            "title": f"🍽️ {hall_name} — {target_date.strftime('%A, %d %b %Y')}",
            "url": menu_url,
            "description": f"Automatic parsing failed ({reason}). Please check the [official menu page]({menu_url}).",
            "color": 0xF4830A,
        }]
    }


HOLIDAY_TITLES = {'festa', 'chiuso', 'geschlossen', 'feiertag', 'holiday', 'closed', 'ferie'}


def decide_message_type(cards: list, meta: ScrapeMeta) -> str:
    if cards:
        if all(card.title.lower() in HOLIDAY_TITLES for card in cards):
            return 'skip_holiday'
        return 'real'
    # If no section was found at all, treat as closed/holiday/no-menu day.
    if not meta.section_extracted:
        return 'skip_no_menu'
    # Section exists but yielded zero cards: parsing likely broke.
    return 'fallback_parse_failure'


def has_any_real_food(day_menus: list[DayMenu]) -> bool:
    return any(
        card.title.lower() not in HOLIDAY_TITLES
        for dm in day_menus
        for card in dm.cards
    )


def fetch_week_day_menus(today: date, today_menu: DayMenu, menu_url: str) -> list[DayMenu] | None:
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
                target_date=d.isoformat(),
                weekday_name=d.strftime('%A'),
                cards=[],
                raw_section=[],
            )
            for d in remaining_dates
        )
    return day_menus if has_any_real_food(day_menus) else None


def main() -> int:
    use_telegram = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
    use_discord = bool(DISCORD_WEBHOOK_URL)
    if not use_telegram and not use_discord:
        raise RuntimeError('Configure TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID and/or DISCORD_WEBHOOK_URL.')

    now_local = datetime.now(ZoneInfo(TIMEZONE))
    today = now_local.date()
    # GitHub scheduler typically fires 1–2 h late; accept a wide window so delays don't cause silent skips.
    SEND_HOUR_MAX = SEND_HOUR_LOCAL + 4
    if GITHUB_EVENT_NAME != 'workflow_dispatch' and not (SEND_HOUR_LOCAL <= now_local.hour < SEND_HOUR_MAX):
        print(
            f'Skip: local time is {now_local.strftime("%H:%M")} in {TIMEZONE}; '
            f'outside send window {SEND_HOUR_LOCAL:02d}:00–{SEND_HOUR_MAX:02d}:00.'
        )
        return 0

    # Menus are identical across campuses; send one combined message.
    campuses_display = " / ".join(get_display_name(key) for key in CAMPUS_INFO.keys())
    menu_url = get_menu_url(DEFAULT_CAMPUS)
    print(f"JOB target_date={today.isoformat()} menu_url={menu_url} telegram={use_telegram} discord={use_discord}")
    try:
        menu, meta = get_day_menu_with_retry(today, menu_url)
    except Exception as exc:
        print(f"OUTCOME type=fallback_fetch_error target_date={today.isoformat()} error={exc}")
        if use_telegram:
            telegram_api('sendMessage', json_body={
                'chat_id': str(TELEGRAM_CHAT_ID),
                'text': build_fallback_message(campuses_display, today, menu_url, reason='fetch_error'),
                'parse_mode': 'HTML',
                'disable_web_page_preview': True,
            })
        if use_discord:
            discord_webhook_send(build_fallback_discord(campuses_display, today, menu_url, reason='fetch_error'))
        return 0

    message_type = decide_message_type(menu.cards, meta)

    if message_type == 'skip_no_menu':
        print(f"OUTCOME type={message_type} target_date={today.isoformat()} parsed_cards={len(menu.cards)} section_extracted={meta.section_extracted}")
        return 0

    # Monday weekly preview — attempt even when Monday itself is a holiday.
    if today.weekday() == 0:
        if message_type == 'real':
            monday_for_preview = menu
        elif message_type == 'skip_holiday':
            # Represent closed Monday with empty cards so Tue–Fri can still be shown.
            monday_for_preview = DayMenu(
                target_date=menu.target_date,
                weekday_name=menu.weekday_name,
                cards=[],
                raw_section=[],
            )
        else:
            monday_for_preview = None

        if monday_for_preview is not None:
            week_day_menus = fetch_week_day_menus(today, monday_for_preview, menu_url)
            if week_day_menus:
                if use_telegram:
                    telegram_api('sendMessage', json_body={
                        'chat_id': str(TELEGRAM_CHAT_ID),
                        'text': format_week_menu(week_day_menus, menu_url),
                        'parse_mode': 'HTML',
                        'disable_web_page_preview': True,
                    })
                if use_discord:
                    discord_webhook_send(format_week_menu_discord(week_day_menus, menu_url))
                print(f"OUTCOME type=weekly_preview target_date={today.isoformat()}")
            else:
                print(f"OUTCOME type=skip_weekly_preview target_date={today.isoformat()}")

    if message_type == 'skip_holiday':
        print(f"OUTCOME type=skip_holiday target_date={today.isoformat()} parsed_cards={len(menu.cards)} section_extracted={meta.section_extracted}")
        return 0

    label = today.strftime('%A')
    if message_type == 'fallback_parse_failure':
        tg_text = build_fallback_message(campuses_display, today, menu_url, reason='parse_error')
        dc_payload = build_fallback_discord(campuses_display, today, menu_url, reason='parse_error')
    else:
        tg_text = format_day_menu(menu, label, menu_url, hall_name=campuses_display)
        dc_payload = format_day_menu_discord(menu, label, menu_url, hall_name=campuses_display)

    if use_telegram:
        telegram_api('sendMessage', json_body={
            'chat_id': str(TELEGRAM_CHAT_ID),
            'text': tg_text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True,
        })
    if use_discord:
        discord_webhook_send(dc_payload)
    print(f"OUTCOME type={message_type} target_date={today.isoformat()} parsed_cards={len(menu.cards)}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
