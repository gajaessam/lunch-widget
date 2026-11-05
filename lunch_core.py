"""화면과 무관한 급식 위젯의 계산 로직.

Tkinter를 쓰지 않으므로 tests/test_core.py에서 자동으로 검사할 수 있다.
"""

import re
from datetime import date, datetime, timedelta

import knowledge_cards as cards

APP_VERSION = "1.6.0"

ALLERGY_MAP = {
    '1': '난류', '2': '우유', '3': '메밀', '4': '땅콩', '5': '대두',
    '6': '밀', '7': '고등어', '8': '게', '9': '새우', '10': '돼지',
    '11': '복숭아', '12': '토마토', '13': '아황산', '14': '호두',
    '15': '닭고기', '16': '쇠고기', '17': '오징어', '18': '조개', '19': '잣'
}

# NEIS 끼니 코드: 1 조식, 2 중식, 3 석식
LUNCH_MEAL_CODE = "2"

# 시·도교육청 코드 (학교 검색의 지역 선택에 쓴다)
OFFICE_REGIONS = (
    ("B10", "서울"), ("C10", "부산"), ("D10", "대구"), ("E10", "인천"),
    ("F10", "광주"), ("G10", "대전"), ("H10", "울산"), ("I10", "세종"),
    ("J10", "경기"), ("K10", "강원"), ("M10", "충북"), ("N10", "충남"),
    ("P10", "전북"), ("Q10", "전남"), ("R10", "경북"), ("S10", "경남"),
    ("T10", "제주"),
)

DEFAULT_CLASS_ORDER = [str(i) for i in range(1, 12)]
DEFAULT_BASE_DATE = "2026-09-01"
MEAL_CACHE_LIMIT = 40

_SEPARATORS = r"[.,·/]"
# (5.6.13), (1. 5), (1,5,6), (2)처럼 괄호 안에 숫자만 있는 알레르기 표기
_CODE_GROUP = re.compile(
    rf"\(\s*(\d{{1,2}}(?:\s*{_SEPARATORS}\s*\d{{1,2}})*)\s*{_SEPARATORS}?\s*\)"
)
# 쌀밥5.6.13. 처럼 괄호 없이 이름 끝에 붙은 표기
_TRAILING_CODES = re.compile(r"(?<![\d.])\s*((?:\d{1,2}\.)+\d{0,2})\s*$")
# 해석하지 못했지만 알레르기 번호처럼 보이는 괄호
_SUSPECT_GROUP = re.compile(rf"\(\s*[\d\s.,·/]+\s*\)")


def _is_allergy_code(number):
    return str(int(number)) in ALLERGY_MAP


def parse_menu_item(raw_item):
    """메뉴 문자열을 (이름, 알레르기 번호 목록, 표기 불확실 여부)로 나눈다.

    알레르기는 안전 정보이므로, 번호처럼 보이는데 해석하지 못한 표기는
    조용히 버리지 않고 불확실로 표시해 화면에서 확인하게 한다.
    """
    text = raw_item.replace("&nbsp;", " ").replace("*", "").replace("#", "").strip()
    numbers = []
    uncertain = False

    def take_codes(match):
        nonlocal uncertain
        found = re.findall(r"\d+", match.group(1))
        if not found or not all(_is_allergy_code(number) for number in found):
            uncertain = True
            return match.group(0)
        for number in found:
            number = str(int(number))
            if number not in numbers:
                numbers.append(number)
        return " "

    text = _CODE_GROUP.sub(take_codes, text)
    text = _TRAILING_CODES.sub(take_codes, text)
    if _SUSPECT_GROUP.search(text):
        uncertain = True
    name = re.sub(r"\s+", " ", text).strip()
    return name, numbers, uncertain


def format_allergies(allergy_numbers):
    names = []
    for number in allergy_numbers:
        name = ALLERGY_MAP.get(number)
        if name and name not in names:
            names.append(name)
    return "알레르기: " + "·".join(names) if names else ""


def select_meal(rows):
    """조식·석식이 함께 오는 학교에서도 중식을 고른다."""
    if not rows:
        return None
    for row in rows:
        if str(row.get("MMEAL_SC_CODE", "")) == LUNCH_MEAL_CODE:
            return row
    return rows[0]


def api_rows(response, service):
    """NEIS 응답에서 행 목록을 꺼낸다. 진짜 오류는 ValueError로 알린다.

    데이터 없음(INFO-200)은 빈 목록이다.
    """
    if service in response:
        for section in response[service]:
            if "row" in section:
                return section["row"]
        return []
    code = response.get("RESULT", {}).get("CODE", "")
    if code and code != "INFO-200":
        raise ValueError(f"NEIS 오류 응답: {code}")
    return []


def meals_by_date(rows):
    """여러 날짜의 행을 날짜별 중식으로 묶는다."""
    grouped = {}
    for row in rows:
        ymd = str(row.get("MLSV_YMD", ""))
        if ymd:
            grouped.setdefault(ymd, []).append(row)
    return {ymd: select_meal(items) for ymd, items in grouped.items()}


def parse_school_search(response):
    """학교 검색 응답을 ([{name, office_code, school_code, label}], 전체 개수)로 만든다."""
    rows = api_rows(response, "schoolInfo")
    total = len(rows)
    for section in response.get("schoolInfo", []):
        for item in section.get("head", []):
            if "list_total_count" in item:
                total = item["list_total_count"]
    schools = []
    for row in rows:
        office_name = str(row.get("ATPT_OFCDC_SC_NM", "")).replace("교육청", "")
        name = str(row.get("SCHUL_NM", ""))
        address = str(row.get("ORG_RDNMA", "")).strip()
        schools.append({
            "name": name,
            "office_code": str(row.get("ATPT_OFCDC_SC_CODE", "")),
            "school_code": str(row.get("SD_SCHUL_CODE", "")),
            "label": f"{name} · {office_name}" + (f" · {address}" if address else ""),
        })
    return schools, total


def cache_key(office_code, school_code, ymd):
    return f"{office_code}|{school_code}|{ymd}"


def prune_meal_cache(cache, limit=MEAL_CACHE_LIMIT):
    """오래된 날짜부터 지워 저장 파일이 커지지 않게 한다."""
    keys = sorted(cache, key=lambda key: key.rsplit("|", 1)[-1], reverse=True)
    return {key: cache[key] for key in keys[:limit]}


def weekdays_between(start, end):
    """start 다음 날부터 end까지의 평일 수 (end가 앞서면 음수)."""
    if end < start:
        return -weekdays_between(end, start)
    weeks, extra = divmod((end - start).days, 7)
    count = weeks * 5
    for offset in range(1, extra + 1):
        if (start + timedelta(days=offset)).weekday() < 5:
            count += 1
    return count


def _monday(day):
    return day - timedelta(days=day.weekday())


def _as_date(value):
    return value.date() if isinstance(value, datetime) else value


def rotate_order(rule, on_date, ensure_class=0):
    """규칙에 따라 on_date의 반별 출발 순서를 돌려준다.

    ensure_class: 예전 기본값(1~11반)을 그대로 쓰는 학교에서 12반 이상을
    고르면 그 반까지 순서를 넓힌다.
    """
    base_order = list(rule.get("base_order", DEFAULT_CLASS_ORDER))
    if ensure_class > len(base_order) and base_order == DEFAULT_CLASS_ORDER:
        base_order = [str(i) for i in range(1, ensure_class + 1)]
    total = len(base_order)
    if total == 0:
        return []

    try:
        base = datetime.strptime(rule.get("base_date", DEFAULT_BASE_DATE), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        base = datetime.strptime(DEFAULT_BASE_DATE, "%Y-%m-%d").date()
    current = _as_date(on_date)

    cycle = rule.get("cycle", "weekly")
    if cycle == "daily":
        if rule.get("day_basis") == "weekday":
            steps = weekdays_between(base, current)
        else:
            steps = (current - base).days
    elif cycle == "monthly":
        steps = (current.year - base.year) * 12 + (current.month - base.month)
    else:
        # 손상된 값도 기본인 주간 순환으로 처리한다.
        steps = (_monday(current) - _monday(base)).days // 7

    # 오른쪽 순환: 맨 뒤 반이 다음 주기에 맨 앞으로 온다 (1-2-3 → 3-1-2)
    shift = steps % total
    return base_order[-shift:] + base_order[:-shift] if shift else base_order


def modernize_daily_rule(rule, today):
    """주말까지 세던 예전 '매일' 규칙을 오늘 순서가 유지되도록 평일 기준으로 바꾼다."""
    if not isinstance(rule, dict) or rule.get("cycle") != "daily" or rule.get("day_basis"):
        return rule
    return {
        "base_order": rotate_order(rule, today),
        "cycle": "daily",
        "day_basis": "weekday",
        "base_date": _as_date(today).strftime("%Y-%m-%d"),
    }


def departure_minutes(start_time, rank, classes_per_slot, interval_minutes):
    """순서(1부터)에 따른 출발 시각을 자정 기준 분으로 돌려준다."""
    try:
        hour, minute = (int(value) for value in start_time.split(":"))
        start = hour * 60 + minute
    except (AttributeError, TypeError, ValueError):
        start = 12 * 60 + 30
    return start + ((rank - 1) // max(1, classes_per_slot)) * max(1, interval_minutes)


def _card_matches(card, menu_names):
    excluded = card.get("exclude", ())
    for name in menu_names:
        if any(word in name for word in excluded):
            continue
        if any(word in name for word in card.get("keywords", ())):
            return True
        if any(name.endswith(word) for word in card.get("endings", ())):
            return True
    return False


def menu_names(meal):
    """식단에서 알레르기 번호를 뺀 메뉴 이름만 꺼낸다."""
    if not meal:
        return []
    names = (parse_menu_item(item)[0] for item in str(meal.get("DDISH_NM", "")).split("<br/>"))
    return [name for name in names if name]


def parse_nutrition(meal):
    """NEIS 영양 정보를 {이름: 값 문자열}로 만든다. 값이 0이거나 비어 있으면 뺀다."""
    if not meal:
        return {}
    values = {}
    for item in str(meal.get("NTR_INFO", "")).split("<br/>"):
        name, _, value = item.partition(":")
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match and float(match.group()) > 0:
            values[name.strip()] = match.group()
    calories = re.search(r"\d+(?:\.\d+)?", str(meal.get("CAL_INFO", "")))
    if calories and float(calories.group()) > 0:
        values["열량"] = calories.group()
    return values


def macro_ratio(meal):
    """열량 기준 탄수화물:단백질:지방 비율을 합이 100인 정수로 돌려준다. 없으면 None."""
    values = parse_nutrition(meal)
    try:
        energy = [
            float(values["탄수화물(g)"]) * 4,
            float(values["단백질(g)"]) * 4,
            float(values["지방(g)"]) * 9,
        ]
    except KeyError:
        return None
    total = sum(energy)
    shares = [value / total * 100 for value in energy]
    ratio = [int(share) for share in shares]
    # 반올림으로 합이 99나 101이 되지 않게, 소수점이 큰 쪽부터 1씩 더한다.
    order = sorted(range(3), key=lambda index: shares[index] - ratio[index], reverse=True)
    for index in order[:100 - sum(ratio)]:
        ratio[index] += 1
    return tuple(ratio)


def meal_summary(meal):
    """메뉴 제목 줄에 보일 열량과 탄단지 비율. 평가 없이 수치만 보여 준다."""
    lines = []
    calories = parse_nutrition(meal).get("열량")
    if calories:
        lines.append(f"{int(float(calories) + 0.5)} kcal")
    ratio = macro_ratio(meal)
    if ratio:
        lines.append("탄단지 {}:{}:{}".format(*ratio))
    return "\n".join(lines)


def _knowledge_tiers(meal):
    """오늘 식단에 맞는 문장을 (메뉴, 음식 종류, 영양 정보) 단계별로 모은다."""
    names = menu_names(meal)
    values = parse_nutrition(meal)
    return (
        [card["text"] for card in cards.MENU_CARDS if _card_matches(card, names)],
        [card["text"] for card in cards.CATEGORY_CARDS if _card_matches(card, names)],
        [
            card["text"].format(value=values[card["key"]])
            for card in cards.NUTRITION_TEMPLATES if card["key"] in values
        ],
    )


def _pick(pool, day, avoid=None):
    """날짜마다 돌아가며 고른다. 같은 날에는 언제 다시 그려도 같은 문장이다."""
    choice = pool[day % len(pool)]
    if choice == avoid and len(pool) > 1:
        choice = pool[(day + 1) % len(pool)]
    return choice


def select_knowledge(meal, on_date, previous_meal=None):
    """오늘 보여 줄 지식 한 줄을 고른다.

    메뉴 → 음식 종류 → 영양 정보 → 일반 지식 순으로 찾아 빈칸이 생기지 않게
    한다. 김치나 국처럼 거의 매일 나오는 메뉴가 있어도 실제 영양 수치를 볼 수
    있도록, 사흘에 하루는 영양 정보를 먼저 쓴다.
    previous_meal: 직전 급식일의 식단. 같은 문장이 연달아 나오지 않게 한다.
    """
    day = _as_date(on_date).toordinal()
    menu, category, nutrition = _knowledge_tiers(meal)
    tiers = [nutrition, menu, category] if day % 3 == 0 else [menu, category, nutrition]
    avoid = None
    if previous_meal:
        avoid = select_knowledge(previous_meal, _as_date(on_date) - timedelta(days=1))
    for pool in tiers:
        if pool:
            # 영양 우선인 날은 사흘 간격이라, 그대로 나누면 일부 카드만 반복된다.
            turn = day // 3 if pool is nutrition and day % 3 == 0 else day
            return _pick(pool, turn, avoid)
    return _pick(cards.GENERAL_CARDS, day, avoid)


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def is_newer_version(latest, current=APP_VERSION):
    if not isinstance(latest, str) or not re.fullmatch(r"\d+(?:\.\d+)*", latest):
        return False
    return version_key(latest) > version_key(current)
