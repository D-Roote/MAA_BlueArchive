from pathlib import Path
from datetime import datetime
from copy import deepcopy
from enum import Enum
import ctypes
import json
import re
import sys

from ctypes import wintypes

from PySide6.QtCore import QDir, QEvent, QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QTextCursor,
                           QColor, QIcon, QPainter, QPen)
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (QApplication, QMainWindow, QAbstractItemView,
                               QHBoxLayout, QVBoxLayout,
                               QListWidget, QListWidgetItem, QWidget, 
                               QButtonGroup, QCheckBox, QComboBox, QLabel, QLineEdit,
                               QPushButton, QRadioButton)

from app.runtime import AppRuntime
from app.settingsUI import SettingsPanel


WINDOW_SIZE = [1200, 800]
WINDOW_TITLE = "MAA_Blue Archive"
UI_FILENAME = "baseUI.ui"
QSS_FILENAME = "style.qss"
SETTINGS_QSS_FILENAME = "settings.qss"
DARK_QSS_FILENAME = "dark.qss"
APP_DIR = Path(__file__).resolve().parent
UI_DIR = APP_DIR / "pySide6"
UI_RESOURCE_DIR = APP_DIR / "resources"
SETTINGS_ICON_PATH = UI_RESOURCE_DIR / "icons/actions/settings.svg"
SETTINGS_ICON_SIZE = QSize(20, 20)
SETTINGS_ICON_HOVER_SIZE = QSize(24, 24)


class TitleBarTheme(str, Enum):
    """애플리케이션 전체와 제목 표시줄에 적용하는 색상 모드."""

    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWM_COLOR_DEFAULT = 0xFFFFFFFF
LIGHT_CAPTION_COLOR = 0x00FFFFFF
LIGHT_CAPTION_TEXT_COLOR = 0x00000000
DARK_CAPTION_COLOR = 0x00382217
DARK_CAPTION_TEXT_COLOR = 0x00F9F5F1


def resolve_effective_theme(theme, system_color_scheme=Qt.ColorScheme.Unknown):
    """설정값과 시스템 색상 모드로 실제 애플리케이션 테마를 결정한다."""
    theme = TitleBarTheme(theme)
    if theme != TitleBarTheme.SYSTEM:
        return theme
    if system_color_scheme == Qt.ColorScheme.Dark:
        return TitleBarTheme.DARK
    return TitleBarTheme.LIGHT


def _create_dwmapi():
    if sys.platform != "win32":
        return None

    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:
        return None

    dwmapi.DwmSetWindowAttribute.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        wintypes.LPCVOID,
        wintypes.DWORD,
    ]
    dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
    return dwmapi


def apply_windows_title_bar_theme(
    hwnd,
    theme,
    dwmapi=None,
    system_color_scheme=Qt.ColorScheme.Unknown,
):
    """Windows 제목 표시줄 테마를 적용하고 지원 여부를 반환한다."""
    theme = TitleBarTheme(theme)
    if sys.platform != "win32" or not hwnd:
        return False

    dwmapi = dwmapi or _create_dwmapi()
    if dwmapi is None:
        return False

    effective_theme = resolve_effective_theme(theme, system_color_scheme)
    dark_mode = wintypes.BOOL(effective_theme == TitleBarTheme.DARK)
    if theme == TitleBarTheme.LIGHT:
        caption_color = wintypes.DWORD(LIGHT_CAPTION_COLOR)
        text_color = wintypes.DWORD(LIGHT_CAPTION_TEXT_COLOR)
    elif theme == TitleBarTheme.DARK:
        caption_color = wintypes.DWORD(DARK_CAPTION_COLOR)
        text_color = wintypes.DWORD(DARK_CAPTION_TEXT_COLOR)
    else:
        caption_color = wintypes.DWORD(DWM_COLOR_DEFAULT)
        text_color = wintypes.DWORD(DWM_COLOR_DEFAULT)

    result = dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(hwnd),
        DWMWA_USE_IMMERSIVE_DARK_MODE,
        ctypes.byref(dark_mode),
        ctypes.sizeof(dark_mode),
    )

    # 색상 속성은 Windows 11부터 지원된다. 미지원 환경에서는 위의 테마 속성만 사용한다.
    for attribute, value in (
        (DWMWA_CAPTION_COLOR, caption_color),
        (DWMWA_TEXT_COLOR, text_color),
    ):
        dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            attribute,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )

    return result == 0


def merge_pipeline_override(target, source):
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_pipeline_override(target[key], value)
        else:
            target[key] = deepcopy(value)


def convert_input_value(input_config, value):
    pipeline_type = input_config.get("pipeline_type", "string")
    if pipeline_type == "string":
        return value
    if pipeline_type == "int":
        try:
            return int(value)
        except (TypeError, ValueError) as error:
            raise ValueError("정수를 입력해 주세요.") from error
    if pipeline_type == "bool":
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
        raise ValueError("true, false, 1, 0 중 하나를 입력해 주세요.")
    raise ValueError(f"지원하지 않는 pipeline_type입니다: {pipeline_type}")


def validate_input_value(input_config, value):
    verify_pattern = input_config.get("verify")
    if verify_pattern:
        try:
            if re.fullmatch(verify_pattern, value) is None:
                return False, input_config.get("pattern_msg", "입력 형식이 올바르지 않습니다.")
        except re.error as error:
            return False, f"검증 정규식이 올바르지 않습니다: {error}"

    try:
        convert_input_value(input_config, value)
    except ValueError as error:
        return False, str(error)
    return True, ""


def substitute_input_placeholders(value, converted_values):
    if isinstance(value, dict):
        return {
            key: substitute_input_placeholders(child, converted_values)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [substitute_input_placeholders(child, converted_values) for child in value]
    if not isinstance(value, str):
        return deepcopy(value)

    exact_match = re.fullmatch(r"\{([^{}]+)\}", value)
    if exact_match and exact_match.group(1) in converted_values:
        return deepcopy(converted_values[exact_match.group(1)])

    def replace_placeholder(match):
        name = match.group(1)
        if name not in converted_values:
            return match.group(0)
        replacement = converted_values[name]
        if isinstance(replacement, bool):
            return "true" if replacement else "false"
        return str(replacement)

    return re.sub(r"\{([^{}]+)\}", replace_placeholder, value)


def build_input_pipeline_override(option_config, input_values):
    converted_values = {}
    for input_config in option_config.get("inputs", []):
        input_name = input_config.get("name")
        if not input_name:
            continue
        value = str(input_values.get(input_name, input_config.get("default", "")))
        is_valid, validation_message = validate_input_value(input_config, value)
        if not is_valid:
            raise ValueError(f"{input_name}: {validation_message}")
        converted_values[input_name] = convert_input_value(input_config, value)

    return substitute_input_placeholders(
        option_config.get("pipeline_override", {}), converted_values
    )


def find_switch_cases(cases):
    yes_names = {"yes", "y"}
    no_names = {"no", "n"}
    yes_case_name = None
    no_case_name = None
    for case in cases:
        name = case.get("name", "")
        if name.lower() in yes_names:
            yes_case_name = name
        elif name.lower() in no_names:
            no_case_name = name
    return yes_case_name, no_case_name


class SettingsIconHoverFilter(QObject):
    """설정 아이콘 버튼의 호버 크기 변경을 공통 처리한다."""

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Enter:
            watched.setIconSize(SETTINGS_ICON_HOVER_SIZE)
        elif event.type() == QEvent.Type.Leave:
            watched.setIconSize(SETTINGS_ICON_SIZE)
        return super().eventFilter(watched, event)


def setup_settings_icon_button(button):
    button.setIcon(QIcon(str(SETTINGS_ICON_PATH)))
    button.setIconSize(SETTINGS_ICON_SIZE)
    button._settings_icon_hover_filter = SettingsIconHoverFilter(button)
    button.installEventFilter(button._settings_icon_hover_filter)


class TaskSettingsButton(QPushButton):
    """클릭 영역은 유지하고 호버 시 아이콘만 확대하는 설정 버튼."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("taskSettingsButton")
        self.setFixedSize(30, 30)
        setup_settings_icon_button(self)


# 동적 List 클래스
class OptionItemWidget(QWidget):
    def __init__(self, task_data, task_options, on_setting_clicked_callback, on_checkbox_toggled_callback, parent=None):
        super().__init__(parent)

        self.task_data = task_data      
        self.task_options = task_options 
        
        self.selected_options = {}
        for opt_name, opt in self.task_options:
            opt_type = opt.get("type", "select")
            default_case = opt.get("default_case")

            if opt_type == "input":
                self.selected_options[opt_name] = {
                    input_config["name"]: str(input_config.get("default", ""))
                    for input_config in opt.get("inputs", [])
                    if input_config.get("name")
                }
            elif opt_type == "switch":
                yes_case_name, no_case_name = find_switch_cases(opt.get("cases", []))
                if default_case and default_case == yes_case_name:
                    self.selected_options[opt_name] = [yes_case_name]
                elif no_case_name is not None:
                    self.selected_options[opt_name] = [no_case_name]
                else:
                    self.selected_options[opt_name] = []
            elif opt_type == "select":
                case_names = [
                    case.get("name") for case in opt.get("cases", []) if case.get("name")
                ]
                if default_case in case_names:
                    self.selected_options[opt_name] = [default_case]
                elif case_names:
                    self.selected_options[opt_name] = [case_names[0]]
                else:
                    self.selected_options[opt_name] = []
            elif isinstance(default_case, list):
                self.selected_options[opt_name] = list(default_case)
            elif default_case:
                self.selected_options[opt_name] = [default_case]
            else:
                self.selected_options[opt_name] = []
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(8)

        self.checkbox = QCheckBox("")
        self.checkbox.setFixedSize(30, 30)
        self.checkbox.toggled.connect(on_checkbox_toggled_callback)
        layout.addWidget(self.checkbox)

        display_name = task_data.get("label", task_data.get("name", "Unknown Task"))
        self.label = QLabel(display_name)
        self.label.setStyleSheet("background: transparent;")

        layout.addWidget(self.label, 1) 

        self.setting_btn = TaskSettingsButton()
        self.setting_btn.setToolTip(f"{display_name} 세부 설정")
        self.setting_btn.setAccessibleName(f"{display_name} 세부 설정")
        self.checkbox.setAccessibleName(f"{display_name} 실행 선택")
        
        if self.task_options:
            layout.addWidget(self.setting_btn)
            self.setting_btn.clicked.connect(lambda: on_setting_clicked_callback(self))
        else:
            self.setting_btn.hide()

    def is_checked(self):
        return self.checkbox.isChecked()

    def has_valid_input(self):
        for opt_name, opt in self.task_options:
            if opt.get("type", "select") != "input":
                continue

            input_values = self.selected_options.get(opt_name, {})
            if not isinstance(input_values, dict):
                return False
            for input_config in opt.get("inputs", []):
                input_name = input_config.get("name")
                if not input_name:
                    continue
                value = str(input_values.get(input_name, input_config.get("default", "")))
                if not validate_input_value(input_config, value)[0]:
                    return False
        return True

    def get_persisted_options(self):
        persisted_options = deepcopy(self.selected_options)
        for opt_name, opt in self.task_options:
            if opt.get("type", "select") != "input":
                continue
            input_values = persisted_options.get(opt_name)
            if not isinstance(input_values, dict):
                continue
            for input_config in opt.get("inputs", []):
                if input_config.get("password"):
                    input_values.pop(input_config.get("name"), None)
        return persisted_options

    def set_locked(self, locked: bool):
        self.checkbox.setEnabled(not locked)
        # 실행 중에도 세부 설정 화면은 열 수 있도록 한다.
        self.setting_btn.setEnabled(True)
        if locked:
            self.label.setStyleSheet("background: transparent; color: #94A3B8;")
        else:
            self.label.setStyleSheet("background: transparent; color: #000000;")

# 커스텀 리스트 위젯
class DragDropListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDropIndicatorShown(False)
        self.drag_line_y = -1
        self.on_order_changed_callback = None
        self._locked = False

    def set_locked(self, locked: bool):
        self._locked = locked
        self.setDragDropMode(
            QAbstractItemView.NoDragDrop if locked else QAbstractItemView.InternalMove
        )

    def dragMoveEvent(self, event):
        super().dragMoveEvent(event)
        if event.isAccepted():
            pos = event.position().toPoint()
            item = self.itemAt(pos)
            if item:
                rect = self.visualItemRect(item)
                if pos.y() < rect.y() + rect.height() / 2:
                    self.drag_line_y = rect.y()
                else:
                    self.drag_line_y = rect.y() + rect.height()
            else:
                if self.count() > 0:
                    last_rect = self.visualItemRect(self.item(self.count() - 1))
                    self.drag_line_y = last_rect.y() + last_rect.height()
                else:
                    self.drag_line_y = 0
            self.viewport().update()
        else:
            self.drag_line_y = -1
            self.viewport().update()

    def dragLeaveEvent(self, event):
        self.drag_line_y = -1
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.drag_line_y = -1
        self.viewport().update()

        widget_by_entry = {}
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            entry = item.data(Qt.UserRole)
            if widget is not None and entry is not None:
                widget_by_entry[entry] = widget

        super().dropEvent(event)

        for i in range(self.count()):
            item = self.item(i)
            entry = item.data(Qt.UserRole)
            if self.itemWidget(item) is None and entry in widget_by_entry:
                widget = widget_by_entry[entry]
                self.setItemWidget(item, widget)
                item.setSizeHint(widget.sizeHint())

        if self.on_order_changed_callback:
            self.on_order_changed_callback()

    def paintEvent(self, event):
        super().paintEvent(event)
        
        if self.drag_line_y != -1:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.Antialiasing)
            
            # 사이 가로줄 색, 두께 하드 코딩
            pen = QPen(QColor("#00AEEF"), 2)
            painter.setPen(pen)
            
            painter.drawLine(0, self.drag_line_y, self.viewport().width(), self.drag_line_y)
            painter.end()

# 메인 윈도우
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        ui_path = UI_DIR / UI_FILENAME
        base_qss_paths = (UI_DIR / QSS_FILENAME, UI_DIR / SETTINGS_QSS_FILENAME)
        dark_qss_path = UI_DIR / DARK_QSS_FILENAME
        # QSS의 아이콘 경로도 작업 디렉토리와 무관하게 해석한다.
        QDir.setSearchPaths("maabaicons", [str(UI_RESOURCE_DIR / "icons")])
        loader = QUiLoader()
        self.ui = loader.load(str(ui_path), self)
        if self.ui is None:
            raise RuntimeError(f"UI 파일을 불러오지 못했습니다: {ui_path}: {loader.errorString()}")

        setup_settings_icon_button(self.ui.endSettingBtn)
        
        self.ui.tabWidget.setUsesScrollButtons(False)
        # Designer에서 어떤 탭을 편집했든 앱은 항상 시작 탭으로 연다.
        self.ui.tabWidget.setCurrentWidget(self.ui.mainTab)

        qss_contents = []
        for qss_path in base_qss_paths:
            if qss_path.exists():
                with open(qss_path, "r", encoding="utf-8") as file:
                    qss_contents.append(file.read())
        self._base_style_sheet = "\n".join(qss_contents)
        self._dark_style_sheet = ""
        if dark_qss_path.exists():
            with open(dark_qss_path, "r", encoding="utf-8") as file:
                self._dark_style_sheet = file.read()
        self.setStyleSheet(self._base_style_sheet)

        self.setCentralWidget(self.ui.centralwidget)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WINDOW_SIZE[0], WINDOW_SIZE[1])
        self._title_bar_theme = TitleBarTheme.LIGHT
        self._effective_theme = TitleBarTheme.LIGHT
        self._style_hints = QApplication.styleHints()
        self._style_hints.colorSchemeChanged.connect(
            self.on_system_color_scheme_changed
        )

        self.runtime = AppRuntime()
        self.log_sink = self.runtime.log_sink
        self.worker = None
        self.stop_worker = None
        self.isRunning = False
        self._close_pending = False
        self._options_locked = False
        self._allow_option_edits_while_running = False

        self.setup_settings_ui()
        self.setup_connections()

        self.setup_dynamic_options()
        self.check_start_button_state()

    def setup_connections(self):
        self.ui.workStartBtn.clicked.connect(self.on_task_start)

        if hasattr(self.ui, 'minimizeEnableBtn'):
            self.ui.minimizeEnableBtn.toggled.connect(
                self.on_main_minimize_setting_changed
            )

    def setup_settings_ui(self):
        config_dir = self.runtime.user_dir / "config"
        resource_config = (
            self.runtime.resource_config
            if isinstance(self.runtime.resource_config, dict)
            else {}
        )
        self.settings_panel = SettingsPanel(
            config_path=config_dir / "maa_config.json",
            legacy_user_config_path=config_dir / "user_config.json",
            controllers=self.runtime.interface.get("controller", []),
            supported_controller_names=resource_config.get("controller"),
            parent=self.ui.settingTab,
        )

        setting_layout = self.ui.settingTab.layout()
        if setting_layout is None:
            setting_layout = QVBoxLayout(self.ui.settingTab)
            setting_layout.setContentsMargins(0, 0, 0, 0)
        setting_layout.addWidget(self.settings_panel)

        self.settings_panel.minimize_changed.connect(
            self.sync_main_minimize_setting
        )
        self.settings_panel.runtime_option_editing_changed.connect(
            self.set_runtime_option_editing_enabled
        )
        self.settings_panel.theme_changed.connect(self.set_title_bar_theme)

        self.sync_main_minimize_setting(self.settings_panel.minimize_enabled())
        self.set_runtime_option_editing_enabled(
            self.settings_panel.runtime_option_editing_enabled()
        )
        self.set_title_bar_theme(self.settings_panel.theme())

    def sync_main_minimize_setting(self, enabled):
        if not hasattr(self.ui, 'minimizeEnableBtn'):
            return
        self.ui.minimizeEnableBtn.blockSignals(True)
        self.ui.minimizeEnableBtn.setChecked(bool(enabled))
        self.ui.minimizeEnableBtn.blockSignals(False)

    def on_main_minimize_setting_changed(self, enabled):
        self.settings_panel.set_minimize_enabled(enabled)

    def on_system_color_scheme_changed(self, _color_scheme):
        if self._title_bar_theme == TitleBarTheme.SYSTEM:
            self.set_title_bar_theme(TitleBarTheme.SYSTEM)

    def set_title_bar_theme(self, theme):
        """선택한 색상 모드를 애플리케이션 전체와 제목 표시줄에 적용한다."""
        self._title_bar_theme = TitleBarTheme(theme)
        system_color_scheme = self._style_hints.colorScheme()
        self._effective_theme = resolve_effective_theme(
            self._title_bar_theme,
            system_color_scheme,
        )
        style_sheet = self._base_style_sheet
        if self._effective_theme == TitleBarTheme.DARK:
            style_sheet = "\n".join(
                content
                for content in (style_sheet, self._dark_style_sheet)
                if content
            )
        self.setStyleSheet(style_sheet)
        return apply_windows_title_bar_theme(
            int(self.winId()),
            self._title_bar_theme,
            system_color_scheme=system_color_scheme,
        )

    def update_tab_widths(self):
        tab_bar = self.ui.tabWidget.tabBar()
        count = tab_bar.count()
        if count == 0:
            return


        # border(좌우 1px씩) + padding(좌우 25px씩)가
        # width 지정값 위에 추가로 그려지므로, 미리 빼야 실제 렌더링 폭이
        # 등분값과 정확히 일치함 (안 빼면 탭들이 넘쳐서 스크롤 화살표가 자동 생성됨)
        EXTRA_PER_TAB = 52  # border 2 + padding 50

        total_width = self.ui.tabWidget.width()
        target_width = total_width // count
        tab_width = max(target_width - EXTRA_PER_TAB, 20)

        tab_bar.setStyleSheet(f"""
            QTabBar::tab {{
                width: {tab_width}px;
            }}
            QTabBar::tab:last {{
                margin-right: 0px;
            }}
        """)

    def showEvent(self, event):
        super().showEvent(event)
        self.set_title_bar_theme(self._title_bar_theme)
        QTimer.singleShot(0, self.update_tab_widths)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_tab_widths()

    def append_log(self, message):
        current_time = datetime.now().strftime("%H:%M:%S")
        time_text = f"[{current_time}] "
        
        cursor = self.ui.logPrintText.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.ui.logPrintText.setTextCursor(cursor)
        
        block_format = cursor.blockFormat()
        block_format.setAlignment(Qt.AlignLeft)
        
        block_format.setTopMargin(0)
        block_format.setBottomMargin(0)
        
        block_format.setLeftMargin(0)
        block_format.setTextIndent(0)
        cursor.setBlockFormat(block_format)
        cursor.insertText(time_text)
        
        block_format.setLeftMargin(58)    
        block_format.setTextIndent(-58)  
        cursor.setBlockFormat(block_format)
        
        cursor.insertText(message + "\n")
        
        self.ui.logPrintText.ensureCursorVisible()

    def on_task_start(self):
        if self.worker is not None or self.stop_worker is not None or self._close_pending:
            return
        self.append_log("작업을 시작합니다...")
        self.ui.workStartBtn.setEnabled(False)

        execution_queue = self.build_execution_queue()

        minimize_window = False
        if hasattr(self.ui, 'minimizeEnableBtn'):
            minimize_window = self.ui.minimizeEnableBtn.isChecked()

        self.worker = RuntimeWorker(
            self.runtime,
            execution_queue,
            minimize_window,
            controller_settings=self.settings_panel.controller_settings(),
        )

        self.worker.log.connect(self.append_log, Qt.QueuedConnection)
        self.worker.finished.connect(self.on_task_finished)

        self.runtime.log_sink.set_log_callback(self.worker.log.emit)

        self.worker.start()

        self.isRunning = True

        self.ui.workStartBtn.setText("작업 중지")
        self.ui.workStartBtn.clicked.disconnect(self.on_task_start)
        self.ui.workStartBtn.clicked.connect(self.on_task_stop)
        self.ui.workStartBtn.setEnabled(True)

        self.set_options_locked(True)

    def on_task_stop(self):
        self.ui.workStartBtn.setEnabled(False)

        if not self.worker:
            self.check_start_button_state()
            return

        # MaaFW task가 아직 시작되지 않은 초기화 단계도 취소할 수 있게 한다.
        self.worker.requestInterruption()

        # 정지 요청이 이미 진행 중이면 재실행하지 않음
        if self.stop_worker is not None:
            return

        self.stop_worker = StopWorker(self.runtime)
        self.stop_worker.finished.connect(self.on_stop_worker_finished)
        self.stop_worker.start()

    def on_stop_worker_finished(self):
        if self.stop_worker is not None:
            if self.stop_worker.succeeded:
                self.append_log(self.stop_worker.result_message)
            elif self.stop_worker.result_message != "실행 중인 Tasker가 없습니다.":
                self.append_log(self.stop_worker.result_message)
            self.stop_worker.deleteLater()
        self.stop_worker = None
        self._finish_run_if_idle()

    def on_task_finished(self):
        worker = self.worker
        self.runtime.log_sink.set_log_callback(None)

        if worker is not None:
            prefix = "▶" if worker.succeeded else "⚠"
            self.append_log(f"{prefix} {worker.result_message}\n")
            worker.deleteLater()
        self.worker = None
        self.ui.workStartBtn.setEnabled(False)
        self._finish_run_if_idle()

    def _finish_run_if_idle(self):
        # 두 finished 콜백이 모두 처리되기 전에는 새 실행을 허용하지 않는다.
        if self.worker is not None or self.stop_worker is not None:
            return
        if self.isRunning:
            self.isRunning = False
            self.ui.workStartBtn.setText("작업 시작")
            self.ui.workStartBtn.clicked.disconnect(self.on_task_stop)
            self.ui.workStartBtn.clicked.connect(self.on_task_start)
        self.set_options_locked(False)
        self.check_start_button_state()
        self._finish_pending_close()

    def closeEvent(self, event):
        worker_running = self.worker is not None
        stop_running = self.stop_worker is not None

        if worker_running or stop_running:
            self._close_pending = True
            event.ignore()
            if worker_running:
                self.on_task_stop()
            return

        super().closeEvent(event)

    def _finish_pending_close(self):
        if not self._close_pending:
            return

        worker_running = self.worker is not None
        stop_running = self.stop_worker is not None
        if not worker_running and not stop_running:
            QTimer.singleShot(0, self.close)

    def setup_dynamic_options(self):
        self.option_list_widget = DragDropListWidget()
        self.option_list_widget.setObjectName("taskOptionList")
        
        self.option_list_widget.setDragDropMode(QAbstractItemView.InternalMove)
        self.option_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)

        target_layout = self.ui.settingStartWidget_1
        if isinstance(target_layout, QWidget):
            if target_layout.layout() is None:
                QVBoxLayout(target_layout)
            target_layout = target_layout.layout()
            
        target_layout.insertWidget(0, self.option_list_widget)
        target_layout.setStretchFactor(self.option_list_widget, 1)
        self.option_list_widget.on_order_changed_callback = self.on_user_config_changed

        raw_tasks = self.runtime.interface.get("task", [])
        options_dict = self.runtime.interface.get("option", {})
        
        task_dict = {t["name"]: t for t in raw_tasks}
        task_dict_by_entry = {}
        for task in raw_tasks:
            task_dict_by_entry.setdefault(task["entry"], task)

        user_config_path = self.runtime.user_dir / "config" / "user_config.json"
        user_config = {}
        if user_config_path.exists():
            try:
                with open(user_config_path, "r", encoding="utf-8") as f:
                    user_config = json.load(f)
                if not isinstance(user_config, dict):
                    user_config = {}
            except Exception as e:
                print(f"설정 파일 로드 실패: {e}")

        saved_tasks = user_config.get("tasks", [])
        if not isinstance(saved_tasks, list):
            saved_tasks = []
        added_tasks = set()

        def add_task_widget(task_data, is_checked, saved_options):
            item = QListWidgetItem()
            item.setFlags(item.flags() & ~Qt.ItemIsDropEnabled)
            item.setData(Qt.UserRole, task_data["name"])
            self.option_list_widget.addItem(item)
            
            task_options = []
            if "option" in task_data:
                for opt_name in task_data["option"]:
                    if opt_name in options_dict:
                        task_options.append((opt_name, options_dict[opt_name]))

            custom_widget = OptionItemWidget(
                task_data, 
                task_options, 
                self.show_sub_cases, 
                self.on_task_checkbox_toggled
            )
            
            if isinstance(saved_options, dict):
                for opt_name, opt in custom_widget.task_options:
                    saved_value = saved_options.get(opt_name)
                    if opt.get("type", "select") == "input":
                        if not isinstance(saved_value, dict):
                            continue
                        current_values = custom_widget.selected_options[opt_name]
                        for input_config in opt.get("inputs", []):
                            input_name = input_config.get("name")
                            if not input_name or input_config.get("password"):
                                continue
                            saved_input = saved_value.get(input_name)
                            if isinstance(saved_input, str):
                                current_values[input_name] = saved_input
                        continue

                    if not isinstance(saved_value, list):
                        continue

                    valid_names = [case.get("name") for case in opt.get("cases", [])]
                    selected = [name for name in valid_names if name in saved_value]
                    if opt.get("type", "select") != "checkbox":
                        selected = selected[:1]

                    if selected or opt.get("type", "select") == "checkbox":
                        custom_widget.selected_options[opt_name] = selected
            
            custom_widget.checkbox.blockSignals(True)
            custom_widget.checkbox.setChecked(is_checked)
            custom_widget.checkbox.blockSignals(False)
            custom_widget.adjustSize()
            item.setSizeHint(custom_widget.sizeHint())
            
            self.option_list_widget.setItemWidget(item, custom_widget)
            added_tasks.add(task_data["name"])

        for saved_task in saved_tasks:
            task_data = task_dict.get(saved_task.get("name"))
            if task_data is None:
                # 기존 entry 기반 설정 파일을 name 기반 형식으로 자동 마이그레이션한다.
                task_data = task_dict_by_entry.get(saved_task.get("entry"))

            if task_data is not None and task_data["name"] not in added_tasks:
                add_task_widget(
                    task_data,
                    saved_task.get("checked", task_data.get("default_check", False)),
                    saved_task.get("selected_options", None)
                )

        for task_data in raw_tasks:
            if task_data["name"] not in added_tasks:
                add_task_widget(task_data, task_data.get("default_check", False), None)

        self.check_start_button_state()

    def show_sub_cases(self, item_widget):
        task_options = item_widget.task_options

        container_widget = self.ui.scrollSettingContents
        layout = container_widget.layout()
        
        if layout is None:
            layout = QVBoxLayout(container_widget)
            layout.setContentsMargins(10, 10, 10, 10)
            
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        for opt_name, opt in task_options:
            opt_type = opt.get("type", "select")
            
            title_label = QLabel(f"[{opt.get('label', opt_name)}]")
            title_label.setStyleSheet("font-weight: bold; font-size: 14px; margin-top: 10px;")
            title_label.setWordWrap(True)
            layout.addWidget(title_label)

            cases = opt.get("cases", [])

            if opt_type == "radio":
                select_container = QWidget(container_widget)
                select_layout = QVBoxLayout(select_container)
                select_layout.setContentsMargins(0, 0, 0, 0)
                select_layout.setSpacing(0)
                button_group = QButtonGroup(select_container)
                
                for case in cases:
                    case_name = case["name"]

                    row_widget = QWidget(select_container)
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 2, 0, 2)
                    row_layout.setSpacing(8)

                    radio_btn = QRadioButton("")
                    button_group.addButton(radio_btn)
                    
                    if case_name in item_widget.selected_options.get(opt_name, []):
                        radio_btn.setChecked(True)

                    case_label = QLabel(case.get('label', case_name))
                    case_label.setStyleSheet("background: transparent;")
                    case_label.setWordWrap(True) 
                    
                    def make_radio_slot(w, o_name, c_name):
                        return lambda checked: self.update_widget_option_radio(w, o_name, c_name, checked)
                        
                    radio_btn.toggled.connect(make_radio_slot(item_widget, opt_name, case_name))
                    
                    row_layout.addWidget(radio_btn)
                    row_layout.addWidget(case_label, 1)
                    select_layout.addWidget(row_widget)

                layout.addWidget(select_container)

            elif opt_type == "select":
                combo_box = QComboBox(container_widget)
                combo_box.setObjectName("optionSelect")
                combo_box.setProperty("optionName", opt_name)
                combo_box.setAccessibleName(opt.get("label", opt_name))

                for case in cases:
                    case_name = case.get("name")
                    if not case_name:
                        continue
                    combo_box.addItem(case.get("label", case_name), case_name)
                    description = case.get("description")
                    if description:
                        combo_box.setItemData(
                            combo_box.count() - 1, description, Qt.ToolTipRole
                        )

                selected_cases = item_widget.selected_options.get(opt_name, [])
                selected_name = selected_cases[0] if selected_cases else None
                selected_index = combo_box.findData(selected_name)
                if selected_index < 0 and combo_box.count() > 0:
                    selected_index = 0
                    item_widget.selected_options[opt_name] = [combo_box.itemData(0)]
                combo_box.setCurrentIndex(selected_index)

                def make_select_slot(w, o_name, select_widget):
                    return lambda index: self.update_widget_option_select(
                        w, o_name, select_widget, index
                    )

                combo_box.currentIndexChanged.connect(
                    make_select_slot(item_widget, opt_name, combo_box)
                )
                layout.addWidget(combo_box)

            elif opt_type == "checkbox":
                for case in cases:
                    case_name = case["name"]
                    
                    row_widget = QWidget()
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 2, 0, 2)
                    row_layout.setSpacing(8)

                    case_cb = QCheckBox("")
                    
                    if case_name in item_widget.selected_options.get(opt_name, []):
                        case_cb.setChecked(True)
                    
                    case_label = QLabel(case.get('label', case_name))
                    case_label.setStyleSheet("background: transparent;")
                    case_label.setWordWrap(True) 
                    
                    def make_checkbox_slot(w, o_name, c_name):
                        return lambda checked: self.update_widget_option_checkbox(w, o_name, c_name, checked)
                    
                    case_cb.toggled.connect(make_checkbox_slot(item_widget, opt_name, case_name))
                    
                    row_layout.addWidget(case_cb)
                    row_layout.addWidget(case_label, 1)
                    layout.addWidget(row_widget)

            elif opt_type == "switch":
                yes_case_name, no_case_name = find_switch_cases(cases)
                yes_case = next((c for c in cases if c.get("name") == yes_case_name), None)
                if yes_case_name is not None:
                    case_label_text = yes_case.get('label', yes_case_name) if yes_case else yes_case_name

                    row_widget = QWidget()
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 2, 0, 2)
                    row_layout.setSpacing(8)

                    # 기능은 CheckBox와 동일 (추후 스타일시트로 토글 모양 변경)
                    switch_cb = QCheckBox("")

                    if yes_case_name in item_widget.selected_options.get(opt_name, []):
                        switch_cb.setChecked(True)

                    case_label = QLabel(case_label_text)
                    case_label.setStyleSheet("background: transparent;")
                    case_label.setWordWrap(True)

                    def make_switch_slot(w, o_name, y_name, n_name):
                        return lambda checked: self.update_widget_option_switch(w, o_name, y_name, n_name, checked)

                    switch_cb.toggled.connect(make_switch_slot(item_widget, opt_name, yes_case_name, no_case_name))

                    row_layout.addWidget(switch_cb)
                    row_layout.addWidget(case_label, 1)
                    layout.addWidget(row_widget)

            elif opt_type == "input":
                input_values = item_widget.selected_options.get(opt_name, {})
                for input_config in opt.get("inputs", []):
                    input_name = input_config.get("name")
                    if not input_name:
                        continue

                    input_container = QWidget(container_widget)
                    input_layout = QVBoxLayout(input_container)
                    input_layout.setContentsMargins(0, 2, 0, 4)
                    input_layout.setSpacing(4)

                    input_label = QLabel(input_config.get("label", input_name))
                    input_label.setStyleSheet("background: transparent;")
                    input_layout.addWidget(input_label)

                    line_edit = QLineEdit(input_container)
                    line_edit.setObjectName("optionInput")
                    line_edit.setProperty("optionName", opt_name)
                    line_edit.setProperty("inputName", input_name)
                    line_edit.setText(
                        str(input_values.get(input_name, input_config.get("default", "")))
                    )
                    description = input_config.get("description")
                    if description:
                        line_edit.setToolTip(description)
                    if input_config.get("password"):
                        line_edit.setEchoMode(QLineEdit.EchoMode.Password)
                    line_edit.setAccessibleName(input_config.get("label", input_name))
                    input_layout.addWidget(line_edit)

                    error_label = QLabel(input_container)
                    error_label.setObjectName("optionInputError")
                    error_label.setWordWrap(True)
                    input_layout.addWidget(error_label)

                    def make_input_slot(w, o_name, i_config, editor, error):
                        return lambda text: self.update_widget_option_input(
                            w, o_name, i_config, editor, error, text
                        )

                    line_edit.textChanged.connect(
                        make_input_slot(
                            item_widget,
                            opt_name,
                            input_config,
                            line_edit,
                            error_label,
                        )
                    )
                    self.update_input_validation_state(
                        line_edit, error_label, input_config, line_edit.text()
                    )
                    layout.addWidget(input_container)

        layout.addStretch()

    def update_widget_option_radio(self, widget, opt_name, case_name, is_checked):
        if is_checked:
            widget.selected_options[opt_name] = [case_name]
            self.on_user_config_changed()

    def update_widget_option_select(self, widget, opt_name, combo_box, index):
        case_name = combo_box.itemData(index)
        if case_name is None:
            return
        widget.selected_options[opt_name] = [case_name]
        self.on_user_config_changed()

    def update_widget_option_checkbox(self, widget, opt_name, case_name, is_checked):
        if opt_name not in widget.selected_options:
            widget.selected_options[opt_name] = []
            
        if is_checked:
            if case_name not in widget.selected_options[opt_name]:
                widget.selected_options[opt_name].append(case_name)
        else:
            if case_name in widget.selected_options[opt_name]:
                widget.selected_options[opt_name].remove(case_name)

        self.on_user_config_changed()

    def update_widget_option_switch(self, widget, opt_name, yes_case_name, no_case_name, is_checked):
        if is_checked and yes_case_name is not None:
            widget.selected_options[opt_name] = [yes_case_name]
        elif not is_checked and no_case_name is not None:
            widget.selected_options[opt_name] = [no_case_name]

        self.on_user_config_changed()

    def update_input_validation_state(self, line_edit, error_label, input_config, value):
        is_valid, validation_message = validate_input_value(input_config, value)
        line_edit.setProperty("inputValid", is_valid)
        line_edit.style().unpolish(line_edit)
        line_edit.style().polish(line_edit)
        error_label.setText(validation_message)
        error_label.setVisible(not is_valid)
        return is_valid

    def update_widget_option_input(
        self, widget, opt_name, input_config, line_edit, error_label, value
    ):
        input_name = input_config["name"]
        input_values = widget.selected_options.setdefault(opt_name, {})
        input_values[input_name] = value
        self.update_input_validation_state(
            line_edit, error_label, input_config, value
        )
        self.check_start_button_state()
        self.on_user_config_changed()

    def check_start_button_state(self):
        if self.isRunning or self.stop_worker is not None or self._close_pending:
            self.ui.workStartBtn.setEnabled(False)
            return
        any_checked = False
        for i in range(self.option_list_widget.count()):
            item = self.option_list_widget.item(i)
            widget = self.option_list_widget.itemWidget(item)
            if widget and widget.is_checked():
                any_checked = True
                if not widget.has_valid_input():
                    self.ui.workStartBtn.setEnabled(False)
                    return
        self.ui.workStartBtn.setEnabled(any_checked)

    def on_task_checkbox_toggled(self, checked):
        self.check_start_button_state()
        self.on_user_config_changed()

    def on_user_config_changed(self):
        self.save_user_config()

    def set_options_locked(self, locked: bool):
        self._options_locked = locked
        for i in range(self.option_list_widget.count()):
            item = self.option_list_widget.item(i)
            widget = self.option_list_widget.itemWidget(item)
            if widget:
                widget.set_locked(locked)

        self.option_list_widget.set_locked(locked)

        if hasattr(self.ui, 'minimizeEnableBtn'):
            self.ui.minimizeEnableBtn.setEnabled(not locked)

        if hasattr(self.ui, 'scrollSettingContents'):
            self.ui.scrollSettingContents.setEnabled(
                not locked or self._allow_option_edits_while_running
            )

    def set_runtime_option_editing_enabled(self, enabled: bool):
        """실행 중 세부 옵션 편집 정책을 설정한다. 추후 설정 탭에서 호출할 진입점이다."""
        self._allow_option_edits_while_running = bool(enabled)
        if hasattr(self.ui, 'scrollSettingContents'):
            self.ui.scrollSettingContents.setEnabled(
                not self._options_locked or self._allow_option_edits_while_running
            )

    def build_execution_queue(self):
        execution_queue = []
        
        for i in range(self.option_list_widget.count()):
            item = self.option_list_widget.item(i)
            widget_in_item = self.option_list_widget.itemWidget(item)
            
            if widget_in_item and widget_in_item.is_checked():
                task_entry = widget_in_item.task_data["entry"]
                override_params = {}
                task_override = widget_in_item.task_data.get("pipeline_override", {})
                if isinstance(task_override, dict):
                    merge_pipeline_override(override_params, task_override)
                
                for opt_name, opt in widget_in_item.task_options:
                    selected_cases = widget_in_item.selected_options.get(opt_name, [])

                    if opt.get("type", "select") == "input":
                        if not isinstance(selected_cases, dict):
                            continue
                        input_override = build_input_pipeline_override(opt, selected_cases)
                        if isinstance(input_override, dict):
                            merge_pipeline_override(override_params, input_override)
                        continue
                    
                    if not selected_cases:
                        continue
                        
                    for case in opt.get("cases", []):
                        if case["name"] in selected_cases:
                            pipeline_override = case.get("pipeline_override", {})
                            if isinstance(pipeline_override, dict):
                                merge_pipeline_override(override_params, pipeline_override)
                
                execution_queue.append((task_entry, override_params))
                
        return execution_queue

    def save_user_config(self):
        config_dir = self.runtime.user_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True) # 폴더가 없으면 자동 생성
        config_path = config_dir / "user_config.json"
        
        tasks_data = []
        
        for i in range(self.option_list_widget.count()):
            item = self.option_list_widget.item(i)
            widget = self.option_list_widget.itemWidget(item)
            if widget:
                tasks_data.append({
                    "name": widget.task_data["name"],
                    "entry": widget.task_data["entry"],
                    "checked": widget.is_checked(),
                    "selected_options": widget.get_persisted_options()
                })
                
        try:
            temp_path = config_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump({"tasks": tasks_data}, f, ensure_ascii=False, indent=4)
                f.flush()
            temp_path.replace(config_path)
        except Exception as e:
            print(f"설정 파일 저장 실패: {e}")

# 정지 요청 전용 스레드
class StopWorker(QThread):
    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.succeeded = False
        self.result_message = ""

    def run(self):
        self.succeeded, self.result_message = self.runtime.stop_task()

# Tasker 스레드
class RuntimeWorker(QThread):
    log = Signal(str)

    def __init__(
        self,
        runtime,
        execution_queue,
        minimize_window=False,
        controller_settings=None,
    ):
        super().__init__()
        self.runtime = runtime
        self.execution_queue = execution_queue
        self.minimize_window = minimize_window
        self.controller_settings = controller_settings
        self.succeeded = False
        self.result_message = "작업을 시작하지 못했습니다."

    def run(self):
        try:
            initialized, init_message = self.runtime.initialize(self.controller_settings)
            if not initialized:
                self.result_message = init_message
                return

            self.log.emit(init_message)
            if self.isInterruptionRequested():
                self.result_message = "작업 시작이 취소되었습니다."
                return

            self.log.emit("▶ 작업 시작...")
            self.succeeded, self.result_message = self.runtime.run_task(
                self.execution_queue,
                self.minimize_window,
                cancellation_requested=self.isInterruptionRequested,
            )
            if self.isInterruptionRequested():
                self.succeeded = False
                self.result_message = "작업이 중지되었습니다."
        except Exception as error:
            self.succeeded = False
            self.result_message = f"Runtime에서 예기치 않은 오류가 발생했습니다: {error}"
        finally:
            released, release_message = self.runtime.release_session()
            if not released:
                self.succeeded = False
                self.result_message += f"\n{release_message}"
