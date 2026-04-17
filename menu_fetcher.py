import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

DEFAULT_MENU_URL = os.getenv(
    'MENU_URL',
    'https://sv-gastronomie.ch/menu/polo%20universitario,%20campus%20est,%20Lugano/Mittagsmen%C3%BC'
)
TIMEZONE = os.getenv('TIMEZONE', 'Europe/Zurich')
HEADLESS = os.getenv('HEADLESS', 'true').lower() != 'false'
DEBUG_SCREENSHOT = os.getenv('DEBUG_SCREENSHOT', 'false').lower() == 'true'

PRICE_RE = re.compile(r'^(EXT|STUD|DOZ)\s+CHF\s+([\d.,]+)$', re.I)
WEEKDAY_LINE_RE = re.compile(r'^(Mo|Di|Mi|Do|Fr|Sa|So|Mon|Tue|Wed|Thu|Fri|Sat|Sun)\.?$', re.I)
DATE_LINE_RE = re.compile(r'^\d{1,2}\.\d{2}\.?$')
STOP_RE = re.compile(
    r'^(filter_list|filter|catering|informationen|öffnungszeiten|gastgeber|'
    r'datenschutzerklärung|impressum|mittagsmenü \||cookie-einstellungen)$',
    re.I,
)
TOP_SKIP = {
    'sv restaurant', 'menüplan', 'menu del pranzo', 'diese woche', 'standorte',
    'informationen', 'account_circle', 'de', 'it', 'en', 'fr', 'clear'
}
WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
WEEKDAY_DISPLAY = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
CATEGORY_EMOJI = {
    'pasta': '🍝',
    'giardino': '🌱',
    'vegetar': '🌱',
    'vegan': '🌱',
    'autentico': '🍽️',
    'grill': '🔥',
    'pizza': '🍕',
    'street': '🌮',
    'asia': '🥢',
}


@dataclass
class MenuCard:
    category: str
    title: str
    description: str
    student_price: str = ''
    staff_price: str = ''
    external_price: str = ''


@dataclass
class DayMenu:
    target_date: str
    weekday_name: str
    cards: list[MenuCard]
    raw_section: list[str]


@dataclass
class ScrapeMeta:
    target_date: str
    menu_url: str
    page_title: str
    tab_click_succeeded: bool
    section_extracted: bool
    section_line_count: int
    parsed_cards_count: int


def escape_html(text: str) -> str:
    return (
        text.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


def clean_line(line: str) -> str:
    return re.sub(r'\s+', ' ', line).strip(' •·-|')


def normalize_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace('\r', '\n').splitlines():
        line = clean_line(raw)
        if line:
            lines.append(line)
    return lines


def target_label(d: date) -> str:
    return f'{d.day:02d}.{d.month:02d}.'


def click_day(page, d: date) -> bool:
    label = target_label(d)
    date_href = f'/date/{d.isoformat()}'
    tab = page.locator(f'a[role="tab"][href$="{date_href}"]')
    if tab.count():
        try:
            tab.first.click(timeout=3000)
            page.wait_for_timeout(1800)
            return True
        except Exception:
            pass

    weekday = WEEKDAY_LABELS[d.weekday()]
    candidates = [
        f'{weekday}. {label}',
        f'{weekday} {label}',
        label,
        weekday + '.',
        weekday,
    ]
    clicked = False
    for candidate in candidates:
        loc = page.get_by_text(candidate, exact=True)
        count = loc.count()
        if count:
            try:
                loc.first.click(timeout=2000)
                page.wait_for_timeout(1200)
                clicked = True
                break
            except Exception:
                pass
    if not clicked:
        print(f'WARN: Could not click tab for {label}; using current visible page content.', file=sys.stderr)
    return clicked


def accept_cookie_banner(page) -> None:
    for label in ['Accept', 'I agree', 'OK', 'Ok', 'Akzeptieren', 'Accetta', 'Alle akzeptieren']:
        try:
            button = page.get_by_role('button', name=label)
            if button.count() > 0:
                button.first.click(timeout=1000)
                page.wait_for_timeout(500)
                break
        except Exception:
            pass


def prepare_menu_page(page, menu_url: str) -> None:
    page.goto(menu_url, wait_until='domcontentloaded', timeout=90000)
    page.wait_for_timeout(4000)
    accept_cookie_banner(page)
    try:
        page.wait_for_load_state('networkidle', timeout=12000)
    except PlaywrightTimeoutError:
        pass


def fetch_body_text_from_loaded_page(page, d: date) -> tuple[str, str, bool]:
    tab_click_succeeded = click_day(page, d)
    title = page.title()
    if DEBUG_SCREENSHOT:
        page.screenshot(path=f'debug-{target_label(d).replace(".", "_")}.png', full_page=True)
    body_text = page.locator('body').inner_text(timeout=15000)
    return title, body_text, tab_click_succeeded


def fetch_body_text_for_date(d: date, menu_url: str = DEFAULT_MENU_URL) -> tuple[str, str, bool]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page(locale='it-CH', timezone_id=TIMEZONE)
        try:
            prepare_menu_page(page, menu_url)
            return fetch_body_text_from_loaded_page(page, d)
        finally:
            browser.close()


def extract_day_section(lines: list[str], d: date) -> list[str]:
    label = target_label(d)
    idxs = [i for i, line in enumerate(lines) if line == label]
    if not idxs:
        return []
    start = idxs[-1] + 1
    while start < len(lines):
        line = lines[start]
        if WEEKDAY_LINE_RE.match(line) or DATE_LINE_RE.match(line) or line.lower() in TOP_SKIP:
            start += 1
            continue
        break
    section: list[str] = []
    for line in lines[start:]:
        if STOP_RE.match(line):
            break
        section.append(line)
    return section


def parse_cards_from_section(section: list[str]) -> list[MenuCard]:
    if not section:
        return []
    groups: list[dict] = []
    current = {'lines': [], 'prices': {}}
    for line in section:
        match = PRICE_RE.match(line)
        if match:
            current['prices'][match.group(1).upper()] = match.group(2)
            continue
        if current['prices'] and current['lines']:
            groups.append(current)
            current = {'lines': [line], 'prices': {}}
        else:
            current['lines'].append(line)
    if current['lines']:
        groups.append(current)

    cards: list[MenuCard] = []
    for group in groups:
        lines = group['lines']
        if not lines:
            continue
        category = lines[0]
        title = lines[1] if len(lines) > 1 else lines[0]
        description = ' '.join(lines[2:]).strip() if len(lines) > 2 else ''
        prices = group['prices']
        cards.append(
            MenuCard(
                category=category,
                title=title,
                description=description,
                student_price=prices.get('STUD', ''),
                staff_price=prices.get('DOZ', ''),
                external_price=prices.get('EXT', ''),
            )
        )
    return cards


def parse_day_menu_from_text(
    d: date,
    menu_url: str,
    title: str,
    body_text: str,
    tab_click_succeeded: bool,
) -> tuple[DayMenu, ScrapeMeta]:
    lines = normalize_lines(body_text)
    section = extract_day_section(lines, d)
    cards = parse_cards_from_section(section)
    day_menu = DayMenu(
        target_date=d.isoformat(),
        weekday_name=WEEKDAY_DISPLAY[d.weekday()],
        cards=cards,
        raw_section=section,
    )
    meta = ScrapeMeta(
        target_date=d.isoformat(),
        menu_url=menu_url,
        page_title=title,
        tab_click_succeeded=tab_click_succeeded,
        section_extracted=bool(section),
        section_line_count=len(section),
        parsed_cards_count=len(cards),
    )
    return day_menu, meta


def log_scrape_result(meta: ScrapeMeta) -> None:
    print(
        "SCRAPE_RESULT "
        f"target_date={meta.target_date} "
        f"tab_click_succeeded={meta.tab_click_succeeded} "
        f"section_extracted={meta.section_extracted} "
        f"section_lines={meta.section_line_count} "
        f"parsed_cards={meta.parsed_cards_count} "
        f"title={meta.page_title!r}"
    )


def empty_day_menu_with_meta(d: date, menu_url: str, page_title: str = '') -> tuple[DayMenu, ScrapeMeta]:
    day_menu = DayMenu(
        target_date=d.isoformat(),
        weekday_name=WEEKDAY_DISPLAY[d.weekday()],
        cards=[],
        raw_section=[],
    )
    meta = ScrapeMeta(
        target_date=d.isoformat(),
        menu_url=menu_url,
        page_title=page_title,
        tab_click_succeeded=False,
        section_extracted=False,
        section_line_count=0,
        parsed_cards_count=0,
    )
    return day_menu, meta


def get_day_menu_with_meta(d: date, menu_url: str = DEFAULT_MENU_URL) -> tuple[DayMenu, ScrapeMeta]:
    # Scrape pipeline: fetch page text -> isolate day section -> parse cards.
    print(f"SCRAPE target_date={d.isoformat()} menu_url={menu_url}")
    title, body_text, tab_click_succeeded = fetch_body_text_for_date(d, menu_url)
    day_menu, meta = parse_day_menu_from_text(d, menu_url, title, body_text, tab_click_succeeded)
    log_scrape_result(meta)
    return day_menu, meta


def get_day_menus_with_meta(dates: list[date], menu_url: str = DEFAULT_MENU_URL) -> list[tuple[DayMenu, ScrapeMeta]]:
    if not dates:
        return []

    print(f"SCRAPE_BATCH target_dates={','.join(d.isoformat() for d in dates)} menu_url={menu_url}")
    results: list[tuple[DayMenu, ScrapeMeta]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page(locale='it-CH', timezone_id=TIMEZONE)
        try:
            prepare_menu_page(page, menu_url)
            for d in dates:
                print(f"SCRAPE target_date={d.isoformat()} menu_url={menu_url}")
                try:
                    title, body_text, tab_click_succeeded = fetch_body_text_from_loaded_page(page, d)
                    day_menu, meta = parse_day_menu_from_text(
                        d,
                        menu_url,
                        title,
                        body_text,
                        tab_click_succeeded,
                    )
                except Exception as exc:
                    print(f"WARN: Could not scrape date={d.isoformat()} in batch: {exc}")
                    try:
                        title = page.title()
                    except Exception:
                        title = ''
                    day_menu, meta = empty_day_menu_with_meta(d, menu_url, title)
                log_scrape_result(meta)
                results.append((day_menu, meta))
            return results
        finally:
            browser.close()


def get_day_menu(d: date, menu_url: str = DEFAULT_MENU_URL) -> DayMenu:
    day_menu, _meta = get_day_menu_with_meta(d, menu_url)
    return day_menu


def category_emoji(name: str) -> str:
    low = name.lower()
    for key, emoji in CATEGORY_EMOJI.items():
        if key in low:
            return emoji
    return '🍽️'


def format_card(card: MenuCard) -> str:
    parts = [f"{category_emoji(card.category)} <b>{escape_html(card.category)}</b>"]
    if card.title:
        parts.append(escape_html(card.title))
    if card.description:
        parts.append(f"<i>{escape_html(card.description)}</i>")
    prices = []
    if card.student_price:
        prices.append(f"Student CHF {escape_html(card.student_price)}")
    if card.staff_price:
        prices.append(f"Staff CHF {escape_html(card.staff_price)}")
    if card.external_price:
        prices.append(f"External CHF {escape_html(card.external_price)}")
    if prices:
        parts.append('💸 ' + ' • '.join(prices))
    return '\n'.join(parts)


def format_day_menu(day_menu: DayMenu, label: str, menu_url: str = DEFAULT_MENU_URL, hall_name: str = "USI Mensa") -> str:
    full_label = label
    try:
        d = date.fromisoformat(day_menu.target_date)
        full_label = f"{label}, {d.day} {d.strftime('%b %Y')}"
    except Exception:
        pass
    header = f"🍽️ <b>{escape_html(hall_name)} — {escape_html(full_label)}</b>\n<a href=\"{menu_url}\">Open menu page</a>"
    if not day_menu.cards:
        return header + '\n\nNo clean menu items were found for this day.'
    body = '\n\n'.join(format_card(card) for card in day_menu.cards)
    return header + '\n\n' + body


def summarize_card_for_week(card: MenuCard) -> str:
    label = card.category
    if card.title and card.title != card.category:
        label = f'{card.category}: {card.title}'
    return re.sub(r'\s+', ' ', label).strip()


def format_week_menu(day_menus: list[DayMenu], menu_url: str = DEFAULT_MENU_URL) -> str:
    parts = [
        f"🗓️ <b>USI Mensa — Week at a glance</b>\n"
        "<i>Tentative, may change. Check the official menu page for updates.</i>\n"
        f"<a href=\"{menu_url}\">Open menu page</a>"
    ]
    table = ['Day        Menu', '---------- ------------------------------------------------------------']
    for day_menu in day_menus:
        label = datetime.fromisoformat(day_menu.target_date).strftime('%a %d %b')
        if not day_menu.cards:
            summary = 'No menu found'
        else:
            summary = '; '.join(summarize_card_for_week(card) for card in day_menu.cards)
        table.append(f'{label:<10} {summary}')
    parts.append('<pre>' + escape_html('\n'.join(table)) + '</pre>')
    return '\n\n'.join(parts)


def has_any_menu_cards(day_menus: list[DayMenu]) -> bool:
    return any(day_menu.cards for day_menu in day_menus)


def remaining_weekdays(start_date: date) -> list[date]:
    days_left = 4 - start_date.weekday()
    if days_left < 0:
        return []
    return [
        date.fromordinal(start_date.toordinal() + offset)
        for offset in range(days_left + 1)
    ]


def match_cards(day_menu: DayMenu, tags: list[str]) -> list[MenuCard]:
    if not tags:
        return []
    needles = [t.lower() for t in tags if t]
    matches: list[MenuCard] = []
    for card in day_menu.cards:
        hay = ' '.join([card.category, card.title, card.description]).lower()
        if any(tag in hay for tag in needles):
            matches.append(card)
    return matches


def format_matches(matches: list[MenuCard]) -> str:
    if not matches:
        return ''
    lines = [f"⭐ {category_emoji(card.category)} {escape_html(card.title or card.category)}" for card in matches]
    return '\n'.join(lines)
