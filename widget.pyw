"""
교실 전자칠판용 급식 위젯 (Windows 데스크톱 프로그램)

- 나이스(NEIS)에서 급식을 받아 오늘의 메뉴와 알레르기 정보를 보여 준다.
- 학교·학급별 출발 순서와 출발 시각을 계산해 보여 준다.
- 화면과 무관한 계산은 lunch_core.py에 있고 tests/에서 검사한다.
"""

import sys
import json
import logging
import logging.handlers
import os
import re
import ctypes
from ctypes import wintypes
import urllib.error
import urllib.request
import urllib.parse
import calendar
import webbrowser
from datetime import datetime, timedelta
import threading

try:
    import winreg
except ImportError:  # Windows가 아닌 환경에서 테스트할 때
    winreg = None

import tkinter as tk
import tkinter.font as tkfont
from tkinter import simpledialog, messagebox

import lunch_core as core
from lunch_core import APP_VERSION

# EXE는 업데이트하거나 다른 폴더로 옮길 수 있으므로, 배포본의 개인 설정은
# 실행 파일 옆이 아니라 Windows 사용자별 설정 폴더에 보관한다. 개발 중에는
# 기존처럼 소스 파일 옆 설정을 사용해 실제 사용자의 설정을 건드리지 않는다.
IS_PACKAGED_APP = bool(getattr(sys, 'frozen', False))
if IS_PACKAGED_APP:
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

LEGACY_CONFIG_PATH = os.path.join(APP_DIR, "widget_config.json")
if IS_PACKAGED_APP:
    USER_CONFIG_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA") or APP_DIR,
        "급식 위젯",
    )
    CONFIG_PATH = os.path.join(USER_CONFIG_DIR, "widget_config.json")
else:
    USER_CONFIG_DIR = APP_DIR
    CONFIG_PATH = LEGACY_CONFIG_PATH

MEAL_CACHE_PATH = os.path.join(USER_CONFIG_DIR, "meal_cache.json")
LOG_PATH = os.path.join(USER_CONFIG_DIR, "widget.log")
log = logging.getLogger("lunch_widget")


def setup_logging():
    """화면에 나오지 않는 오류도 교실 PC에서 확인할 수 있게 파일로 남긴다."""
    log.setLevel(logging.INFO)
    try:
        os.makedirs(USER_CONFIG_DIR, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=256 * 1024, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
    except OSError:
        log.addHandler(logging.NullHandler())


# EXE에 함께 넣은 Pretendard를 이 프로그램 안에서만 임시 등록한다.
# 설치 권한 없이도 모든 교실 PC에서 같은 서체로 보이게 한다.
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    FONT_DIR = os.path.join(sys._MEIPASS, "assets", "fonts")
else:
    FONT_DIR = os.path.join(APP_DIR, "assets", "fonts")

UI_FONT = "Pretendard"
UI_STRONG_FONT = "Pretendard"

if os.name == "nt":
    for _font_file in ("Pretendard-Regular.ttf", "Pretendard-SemiBold.ttf"):
        _font_path = os.path.join(FONT_DIR, _font_file)
        if os.path.exists(_font_path):
            try:
                # FR_PRIVATE: 다른 프로그램이나 시스템 글꼴 목록에는 남기지 않는다.
                ctypes.windll.gdi32.AddFontResourceExW(_font_path, 0x10, 0)
            except Exception:
                pass

NEIS_API_URL = "https://open.neis.go.kr/hub/mealServiceDietInfo"
NEIS_SCHOOL_URL = "https://open.neis.go.kr/hub/schoolInfo"
DEFAULT_SCHOOL_NAME = "서일중학교"
DEFAULT_OFFICE_CODE = "B10"      # 서울시교육청
DEFAULT_SCHOOL_CODE = "7091433"  # 서일중학교
FEEDBACK_CONTACT = "가정재연쌤"  # 개발자. 모든 학교에서 같다.

# 업데이트 안내: 프로그램은 스스로를 교체하지 않고 새 버전만 알려 준다.
GITHUB_RELEASE_BASE = "https://github.com/gajaessam/lunch-widget/releases/latest/download"
UPDATE_INFO_URL = f"{GITHUB_RELEASE_BASE}/latest.json"
DOWNLOAD_URL = f"{GITHUB_RELEASE_BASE}/lunch-widget.zip"
HTTP_HEADERS = {"User-Agent": f"LunchWidget/{APP_VERSION}"}

# Windows 로그인 때 자동 실행 (설치 프로그램과 같은 이름을 쓴다)
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "LunchWidget"
# 예전 버전이 시작프로그램 폴더에 남겼을 수 있는 VBS 파일
LEGACY_STARTUP_SCRIPTS = ("서일중_급식위젯.vbs", "LunchWidget_AutoStart.vbs")

# 조회 실패 뒤 다시 시도하기까지의 시간(분)과, 다른 날짜를 보다가 조작이
# 없을 때 오늘로 돌아오기까지의 시간(분).
FETCH_RETRY_MINUTES = 2
DATE_AUTO_RETURN_MINUTES = 5
MIN_MENU_FONT_SIZE = 8

# 1990년대 PC 프로그램을 닮은, 각진 레트로 팔레트.
# 둥근 카드나 그림 대신 진한 제목 막대·회색 패널·작은 포인트색으로
# 전자칠판에서도 또렷하고 안정적인 화면을 만든다.
COLORS = {
    "background": "#D7D8DA",
    "surface": "#FFFFFF",
    "border": "#2A2BAB",
    "text": "#0B183D",
    "muted": "#4F5665",
    "navy": "#0000A8",
    "navy_light": "#D6E6FF",
    "mint": "#C7F5E3",
    "mint_dark": "#176B59",
    "soft_mint": "#C5F4E7",
    "coral": "#E34D55",
    "soft_coral": "#FFE8E8",
    "yellow": "#FFF04C",
    "soft_lilac": "#D8D8DA",
    "button": "#E7E7E7",
    "button_shadow": "#7E8088",
    "tip": "#FDF8C9",
}

class LunchWidget:
    def __init__(self, root):
        self.root = root
        self.current_date = datetime.now()
        self._update_prompted = ""
        self._prefetched_on = None
        # 위젯은 다른 프로그램 위에 계속 떠 있지 않는다. 대신 사용자가
        # 원하는 위치에서 움직이지 않도록 '위치 고정'만 제공한다.
        self.is_position_locked = False
        self.widget_opacity = 1.0
        # 배포 EXE에서만 Windows 로그인 뒤 자동 실행을 등록한다. 개발용
        # .pyw를 실행해서 시작프로그램이 엉키는 일을 막기 위해 기본은 꺼짐이다.
        self.start_with_windows = False
        self._startup_preference_migrated = False
        self.drag_x = 0
        self.drag_y = 0
        self.quest_log = {}
        self.current_exploration = None
        self.current_meal_data = None
        # 인터넷이 끊겨도 보여 줄 수 있도록 받아 둔 날짜별 메뉴
        self.meal_cache = self._load_meal_cache()
        self._showing_cached_meal = False
        self._last_date_change = datetime.now()

        # 학교별 값은 설정 파일에 저장한다. 새 설치본에서는 학교를 먼저
        # 입력받고, 기존 서일중 설정 파일은 기존 값으로 자연스럽게 이어 간다.
        self.school_name = ""
        self.office_code = ""
        self.school_code = ""
        self.departure_start_time = "12:30"
        self.departure_interval_minutes = 5
        self.classes_per_slot = 2
        self.school_configured = False

        # 학급 설정 (기본 2학년 3반, 서일중 11반)
        self.my_grade = 2
        self.my_class = 3

        # 급식 순서 규칙
        self.order_rules = {
            "1": {"base_order": [str(i) for i in range(1, 12)], "cycle": "weekly", "base_date": "2026-09-01"},
            "2": {"base_order": [str(i) for i in range(1, 12)], "cycle": "weekly", "base_date": "2026-09-01"},
            "3": {"base_order": [str(i) for i in range(1, 12)], "cycle": "weekly", "base_date": "2026-09-01"}
        }

        # 전자칠판에서 멀리서도 읽히는 기준 보드. 오른쪽 아래 손잡이는
        # 이 500×730 비율을 고정한 채 확대·축소한다.
        self.base_width = 500
        self.base_height = 730
        self.min_ui_scale = 0.80
        self.max_ui_scale = 1.40
        self.width = self.base_width
        self.expanded_height = self.base_height
        self.ui_scale = 1.0
        self._base_fonts = []
        self._resize_save_job = None
        self._resize_start = None
        # 세 핵심 카드 사이에 쓰는 기준 여백과, 최소 크기에서도 글자가
        # 잘리지 않도록 확보한 카드 내부 높이다.
        self._base_card_gap = 7
        self._base_order_card_height = 112
        self._base_order_info_height = 68
        self._base_knowledge_height = 94
        self.menu_row_widgets = []
        self._menu_layout_job = None
        self.menu_status_label = None
        self._allergy_popup = None
        self._allergy_popup_source = None
        self._icon_hint_popup = None
        self._icon_hint_job = None
        self._aspect_correction_job = None

        self.root.overrideredirect(True)
        self.root.attributes("-alpha", self.widget_opacity)
        self.root.attributes("-topmost", False)
        self.root.config(bg=COLORS["background"])

        self.load_config()
        # 설치 프로그램에서 자동 실행을 고른 경우도 같은 상태로 본다.
        if IS_PACKAGED_APP and self._read_run_value():
            self.start_with_windows = True
        startup_synced = self.sync_startup_registration()
        # 이전 자동 실행 사용자에게 새 선택 값을 한 번만 저장한다. 그렇지 않으면
        # 다음 실행 때 옛 VBS가 이미 지워져 자동 실행이 꺼진 것으로 판단한다.
        if startup_synced and self._startup_preference_migrated:
            self.save_config()
        self.setup_ui()
        self._capture_base_fonts()
        self._apply_ui_scale()

        # 테두리가 없는 위젯이라 오른쪽 아래의 ↘ 손잡이로 크기를 조절한다.
        self.root.resizable(True, True)
        self.root.bind("<Configure>", self._on_window_configure)
        self.bring_to_front()

        # 마우스 드래그 바인딩. 위치를 고정하면 이 대상들만 움직이지 않는다.
        self._drag_targets = [
            self.header_frame, self.title_area, self.title_shadow, self.title_label,
            self.school_label, self.mascot_view, self.sub_spacer
        ]
        for widget in self._drag_targets:
            widget.bind("<Button-1>", self.start_drag)
            widget.bind("<B1-Motion>", self.do_drag)
            widget.bind("<ButtonRelease-1>", self.end_drag)
        self._refresh_position_lock_button()

        # 첫 조회가 실패하면 재시도 시점을 기록하므로 조회 전에 준비한다.
        self.last_checked_date = datetime.now().date()
        self._auto_refresh_counter = 0
        self.load_all_data_async()

        # 새 폴더에 처음 설치한 경우에만 바로 학교 설정을 안내한다.
        if not self.school_configured:
            self.root.after(350, self.prompt_initial_school_setup)

        # 자동 새로고침 타이머 시작 (1분 주기 자정 감지 & 30분 주기 데이터 갱신)
        self._schedule_auto_refresh()

        # 백그라운드에서 업데이트 확인
        threading.Thread(target=self.check_for_update, daemon=True).start()

    def bring_to_front(self):
        """실행·중복 실행 시에만 일반 창처럼 앞으로 보여 준다.

        topmost 속성은 사용하지 않으므로, 다른 프로그램을 클릭하면 위젯은
        일반 창과 마찬가지로 자연스럽게 뒤로 간다.
        """
        try:
            self._ensure_window_on_screen()
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def _schedule_auto_refresh(self):
        """전자칠판 및 상시 켜진 PC를 위한 자동 새로고침 (1분마다 체크)"""
        try:
            self._auto_refresh_tick()
        except Exception:
            # 한 번의 오류로 하루 종일 갱신이 멈추지 않게 타이머는 유지한다.
            log.exception("자동 새로고침 실패")
        self.root.after(60000, self._schedule_auto_refresh)

    def _auto_refresh_tick(self):
        now_date = datetime.now().date()
        self._auto_refresh_counter += 1

        # 메뉴를 다시 받지 않는 분에도 출발 안내의 남은 시간은 갱신한다.
        self.render_order_queue()

        # 1) 자정이 지나 날짜가 변경되었을 때: 오늘 날짜로 자동 전환 및 갱신
        if now_date != self.last_checked_date:
            self.last_checked_date = now_date
            self.current_date = datetime.now()
            self.load_all_data_async()
            self._auto_refresh_counter = 0
            threading.Thread(target=self.check_for_update, daemon=True).start()
        # 2) 다른 날짜를 본 뒤 조작이 없으면 칠판을 오늘 화면으로 되돌린다.
        elif (
            self.current_date.date() != now_date
            and datetime.now() - self._last_date_change >= timedelta(minutes=DATE_AUTO_RETURN_MINUTES)
        ):
            self._auto_refresh_counter = 0
            self.go_today()
        # 3) 30분이 지났고 현재 '오늘' 화면을 보고 있다면 최신 급식 데이터 재조회
        elif self._auto_refresh_counter >= 30:
            self._auto_refresh_counter = 0
            if self.current_date.date() == now_date:
                self.load_all_data_async()

    def _capture_base_fonts(self):
        """처음 만든 글꼴을 기억해 두고 창 너비에 맞춰 같은 비율로 조절한다."""
        self._base_fonts = []

        def collect(parent):
            for widget in parent.winfo_children():
                try:
                    font = tkfont.Font(font=widget.cget("font"))
                    self._base_fonts.append((widget, {
                        "family": font.cget("family"),
                        "size": int(font.cget("size")),
                        "weight": font.cget("weight"),
                        "slant": font.cget("slant"),
                        "underline": font.cget("underline"),
                        "overstrike": font.cget("overstrike"),
                    }))
                except (tk.TclError, TypeError, ValueError):
                    pass
                collect(widget)

        collect(self.root)

    def _ui_scale_limits(self):
        """현재 화면 안에서 안전하게 쓸 수 있는 최소·최대 배율을 계산한다."""
        screen_limit = min(
            (self.root.winfo_screenwidth() - 20) / self.base_width,
            (self.root.winfo_screenheight() - 40) / self.base_height,
            self.max_ui_scale,
        )
        return self.min_ui_scale, max(self.min_ui_scale, screen_limit)

    def _clamp_ui_scale(self, value):
        min_scale, max_scale = self._ui_scale_limits()
        return max(min_scale, min(max_scale, value))

    def _dimensions_for_scale(self, scale):
        scale = self._clamp_ui_scale(scale)
        return round(self.base_width * scale), round(self.base_height * scale)

    def _scaled_px(self, value, minimum=1):
        return max(minimum, round(value * self.ui_scale))

    def _apply_ui_scale(self):
        """창 배율에 맞춰 글자·아이콘·주요 카드의 크기를 함께 조절한다."""
        self.ui_scale = self._clamp_ui_scale(self.width / self.base_width)
        for widget, base in self._base_fonts:
            try:
                size = base["size"]
                scaled_size = max(6, round(abs(size) * self.ui_scale))
                if size < 0:
                    scaled_size = -scaled_size
                styles = [base["weight"], base["slant"]]
                if base["underline"]:
                    styles.append("underline")
                if base["overstrike"]:
                    styles.append("overstrike")
                widget.configure(font=(base["family"], scaled_size, *styles))
            except tk.TclError:
                continue

        def scaled(size):
            return max(6, round(size * self.ui_scale))

        # Text 위젯의 태그는 일반 위젯과 달리 별도로 글꼴을 다시 지정해야 한다.
        try:
            # 기본 폭에서는 키운 9pt를 유지하되, 390px 최소 폭에서는 한 줄
            # 보조정보가 잘리지 않게 한 단계만 압축한다.
            queue_font_size = 8 if self.width <= 420 else 9
            self.order_queue_text.tag_configure("normal", font=(UI_FONT, scaled(queue_font_size)))
            self.order_queue_text.tag_configure("my_class", font=(UI_FONT, scaled(queue_font_size), "bold"))
            self.order_queue_text.tag_configure("arrow", font=(UI_FONT, scaled(max(7, queue_font_size - 1))))
            # 커진 아이콘/저작권 영역을 빼고도 문장이 자연스럽게 줄바꿈되게 한다.
            # 48px 아이콘으로 줄인 만큼 본문 폭을 넓혀, 영양 문장이 2줄 안에
            # 들어가도록 한다.
            self.quest_question_label.configure(wraplength=max(235, self.width - 104))
            self.footer_feedback_label.configure(wraplength=max(160, self.width - 30))
        except tk.TclError:
            pass
        self._apply_scaled_layout()
        self._apply_menu_row_scale()

    def _draw_header_notepad_icon(self):
        canvas = self.mascot_view
        canvas.delete("all")
        p = lambda value: round(value * self.ui_scale)
        line = lambda value: max(1, round(value * self.ui_scale))
        canvas.create_rectangle(p(7), p(7), p(30), p(33), fill="#FFFFFF", outline="#A8B8E8", width=line(2))
        canvas.create_rectangle(p(10), p(12), p(27), p(14), fill=COLORS["navy"], outline="")
        canvas.create_rectangle(p(10), p(18), p(27), p(20), fill="#A8B8E8", outline="")
        canvas.create_rectangle(p(10), p(23), p(25), p(25), fill="#A8B8E8", outline="")
        canvas.create_rectangle(p(10), p(28), p(22), p(30), fill="#A8B8E8", outline="")
        for x in (11, 18, 25):
            canvas.create_rectangle(p(x), p(3), p(x + 3), p(9), fill="#FFFFFF", outline=COLORS["navy"], width=line(1))

    def _draw_clock_icon(self):
        canvas = self.clock_icon
        canvas.delete("all")
        p = lambda value: round(value * self.ui_scale)
        line = lambda value: max(1, round(value * self.ui_scale))
        canvas.create_polygon(
            p(12), p(3), p(32), p(3), p(41), p(12), p(41), p(32),
            p(32), p(41), p(12), p(41), p(3), p(32), p(3), p(12),
            fill="#FFFCE5", outline=COLORS["navy"], width=line(2)
        )
        for x1, y1, x2, y2 in ((22, 7, 22, 10), (22, 34, 22, 37), (7, 22, 10, 22), (34, 22, 37, 22)):
            canvas.create_line(p(x1), p(y1), p(x2), p(y2), fill=COLORS["navy"], width=line(1))
        canvas.create_line(p(22), p(10), p(22), p(22), fill=COLORS["navy"], width=line(2))
        canvas.create_line(p(22), p(22), p(30), p(27), fill=COLORS["navy"], width=line(2))
        canvas.create_rectangle(p(20), p(20), p(24), p(24), fill=COLORS["navy"], outline="")

    def _draw_people_icon(self):
        canvas = self.people_icon
        canvas.delete("all")
        p = lambda value: round(value * self.ui_scale)
        line = lambda value: max(1, round(value * self.ui_scale))
        for x, y in ((9, 13), (22, 9), (35, 13)):
            canvas.create_oval(p(x - 4), p(y - 4), p(x + 4), p(y + 4), fill="#C5C7D8", outline="#28305D", width=line(1))
            canvas.create_rectangle(p(x - 6), p(y + 5), p(x + 6), p(y + 16), fill="#C5C7D8", outline="#28305D", width=line(1))

    def _draw_knowledge_mascot(self):
        canvas = self.knowledge_mascot
        canvas.delete("all")
        p = lambda value: round(value * self.ui_scale)
        line = lambda value: max(1, round(value * self.ui_scale))
        canvas.create_oval(p(7), p(6), p(39), p(24), fill="#FFFDF2", outline=COLORS["navy"], width=line(2))
        for x, y in ((14, 11), (21, 8), (28, 11), (18, 15), (25, 15), (32, 17)):
            canvas.create_oval(p(x), p(y), p(x + 3), p(y + 3), fill="#FFFFFF", outline="#A0A0A0", width=line(1))
        canvas.create_polygon(p(5), p(21), p(41), p(21), p(35), p(40), p(11), p(40),
                              fill="#7CB7F1", outline=COLORS["navy"], width=line(2))
        canvas.create_line(p(11), p(29), p(35), p(29), fill="#D6E6FF", width=line(2))
        canvas.create_line(p(13), p(34), p(33), p(34), fill="#D6E6FF", width=line(2))

    def _draw_knowledge_divider(self):
        canvas = self.knowledge_divider
        canvas.delete("all")
        p = lambda value: round(value * self.ui_scale)
        canvas.create_line(p(2), p(3), p(2), p(49), fill="#A9A9A9", dash=(max(1, p(2)), max(1, p(2))))

    def _draw_menu_icon(self):
        canvas = self.menu_icon
        canvas.delete("all")
        p = lambda value: round(value * self.ui_scale)
        for x1, y1, x2, y2 in (
            (4, 4, 6, 12), (8, 4, 10, 12), (12, 4, 14, 12), (4, 11, 14, 14),
            (8, 13, 10, 23), (19, 4, 22, 18), (18, 4, 22, 7), (19, 17, 21, 23),
        ):
            canvas.create_rectangle(p(x1), p(y1), p(x2), p(y2), fill=COLORS["text"], outline="")

    def _apply_scaled_layout(self):
        """고정 px로 만들었던 카드·아이콘·여백을 현재 배율로 함께 맞춘다."""
        if not hasattr(self, "header_frame"):
            return
        p = self._scaled_px

        self.main_container.pack_configure(padx=p(2), pady=p(2))
        self.board.pack_configure(padx=p(5), pady=p(5))
        self.header_frame.configure(height=p(52))
        self.brand_frame.pack_configure(padx=(p(7), 0))
        self.mascot_view.configure(width=p(38), height=p(38))
        self.mascot_view.pack_configure(padx=(0, p(6)), pady=p(7))
        self.title_area.pack_configure(pady=p(8))
        self.title_shadow.place(x=p(2), y=p(2))
        self._draw_header_notepad_icon()

        # 닫기는 보편적인 × 아이콘을 유지하고, 고정·설정은 처음 보는
        # 사용자도 바로 알 수 있게 짧은 텍스트 버튼으로 표시한다.
        tool_size = p(30, 18)
        self.btn_close.configure(width=tool_size, height=tool_size)
        self.btn_close.pack_configure(padx=(p(3), p(5)), pady=p(6))
        self._draw_retro_icon_button(
            self.btn_close, self.btn_close._retro_icon, selected=self.btn_close._retro_selected
        )
        for button in (self.btn_setting, self.btn_position_lock):
            button.configure(padx=p(6), pady=p(3), bd=p(2))
            button.pack_configure(padx=p(2), pady=p(7))

        self.body_frame.pack_configure(pady=(p(5), 0))
        card_gap = p(self._base_card_gap)
        self.date_frame.configure(height=p(42))
        self.date_frame.pack_configure(pady=(0, card_gap))
        self.date_prev_button.configure(padx=p(6), pady=p(4))
        self.date_prev_button.pack_configure(padx=(p(2), p(7)), pady=p(3))
        self.date_next_button.configure(padx=p(6), pady=p(4))
        self.date_next_button.pack_configure(padx=(p(7), p(2)), pady=p(3))
        self.date_label.configure(pady=p(4))
        self.date_label.pack_configure(pady=p(3))

        self.order_card_base_height = p(self._base_order_card_height)
        self.order_queue_extra_height = p(16)
        self.order_info.configure(height=p(self._base_order_info_height))
        self.order_info.pack_configure(padx=p(8), pady=(p(4), 0))
        for canvas, draw in ((self.clock_icon, self._draw_clock_icon), (self.people_icon, self._draw_people_icon)):
            canvas.configure(width=p(44), height=p(44))
            draw()
        self.clock_icon.pack_configure(padx=(p(2), p(7)), pady=p(5))
        self.people_icon.pack_configure(padx=(p(1), p(6)), pady=p(5))
        # 작은 크기에서도 노란 숫자 라벨의 위·아래 글리프가 눌리지 않게 한다.
        self.time_words.pack_configure(pady=(p(1), 0))
        self.rank_words.pack_configure(pady=(p(1), 0))
        self.order_divider.configure(width=p(3), height=p(self._base_order_info_height))
        self.order_divider.delete("all")
        self.order_divider.create_line(
            p(1), p(3), p(1), p(self._base_order_info_height - 3),
            fill="#9AA3A6", dash=(max(1, p(2)), max(1, p(2)))
        )
        self.order_divider.pack_configure(padx=p(7), pady=p(2))
        self.departure_time_value.configure(padx=p(8))
        self.departure_order_value.configure(padx=p(10))
        self.order_queue_text.configure(padx=p(10))
        # 노란 핵심 정보 칸과 반 순서 안내 줄이 붙어 보이지 않게 작은 숨을 둔다.
        self.order_queue_text.pack_configure(pady=(p(4), p(2)))
        self.order_card_canvas.pack_configure(pady=(0, card_gap))

        self.knowledge_frame.configure(height=p(self._base_knowledge_height))
        self.knowledge_frame.pack_configure(pady=(card_gap, p(6)))
        self.knowledge_mascot.configure(width=p(48), height=p(52))
        self.knowledge_mascot.pack_configure(padx=(p(8), p(3)), pady=p(4))
        self.knowledge_divider.configure(width=p(5), height=p(52))
        self.knowledge_divider.pack_configure(padx=(0, p(4)), pady=p(4))
        self.memo_body.pack_configure(padx=(p(6), p(10)), pady=p(4))
        self._draw_knowledge_mascot()
        self._draw_knowledge_divider()

        self.meal_title_row.configure(height=p(42))
        self.menu_icon.configure(width=p(25), height=p(26))
        self.menu_icon.pack_configure(padx=(p(10), p(7)), pady=p(7))
        self._draw_menu_icon()
        self.menu_header_line.configure(height=p(2))
        self.menu_rows_frame.pack_configure(padx=p(10), pady=(p(3), p(2)))
        self.resize_grip.configure(padx=p(3), pady=p(1))

        # 카드의 기준 높이가 바뀐 뒤 현재 반 순서를 다시 그려, 12반 이상
        # 확장 높이도 같은 비율로 적용한다.
        self.render_order_queue()

    def _on_window_configure(self, event):
        if event.widget is not self.root:
            return
        new_width, new_height = self.root.winfo_width(), self.root.winfo_height()
        if new_width < 100 or new_height < 100:
            return
        expected_width, expected_height = self._dimensions_for_scale(new_width / self.base_width)
        if abs(new_width - expected_width) > 2 or abs(new_height - expected_height) > 2:
            # 다른 코드나 기존 설정 파일이 찌그러진 크기를 요청해도, 다음
            # Configure에서 기준 비율의 정확한 크기로 한 번만 보정한다.
            self.root.geometry(f"{expected_width}x{expected_height}")
            return
        if new_width != self.width or new_height != self.expanded_height:
            self.width, self.expanded_height = new_width, new_height
            self._apply_ui_scale()
            if self._resize_save_job is not None:
                self.root.after_cancel(self._resize_save_job)
            self._resize_save_job = self.root.after(600, self.save_config)

    def start_resize(self, event):
        self._resize_start = (
            event.x_root, event.y_root,
            self.root.winfo_width(), self.root.winfo_height(),
            self.root.winfo_width() / self.base_width,
        )

    def do_resize(self, event):
        if not self._resize_start:
            return
        start_x, start_y, start_w, start_h, start_scale = self._resize_start
        width_scale = (start_w + event.x_root - start_x) / self.base_width
        height_scale = (start_h + event.y_root - start_y) / self.base_height
        # 모서리를 가로 또는 세로 어느 방향으로 끌어도 자연스럽게 같은 배율로
        # 반응하도록, 더 크게 움직인 축을 기준으로 삼는다.
        new_scale = width_scale if abs(width_scale - start_scale) >= abs(height_scale - start_scale) else height_scale
        new_w, new_h = self._dimensions_for_scale(new_scale)
        self.root.geometry(f"{new_w}x{new_h}")

    @staticmethod
    def _draw_round_card(canvas, width, height, radius, color, tag):
        """각진 90년대 프로그램 패널을 캔버스에 그린다.

        기존 메서드 이름은 유지해 리사이즈 로직을 건드리지 않는다.
        """
        canvas.delete(tag)
        if width < 8 or height < 8:
            return
        # 목표 화면처럼 검정 대신 진청색 외곽선, 안쪽 흰 하이라이트와 회색
        # 하단 그림자로 고전 프로그램 창의 깊이를 만든다.
        canvas.create_rectangle(1, 1, width - 2, height - 2, fill=COLORS["button_shadow"], outline="", tags=tag)
        canvas.create_rectangle(0, 0, width - 3, height - 3, fill=COLORS["button"], outline=COLORS["border"], width=1, tags=tag)
        canvas.create_rectangle(2, 2, width - 5, height - 5, fill=color, outline="#FFFFFF", width=1, tags=tag)

    def _resize_order_card(self, event):
        width, height = event.width, event.height
        self._draw_round_card(self.order_card_canvas, width, height, 0, COLORS["soft_mint"], "card")
        self.order_card_canvas.coords(self.order_card_window, 4, 4)
        self.order_card_canvas.itemconfigure(self.order_card_window, width=max(1, width - 8), height=max(1, height - 8))
        self.order_card_canvas.tag_raise(self.order_card_window)

    def _resize_menu_card(self, event):
        width, height = event.width, event.height
        self._draw_round_card(self.menu_card_canvas, width, height, 0, COLORS["surface"], "card")
        self.menu_card_canvas.coords(self.menu_card_window, 4, 4)
        self.menu_card_canvas.itemconfigure(self.menu_card_window, width=max(1, width - 8), height=max(1, height - 8))
        self.menu_card_canvas.tag_raise(self.menu_card_window)
        self._schedule_menu_row_layout()

    def _draw_retro_icon_button(self, canvas, icon, selected=False):
        """크기에 맞춰 그려지는 90년대 도구 모음 아이콘."""
        canvas.delete("all")
        bg = "#FFF4A7" if selected else COLORS["button"]
        canvas.configure(bg=COLORS["navy"])
        # 창이 줄어들 때도 버튼 안의 아이콘을 같은 비율로 줄인다.
        size = max(18, min(int(canvas.cget("width")), int(canvas.cget("height"))))
        scale = size / 30
        p = lambda value: round(value * scale)
        line = lambda width: max(1, round(width * scale))
        edge = size - 2
        canvas.create_rectangle(p(1), p(1), edge, edge, fill=bg, outline=COLORS["border"], width=line(1))
        canvas.create_line(p(2), p(2), p(28), p(2), fill="#FFFFFF", width=line(2))
        canvas.create_line(p(2), p(2), p(2), p(28), fill="#FFFFFF", width=line(2))
        canvas.create_line(p(2), p(28), p(28), p(28), fill=COLORS["button_shadow"], width=line(2))
        canvas.create_line(p(28), p(2), p(28), p(28), fill=COLORS["button_shadow"], width=line(2))
        ink = COLORS["navy"]

        if icon == "settings":
            # 여섯 톱니와 비어 있는 중심을 가진 기어. 작은 화면에서도
            # '설정'임을 즉시 읽을 수 있도록 사각 부품 모양을 피한다.
            for x1, y1, x2, y2 in (
                (13, 5, 17, 9), (20, 8, 24, 12), (20, 18, 24, 22),
                (13, 21, 17, 25), (6, 18, 10, 22), (6, 8, 10, 12),
            ):
                canvas.create_rectangle(p(x1), p(y1), p(x2), p(y2), fill=ink, outline="")
            canvas.create_oval(p(8), p(8), p(22), p(22), fill=ink, outline=ink)
            canvas.create_oval(p(12), p(12), p(18), p(18), fill=bg, outline="")
        elif icon == "pin":
            # 비스듬한 깃발이 아니라 세로 압정 실루엣으로 고정 기능을 분명히 한다.
            canvas.create_polygon(
                p(10), p(6), p(20), p(6), p(19), p(11), p(23), p(14),
                p(23), p(16), p(7), p(16), p(7), p(14), p(11), p(11),
                fill=ink, outline=ink
            )
            canvas.create_line(p(11), p(7), p(19), p(7), fill="#FFFFFF", width=line(1))
            canvas.create_line(p(15), p(16), p(15), p(24), fill=ink, width=line(2))
            canvas.create_line(p(13), p(24), p(15), p(27), p(17), p(24), fill=ink, width=line(2))
        elif icon == "close":
            canvas.create_line(p(9), p(9), p(20), p(20), fill=ink, width=line(3))
            canvas.create_line(p(20), p(9), p(9), p(20), fill=ink, width=line(3))

        canvas._retro_icon = icon
        canvas._retro_selected = selected

    def _hide_icon_hint(self):
        if self._icon_hint_job is not None:
            try:
                self.root.after_cancel(self._icon_hint_job)
            except tk.TclError:
                pass
            self._icon_hint_job = None
        popup = self._icon_hint_popup
        self._icon_hint_popup = None
        if popup is not None:
            try:
                popup.destroy()
            except tk.TclError:
                pass

    def _schedule_icon_hint(self, button):
        self._hide_icon_hint()
        hint = getattr(button, "_retro_hint", "")
        if not hint:
            return
        self._icon_hint_job = self.root.after(450, lambda: self._show_icon_hint(button, hint))

    def _show_icon_hint(self, button, hint):
        self._icon_hint_job = None
        try:
            if not button.winfo_ismapped():
                return
            popup = tk.Toplevel(self.root)
            popup.overrideredirect(True)
            popup.transient(self.root)
            popup.configure(bg=COLORS["border"])
            tk.Label(
                popup, text=hint, font=(UI_FONT, 9), fg=COLORS["text"],
                bg="#FFFDF2", padx=7, pady=4
            ).pack(padx=1, pady=1)
            popup.update_idletasks()
            x = button.winfo_rootx() + button.winfo_width() // 2 - popup.winfo_reqwidth() // 2
            y = button.winfo_rooty() + button.winfo_height() + 4
            x = max(4, min(x, popup.winfo_screenwidth() - popup.winfo_reqwidth() - 4))
            if y + popup.winfo_reqheight() > popup.winfo_screenheight() - 4:
                y = max(4, button.winfo_rooty() - popup.winfo_reqheight() - 4)
            popup.geometry(f"+{x}+{y}")
            self._icon_hint_popup = popup
        except tk.TclError:
            self._hide_icon_hint()

    def _make_retro_icon_button(self, parent, icon, command, *, selected=False, hint=""):
        button = tk.Canvas(
            parent, width=30, height=30, bg=COLORS["navy"], highlightthickness=0,
            cursor="hand2", takefocus=0
        )
        self._draw_retro_icon_button(button, icon, selected)
        button.bind("<Button-1>", lambda event: command())
        button._retro_hint = hint
        button.bind("<Enter>", lambda event, target=button: self._schedule_icon_hint(target))
        button.bind("<Leave>", lambda event: self._hide_icon_hint())
        button.bind("<Button-1>", lambda event: self._hide_icon_hint(), add="+")
        return button

    def _make_header_text_button(self, parent, text, command):
        """아이콘 해석이 필요 없는 상단용 레트로 텍스트 버튼."""
        button = tk.Label(
            parent, text=text, font=(UI_FONT, 9, "bold"),
            fg=COLORS["text"], bg=COLORS["button"], cursor="hand2",
            relief=tk.RAISED, bd=2, padx=6, pady=3, anchor="center"
        )
        button.bind("<Button-1>", lambda event: command())
        return button

    def _refresh_position_lock_button(self):
        """위치 잠금 상태를 문구·색·드래그 커서로 함께 알려 준다."""
        locked = self.is_position_locked
        self.btn_position_lock.configure(
            text="🔒 위치 고정됨" if locked else "📌 위치 고정",
            bg=COLORS["yellow"] if locked else COLORS["button"],
            fg=COLORS["text"], relief=tk.RAISED
        )
        for widget in getattr(self, "_drag_targets", ()):
            try:
                widget.configure(cursor="arrow" if locked else "fleur")
            except tk.TclError:
                pass

    def setup_ui(self):
        # 레트로 PC 프로그램처럼 보이되, 기본 기능은 기존과 완전히 같다.
        self.border_frame = tk.Frame(self.root, bg=COLORS["border"])
        self.border_frame.pack(fill=tk.BOTH, expand=True)
        self.main_container = tk.Frame(self.border_frame, bg=COLORS["background"])
        self.main_container.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        board = tk.Frame(self.main_container, bg=COLORS["background"])
        board.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.board = board

        def retro_button(parent, text, *, width=None, bg=None, fg=None, font_size=8, padx=5, pady=3):
            """네이티브 버튼 대신 색과 크기가 일정한 레트로 사각 버튼."""
            options = {
                "text": text,
                "font": (UI_FONT, font_size, "bold"),
                "fg": fg or COLORS["text"],
                "bg": bg or COLORS["button"],
                "cursor": "hand2",
                "relief": tk.RAISED,
                "bd": 2,
                "padx": padx,
                "pady": pady,
            }
            if width is not None:
                options["width"] = width
            return tk.Label(parent, **options)

        # 1. 파란 프로그램 제목 막대
        self.header_frame = tk.Frame(board, bg=COLORS["navy"], cursor="fleur", height=52)
        self.header_frame.pack(fill=tk.X, side=tk.TOP)
        self.header_frame.pack_propagate(False)
        brand_frame = tk.Frame(self.header_frame, bg=COLORS["navy"], cursor="fleur")
        brand_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(7, 0))
        self.brand_frame = brand_frame
        self.mascot_view = tk.Canvas(
            brand_frame, width=38, height=38, bg=COLORS["navy"], highlightthickness=0, cursor="fleur"
        )
        # 제목 글씨와 비례가 맞는 큰 픽셀 메모 아이콘.
        self.mascot_view.create_rectangle(7, 7, 30, 33, fill="#FFFFFF", outline="#A8B8E8", width=2)
        self.mascot_view.create_rectangle(10, 12, 27, 14, fill=COLORS["navy"], outline="")
        self.mascot_view.create_rectangle(10, 18, 27, 20, fill="#A8B8E8", outline="")
        self.mascot_view.create_rectangle(10, 23, 25, 25, fill="#A8B8E8", outline="")
        self.mascot_view.create_rectangle(10, 28, 22, 30, fill="#A8B8E8", outline="")
        for x in (11, 18, 25):
            self.mascot_view.create_rectangle(x, 3, x + 3, 9, fill="#FFFFFF", outline=COLORS["navy"], width=1)
        self.mascot_view.pack(side=tk.LEFT, pady=7, padx=(0, 6))
        title_area = tk.Frame(brand_frame, bg=COLORS["navy"], cursor="fleur")
        title_area.pack(side=tk.LEFT, pady=9)
        self.title_area = title_area
        # 제목만 실제 SemiBold 글꼴을 한 단계 더 굵게 쓰고, 짙은 그림자를
        # 겹쳐 멀리서도 또렷한 고전 프로그램 제목처럼 보이게 한다.
        # (본문은 기존 Pretendard 굵기를 유지해 과하게 무거워지지 않는다.)
        title_font = ("Pretendard SemiBold", 28, "bold")
        self.title_shadow = tk.Label(
            title_area, text="급식 위젯", font=title_font,
            fg="#18185E", bg=COLORS["navy"], cursor="fleur"
        )
        self.title_shadow.place(x=2, y=2)
        self.title_label = tk.Label(
            title_area, text="급식 위젯", font=title_font,
            fg="#FFFFFF", bg=COLORS["navy"], cursor="fleur"
        )
        self.title_label.pack(anchor="w")
        self.school_label = tk.Label(
            title_area, text=self.school_name or "오늘도 맛있고 건강하게!", font=(UI_FONT, 7, "bold"),
            fg="#D6E6FF", bg=COLORS["navy"], cursor="fleur"
        )
        # 제목 비율을 살리기 위해 학교명은 설정 창에서 확인하도록 하고, 상단에는 숨긴다.

        # 제목 막대에는 기능을 바로 읽을 수 있는 위치 고정·설정 텍스트 버튼과
        # 보편적인 닫기 아이콘만 둔다.
        self.btn_close = self._make_retro_icon_button(self.header_frame, "close", self.quit_app, hint="닫기")
        self.btn_close.pack(side=tk.RIGHT, padx=(3, 5), pady=11)
        self.btn_setting = self._make_header_text_button(
            self.header_frame, "⚙ 설정", self.open_settings_dialog
        )
        self.btn_setting.pack(side=tk.RIGHT, padx=2, pady=9)
        self.btn_position_lock = self._make_header_text_button(
            self.header_frame,
            "🔒 위치 고정됨" if self.is_position_locked else "📌 위치 고정",
            self.toggle_position_lock
        )
        self._refresh_position_lock_button()
        self.btn_position_lock.pack(side=tk.RIGHT, padx=2, pady=9)
        self.sub_spacer = tk.Label(self.header_frame, text="", bg=COLORS["navy"], cursor="fleur")
        self.sub_spacer.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        # 본문은 항상 표시한다.
        self.body_frame = tk.Frame(board, bg=COLORS["background"])
        self.body_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        # 2. 날짜 탐색: 작은 3D 버튼과 움푹 들어간 날짜 칸
        self.date_frame = tk.Frame(self.body_frame, bg=COLORS["background"], height=42)
        self.date_frame.pack(fill=tk.X, side=tk.TOP, pady=(0, self._base_card_gap))
        self.date_frame.pack_propagate(False)
        self.date_prev_button = retro_button(self.date_frame, "◀", width=3, font_size=10, padx=6, pady=4)
        self.date_prev_button.pack(side=tk.LEFT, padx=(2, 7), pady=3)
        self.date_prev_button.bind("<Button-1>", lambda e: self.change_day(-1))
        self.date_label = tk.Label(
            self.date_frame, text="날짜 로딩...", font=(UI_STRONG_FONT, 15, "bold"),
            fg=COLORS["text"], bg=COLORS["surface"], cursor="hand2", relief=tk.SUNKEN, bd=2, pady=4
        )
        self.date_label.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=3)
        self.date_label.bind("<Button-1>", lambda e: self.open_calendar_picker())
        # 날짜 칸 오른쪽 클릭은 숨긴 '오늘' 버튼과 같은 동작이다. 한 번
        # 클릭해서 여는 달력 팝업과 충돌하지 않도록 더블클릭은 쓰지 않는다.
        self.date_label.bind("<Button-3>", lambda e: self.go_today())
        self.date_next_button = retro_button(self.date_frame, "▶", width=3, font_size=10, padx=6, pady=4)
        self.date_next_button.pack(side=tk.RIGHT, padx=(7, 2), pady=3)
        self.date_next_button.bind("<Button-1>", lambda e: self.change_day(1))

        # 3. 민트색 출발 카드: 시각과 순서를 크게, 전체 반 순서는 얇게 유지한다.
        # 최소 크기에서도 큰 시간·순서 숫자가 잘리지 않도록 내부 여유를 둔다.
        self.order_card_base_height = self._base_order_card_height
        self.order_queue_extra_height = 16
        self.order_card_canvas = tk.Canvas(
            self.body_frame, bg=COLORS["background"], height=self.order_card_base_height, highlightthickness=0
        )
        self.order_card_canvas.pack(fill=tk.X, side=tk.TOP, pady=(0, 7))
        self.order_banner_frame = tk.Frame(self.order_card_canvas, bg=COLORS["soft_mint"])
        self.order_card_window = self.order_card_canvas.create_window(4, 4, anchor="nw", window=self.order_banner_frame)
        self.order_card_canvas.bind("<Configure>", self._resize_order_card)
        self.order_banner_frame.pack_propagate(False)
        # 남은 시간은 계속 계산하지만, 시안처럼 카드에는 핵심 숫자만 둔다.
        self.departure_state_label = tk.Label(
            self.order_banner_frame, text="순서 확인 중", font=(UI_FONT, 7, "bold"),
            fg=COLORS["mint_dark"], bg=COLORS["soft_mint"]
        )
        order_info = tk.Frame(
            self.order_banner_frame, bg=COLORS["soft_mint"], height=self._base_order_info_height
        )
        order_info.pack(fill=tk.X, padx=8, pady=(4, 0))
        order_info.pack_propagate(False)
        self.order_info = order_info

        time_group = tk.Frame(order_info, bg=COLORS["soft_mint"])
        time_group.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.time_group = time_group
        clock = tk.Canvas(time_group, width=44, height=44, bg=COLORS["soft_mint"], highlightthickness=0)
        clock.pack(side=tk.LEFT, padx=(2, 7), pady=5)
        self.clock_icon = clock
        # 목표의 계단형 픽셀 시계: 부드러운 원보다 고전 프로그램 감성이 강하다.
        clock.create_polygon(12, 3, 32, 3, 41, 12, 41, 32, 32, 41, 12, 41, 3, 32, 3, 12,
                             fill="#FFFCE5", outline=COLORS["navy"], width=2)
        for x1, y1, x2, y2 in ((22, 7, 22, 10), (22, 34, 22, 37), (7, 22, 10, 22), (34, 22, 37, 22)):
            clock.create_line(x1, y1, x2, y2, fill=COLORS["navy"], width=1)
        clock.create_line(22, 10, 22, 22, fill=COLORS["navy"], width=2)
        clock.create_line(22, 22, 30, 27, fill=COLORS["navy"], width=2)
        clock.create_rectangle(20, 20, 24, 24, fill=COLORS["navy"], outline="")
        time_words = tk.Frame(time_group, bg=COLORS["soft_mint"])
        time_words.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(1, 0))
        self.time_words = time_words
        tk.Label(
            time_words, text="출발 시간", font=(UI_STRONG_FONT, 12, "bold"),
            fg=COLORS["text"], bg=COLORS["soft_mint"], anchor="w"
        ).pack(anchor="w")
        time_font = (UI_STRONG_FONT, 24, "bold")
        self.departure_time_value = tk.Label(
            time_words, text="-- : --", font=time_font, fg=COLORS["text"], bg=COLORS["tip"],
            relief=tk.SUNKEN, bd=2, padx=8, pady=0, anchor="center"
        )
        self.departure_time_value.pack(anchor="w", pady=(1, 0))

        divider = tk.Canvas(order_info, width=3, bg=COLORS["soft_mint"], highlightthickness=0)
        divider.create_line(
            1, 3, 1, self._base_order_info_height - 3,
            fill="#9AA3A6", dash=(2, 2)
        )
        divider.pack(side=tk.LEFT, fill=tk.Y, padx=7, pady=2)
        self.order_divider = divider
        rank_group = tk.Frame(order_info, bg=COLORS["soft_mint"])
        rank_group.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.rank_group = rank_group
        people = tk.Canvas(rank_group, width=44, height=44, bg=COLORS["soft_mint"], highlightthickness=0)
        people.pack(side=tk.LEFT, padx=(1, 6), pady=5)
        self.people_icon = people
        # 목표처럼 회색 기운의 세 사람 아이콘을 작은 윤곽으로 그린다.
        for x, y in ((9, 13), (22, 9), (35, 13)):
            people.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#C5C7D8", outline="#28305D", width=1)
            people.create_rectangle(x - 6, y + 5, x + 6, y + 16, fill="#C5C7D8", outline="#28305D", width=1)
        rank_words = tk.Frame(rank_group, bg=COLORS["soft_mint"])
        rank_words.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(1, 0))
        self.rank_words = rank_words
        tk.Label(
            rank_words, text="급식 대기 순서", font=(UI_STRONG_FONT, 12, "bold"),
            fg=COLORS["text"], bg=COLORS["soft_mint"], anchor="w"
        ).pack(anchor="w")
        rank_font = (UI_STRONG_FONT, 24, "bold")
        self.departure_order_value = tk.Label(
            rank_words, text="-- 번", font=rank_font, fg=COLORS["text"], bg=COLORS["tip"],
            relief=tk.SUNKEN, bd=2, padx=10, pady=0, anchor="center"
        )
        self.departure_order_value.pack(anchor="w", pady=(1, 0))
        self.order_queue_text = tk.Text(
            self.order_banner_frame, bg=COLORS["soft_mint"], bd=0, height=1,
            padx=10, pady=0, wrap=tk.WORD, cursor="arrow", highlightthickness=0
        )
        self.order_queue_text.pack(fill=tk.X, pady=(4, 2))
        self.order_queue_text.bind("<MouseWheel>", lambda e: "break")
        self.order_queue_text.tag_configure("normal", font=(UI_FONT, 9), foreground="#397A69")
        self.order_queue_text.tag_configure("my_class", font=(UI_FONT, 9, "bold"), foreground=COLORS["navy"])
        self.order_queue_text.tag_configure("arrow", font=(UI_FONT, 8), foreground="#76B9A7")
        self.order_queue_text.tag_configure("center", justify="center")
        self.order_queue_text.config(state=tk.DISABLED)

        # 4. 아래 상태줄: 중복되는 날짜·시계 대신 오류·개선 의견 창구만 둔다.
        self.message_frame = tk.Frame(
            self.body_frame, bg=COLORS["button"], highlightbackground=COLORS["border"], highlightthickness=1
        )
        self.message_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 0))
        self.footer_feedback_label = tk.Label(
            self.message_frame, text="", font=(UI_FONT, 9, "bold"),
            fg=COLORS["muted"], bg=COLORS["button"], padx=7, pady=3, anchor="center"
        )
        self.footer_feedback_label.pack(fill=tk.X)

        # 5. 노란 영양 메모: 최소 크기에서 두 줄 본문까지 안정적으로 보인다.
        self.knowledge_frame = tk.Frame(
            self.body_frame, bg=COLORS["tip"], highlightbackground=COLORS["border"], highlightthickness=2,
            height=self._base_knowledge_height
        )
        self.knowledge_frame.pack(
            fill=tk.X, side=tk.BOTTOM, pady=(self._base_card_gap, 6)
        )
        self.knowledge_frame.pack_propagate(False)
        self.knowledge_mascot = tk.Canvas(
            self.knowledge_frame, width=48, height=52, bg=COLORS["tip"], highlightthickness=0
        )
        self.knowledge_mascot.pack(side=tk.LEFT, padx=(8, 3), pady=4)
        self.knowledge_divider = tk.Canvas(
            self.knowledge_frame, width=5, height=52, bg=COLORS["tip"], highlightthickness=0
        )
        self.knowledge_divider.create_line(2, 3, 2, 49, fill="#A9A9A9", dash=(2, 2))
        self.knowledge_divider.pack(side=tk.LEFT, padx=(0, 4), pady=4)
        # 목표 카드처럼 더 크고 둥근 밥그릇. 글꼴/이모지에 의존하지 않아
        # 어떤 교실 PC에서도 같은 아이콘으로 보인다.
        self.knowledge_mascot.create_oval(7, 6, 39, 24, fill="#FFFDF2", outline=COLORS["navy"], width=2)
        for x, y in ((14, 11), (21, 8), (28, 11), (18, 15), (25, 15), (32, 17)):
            self.knowledge_mascot.create_oval(x, y, x + 3, y + 3, fill="#FFFFFF", outline="#A0A0A0", width=1)
        self.knowledge_mascot.create_polygon(5, 21, 41, 21, 35, 40, 11, 40,
                                              fill="#7CB7F1", outline=COLORS["navy"], width=2)
        self.knowledge_mascot.create_line(11, 29, 35, 29, fill="#D6E6FF", width=2)
        self.knowledge_mascot.create_line(13, 34, 33, 34, fill="#D6E6FF", width=2)
        memo_body = tk.Frame(self.knowledge_frame, bg=COLORS["tip"])
        memo_body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 10), pady=4)
        self.memo_body = memo_body
        self.quest_category_label = tk.Label(
            memo_body, text="오늘의 가정 지식 한 스푼", font=(UI_STRONG_FONT, 17, "bold"),
            fg=COLORS["text"], bg=COLORS["tip"], anchor="w"
        )
        self.quest_category_label.pack(fill=tk.X)
        self.quest_question_label = tk.Label(
            memo_body, text="오늘의 메뉴를 불러오는 중이에요.",
            font=(UI_FONT, 12), fg=COLORS["text"], bg=COLORS["tip"],
            justify=tk.LEFT, anchor="w", wraplength=425
        )
        self.quest_question_label.pack(fill=tk.X, pady=(1, 1))

        # 6. 흰색 목록 창: 식단과 알레르기 정보의 가독성을 우선한다.
        self.menu_card_canvas = tk.Canvas(self.body_frame, bg=COLORS["background"], highlightthickness=0)
        self.menu_card_canvas.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
        self.content_frame = tk.Frame(self.menu_card_canvas, bg=COLORS["surface"])
        self.menu_card_window = self.menu_card_canvas.create_window(4, 4, anchor="nw", window=self.content_frame)
        self.menu_card_canvas.bind("<Configure>", self._resize_menu_card)
        meal_title_row = tk.Frame(self.content_frame, bg=COLORS["soft_lilac"], height=42)
        meal_title_row.pack(fill=tk.X)
        meal_title_row.pack_propagate(False)
        self.meal_title_row = meal_title_row
        menu_icon = tk.Canvas(meal_title_row, width=25, height=26, bg=COLORS["soft_lilac"], highlightthickness=0)
        self.menu_icon = menu_icon
        # 포크와 나이프를 픽셀 선으로 그려 어떤 PC에서도 같은 모양을 유지한다.
        menu_icon.create_rectangle(4, 4, 6, 12, fill=COLORS["text"], outline="")
        menu_icon.create_rectangle(8, 4, 10, 12, fill=COLORS["text"], outline="")
        menu_icon.create_rectangle(12, 4, 14, 12, fill=COLORS["text"], outline="")
        menu_icon.create_rectangle(4, 11, 14, 14, fill=COLORS["text"], outline="")
        menu_icon.create_rectangle(8, 13, 10, 23, fill=COLORS["text"], outline="")
        menu_icon.create_rectangle(19, 4, 22, 18, fill=COLORS["text"], outline="")
        menu_icon.create_rectangle(18, 4, 22, 7, fill=COLORS["text"], outline="")
        menu_icon.create_rectangle(19, 17, 21, 23, fill=COLORS["text"], outline="")
        menu_icon.pack(side=tk.LEFT, padx=(10, 7), pady=7)
        self.menu_title_label = tk.Label(
            meal_title_row, text="오늘의 급식 메뉴", font=(UI_STRONG_FONT, 17, "bold"),
            fg=COLORS["text"], bg=COLORS["soft_lilac"]
        )
        self.menu_title_label.pack(side=tk.LEFT, pady=7)
        # 다른 날짜를 볼 때만 나타나, 터치 칠판에서도 바로 오늘로 돌아온다.
        self.today_button = retro_button(
            meal_title_row, "오늘로", bg=COLORS["yellow"], font_size=10, padx=8, pady=2
        )
        self.today_button.bind("<Button-1>", lambda e: self.go_today())
        # 열량과 탄단지 비율은 수치만 작게 보여 주고 평가는 붙이지 않는다.
        self.calorie_label = tk.Label(
            meal_title_row, text="", font=(UI_FONT, 9, "bold"), justify=tk.RIGHT,
            fg=COLORS["muted"], bg=COLORS["soft_lilac"]
        )
        self.calorie_label.pack(side=tk.RIGHT, padx=(4, 10))
        self.menu_header_line = tk.Frame(self.content_frame, height=2, bg=COLORS["border"])
        self.menu_header_line.pack(fill=tk.X)
        # 한 줄짜리 Text에 메뉴와 알레르기를 이어 쓰면 길이에 따라 서로
        # 밀리므로, 각 메뉴를 독립된 행으로 만든다. 오른쪽 알레르기 태그는
        # 메뉴명과 겹치지 않고 줄바꿈될 수 있다.
        self.menu_rows_frame = tk.Frame(self.content_frame, bg=COLORS["surface"])
        self.menu_rows_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(3, 2))
        self.menu_rows_frame.bind("<Configure>", lambda event: self._schedule_menu_row_layout())

        # 창 테두리가 없어도 오른쪽 아래에서 크기를 조절할 수 있다.
        self.resize_grip = tk.Label(
            self.main_container, text="↘", font=("Arial", 10, "bold"),
            fg=COLORS["muted"], bg=COLORS["background"], cursor="size_nw_se", padx=3, pady=1
        )
        self.resize_grip.place(relx=1.0, rely=1.0, anchor="se")
        self.resize_grip.bind("<Button-1>", self.start_resize)
        self.resize_grip.bind("<B1-Motion>", self.do_resize)

    def _refresh_footer_status(self):
        """하단 줄에 저장된 메뉴 안내 또는 개발자 문의처를 보여 준다."""
        if not hasattr(self, "footer_feedback_label"):
            return
        if self._showing_cached_meal:
            text = "저장된 메뉴 표시 중 · 인터넷 연결을 확인해 주세요"
        else:
            text = f"오류·개선 의견 → {FEEDBACK_CONTACT}"
        self.footer_feedback_label.config(text=text)

    def start_drag(self, event):
        if self.is_position_locked:
            return "break"
        self.drag_x = event.x
        self.drag_y = event.y

    def do_drag(self, event):
        if self.is_position_locked:
            return "break"
        x = self.root.winfo_x() + (event.x - self.drag_x)
        y = self.root.winfo_y() + (event.y - self.drag_y)
        self.root.geometry(f"+{x}+{y}")

    def end_drag(self, event):
        # 움직이는 동안이 아니라 손을 뗐을 때 한 번만 저장한다.
        if not self.is_position_locked:
            self.save_config()

    def toggle_position_lock(self):
        """창의 위치만 잠그며, 다른 창 위에 띄우는 기능은 사용하지 않는다."""
        self.is_position_locked = not self.is_position_locked
        self._refresh_position_lock_button()
        self.save_config()

    def set_widget_opacity(self, opacity):
        """70~100% 범위에서 위젯 본문 투명도를 즉시 적용한다."""
        try:
            opacity = float(opacity)
        except (TypeError, ValueError):
            return
        self.widget_opacity = max(0.70, min(1.0, opacity))
        try:
            self.root.attributes("-alpha", self.widget_opacity)
        except tk.TclError:
            pass

    def set_start_with_windows(self, enabled):
        """사용자 선택에 따라 최종 EXE의 로그인 자동 실행을 동기화한다."""
        previous_value = self.start_with_windows
        self.start_with_windows = bool(enabled)
        if not self.sync_startup_registration():
            self.start_with_windows = previous_value
            return False
        self.save_config()
        return True

    @staticmethod
    def _startup_command():
        return f'"{os.path.abspath(sys.executable)}"'

    @staticmethod
    def _read_run_value():
        """Windows 자동 실행에 등록된 명령을 읽는다. 없으면 None."""
        if winreg is None:
            return None
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
                return winreg.QueryValueEx(key, RUN_VALUE_NAME)[0]
        except OSError:
            return None

    def get_startup_status(self):
        """설정 창에서 실제 Windows 자동 실행 등록 상태를 읽기 쉽게 표시한다."""
        if not IS_PACKAGED_APP:
            return "설치한 프로그램에서만 사용할 수 있습니다.", COLORS["muted"]
        if not self.start_with_windows:
            return "꺼짐 · Windows를 켜도 자동으로 열리지 않습니다.", COLORS["muted"]
        if self._read_run_value() != self._startup_command():
            return "등록 오류 · ‘다시 등록’을 눌러 주세요.", COLORS["coral"]
        return "등록됨 · 로그인하면 설정 그대로 열립니다.", COLORS["mint_dark"]

    # 📅 미니 달력 팝업 (날짜 직접 선택)
    def open_calendar_picker(self):
        cal_win = tk.Toplevel(self.root)
        cal_win.title("날짜 선택")
        cal_win.overrideredirect(True)  # 테두리 없는 깔끔한 팝업
        cal_win.transient(self.root)
        cal_win.config(bg=COLORS["navy"])

        # 위치 계산 (위젯 상단 날짜바 바로 아래, 여백 없는 최적 높이 220px)
        wx = self.root.winfo_x() + 35
        wy = self.root.winfo_y() + 85
        cal_win.geometry(f"330x220+{wx}+{wy}")

        def raise_above_widget():
            """달력은 부모 위젯 앞에만 올리고, OS 전체 최상단은 사용하지 않는다."""
            try:
                if cal_win.winfo_exists():
                    cal_win.lift(self.root)
                    cal_win.focus_force()
            except tk.TclError:
                pass

        # overrideredirect 팝업은 Windows에서 transient만으로는 부모 창보다
        # 아래에 배치될 수 있어, 창이 화면에 올라온 직후 한 번만 올린다.
        cal_win.after_idle(raise_above_widget)

        # 외곽 테두리
        border = tk.Frame(cal_win, bg=COLORS["navy"], bd=1)
        border.pack(fill=tk.BOTH, expand=True)
        container = tk.Frame(border, bg=COLORS["surface"], padx=8, pady=2)
        container.pack(fill=tk.BOTH, expand=True)

        # 조회할 년/월 상태
        picker_year = self.current_date.year
        picker_month = self.current_date.month

        # 상단 네비게이션 헤더
        nav_header = tk.Frame(container, bg=COLORS["surface"])
        nav_header.pack(fill=tk.X, pady=(2, 8))

        lbl_year_month = tk.Label(nav_header, text="", font=(UI_FONT, 12, "bold"), fg=COLORS["navy"], bg=COLORS["surface"])

        def prev_month():
            nonlocal picker_year, picker_month
            picker_month -= 1
            if picker_month < 1:
                picker_month = 12
                picker_year -= 1
            draw_calendar()

        def next_month():
            nonlocal picker_year, picker_month
            picker_month += 1
            if picker_month > 12:
                picker_month = 1
                picker_year += 1
            draw_calendar()

        btn_prev_m = tk.Label(nav_header, text=" ‹ ", font=(UI_FONT, 12, "bold"), fg=COLORS["navy"], bg=COLORS["soft_mint"], cursor="hand2", padx=4, pady=2)
        btn_prev_m.pack(side=tk.LEFT)
        btn_prev_m.bind("<Button-1>", lambda e: prev_month())

        lbl_year_month.pack(side=tk.LEFT, expand=True)

        btn_close_c = tk.Label(nav_header, text=" × ", font=("Arial", 13, "bold"), fg=COLORS["muted"], bg=COLORS["surface"], cursor="hand2")
        btn_close_c.pack(side=tk.RIGHT)
        btn_close_c.bind("<Button-1>", lambda e: cal_win.destroy())

        btn_next_m = tk.Label(nav_header, text=" › ", font=(UI_FONT, 12, "bold"), fg=COLORS["navy"], bg=COLORS["soft_mint"], cursor="hand2", padx=4, pady=2)
        btn_next_m.pack(side=tk.RIGHT, padx=4)
        btn_next_m.bind("<Button-1>", lambda e: next_month())

        # 요일 헤더
        days_header = tk.Frame(container, bg=COLORS["surface"])
        days_header.pack(fill=tk.X, pady=(0, 4))
        day_names = ["일", "월", "화", "수", "목", "금", "토"]
        for idx, d in enumerate(day_names):
            color = COLORS["coral"] if idx == 0 else ("#4C7FD9" if idx == 6 else COLORS["muted"])
            lbl_d = tk.Label(days_header, text=d, font=(UI_FONT, 10, "bold"), fg=color, bg=COLORS["surface"], width=4)
            lbl_d.pack(side=tk.LEFT, expand=True)

        # 날짜 그리드 프레임
        grid_frame = tk.Frame(container, bg=COLORS["surface"])
        grid_frame.pack(fill=tk.BOTH, expand=True)

        def select_date(y, m, d):
            self.current_date = datetime(y, m, d)
            self._prepare_date_change()
            cal_win.destroy()
            self.load_all_data_async()

        def draw_calendar():
            lbl_year_month.config(text=f"{picker_year}년 {picker_month}월")
            for w in grid_frame.winfo_children():
                w.destroy()

            # 월의 달력 매트릭스 (일요일 시작)
            cal = calendar.Calendar(firstweekday=6)
            month_days = cal.monthdayscalendar(picker_year, picker_month)

            today = datetime.now()

            for week_row in month_days:
                row_f = tk.Frame(grid_frame, bg=COLORS["surface"])
                row_f.pack(fill=tk.X, pady=0)
                for c_idx, day in enumerate(week_row):
                    if day == 0:
                        lbl_empty = tk.Label(row_f, text="", bg=COLORS["surface"], width=4, pady=2)
                        lbl_empty.pack(side=tk.LEFT, expand=True)
                    else:
                        is_today = (picker_year == today.year and picker_month == today.month and day == today.day)
                        is_selected = (picker_year == self.current_date.year and picker_month == self.current_date.month and day == self.current_date.day)

                        fg_col = COLORS["text"]
                        if is_selected:
                            bg_col = COLORS["mint"]
                            fg_col = COLORS["navy"]
                        elif is_today:
                            bg_col = COLORS["soft_coral"]
                            fg_col = COLORS["coral"]
                        elif c_idx == 0:
                            bg_col = COLORS["background"]
                            fg_col = COLORS["coral"]
                        elif c_idx == 6:
                            bg_col = COLORS["background"]
                            fg_col = "#4C7FD9"
                        else:
                            bg_col = COLORS["background"]

                        btn_d = tk.Label(
                            row_f, 
                            text=str(day), 
                            font=(UI_FONT, 10, "bold" if (is_selected or is_today) else "normal"),
                            fg=fg_col, 
                            bg=bg_col, 
                            cursor="hand2", 
                            width=4, 
                            pady=2
                        )
                        btn_d.pack(side=tk.LEFT, expand=True, padx=1, pady=1)
                        btn_d.bind("<Button-1>", lambda e, y=picker_year, m=picker_month, d=day: select_date(y, m, d))

        draw_calendar()

    def change_class_setting(self):
        grade = simpledialog.askinteger("우리 반 설정", "학년을 입력하세요 (1~12):", parent=self.root, minvalue=1, maxvalue=12, initialvalue=self.my_grade)
        if grade is None:
            return
        cls = simpledialog.askinteger("우리 반 설정", "반을 입력하세요 (1~60반):", parent=self.root, minvalue=1, maxvalue=60, initialvalue=self.my_class)
        if cls is None:
            return

        self.my_grade = grade
        self.my_class = cls
        self.save_config()
        self.render_order_queue()

    def prompt_initial_school_setup(self):
        """새 설치본에서만 학교별 급식 조회값을 안내한다."""
        if self.school_configured:
            return
        if messagebox.askyesno(
            "학교 기본 설정",
            "이 위젯은 어느 학교에서나 사용할 수 있어요.\n\n"
            "학교를 한 번 검색해 고르면 이 PC에 저장되어\n"
            "다음부터는 바로 급식을 불러옵니다.\n\n"
            "지금 설정할까요?",
            parent=self.root
        ):
            self.open_school_setting()

    def open_settings_dialog(self):
        """학교 정보와 반별 순서를 분리해 보여 주는 간단한 설정 창."""
        settings = tk.Toplevel(self.root)
        settings.title("급식 위젯 설정")
        settings.configure(bg=COLORS["background"])
        settings.resizable(False, False)
        settings.transient(self.root)
        settings.geometry(f"330x500+{self.root.winfo_x() + 75}+{self.root.winfo_y() + 85}")

        container = tk.Frame(settings, bg=COLORS["background"], padx=18, pady=16)
        container.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            container, text="급식 위젯 설정", font=(UI_FONT, 14, "bold"),
            fg=COLORS["navy"], bg=COLORS["background"]
        ).pack(anchor="w")
        tk.Label(
            container, text="학교·우리 반 순서·위젯 표시 방식을 조정하세요.",
            font=(UI_FONT, 9), fg=COLORS["muted"], bg=COLORS["background"]
        ).pack(anchor="w", pady=(3, 10))

        # 투명도는 설정에서만 조절해 상단 도구 모음을 단정하게 유지한다.
        opacity_var = tk.IntVar(value=round(self.widget_opacity * 100))
        opacity_card = tk.Frame(
            container, bg=COLORS["surface"], highlightbackground=COLORS["border"], highlightthickness=1
        )
        opacity_card.pack(fill=tk.X, pady=(0, 8))
        opacity_header = tk.Frame(opacity_card, bg=COLORS["surface"])
        opacity_header.pack(fill=tk.X, padx=10, pady=(6, 0))
        tk.Label(
            opacity_header, text="창 투명도", font=(UI_FONT, 10, "bold"),
            fg=COLORS["text"], bg=COLORS["surface"]
        ).pack(side=tk.LEFT)
        opacity_value_label = tk.Label(
            opacity_header, text=f"{opacity_var.get()}%", font=(UI_FONT, 9, "bold"),
            fg=COLORS["mint_dark"], bg=COLORS["surface"]
        )
        opacity_value_label.pack(side=tk.RIGHT)

        def preview_opacity(value):
            percent = max(70, min(100, int(float(value))))
            opacity_value_label.configure(text=f"{percent}%")
            self.set_widget_opacity(percent / 100)

        opacity_scale = tk.Scale(
            opacity_card, from_=70, to=100, orient=tk.HORIZONTAL,
            variable=opacity_var, showvalue=False, resolution=1, length=230,
            bg=COLORS["surface"], fg=COLORS["text"], activebackground=COLORS["yellow"],
            troughcolor="#C9D2CF", highlightthickness=0, bd=0, command=preview_opacity
        )
        opacity_scale.pack(fill=tk.X, padx=8, pady=(0, 4))

        # 자동 실행은 각 교실 PC에서 필요할 때만 선택한다. 체크를 바꾸는 즉시
        # Windows 시작프로그램 등록에 반영하고, 실제 등록 상태도 함께 보여 준다.
        startup_var = tk.BooleanVar(value=self.start_with_windows)
        startup_card = tk.Frame(
            container, bg=COLORS["surface"], highlightbackground=COLORS["border"], highlightthickness=1
        )
        startup_card.pack(fill=tk.X, pady=(0, 8))
        startup_header = tk.Frame(startup_card, bg=COLORS["surface"])
        startup_header.pack(fill=tk.X, padx=10, pady=(6, 0))
        tk.Label(
            startup_header, text="Windows 시작 시 자동 실행", font=(UI_FONT, 10, "bold"),
            fg=COLORS["text"], bg=COLORS["surface"]
        ).pack(side=tk.LEFT)
        startup_checkbox = tk.Checkbutton(
            startup_header, text="사용", variable=startup_var, onvalue=True, offvalue=False,
            font=(UI_FONT, 9, "bold"), fg=COLORS["navy"], bg=COLORS["surface"],
            activebackground=COLORS["surface"], selectcolor=COLORS["yellow"],
            highlightthickness=0, bd=0, cursor="hand2"
        )
        startup_checkbox.pack(side=tk.RIGHT)
        startup_status_label = tk.Label(
            startup_card, text="", font=(UI_FONT, 8, "bold"),
            fg=COLORS["muted"], bg=COLORS["surface"], anchor="w", wraplength=285,
        )
        startup_status_label.pack(fill=tk.X, padx=10, pady=(4, 0))
        startup_help_label = tk.Label(
            startup_card,
            text=(
                "켜면 로그인 뒤 저장한 설정 그대로 위젯을 표시합니다."
                if IS_PACKAGED_APP else "자동 실행은 설치한 프로그램에서 사용할 수 있습니다."
            ),
            font=(UI_FONT, 8), fg=COLORS["muted"], bg=COLORS["surface"], anchor="w"
        )
        startup_help_label.pack(fill=tk.X, padx=10, pady=(1, 4))

        def refresh_startup_status():
            status_text, status_color = self.get_startup_status()
            startup_status_label.configure(text=status_text, fg=status_color)

        def apply_startup_setting():
            if not self.set_start_with_windows(startup_var.get()):
                startup_var.set(self.start_with_windows)
                refresh_startup_status()
                messagebox.showwarning(
                    "자동 실행 설정",
                    "자동 실행을 등록하지 못했습니다.\n"
                    "잠시 뒤 ‘다시 등록’을 눌러 주세요.",
                    parent=settings,
                )
                return
            refresh_startup_status()

        def repair_startup_setting():
            if not startup_var.get():
                startup_var.set(True)
                apply_startup_setting()
                if not self.start_with_windows:
                    return
            else:
                if not self.sync_startup_registration():
                    refresh_startup_status()
                    messagebox.showwarning(
                        "자동 실행 설정",
                        "자동 실행을 다시 등록하지 못했습니다.\n"
                        "잠시 뒤 다시 시도해 주세요.",
                        parent=settings,
                    )
                    return
                self.save_config()
                refresh_startup_status()
            messagebox.showinfo(
                "자동 실행 설정",
                "자동 실행을 다시 등록했습니다.\n"
                "다음 Windows 로그인 때 위젯이 자동으로 열립니다.",
                parent=settings,
            )

        actions = tk.Frame(startup_card, bg=COLORS["surface"])
        actions.pack(fill=tk.X, padx=10, pady=(0, 7))
        action_state = tk.NORMAL if IS_PACKAGED_APP else tk.DISABLED
        repair_button = tk.Button(
            actions, text="다시 등록", command=repair_startup_setting,
            font=(UI_FONT, 8, "bold"), fg=COLORS["text"], bg=COLORS["button"],
            activebackground=COLORS["yellow"], relief=tk.RAISED, bd=1,
            cursor="hand2" if IS_PACKAGED_APP else "arrow", state=action_state,
        )
        repair_button.pack(side=tk.LEFT, fill=tk.X, expand=True)
        startup_checkbox.configure(command=apply_startup_setting)
        if not IS_PACKAGED_APP:
            # 개발용 소스를 직접 열어도 시작프로그램을 건드릴 수 없게 한다.
            startup_checkbox.configure(state=tk.DISABLED, cursor="arrow")
        refresh_startup_status()

        def close_settings():
            self.set_widget_opacity(opacity_var.get() / 100)
            self.save_config()
            settings.destroy()

        def open_school():
            close_settings()
            self.open_school_setting()

        def open_order():
            close_settings()
            self.open_order_config_dialog()

        def open_class():
            close_settings()
            self.change_class_setting()

        school_button = tk.Label(
            container, text="학교 정보 · 출발 시간 설정", font=(UI_FONT, 10, "bold"),
            fg=COLORS["text"], bg=COLORS["surface"], cursor="hand2", anchor="w",
            padx=12, pady=10, highlightbackground=COLORS["border"], highlightthickness=1
        )
        school_button.pack(fill=tk.X)
        school_button.bind("<Button-1>", lambda e: open_school())
        order_button = tk.Label(
            container, text="우리 반 순서 · 순환 설정", font=(UI_FONT, 10, "bold"),
            fg=COLORS["text"], bg=COLORS["surface"], cursor="hand2", anchor="w",
            padx=12, pady=10, highlightbackground=COLORS["border"], highlightthickness=1
        )
        order_button.pack(fill=tk.X, pady=(8, 0))
        order_button.bind("<Button-1>", lambda e: open_order())
        class_button = tk.Label(
            container, text=f"우리 반 설정 · 현재 {self.my_grade}학년 {self.my_class}반", font=(UI_FONT, 10, "bold"),
            fg=COLORS["text"], bg=COLORS["surface"], cursor="hand2", anchor="w",
            padx=12, pady=10, highlightbackground=COLORS["border"], highlightthickness=1
        )
        class_button.pack(fill=tk.X, pady=(8, 0))
        class_button.bind("<Button-1>", lambda e: open_class())

        update_text = f"업데이트 정보 · 현재 v{APP_VERSION}"
        update_button = tk.Label(
            container, text=update_text, font=(UI_FONT, 10, "bold"),
            fg=COLORS["text"], bg=COLORS["surface"], cursor="hand2", anchor="w",
            padx=12, pady=10, highlightbackground=COLORS["border"], highlightthickness=1
        )
        update_button.pack(fill=tk.X, pady=(8, 0))

        def restore_update_button():
            if update_button.winfo_exists():
                update_button.configure(text=update_text)

        def start_update_check(event=None):
            update_button.configure(text="업데이트 확인 중...")
            self.check_update_manually(settings, restore_update_button)

        update_button.bind("<Button-1>", start_update_check)
        close_button = tk.Label(
            container, text="닫기", font=(UI_FONT, 9), fg=COLORS["muted"],
            bg=COLORS["background"], cursor="hand2", padx=4, pady=8
        )
        close_button.pack(anchor="e", pady=(2, 0))
        close_button.bind("<Button-1>", lambda e: close_settings())
        settings.protocol("WM_DELETE_WINDOW", close_settings)

    def search_schools(self, name, office_code=""):
        """나이스에서 학교를 찾는다. 키 없는 호출은 최대 5개까지만 돌려준다."""
        params = {"Type": "json", "pIndex": 1, "pSize": 5, "SCHUL_NM": name}
        if office_code:
            params["ATPT_OFCDC_SC_CODE"] = office_code
        url = f"{NEIS_SCHOOL_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=6) as response:
            return core.parse_school_search(json.loads(response.read().decode("utf-8")))

    def _ask_school(self):
        """학교를 이름으로 검색해 고른다. (이름, 교육청 코드, 학교 코드) 또는 None."""
        dialog = tk.Toplevel(self.root)
        dialog.title("학교 찾기")
        dialog.configure(bg=COLORS["background"])
        dialog.resizable(False, False)
        dialog.transient(self.root)
        x = max(0, self.root.winfo_x() + 10)
        y = max(0, self.root.winfo_y() + 60)
        dialog.geometry(f"+{x}+{y}")

        result = {"school": None, "manual": False}
        schools = []
        region_labels = ["전국"] + [name for _, name in core.OFFICE_REGIONS]
        region_var = tk.StringVar(value="전국")
        name_var = tk.StringVar(value=self.school_name)

        body = tk.Frame(dialog, bg=COLORS["background"], padx=14, pady=12)
        body.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            body, text="학교 이름을 입력하고 검색하세요.", font=(UI_FONT, 11, "bold"),
            fg=COLORS["navy"], bg=COLORS["background"]
        ).pack(anchor="w")

        search_row = tk.Frame(body, bg=COLORS["background"])
        search_row.pack(fill=tk.X, pady=(8, 6))
        region_menu = tk.OptionMenu(search_row, region_var, *region_labels)
        region_menu.configure(font=(UI_FONT, 10), width=5, highlightthickness=0)
        region_menu.pack(side=tk.LEFT)
        name_entry = tk.Entry(search_row, textvariable=name_var, font=(UI_FONT, 12), width=18)
        name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6, ipady=3)

        listbox = tk.Listbox(
            body, font=(UI_FONT, 11), height=5, width=46, activestyle="none",
            selectbackground=COLORS["navy"], selectforeground="#FFFFFF"
        )
        listbox.pack(fill=tk.BOTH, expand=True)
        status = tk.Label(
            body, text="", font=(UI_FONT, 9), fg=COLORS["muted"], bg=COLORS["background"],
            anchor="w", justify=tk.LEFT, wraplength=420
        )
        status.pack(fill=tk.X, pady=(6, 6))

        def run_search(event=None):
            name = name_var.get().strip()
            if not name:
                status.config(text="학교 이름을 입력해 주세요.", fg=COLORS["coral"])
                return
            office = dict((label, code) for code, label in core.OFFICE_REGIONS).get(region_var.get(), "")
            status.config(text="찾는 중...", fg=COLORS["muted"])
            dialog.update_idletasks()
            listbox.delete(0, tk.END)
            schools.clear()
            try:
                found, total = self.search_schools(name, office)
            except Exception:
                log.warning("학교 검색 실패", exc_info=True)
                status.config(
                    text="검색하지 못했습니다. 인터넷을 확인하거나 ‘코드 직접 입력’을 눌러 주세요.",
                    fg=COLORS["coral"]
                )
                return
            schools.extend(found)
            for school in found:
                listbox.insert(tk.END, school["label"])
            if not found:
                status.config(text="찾는 학교가 없습니다. 이름을 다시 확인해 주세요.", fg=COLORS["coral"])
            elif total > len(found):
                status.config(
                    text=f"{total}개 중 {len(found)}개만 표시했습니다. 지역을 고르거나 이름을 더 자세히 입력해 주세요.",
                    fg=COLORS["mint_dark"]
                )
            else:
                status.config(text="학교를 고른 뒤 ‘선택’을 눌러 주세요.", fg=COLORS["mint_dark"])
                if len(found) == 1:
                    listbox.selection_set(0)

        def choose(event=None):
            selected = listbox.curselection()
            if not selected:
                status.config(text="목록에서 학교를 골라 주세요.", fg=COLORS["coral"])
                return
            result["school"] = schools[selected[0]]
            dialog.destroy()

        def use_manual():
            result["manual"] = True
            dialog.destroy()

        tk.Button(search_row, text="검색", font=(UI_FONT, 10, "bold"), command=run_search).pack(side=tk.LEFT)
        buttons = tk.Frame(body, bg=COLORS["background"])
        buttons.pack(fill=tk.X)
        tk.Button(buttons, text="코드 직접 입력", font=(UI_FONT, 9), command=use_manual).pack(side=tk.LEFT)
        tk.Button(buttons, text="취소", font=(UI_FONT, 10), command=dialog.destroy).pack(side=tk.RIGHT)
        tk.Button(buttons, text="선택", font=(UI_FONT, 10, "bold"), command=choose).pack(side=tk.RIGHT, padx=6)
        name_entry.bind("<Return>", run_search)
        listbox.bind("<Double-Button-1>", choose)

        dialog.lift()
        name_entry.focus_force()
        dialog.grab_set()
        self.root.wait_window(dialog)

        if result["manual"]:
            return self._ask_school_manual()
        school = result["school"]
        if school is None:
            return None
        return school["name"], school["office_code"], school["school_code"]

    def _ask_school_manual(self):
        """검색이 안 될 때를 위한 코드 직접 입력."""
        school_name = simpledialog.askstring(
            "학교 직접 입력 (1/3)", "학교 이름을 입력하세요.",
            parent=self.root, initialvalue=self.school_name
        )
        if school_name is None:
            return None
        school_name = school_name.strip()
        if not school_name:
            messagebox.showwarning("입력 오류", "학교 이름을 입력해 주세요.", parent=self.root)
            return None

        office_code = simpledialog.askstring(
            "학교 직접 입력 (2/3)",
            "교육청 코드를 입력하세요.\n예: 서울 B10 / 경기 J10 / 부산 C10",
            parent=self.root, initialvalue=self.office_code or DEFAULT_OFFICE_CODE
        )
        if office_code is None:
            return None
        office_code = office_code.strip().upper()
        if not re.fullmatch(r"[A-Z]\d{2}", office_code):
            messagebox.showwarning("입력 오류", "교육청 코드는 B10처럼 영문 1자와 숫자 2자리여야 합니다.", parent=self.root)
            return None

        school_code = simpledialog.askstring(
            "학교 직접 입력 (3/3)",
            "학교의 행정표준코드(SD_SCHUL_CODE)를 입력하세요.\n"
            "예: 7091433\n"
            "나이스 교육정보 개방포털의 ‘학교기본정보’에서 확인할 수 있어요.",
            parent=self.root, initialvalue=self.school_code
        )
        if school_code is None:
            return None
        school_code = school_code.strip()
        if not re.fullmatch(r"\d{5,10}", school_code):
            messagebox.showwarning("입력 오류", "행정표준코드는 숫자 5~10자리로 입력해 주세요.", parent=self.root)
            return None

        return school_name, office_code, school_code

    def open_school_setting(self):
        """학교와 급식실 출발 규칙을 정해 저장한다."""
        school = self._ask_school()
        if school is None:
            return
        school_name, office_code, school_code = school

        start_time = simpledialog.askstring(
            "출발 규칙 설정 (1/3)",
            "첫 번째 반의 급식실 출발 시각을 입력하세요.\n예: 12:30",
            parent=self.root, initialvalue=self.departure_start_time
        )
        if start_time is None:
            return
        start_time = start_time.strip()
        if not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", start_time):
            messagebox.showwarning("입력 오류", "시각은 12:30처럼 24시간 형식으로 입력해 주세요.", parent=self.root)
            return
        start_hour, start_minute = (int(value) for value in start_time.split(":"))
        start_time = f"{start_hour:02d}:{start_minute:02d}"

        interval = simpledialog.askinteger(
            "출발 규칙 설정 (2/3)",
            "몇 분 간격으로 다음 순서가 출발하나요?",
            parent=self.root, minvalue=1, maxvalue=60, initialvalue=self.departure_interval_minutes
        )
        if interval is None:
            return
        per_slot = simpledialog.askinteger(
            "출발 규칙 설정 (3/3)",
            "한 번에 출발하는 반 수를 입력하세요.\n예: 2반씩 함께 출발하면 2",
            parent=self.root, minvalue=1, maxvalue=20, initialvalue=self.classes_per_slot
        )
        if per_slot is None:
            return

        self.school_name = school_name
        self.office_code = office_code
        self.school_code = school_code
        self.departure_start_time = start_time
        self.departure_interval_minutes = interval
        self.classes_per_slot = per_slot
        self.school_configured = True
        self.school_label.config(text=self.school_name)
        self.root.title(f"{self.school_name} 급식 위젯")
        self.save_config()
        self._prepare_date_change()
        self.load_all_data_async()
        messagebox.showinfo("설정 완료", f"{self.school_name} 정보가 이 PC에 저장됐습니다.", parent=self.root)

    def open_order_config_dialog(self):
        g_key = str(self.my_grade)
        current_rule = self.order_rules.get(g_key, {
            "base_order": [str(i) for i in range(1, 12)],
            "cycle": "weekly",
            "base_date": "2026-09-01"
        })

        # 저장하면 입력한 순서가 '오늘의 순서'가 되고 오늘부터 다시 순환한다.
        # 그래서 오늘 실제 순서를 미리 채워, 내용을 바꾸지 않고 확인만 눌러도
        # 순서가 초기화되지 않게 한다.
        cur_order_str = ", ".join(self.compute_current_order(self.my_grade, datetime.now()))
        order_input = simpledialog.askstring(
            f"{self.my_grade}학년 순서 설정 (1/2)",
            f"{self.my_grade}학년의 오늘 급식실 출발 반 순서를 입력하세요.\n"
            "반 수와 관계없이 쉼표로 입력할 수 있어요.\n"
            "(예: 1, 2, 3, 4, 5, ..., 15)",
            parent=self.root,
            initialvalue=cur_order_str
        )
        if order_input is None:
            return

        # 같은 반을 두 번 입력해도 한 번만 넣는다.
        parsed_order = list(dict.fromkeys(re.findall(r'\d+', order_input)))
        if not parsed_order:
            messagebox.showwarning("입력 오류", "최소 1개 이상의 반 번호를 입력해야 합니다.")
            return

        cycle_choice = simpledialog.askinteger(
            f"{self.my_grade}학년 주기 설정 (2/2)",
            "어떤 주기로 급식 순서가 바뀌나요?\n\n"
            "1 : 매일 (평일마다 1반씩 순환, 주말 제외)\n"
            "2 : 일주일마다 (매주 월요일에 1반씩 순환)\n"
            "3 : 한 달마다 (매월 1일에 1반씩 순환)\n\n"
            "번호를 입력하세요 (1~3):",
            parent=self.root,
            minvalue=1,
            maxvalue=3,
            initialvalue=2 if current_rule.get("cycle") == "weekly" else (1 if current_rule.get("cycle") == "daily" else 3)
        )
        if cycle_choice is None:
            return

        cycle_map = {1: "daily", 2: "weekly", 3: "monthly"}
        selected_cycle = cycle_map.get(cycle_choice, "weekly")

        self.order_rules[g_key] = {
            "base_order": parsed_order,
            "cycle": selected_cycle,
            "day_basis": "weekday",
            "base_date": datetime.now().strftime("%Y-%m-%d")
        }
        self.save_config()
        self.render_order_queue()
        messagebox.showinfo("설정 완료", f"{self.my_grade}학년의 순서와 주기가 저장되었습니다!")

    def change_day(self, delta):
        self.current_date += timedelta(days=delta)
        self._prepare_date_change()
        self.load_all_data_async()

    def go_today(self):
        self.current_date = datetime.now()
        self._prepare_date_change()
        self.load_all_data_async()

    def _prepare_date_change(self):
        """새 날짜의 메뉴와 연결되는 한 줄 지식을 기다린다."""
        self.current_meal_data = None
        self.current_exploration = None
        self._last_date_change = datetime.now()
        if hasattr(self, "quest_question_label"):
            self.quest_question_label.config(text="메뉴를 불러오는 중이에요.")
        self._refresh_date_context()

    def _refresh_date_context(self):
        """제목과 '오늘로' 버튼을 지금 보고 있는 날짜에 맞춘다."""
        if not hasattr(self, "today_button"):
            return
        viewed = self.current_date
        if viewed.date() == datetime.now().date():
            self.menu_title_label.config(text="오늘의 급식 메뉴")
            self.today_button.pack_forget()
        else:
            self.menu_title_label.config(text=f"{viewed.month}월 {viewed.day}일 급식 메뉴")
            self.today_button.pack(
                side=tk.RIGHT, padx=(0, self._scaled_px(10)), before=self.calorie_label
            )

    def load_all_data_async(self):
        # 요청을 시작한 시점의 날짜를 고정한다. 사용자가 날짜를 빠르게
        # 넘기거나 학교를 바꿔도 늦게 도착한 이전 요청이 화면을 덮어쓰지 않게 한다.
        requested_date = self.current_date
        requested_school = (self.office_code, self.school_code)
        threading.Thread(
            target=self._fetch_all_worker,
            args=(requested_date, requested_school),
            daemon=True
        ).start()

    def _fetch_all_worker(self, requested_date, requested_school):
        ymd = requested_date.strftime("%Y%m%d")
        day_names = ["월", "화", "수", "목", "금", "토", "일"]
        date_display = (
            f"{requested_date.year}년 {requested_date.month:02d}월 "
            f"{requested_date.day:02d}일 ({day_names[requested_date.weekday()]})"
        )

        office_code, school_code = requested_school
        if not office_code or not school_code:
            self.root.after(
                0,
                lambda: self._update_ui_complete(
                    requested_date, date_display, None, requested_school,
                    school_not_configured=True
                )
            )
            return

        meal_data = None
        is_error = False
        try:
            rows = self._fetch_meal_rows(office_code, school_code, MLSV_YMD=ymd)
            meal_data = core.select_meal(rows)
        except Exception:
            log.warning("급식 조회 실패 (%s)", ymd, exc_info=True)
            is_error = True

        self.root.after(
            0,
            lambda: self._update_ui_complete(
                requested_date, date_display, meal_data, requested_school, is_error
            )
        )

        today = datetime.now().date()
        if not is_error and requested_date.date() == today and self._prefetched_on != today:
            self._prefetched_on = today
            self._prefetch_coming_meals(office_code, school_code, today)

    @staticmethod
    def _fetch_meal_rows(office_code, school_code, **dates):
        params = {
            "Type": "json", "pIndex": 1, "pSize": 5,
            "ATPT_OFCDC_SC_CODE": office_code, "SD_SCHUL_CODE": school_code, **dates,
        }
        url = f"{NEIS_API_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=4) as response:
            return core.api_rows(json.loads(response.read().decode("utf-8")), "mealServiceDietInfo")

    def _prefetch_coming_meals(self, office_code, school_code, today):
        """인터넷이 끊긴 날에도 보이도록 앞으로의 메뉴를 하루 한 번 저장해 둔다."""
        try:
            rows = self._fetch_meal_rows(
                office_code, school_code,
                MLSV_FROM_YMD=today.strftime("%Y%m%d"),
                MLSV_TO_YMD=(today + timedelta(days=7)).strftime("%Y%m%d"),
            )
            meals = core.meals_by_date(rows)
            self.root.after(0, lambda: self._remember_meals(office_code, school_code, meals))
        except Exception:
            log.info("앞으로의 메뉴 저장 실패", exc_info=True)

    @staticmethod
    def _load_meal_cache():
        try:
            with open(MEAL_CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
            return cache if isinstance(cache, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_meal_cache(self):
        try:
            self._write_config_file(MEAL_CACHE_PATH, self.meal_cache)
        except OSError:
            log.warning("메뉴 저장 실패", exc_info=True)

    def _remember_meals(self, office_code, school_code, meals):
        """날짜별 메뉴를 저장한다. 내용이 바뀐 경우에만 파일을 쓴다."""
        changed = False
        for ymd, meal in meals.items():
            if not meal:
                continue
            key = core.cache_key(office_code, school_code, ymd)
            entry = {
                "DDISH_NM": meal.get("DDISH_NM", ""),
                "CAL_INFO": meal.get("CAL_INFO", ""),
                "NTR_INFO": meal.get("NTR_INFO", ""),
            }
            if self.meal_cache.get(key) != entry:
                self.meal_cache[key] = entry
                changed = True
        if changed:
            self.meal_cache = core.prune_meal_cache(self.meal_cache)
            self._save_meal_cache()

    def _clear_menu_rows(self):
        """메뉴 카드 안의 동적 행만 비운다. 제목과 열량 표시는 유지한다."""
        self._close_allergy_popup()
        if self._menu_layout_job is not None:
            try:
                self.root.after_cancel(self._menu_layout_job)
            except tk.TclError:
                pass
            self._menu_layout_job = None
        for child in self.menu_rows_frame.winfo_children():
            child.destroy()
        self.menu_row_widgets = []
        self.menu_status_label = None

    def _close_allergy_popup(self):
        """메뉴가 바뀌거나 접힐 때 남아 있는 알레르기 팝오버를 닫는다."""
        popup = self._allergy_popup
        self._allergy_popup = None
        self._allergy_popup_source = None
        if popup is not None:
            try:
                popup.destroy()
            except tk.TclError:
                pass

    def _toggle_allergy_popup(self, event, dish_name, allergy_text):
        """짧은 경고 배지는 유지하고, 누르면 정확한 알레르기명을 보여 준다."""
        key = (dish_name, allergy_text)
        if self._allergy_popup is not None and self._allergy_popup_source == key:
            self._close_allergy_popup()
            return "break"

        self._close_allergy_popup()
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.configure(bg=COLORS["border"])
        popup.transient(self.root)

        panel = tk.Frame(popup, bg="#FFF8F8", padx=9, pady=7)
        panel.pack(padx=1, pady=1)
        tk.Label(
            panel, text="알레르기 정보", font=(UI_STRONG_FONT, 9, "bold"),
            fg=COLORS["coral"], bg="#FFF8F8", anchor="w"
        ).pack(fill=tk.X)
        detail = tk.Label(
            panel, text=f"{dish_name}\n{allergy_text}", font=(UI_FONT, 9),
            fg=COLORS["text"], bg="#FFF8F8", anchor="w", justify=tk.LEFT,
            wraplength=250
        )
        detail.pack(fill=tk.X, pady=(2, 0))
        for widget in (panel, detail):
            widget.bind("<Button-1>", lambda e: self._close_allergy_popup())

        popup.update_idletasks()
        popup_width, popup_height = popup.winfo_reqwidth(), popup.winfo_reqheight()
        x = event.widget.winfo_rootx()
        y = event.widget.winfo_rooty() + event.widget.winfo_height() + 4
        screen_w, screen_h = popup.winfo_screenwidth(), popup.winfo_screenheight()
        if x + popup_width > screen_w - 8:
            x = max(8, screen_w - popup_width - 8)
        if y + popup_height > screen_h - 8:
            y = max(8, event.widget.winfo_rooty() - popup_height - 4)
        popup.geometry(f"+{x}+{y}")

        def raise_above_widget():
            """팝오버는 부모 위젯 앞에만 한 번 올린다."""
            try:
                if popup.winfo_exists():
                    popup.lift(self.root)
                    popup.focus_force()
            except tk.TclError:
                pass

        # 테두리 없는 transient 창은 Windows에서 부모보다 뒤에 매핑될 수 있다.
        # OS 전체 최상단은 사용하지 않고, 화면에 뜬 직후 부모 위젯보다만 올린다.
        popup.after_idle(raise_above_widget)
        self._allergy_popup = popup
        self._allergy_popup_source = key
        return "break"

    def _show_menu_status(self, text):
        self.menu_status_label = tk.Label(
            self.menu_rows_frame, text=text, font=(UI_STRONG_FONT, 11, "bold"),
            fg=COLORS["muted"], bg=COLORS["surface"], justify=tk.CENTER,
            anchor="center", wraplength=max(230, self.width - 70)
        )
        self.menu_status_label.pack(fill=tk.BOTH, expand=True, padx=12, pady=18)

    def _add_menu_row(self, index, name, allergy_numbers, uncertain=False):
        """모든 메뉴를 같은 높이로 두고, 알레르기는 통일된 경고 배지로 표시한다."""
        marker_colors = ("#FF873C", "#FFCE35", "#3DBB70", "#FF5C69", "#A178F0", "#6AB8F2")
        row = tk.Frame(self.menu_rows_frame, bg=COLORS["surface"])
        row.pack(fill=tk.X, pady=(0, 0))
        row.grid_columnconfigure(1, weight=1)
        row.grid_columnconfigure(2, minsize=92)
        row.grid_rowconfigure(0, minsize=32)

        marker = tk.Frame(
            row, width=16, height=16, bg=marker_colors[index % len(marker_colors)],
            highlightbackground=COLORS["surface"], highlightthickness=0
        )
        marker.grid(row=0, column=0, sticky="w", padx=(2, 11), pady=1)
        marker.grid_propagate(False)

        dish = tk.Label(
            row, text=name, font=(UI_STRONG_FONT, 15, "bold"), fg=COLORS["text"],
            bg=COLORS["surface"], anchor="w", justify=tk.LEFT, bd=0, pady=0
        )
        dish.grid(row=0, column=1, sticky="ew", pady=1)

        allergy_text = core.format_allergies(allergy_numbers)
        badge_text = "알레르기 주의"
        if uncertain:
            # 번호를 해석하지 못해도 '알레르기 없음'처럼 보이지 않게 한다.
            badge_text = "표기 확인"
            allergy_text = "\n".join(filter(None, (
                allergy_text, "알레르기 표기를 해석하지 못했습니다. 메뉴 이름의 번호를 확인해 주세요."
            )))
        badge_border = None
        badge_label = None
        if allergy_text:
            # 외곽 Frame을 1px 코랄로 두면 일반 Tk 레이블에서도 목표의
            # 분홍 경고칩과 가까운 테두리를 안정적으로 만들 수 있다.
            badge_border = tk.Frame(row, bg=COLORS["coral"], cursor="hand2", padx=1, pady=1)
            badge_label = tk.Label(
                badge_border, text=badge_text, font=(UI_STRONG_FONT, 8, "bold"),
                fg=COLORS["coral"], bg=COLORS["soft_coral"], cursor="hand2", padx=5, pady=1
            )
            badge_label.pack()
            badge_border.grid(row=0, column=2, sticky="e", padx=(7, 2), pady=1)
            for widget in (badge_border, badge_label):
                widget.bind(
                    "<Button-1>",
                    lambda event, dish_name=name, details=allergy_text: self._toggle_allergy_popup(
                        event, dish_name, details
                    )
                )

        divider = tk.Canvas(self.menu_rows_frame, height=3, bg=COLORS["surface"], highlightthickness=0)
        divider.pack(fill=tk.X, padx=3, pady=(0, 0))

        def draw_divider(event, canvas=divider):
            canvas.delete("dots")
            canvas.create_line(0, 1, event.width, 1, fill="#D7D7D7", dash=(1, 2), tags="dots")

        divider.bind("<Configure>", draw_divider)
        self.menu_row_widgets.append({
            "row": row,
            "marker": marker,
            "dish": dish,
            "badge_border": badge_border,
            "badge_label": badge_label,
            "divider": divider,
        })

    def _schedule_menu_row_layout(self):
        if not hasattr(self, "menu_rows_frame"):
            return
        if self._menu_layout_job is not None:
            try:
                self.root.after_cancel(self._menu_layout_job)
            except tk.TclError:
                pass
        try:
            self._menu_layout_job = self.root.after_idle(self._layout_menu_rows)
        except tk.TclError:
            self._menu_layout_job = None

    def _layout_menu_rows(self):
        self._menu_layout_job = None
        if not hasattr(self, "menu_rows_frame"):
            return
        width = self.menu_rows_frame.winfo_width()
        if width < 100:
            # 숨겨진 창이 처음 화면에 나타날 때는 아직 폭이 1px일 수 있다.
            # 잠시 뒤 한 번 더 계산해 긴 알레르기 문구가 메뉴를 밀지 않게 한다.
            try:
                self._menu_layout_job = self.root.after(80, self._layout_menu_rows)
            except tk.TclError:
                self._menu_layout_job = None
            return

        if self.menu_status_label is not None:
            self.menu_status_label.configure(wraplength=max(230, width - 36))
            return

        # 7개 메뉴여도 모든 행은 하나의 동일한 grid 구조를 사용한다.
        for widgets in self.menu_row_widgets:
            badge_reserve = max(84, round(92 * getattr(self, "ui_scale", 1.0)))
            widgets["row"].grid_columnconfigure(1, weight=1, minsize=0)
            widgets["row"].grid_columnconfigure(2, minsize=badge_reserve)
            widgets["dish"].configure(wraplength=max(105, width - badge_reserve - 42))
        self._fit_menu_rows()

    def _fit_menu_rows(self):
        """스크롤 없이 모든 메뉴가 보이도록 글자와 행 높이를 맞춘다.

        칠판에서는 스크롤이 불편하므로, 공간이 충분하면 기본 크기를 쓰고
        메뉴가 많거나 이름이 길어 넘칠 때만 필요한 만큼 줄인다.
        """
        rows = self.menu_row_widgets
        if not rows:
            return
        available = self.menu_rows_frame.winfo_height()
        scale = getattr(self, "ui_scale", 1.0)
        preferred_font = max(11, round((16 if len(rows) <= 6 else 15) * scale))
        preferred_row = max(28, round((36 if len(rows) <= 6 else 32) * scale))
        row_padding = 2
        divider_height = 3

        def content_heights():
            heights = []
            for widgets in rows:
                height = widgets["dish"].winfo_reqheight()
                if widgets["badge_border"] is not None:
                    height = max(height, widgets["badge_border"].winfo_reqheight())
                heights.append(height + row_padding)
            return heights

        def total_height(row_height, heights):
            return sum(max(row_height, height) + divider_height for height in heights)

        heights = []
        for font_size in range(preferred_font, MIN_MENU_FONT_SIZE - 1, -1):
            for widgets in rows:
                widgets["dish"].configure(font=(UI_STRONG_FONT, font_size, "bold"))
            heights = content_heights()
            if total_height(0, heights) <= available:
                break

        row_height = preferred_row
        while row_height > 0 and total_height(row_height, heights) > available:
            row_height -= 1
        for widgets in rows:
            widgets["row"].grid_rowconfigure(0, minsize=row_height)

    def _apply_menu_row_scale(self):
        if not hasattr(self, "menu_rows_frame"):
            return
        scale = getattr(self, "ui_scale", 1.0)
        # 6개 이하 메뉴는 시안처럼 시원하게, 7개 이상일 때만 한 단계 압축한다.
        # 메뉴 이름 크기와 행 높이는 실제 공간을 재는 _fit_menu_rows가 정한다.
        marker_base = 18 if len(self.menu_row_widgets) <= 6 else 16
        allergy_size = max(8, round(8 * scale))
        marker_size = max(13, round(marker_base * scale))
        for widgets in self.menu_row_widgets:
            widgets["marker"].configure(width=marker_size, height=marker_size)
            if widgets["badge_label"] is not None:
                widgets["badge_label"].configure(
                    font=(UI_STRONG_FONT, allergy_size, "bold"),
                    padx=max(4, round(5 * scale)), pady=max(1, round(scale))
                )
        if self.menu_status_label is not None:
            self.menu_status_label.configure(font=(UI_STRONG_FONT, max(9, round(11 * scale)), "bold"))
        self._schedule_menu_row_layout()

    def _update_ui_complete(self, requested_date, date_display, meal_data, requested_school,
                            is_error=False, school_not_configured=False):
        # 비동기 조회가 끝나는 순서는 보장되지 않는다. 현재 선택 날짜가
        # 또는 학교가 바뀌었다면 이 응답은 이미 오래된 것이므로 화면에 반영하지 않는다.
        if (
            requested_date.date() != self.current_date.date()
            or requested_school != (self.office_code, self.school_code)
        ):
            return

        office_code, school_code = requested_school
        ymd = requested_date.strftime("%Y%m%d")
        key = core.cache_key(office_code, school_code, ymd)
        self._showing_cached_meal = False
        if is_error:
            # 조회 주기(30분)를 기다리지 않고 곧 다시 시도한다.
            self._auto_refresh_counter = max(
                self._auto_refresh_counter, 30 - FETCH_RETRY_MINUTES
            )
            # 이미 받아 둔 같은 날짜·학교의 메뉴가 있으면 그대로 보여 준다.
            if key in self.meal_cache:
                meal_data = self.meal_cache[key]
                is_error = False
                self._showing_cached_meal = True
        elif meal_data:
            self._remember_meals(office_code, school_code, {ymd: meal_data})
        elif not school_not_configured and self.meal_cache.pop(key, None):
            self._save_meal_cache()

        self.date_label.config(text=date_display)
        self._refresh_date_context()
        self._refresh_footer_status()
        self.current_meal_data = meal_data
        self.render_order_queue()
        self.update_exploration_card(meal_data)

        self._clear_menu_rows()
        self.calorie_label.config(text="")

        if school_not_configured:
            self._show_menu_status("설정에서 학교 정보를 입력해 주세요.")
        elif is_error:
            self._show_menu_status("인터넷 연결 또는 급식 서버 응답을 확인해 주세요.")
        elif not meal_data:
            # 주말(토/일)과 평일(미등록/방학) 구분 처리
            if self.current_date.weekday() >= 5:
                self._show_menu_status("주말(휴일)입니다")
            else:
                self._show_menu_status("아직 급식 식단이 등록되지 않았습니다")
        else:
            self.calorie_label.config(text=core.meal_summary(meal_data))

            menu_items = self._meal_items(meal_data)
            if menu_items:
                for index, (name, allergy_numbers, uncertain) in enumerate(menu_items):
                    self._add_menu_row(index, name, allergy_numbers, uncertain)
            else:
                self._show_menu_status("오늘 급식 메뉴가 비어 있습니다")

        self._apply_menu_row_scale()

    def _meal_items(self, meal_data):
        if not meal_data:
            return []
        items = []
        for raw_item in meal_data.get("DDISH_NM", "").split("<br/>"):
            item = core.parse_menu_item(raw_item)
            if item[0]:
                items.append(item)
        return items

    def _previous_cached_meal(self):
        """같은 지식이 연달아 나오지 않도록 직전 급식일의 저장된 식단을 찾는다."""
        for days_back in range(1, 4):  # 월요일이면 금요일까지 본다
            day = self.current_date - timedelta(days=days_back)
            key = core.cache_key(self.office_code, self.school_code, day.strftime("%Y%m%d"))
            if key in self.meal_cache:
                return self.meal_cache[key]
        return None

    def update_exploration_card(self, meal_data=None):
        """그날 식단과 연결되는 지식 한 줄을 보드에 표시한다."""
        text = core.select_knowledge(meal_data, self.current_date, self._previous_cached_meal())
        if self.current_date.date() != datetime.now().date():
            text = text.replace("오늘 급식", "이날 급식")
        self.current_exploration = text
        self.quest_category_label.config(text="오늘의 가정 지식 한 스푼")
        self.quest_question_label.config(text=text)

    def compute_current_order(self, grade, on_date=None):
        """on_date(기본: 화면에서 보는 날짜)의 반별 출발 순서를 돌려준다."""
        rule = self.order_rules.get(str(grade), {})
        ensure_class = self.my_class if int(grade) == self.my_grade else 0
        return core.rotate_order(rule, on_date or self.current_date, ensure_class)

    def render_order_queue(self):
        order_list = self.compute_current_order(self.my_grade)
        self._refresh_footer_status()

        self.order_queue_text.config(state=tk.NORMAL)
        self.order_queue_text.delete("1.0", tk.END)

        if not order_list:
            self.order_queue_text.config(height=1)
            self.order_card_canvas.config(height=self.order_card_base_height)
            self.departure_time_value.config(text="-- : --", fg=COLORS["navy"])
            self.departure_order_value.config(text="-- 번", fg=COLORS["navy"])
            self.departure_state_label.config(text="설정을 확인해 주세요", fg=COLORS["coral"])
            self.order_queue_text.config(state=tk.DISABLED)
            return

        total_classes = len(order_list)
        # 보통 11반까지는 작지만 한 줄인 보조정보로 유지한다. 12반 이상만
        # 카드 높이를 늘려 두 줄로 보여 준다.
        queue_lines = 2 if total_classes > 11 else 1
        self.order_queue_text.config(height=queue_lines)
        self.order_card_canvas.config(
            height=self.order_card_base_height + (queue_lines - 1) * self.order_queue_extra_height
        )
        my_rank = -1
        # 두 줄일 때는 Tk의 자동 줄바꿈에 맡기지 않고 중간에서 직접 나눠,
        # 마지막 반 하나만 다음 줄에 떨어지는 모습을 방지한다.
        split_after = (total_classes + 1) // 2 if queue_lines == 2 else None

        for idx, cls_num in enumerate(order_list):
            try:
                is_my = (int(cls_num) == self.my_class)
            except (TypeError, ValueError):
                is_my = False
            if is_my:
                my_rank = idx + 1
                # 우리 반은 잉크색과 다른 코랄색으로만 조용히 강조한다.
                self.order_queue_text.insert(tk.END, f"{cls_num}반", "my_class")
            else:
                self.order_queue_text.insert(tk.END, f"{cls_num}반", "normal")

            if idx < total_classes - 1:
                separator = "  →\n" if split_after is not None and idx + 1 == split_after else "  →  "
                self.order_queue_text.insert(tk.END, separator, "arrow")

        # 전체 텍스트 가운데 정렬
        self.order_queue_text.tag_add("center", "1.0", tk.END)
        self.order_queue_text.config(state=tk.DISABLED)

        # 학교별 설정값으로 출발 시간을 계산한다.
        if my_rank != -1:
            departure_total_minutes = core.departure_minutes(
                self.departure_start_time, my_rank,
                self.classes_per_slot, self.departure_interval_minutes
            )
            dep_hour, dep_minute = divmod(departure_total_minutes % (24 * 60), 60)
            dep_time = f"{dep_hour:02d} : {dep_minute:02d}"
            self.departure_time_value.config(text=dep_time, fg=COLORS["text"])
            self.departure_order_value.config(text=f"{my_rank} 번", fg=COLORS["text"])
            self._render_departure_status(departure_total_minutes)
        else:
            self.departure_time_value.config(text="-- : --", fg=COLORS["navy"])
            self.departure_order_value.config(text="순서 없음", fg=COLORS["navy"])
            self.departure_state_label.config(text="순서 설정을 확인해 주세요", fg=COLORS["coral"])

    def _render_departure_status(self, departure_total_minutes):
        """오늘일 때만 남은 시간에 따라 간단한 신호등 문구를 보여 준다."""
        now = datetime.now()
        if self.current_date.date() != now.date():
            self.departure_state_label.config(text="출발 예정", fg=COLORS["muted"])
            return

        departure = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            minutes=departure_total_minutes
        )
        remaining_minutes = (departure - now).total_seconds() / 60
        if remaining_minutes > 5:
            self.departure_state_label.config(
                text=f"약 {int(remaining_minutes) + 1}분 뒤", fg=COLORS["mint_dark"]
            )
        elif remaining_minutes >= -4:
            self.departure_state_label.config(text="지금 출발", fg=COLORS["coral"])
        else:
            self.departure_state_label.config(text="급식 시간", fg=COLORS["muted"])

    def load_config(self):
        screen_w = self.root.winfo_screenwidth()
        default_x = screen_w - self.base_width - 40
        default_y = 50
        config_path = self._prepare_config_path()

        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    if not isinstance(cfg, dict):
                        raise ValueError("설정 파일 형식이 올바르지 않습니다.")
                    x, y = cfg.get("x", default_x), cfg.get("y", default_y)
                    saved_width = cfg.get("width", self.width)
                    saved_height = cfg.get("height", self.expanded_height)
                    if isinstance(saved_width, (int, float)):
                        requested_scale = saved_width / self.base_width
                    elif isinstance(saved_height, (int, float)):
                        requested_scale = saved_height / self.base_height
                    else:
                        requested_scale = 1.0
                    self.width, self.expanded_height = self._dimensions_for_scale(requested_scale)
                    # 이전 버전에서 '고정'을 켰던 경우에는 같은 의도를
                    # 위치 잠금으로만 옮긴다. 항상 위(topmost)는 복원하지 않는다.
                    self.is_position_locked = bool(
                        cfg.get("position_locked", cfg.get("topmost", False))
                    )
                    saved_opacity = cfg.get("widget_opacity", self.widget_opacity)
                    if isinstance(saved_opacity, (int, float)) and not isinstance(saved_opacity, bool):
                        self.widget_opacity = max(0.70, min(1.0, float(saved_opacity)))
                    saved_startup = cfg.get("start_with_windows")
                    if isinstance(saved_startup, bool):
                        self.start_with_windows = saved_startup
                    else:
                        # 이전 버전에서 자동 실행을 사용한 PC는 사용 의도를
                        # 보존하되, 새 설치에서는 명시적으로 켜도록 기본값을 둔다.
                        self.start_with_windows = self._legacy_startup_launcher_exists()
                        self._startup_preference_migrated = IS_PACKAGED_APP
                    self.my_grade = cfg.get("my_grade", 2)
                    self.my_class = cfg.get("my_class", 3)
                    if isinstance(cfg.get("order_rules"), dict):
                        today = datetime.now()
                        self.order_rules = {
                            grade: core.modernize_daily_rule(rule, today)
                            for grade, rule in cfg["order_rules"].items()
                        }
                    if isinstance(cfg.get("quest_log"), dict):
                        self.quest_log = cfg["quest_log"]

                    school = cfg.get("school")
                    departure_rule = cfg.get("departure_rule")
                    if isinstance(school, dict):
                        self.school_name = str(school.get("name", "")).strip()
                        self.office_code = str(school.get("office_code", "")).strip().upper()
                        self.school_code = str(school.get("school_code", "")).strip()
                        self.school_configured = bool(
                            self.school_name
                            and re.fullmatch(r"[A-Z]\d{2}", self.office_code)
                            and re.fullmatch(r"\d{5,10}", self.school_code)
                        )
                    else:
                        # 기존 버전의 설정은 기본값으로 채우되, 한 번은 학교
                        # 정보를 확인하게 해 다른 학교로 복사해도 그대로 남지 않는다.
                        self.school_name = DEFAULT_SCHOOL_NAME
                        self.office_code = DEFAULT_OFFICE_CODE
                        self.school_code = DEFAULT_SCHOOL_CODE
                        self.school_configured = False

                    if isinstance(departure_rule, dict):
                        configured_time = str(departure_rule.get("start_time", ""))
                        if re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", configured_time):
                            hour, minute = (int(value) for value in configured_time.split(":"))
                            self.departure_start_time = f"{hour:02d}:{minute:02d}"
                        interval = departure_rule.get("interval_minutes", self.departure_interval_minutes)
                        per_slot = departure_rule.get("classes_per_slot", self.classes_per_slot)
                        if isinstance(interval, int) and 1 <= interval <= 60:
                            self.departure_interval_minutes = interval
                        if isinstance(per_slot, int) and 1 <= per_slot <= 20:
                            self.classes_per_slot = per_slot
                    if not (
                        isinstance(x, int) and isinstance(y, int)
                        and self._position_is_visible(x, y, self.width)
                    ):
                        # 해상도가 다른 PC에서는 저장된 위치가 화면 밖일 수 있다.
                        x, y = default_x, default_y
                    self.root.attributes("-topmost", False)
                    self.root.attributes("-alpha", self.widget_opacity)
                    self.root.geometry(f"{self.width}x{self.expanded_height}+{x}+{y}")
                    return
            except Exception:
                log.exception("설정 파일을 읽지 못했습니다")

        if IS_PACKAGED_APP and self._legacy_startup_launcher_exists():
            # 설정 파일이 없던 아주 오래된 설치본도 자동 실행 사용 의도를
            # 잃지 않도록, 새 설정 파일에 한 번만 기록한다.
            self.start_with_windows = True
            self._startup_preference_migrated = True

        self.width, self.expanded_height = self._dimensions_for_scale(1.0)
        self.root.geometry(f"{self.width}x{self.expanded_height}+{default_x}+{default_y}")

    @staticmethod
    def _position_is_visible(x, y, width):
        """창의 제목 막대가 연결된 모니터 안에 있는지 확인한다."""
        if os.name != "nt":
            return True
        try:
            rect = wintypes.RECT(x, y, x + width, y + 52)
            monitor_from_rect = ctypes.windll.user32.MonitorFromRect
            monitor_from_rect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
            monitor_from_rect.restype = wintypes.HMONITOR
            return bool(monitor_from_rect(ctypes.byref(rect), 0))  # 0: 겹치는 모니터 없으면 NULL
        except (OSError, AttributeError, ValueError):
            log.warning("화면 위치 확인 실패", exc_info=True)
            return True

    def _ensure_window_on_screen(self):
        """모니터 구성이 바뀌어 창이 화면 밖에 있으면 기본 위치로 옮긴다."""
        if self._position_is_visible(self.root.winfo_x(), self.root.winfo_y(), self.width):
            return
        x = self.root.winfo_screenwidth() - self.base_width - 40
        self.root.geometry(f"+{max(0, x)}+50")
        self.save_config()

    def _prepare_config_path(self):
        """배포본의 사용자 설정 폴더를 준비하고, 이전 설정을 한 번만 복사한다."""
        if not IS_PACKAGED_APP:
            return CONFIG_PATH

        try:
            os.makedirs(USER_CONFIG_DIR, exist_ok=True)
        except OSError:
            # 매우 제한된 PC에서는 예전처럼 EXE 옆 파일을 최후의 대안으로 쓴다.
            return LEGACY_CONFIG_PATH

        if (
            not os.path.exists(CONFIG_PATH)
            and os.path.isfile(LEGACY_CONFIG_PATH)
            and os.path.abspath(CONFIG_PATH) != os.path.abspath(LEGACY_CONFIG_PATH)
        ):
            try:
                # JSON 객체인지 먼저 확인한 뒤 새 폴더에 복사한다. 원본 파일은
                # 그대로 남겨 두어 기존 사용자의 백업 역할을 하게 한다.
                with open(LEGACY_CONFIG_PATH, "r", encoding="utf-8") as source:
                    legacy_config = json.load(source)
                if isinstance(legacy_config, dict):
                    self._write_config_file(CONFIG_PATH, legacy_config)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass

        return CONFIG_PATH

    @staticmethod
    def _write_config_file(config_path, config):
        """전원 종료 중에도 설정 파일이 반쯤 저장되지 않도록 임시 파일로 교체한다."""
        temp_path = f"{config_path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, config_path)

    def save_config(self):
        cfg = {
            "x": self.root.winfo_x(),
            "y": self.root.winfo_y(),
            "width": self.width,
            "height": self.expanded_height,
            "position_locked": self.is_position_locked,
            "widget_opacity": round(self.widget_opacity, 2),
            "start_with_windows": self.start_with_windows,
            "my_grade": self.my_grade,
            "my_class": self.my_class,
            "order_rules": self.order_rules,
            "school": {
                "name": self.school_name,
                "office_code": self.office_code,
                "school_code": self.school_code
            },
            "departure_rule": {
                "start_time": self.departure_start_time,
                "interval_minutes": self.departure_interval_minutes,
                "classes_per_slot": self.classes_per_slot
            },
            # 이전 버전의 기록을 보존하기 위한 로컬 데이터다.
            "quest_log": self.quest_log
        }
        try:
            config_path = self._prepare_config_path()
            self._write_config_file(config_path, cfg)
        except Exception:
            log.exception("설정 저장 실패")

    @staticmethod
    def _startup_folder():
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            return ""
        return os.path.join(
            appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
        )

    def _legacy_startup_launcher_exists(self):
        startup_folder = self._startup_folder()
        return bool(startup_folder) and os.path.isfile(
            os.path.join(startup_folder, LEGACY_STARTUP_SCRIPTS[0])
        )

    def _remove_legacy_startup_scripts(self):
        startup_folder = self._startup_folder()
        if not startup_folder:
            return
        for name in LEGACY_STARTUP_SCRIPTS:
            path = os.path.join(startup_folder, name)
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                log.warning("예전 자동 실행 파일을 지우지 못했습니다: %s", path)

    def sync_startup_registration(self):
        """설치된 프로그램만 Windows 로그인 자동 실행을 등록·해제한다.

        개발용 .pyw는 시작프로그램에 넣지 않는다. 스크립트 파일 대신 이
        사용자 전용 Run 항목을 써서 관리자 권한 없이 동작한다.
        """
        if not IS_PACKAGED_APP or winreg is None:
            return True
        self._remove_legacy_startup_scripts()
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
                if self.start_with_windows:
                    winreg.SetValueEx(
                        key, RUN_VALUE_NAME, 0, winreg.REG_SZ, self._startup_command()
                    )
                else:
                    try:
                        winreg.DeleteValue(key, RUN_VALUE_NAME)
                    except FileNotFoundError:
                        pass
            return True
        except OSError:
            log.exception("자동 실행 등록 실패")
            return False

    # ─── 업데이트 안내 ───
    @staticmethod
    def _fetch_latest_version():
        req = urllib.request.Request(UPDATE_INFO_URL, headers=HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=8) as response:
            return json.loads(response.read().decode("utf-8")).get("version", "")

    def check_update_manually(self, parent, on_done):
        """사용자가 누르면 바로 확인하고, 최신이어도 결과를 알려 준다."""
        def worker():
            try:
                latest = self._fetch_latest_version()
            except urllib.error.HTTPError as error:
                # 404는 아직 게시된 정식 버전이 없다는 뜻이다.
                latest = "" if error.code == 404 else None
                if latest is None:
                    log.warning("업데이트 확인 실패", exc_info=True)
            except Exception:
                log.warning("업데이트 확인 실패", exc_info=True)
                latest = None
            self.root.after(0, lambda: finish(latest))

        def finish(latest):
            on_done()
            target = parent if parent.winfo_exists() else self.root
            if latest is None:
                messagebox.showwarning(
                    "업데이트 정보",
                    "최신 버전을 확인하지 못했습니다.\n인터넷 연결을 확인해 주세요.",
                    parent=target,
                )
            elif core.is_newer_version(latest):
                self.prompt_update(latest, target)
            else:
                messagebox.showinfo(
                    "업데이트 정보",
                    f"현재 최신 버전입니다.\n\n현재 버전: v{APP_VERSION}",
                    parent=target,
                )

        threading.Thread(target=worker, daemon=True).start()

    def check_for_update(self):
        """새 버전이 있으면 알려 준다. 프로그램을 스스로 교체하지는 않는다."""
        try:
            latest = self._fetch_latest_version()
            if core.is_newer_version(latest) and latest != self._update_prompted:
                self._update_prompted = latest
                self.root.after(0, lambda: self.prompt_update(latest))
        except Exception:
            log.info("업데이트 확인 실패", exc_info=True)

    def prompt_update(self, new_version, parent=None):
        if messagebox.askyesno(
            "업데이트 알림",
            f"새 버전이 있습니다.\n\n"
            f"현재 버전: v{APP_VERSION}\n"
            f"최신 버전: v{new_version}\n\n"
            f"내려받기를 시작할까요?\n"
            f"받은 zip 파일 안의 설치 프로그램을 실행하면 설정은 그대로 유지됩니다.",
            parent=parent or self.root,
        ):
            webbrowser.open(DOWNLOAD_URL)

    def quit_app(self):
        self.save_config()
        self.root.destroy()
        sys.exit(0)

# 설치 프로그램의 AppMutex와 같은 이름이어야 한다.
SINGLE_INSTANCE_MUTEX = "LunchWidgetSingleInstance"
ACTIVATE_EVENT = "LunchWidgetActivate"
ERROR_ALREADY_EXISTS = 183
WAIT_INFINITE = 0xFFFFFFFF


def acquire_single_instance():
    """(이미 실행 중인지, 활성화 이벤트 핸들)을 돌려준다.

    포트와 달리 이름이 있는 뮤텍스는 다른 프로그램과 겹치지 않고, 프로그램이
    끝나면 Windows가 자동으로 풀어 준다.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]

    # 핸들을 닫지 않고 프로세스가 끝날 때까지 유지한다.
    mutex = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
    already_running = bool(mutex) and ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    event = kernel32.CreateEventW(None, False, False, ACTIVATE_EVENT)
    if already_running and event:
        kernel32.SetEvent(event)
    return already_running, event


def listen_for_activation(app, event):
    """다른 실행이 신호를 보내면 기존 창을 앞으로 보여 준다."""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD

    def wait_loop():
        try:
            while kernel32.WaitForSingleObject(event, WAIT_INFINITE) == 0:
                app.root.after(0, app.bring_to_front)
        except RuntimeError:
            pass  # 창이 이미 닫혔다.

    threading.Thread(target=wait_loop, daemon=True).start()


if __name__ == "__main__":
    setup_logging()
    threading.excepthook = lambda args: log.error(
        "스레드 오류", exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
    )
    already_running, activate_event = acquire_single_instance()
    if already_running:
        sys.exit(0)

    root = tk.Tk()
    root.title("급식 위젯")
    root.report_callback_exception = lambda *exc_info: log.error("화면 오류", exc_info=exc_info)
    app = LunchWidget(root)
    if activate_event:
        listen_for_activation(app, activate_event)
    root.mainloop()
