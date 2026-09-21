from pathlib import Path
from datetime import datetime
from copy import deepcopy
from enum import Enum
from uuid import uuid4
import ctypes
import json
import re
import sys

from ctypes import wintypes

from PySide6.QtCore import QDir, QEvent, QMimeData, QModelIndex, QObject, QPoint, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QTextCursor,
                           QColor, QCursor, QDrag, QIcon, QPainter, QPen, QPixmap)
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (QApplication, QMainWindow, QAbstractItemView,
                               QHBoxLayout, QVBoxLayout,
                               QListWidget, QListWidgetItem, QWidget, 
                               QButtonGroup, QCheckBox, QComboBox, QLabel, QLineEdit,
                               QPushButton, QRadioButton)

from app.runtime import AppRuntime, PROGRAM_LAUNCH_ENTRY
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
RESET_ICON_SIZE = QSize(18, 18)
RESET_ICON_HOVER_SIZE = QSize(22, 22)
PROGRAM_LAUNCH_TASK_NAME = "__ProgramLaunch"
PROGRAM_LAUNCH_TASK = {
    "name": PROGRAM_LAUNCH_TASK_NAME,
    "label": "자동 실행",
    "default_check": True,
    "entry": PROGRAM_LAUNCH_ENTRY,
    "builtin": True,
    "requires_program": True,
}


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


class ResetIconHoverFilter(QObject):
    """초기화 버튼의 히트박스는 유지하고 호버 시 아이콘만 확대한다."""

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Enter:
            watched.setIconSize(RESET_ICON_HOVER_SIZE)
        elif event.type() == QEvent.Type.Leave:
            watched.setIconSize(RESET_ICON_SIZE)
        return super().eventFilter(watched, event)


class TaskFooterHoverFilter(QObject):
    """두 버튼을 하나의 작업 영역처럼 호버 표시한다."""

    def __init__(self, footer, watched_widgets):
        super().__init__(footer)
        self.footer = footer
        for widget in watched_widgets:
            widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Enter:
            self._set_hovered(True)
        elif event.type() == QEvent.Type.Leave:
            QTimer.singleShot(0, self._sync_hovered)
        return super().eventFilter(watched, event)

    def _sync_hovered(self):
        local_position = self.footer.mapFromGlobal(QCursor.pos())
        self._set_hovered(self.footer.rect().contains(local_position))

    def _set_hovered(self, hovered):
        if self.footer.property("groupHovered") == hovered:
            return
        self.footer.setProperty("groupHovered", hovered)
        self.footer.style().unpolish(self.footer)
        self.footer.style().polish(self.footer)
        self.footer.update()


class DeleteDropEventFilter(QObject):
    """삭제 영역 위에서 드래그 가능 커서를 유지하되 삭제는 목록이 처리한다."""

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            event.acceptProposedAction()
            return True
        if event.type() == QEvent.Type.Drop:
            event.ignore()
            return True
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


class TaskPickerPopup(QListWidget):
    task_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._anchor_button = None
        self.setObjectName("taskPickerPopup")
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.setInterval(80)
        self._dismiss_timer.timeout.connect(self._hide_if_pointer_outside)
        self.itemClicked.connect(self._select_task)
        self.itemActivated.connect(self._select_task)

    def set_anchor_button(self, button):
        if self._anchor_button is not None:
            self._anchor_button.removeEventFilter(self)
        self._anchor_button = button
        button.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is getattr(self, "_anchor_button", None):
            if event.type() == QEvent.Type.Enter:
                self._dismiss_timer.stop()
            elif event.type() == QEvent.Type.Leave:
                self._dismiss_timer.start()
        return super().eventFilter(watched, event)

    def enterEvent(self, event):
        self._dismiss_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._dismiss_timer.start()
        super().leaveEvent(event)

    @staticmethod
    def _contains_global_position(widget, global_position):
        return widget.rect().contains(widget.mapFromGlobal(global_position))

    def _hide_if_pointer_outside(self):
        if not self.isVisible():
            return
        global_position = QCursor.pos()
        if self._contains_global_position(self, global_position):
            return
        if self._anchor_button is not None and self._contains_global_position(
            self._anchor_button, global_position
        ):
            return
        self.hide()

    def show_above(self, anchor, task_list, tasks, width_reference=None):
        self.clear()
        task_row_height = task_list.sizeHintForRow(0)
        if task_row_height <= 0:
            task_row_height = 34
        for task in tasks:
            item = QListWidgetItem(task.get("label", task["name"]))
            item.setData(Qt.UserRole, task)
            item.setSizeHint(QSize(0, task_row_height))
            self.addItem(item)
        if not self.count():
            return

        self.ensurePolished()
        position = anchor.mapToGlobal(QPoint(0, 0))
        available_height = max(1, position.y() - anchor.screen().availableGeometry().top())
        viewport_chrome = max(0, self.height() - self.viewport().height())
        height = min(
            task_row_height * self.count() + viewport_chrome,
            task_list.height(),
            available_height,
        )
        width_reference = width_reference or task_list
        horizontal_position = width_reference.mapToGlobal(QPoint(0, 0))
        self.setFixedSize(width_reference.width(), height)
        self.move(horizontal_position.x(), position.y() - height)
        self.setCurrentRow(0)
        self.show()
        self.setFocus()

    def _select_task(self, item):
        task = item.data(Qt.UserRole)
        self.hide()
        self.task_selected.emit(task)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)


# 동적 List 클래스
class OptionItemWidget(QWidget):
    def __init__(self, task_data, task_options, on_setting_clicked_callback, on_checkbox_toggled_callback, parent=None):
        super().__init__(parent)

        self.task_data = task_data      
        self.task_options = task_options 
        self._available = True
        self._locked = False
        
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
        # 설정 아이콘은 30px 버튼 안에서 20px로 중앙 정렬되므로 우측 여백을
        # 0으로 두어 체크박스의 좌측 5px 라인과 시각적 끝을 맞춘다.
        layout.setContentsMargins(5, 2, 0, 2)
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
        return self._available and self.checkbox.isChecked()

    def set_available(self, available, reason=""):
        self._available = bool(available)
        if not self._available:
            self.checkbox.blockSignals(True)
            self.checkbox.setChecked(False)
            self.checkbox.blockSignals(False)
        self.setToolTip("" if self._available else reason)
        self.checkbox.setEnabled(self._available and not self._locked)
        self.label.setStyleSheet(
            "background: transparent;"
            if self._available and not self._locked
            else "background: transparent; color: #94A3B8;"
        )

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
        self._locked = bool(locked)
        self.checkbox.setEnabled(self._available and not self._locked)
        # 실행 중에도 세부 설정 화면은 열 수 있도록 한다.
        self.setting_btn.setEnabled(True)
        if self._locked or not self._available:
            self.label.setStyleSheet("background: transparent; color: #94A3B8;")
        else:
            self.label.setStyleSheet("background: transparent;")

# 커스텀 리스트 위젯
class DragDropListWidget(QListWidget):
    TASK_DRAG_MIME = "application/x-maaba-task-item"
    DRAG_LINE_MARGIN = 5
    drag_started = Signal()
    drag_finished = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDropIndicatorShown(False)
        self.drag_line_y = -1
        self.on_order_changed_callback = None
        self._locked = False
        self._dragged_item = None
        self._drop_before_item = None

    def set_locked(self, locked: bool):
        self._locked = locked
        self.setDragDropMode(
            QAbstractItemView.NoDragDrop if locked else QAbstractItemView.InternalMove
        )

    def startDrag(self, supported_actions):
        dragged_item = self.currentItem()
        if dragged_item is None or self._is_pinned_item(dragged_item):
            return

        indexes = self.selectedIndexes()
        mime_data = self.model().mimeData(indexes) if indexes else QMimeData()
        mime_data.setData(self.TASK_DRAG_MIME, b"1")
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        preview = self._create_drag_preview(dragged_item)
        drag.setPixmap(preview)
        cursor_position = self.viewport().mapFromGlobal(QCursor.pos())
        hotspot_x = max(
            8,
            min(
                preview.width() - 8,
                round(cursor_position.x() * preview.width() / max(1, self.viewport().width())),
            ),
        )
        drag.setHotSpot(QPoint(hotspot_x, preview.height() // 2))

        self._dragged_item = dragged_item
        self._drop_before_item = None
        dragged_item.setHidden(True)
        self.viewport().update()
        self.drag_started.emit()
        try:
            drag.exec(Qt.DropAction.MoveAction, Qt.DropAction.MoveAction)
        finally:
            if self.row(dragged_item) >= 0:
                dragged_item.setHidden(False)
            self.drag_line_y = -1
            self._dragged_item = None
            self._drop_before_item = None
            self.viewport().update()
            self.drag_finished.emit(dragged_item, QCursor.pos())

    def _create_drag_preview(self, item):
        item_widget = self.itemWidget(item)
        label = getattr(item_widget, "label", None)
        text = label.text() if label is not None else item.text()
        width = max(140, round(self.viewport().width() * 0.86))
        height = max(24, item.sizeHint().height() - 6)
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)

        background = self.palette().base().color()
        background.setAlpha(205)
        border = QColor("#00AEEF")
        border.setAlpha(220)
        foreground = self.palette().text().color()
        foreground.setAlpha(235)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(background)
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(pixmap.rect().adjusted(1, 1, -1, -1), 5, 5)
        painter.setPen(foreground)
        painter.setFont(label.font() if label is not None else self.font())
        text_rect = pixmap.rect().adjusted(12, 0, -12, 0)
        elided_text = painter.fontMetrics().elidedText(
            text,
            Qt.TextElideMode.ElideRight,
            text_rect.width(),
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            elided_text,
        )
        painter.end()
        return pixmap

    def dragEnterEvent(self, event):
        super().dragEnterEvent(event)
        if self._is_internal_task_drag(event):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()

    def dragMoveEvent(self, event):
        super().dragMoveEvent(event)
        if self._is_internal_task_drag(event):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            self._update_drop_target(event.position().toPoint())
            self.viewport().update()
            return

        if not event.isAccepted():
            self._clear_drop_indicator()

    def _is_internal_task_drag(self, event):
        return (
            event.source() is self
            and event.mimeData().hasFormat(self.TASK_DRAG_MIME)
        )

    def _visible_items(self):
        return [
            self.item(row)
            for row in range(self.count())
            if self.item(row) is not self._dragged_item
            and not self.item(row).isHidden()
        ]

    @staticmethod
    def _is_pinned_item(item):
        return (
            item is not None
            and item.data(Qt.ItemDataRole.UserRole) == PROGRAM_LAUNCH_TASK_NAME
        )

    def _update_drop_target(self, position):
        visible_items = [
            item for item in self._visible_items() if not self._is_pinned_item(item)
        ]
        if not visible_items:
            self._drop_before_item = None
            pinned_items = [
                self.item(row)
                for row in range(self.count())
                if self._is_pinned_item(self.item(row))
            ]
            self.drag_line_y = (
                self.visualItemRect(pinned_items[0]).bottom() + 1
                if pinned_items
                else 0
            )
            return

        first_item = visible_items[0]
        first_rect = self.visualItemRect(first_item)
        if position.y() <= first_rect.center().y():
            self._drop_before_item = first_item
            self.drag_line_y = first_rect.top()
            return

        for index, item in enumerate(visible_items):
            rect = self.visualItemRect(item)
            if position.y() <= rect.bottom():
                if position.y() < rect.center().y():
                    self._drop_before_item = item
                    self.drag_line_y = rect.top()
                else:
                    self._drop_before_item = (
                        visible_items[index + 1]
                        if index + 1 < len(visible_items)
                        else None
                    )
                    self.drag_line_y = rect.bottom() + 1
                return

        last_rect = self.visualItemRect(visible_items[-1])
        self._drop_before_item = None
        self.drag_line_y = last_rect.bottom() + 1

    def _clear_drop_indicator(self):
        self.drag_line_y = -1
        self._drop_before_item = None
        self.viewport().update()

    def dragLeaveEvent(self, event):
        self._clear_drop_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if self._is_internal_task_drag(event):
            self._update_drop_target(event.position().toPoint())
            moved = self._move_dragged_item(self._drop_before_item)
            self._clear_drop_indicator()
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            if moved and self.on_order_changed_callback:
                self.on_order_changed_callback()
            return

        self._clear_drop_indicator()

        widget_by_id = {}
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            instance_id = item.data(Qt.UserRole + 1)
            if widget is not None and instance_id is not None:
                widget_by_id[instance_id] = widget

        super().dropEvent(event)

        for i in range(self.count()):
            item = self.item(i)
            instance_id = item.data(Qt.UserRole + 1)
            if self.itemWidget(item) is None and instance_id in widget_by_id:
                widget = widget_by_id[instance_id]
                self.setItemWidget(item, widget)
                item.setSizeHint(widget.sizeHint())

        if self.on_order_changed_callback:
            self.on_order_changed_callback()

    def _move_dragged_item(self, before_item):
        dragged_item = self._dragged_item
        if dragged_item is None or self._is_pinned_item(dragged_item):
            return False
        source_row = self.row(dragged_item)
        if source_row < 0:
            return False

        destination_child = self.count() if before_item is None else self.row(before_item)
        if destination_child < 0:
            destination_child = self.count()
        pinned_rows = [
            row
            for row in range(self.count())
            if self._is_pinned_item(self.item(row))
        ]
        if pinned_rows:
            destination_child = max(destination_child, pinned_rows[0] + 1)
        if destination_child in (source_row, source_row + 1):
            return False

        # index widget의 소유권은 Qt가 관리하므로 분리/재연결하지 않고 행 자체를 옮긴다.
        moved = self.model().moveRow(
            QModelIndex(),
            source_row,
            QModelIndex(),
            destination_child,
        )
        if moved:
            self.setCurrentItem(dragged_item)
        return moved

    def paintEvent(self, event):
        super().paintEvent(event)
        
        if self.drag_line_y != -1:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.Antialiasing)
            
            pen = QPen(QColor("#00AEEF"), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)

            # 2px 선의 중심이 경계 밖으로 나가지 않게 해 최상단에서도 두께를 보존한다.
            line_y = self._bounded_drag_line_y()
            line_start, line_end = self._drag_line_span()
            painter.drawLine(line_start, line_y, line_end, line_y)
            painter.end()

    def _bounded_drag_line_y(self):
        return max(1, min(self.viewport().height() - 2, self.drag_line_y))

    def _drag_line_span(self):
        return (
            self.DRAG_LINE_MARGIN,
            max(self.DRAG_LINE_MARGIN, self.viewport().width() - self.DRAG_LINE_MARGIN),
        )

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
        loader.registerCustomWidget(DragDropListWidget)
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
        self.on_program_settings_changed(self.settings_panel.program_settings())
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
        self.settings_panel.program_launch_task_enabled_changed.connect(
            self.set_program_launch_task_enabled
        )
        self.settings_panel.runtime_option_editing_changed.connect(
            self.set_runtime_option_editing_enabled
        )
        self.settings_panel.theme_changed.connect(self.set_title_bar_theme)
        self.settings_panel.program_changed.connect(
            self.on_program_settings_changed
        )

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

    def on_program_settings_changed(self, program_settings):
        if not hasattr(self, "option_list_widget"):
            return
        available = bool(program_settings.get("resolved_path"))
        reason = "설정에서 실행할 프로그램 경로를 먼저 확인해 주세요."
        for row in range(self.option_list_widget.count()):
            item = self.option_list_widget.item(row)
            widget = self.option_list_widget.itemWidget(item)
            if (
                widget is not None
                and widget.task_data.get("name") == PROGRAM_LAUNCH_TASK_NAME
            ):
                widget.set_available(available, reason)
        self.check_start_button_state()
        self.save_user_config()

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
        if self.settings_panel.clear_log_on_start_enabled():
            self.ui.logPrintText.clear()
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
            program_settings=self.settings_panel.program_settings(),
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
        self.option_list_widget = self.ui.taskOptionList
        self.option_list_widget.on_order_changed_callback = self.on_user_config_changed

        self.task_list_actions = self.ui.taskListFooter
        self.task_list_actions_stack = self.ui.taskListActionsStack
        self.task_delete_page = self.ui.taskDeletePage
        self.task_delete_drop_zone = self.ui.taskDeleteDropZone
        self.task_reset_button = self.ui.taskResetButton
        self.task_add_button = self.ui.taskAddButton
        self.task_reset_button._reset_icon_hover_filter = ResetIconHoverFilter(
            self.task_reset_button
        )

        self.task_reset_button.installEventFilter(
            self.task_reset_button._reset_icon_hover_filter
        )
        self.task_list_actions._footer_hover_filter = TaskFooterHoverFilter(
            self.task_list_actions,
            (
                self.task_list_actions,
                self.task_reset_button,
                self.task_add_button,
            ),
        )
        self.task_delete_drop_zone._drop_event_filter = DeleteDropEventFilter(
            self.task_delete_drop_zone
        )
        self.task_delete_drop_zone.installEventFilter(
            self.task_delete_drop_zone._drop_event_filter
        )
        self.option_list_widget.drag_started.connect(self.on_task_drag_started)
        self.option_list_widget.drag_finished.connect(self.on_task_drag_finished)
        self.task_picker = TaskPickerPopup(self)
        self.task_picker.set_anchor_button(self.task_add_button)
        self.task_picker.task_selected.connect(self.add_task)
        self.task_add_button.clicked.connect(self.show_task_picker)
        self.task_reset_button.clicked.connect(self.reset_task_list)

        raw_tasks = self.runtime.interface.get("task", [])
        
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

        saved_program_task = next(
            (
                task
                for task in saved_tasks
                if isinstance(task, dict)
                and task.get("name") == PROGRAM_LAUNCH_TASK_NAME
            ),
            None,
        )
        self._program_launch_checked_before_disable = (
            saved_program_task.get("checked", True)
            if saved_program_task is not None
            else PROGRAM_LAUNCH_TASK["default_check"]
        )
        if self.settings_panel.program_launch_task_enabled():
            self.add_task_widget(
                PROGRAM_LAUNCH_TASK,
                self._program_launch_checked_before_disable,
            )

        for saved_task in saved_tasks:
            if not isinstance(saved_task, dict):
                continue
            if saved_task.get("name") == PROGRAM_LAUNCH_TASK_NAME:
                continue
            task_data = task_dict.get(saved_task.get("name"))
            if task_data is None:
                # 기존 entry 기반 설정 파일을 name 기반 형식으로 자동 마이그레이션한다.
                task_data = task_dict_by_entry.get(saved_task.get("entry"))

            if task_data is not None:
                self.add_task_widget(
                    task_data,
                    saved_task.get("checked", task_data.get("default_check", False)),
                    saved_task.get("selected_options", None)
                )
                added_tasks.add(task_data["name"])

        for task_data in raw_tasks:
            if task_data["name"] not in added_tasks:
                self.add_task_widget(task_data, task_data.get("default_check", False), None)

        self._apply_option_editing_policy()
        self.check_start_button_state()

    def add_task_widget(self, task_data, is_checked, saved_options=None):
        item = QListWidgetItem()
        item.setFlags(item.flags() & ~Qt.ItemIsDropEnabled)
        item.setData(Qt.UserRole, task_data["name"])
        # 같은 Task를 여러 번 추가해도 드래그 시 각 항목의 옵션을 유지한다.
        item.setData(Qt.UserRole + 1, uuid4().hex)
        if task_data.get("name") == PROGRAM_LAUNCH_TASK_NAME:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            self.option_list_widget.insertItem(0, item)
        else:
            self.option_list_widget.addItem(item)

        options_dict = self.runtime.interface.get("option", {})
        task_options = [
            (name, options_dict[name])
            for name in task_data.get("option", [])
            if name in options_dict
        ]
        custom_widget = OptionItemWidget(
            task_data, task_options, self.show_sub_cases, self.on_task_checkbox_toggled
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
        if task_data.get("requires_program"):
            program_settings = self.settings_panel.program_settings()
            custom_widget.set_available(
                bool(program_settings.get("resolved_path")),
                "설정에서 실행할 프로그램 경로를 먼저 확인해 주세요.",
            )
        custom_widget.adjustSize()
        item.setSizeHint(custom_widget.sizeHint())
        self.option_list_widget.setItemWidget(item, custom_widget)

    def show_task_picker(self):
        if not self.task_add_button.isEnabled():
            return
        self.task_picker.show_above(
            self.task_list_actions,
            self.option_list_widget,
            self.runtime.interface.get("task", []),
            self.ui.taskListContainer,
        )

    def set_program_launch_task_enabled(self, enabled):
        launch_item = None
        for row in range(self.option_list_widget.count()):
            item = self.option_list_widget.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == PROGRAM_LAUNCH_TASK_NAME:
                launch_item = item
                break

        if not enabled and launch_item is not None:
            widget = self.option_list_widget.itemWidget(launch_item)
            if widget is not None:
                self._program_launch_checked_before_disable = widget.is_checked()
                widget.hide()
                widget.deleteLater()
            row = self.option_list_widget.row(launch_item)
            removed_item = self.option_list_widget.takeItem(row)
            del removed_item
        elif enabled and launch_item is None:
            self.add_task_widget(
                PROGRAM_LAUNCH_TASK,
                getattr(
                    self,
                    "_program_launch_checked_before_disable",
                    PROGRAM_LAUNCH_TASK["default_check"],
                ),
            )
            self.option_list_widget.scrollToTop()
            self._apply_option_editing_policy()

        self.clear_sub_cases()
        self.check_start_button_state()
        self.save_user_config()

    def on_task_drag_started(self):
        self.task_picker.hide()
        self.ui.taskListSeparator.hide()
        self.task_list_actions_stack.setCurrentWidget(self.task_delete_page)

    def on_task_drag_finished(self, dragged_item, global_position):
        try:
            drop_position = self.task_delete_drop_zone.mapFromGlobal(global_position)
            if not self.task_delete_drop_zone.rect().contains(drop_position):
                return
            row = self.option_list_widget.row(dragged_item)
            if row < 0:
                return
            item_widget = self.option_list_widget.itemWidget(dragged_item)
            removed_item = self.option_list_widget.takeItem(row)
            if item_widget is not None:
                item_widget.hide()
                item_widget.deleteLater()
            del removed_item
            self.clear_sub_cases()
            self.check_start_button_state()
            self.save_user_config()
        finally:
            self.task_list_actions_stack.setCurrentWidget(self.task_list_actions)
            self.ui.taskListSeparator.show()

    def add_task(self, task_data):
        if not self.task_add_button.isEnabled():
            return
        if task_data.get("name") == PROGRAM_LAUNCH_TASK_NAME:
            for row in range(self.option_list_widget.count()):
                widget = self.option_list_widget.itemWidget(
                    self.option_list_widget.item(row)
                )
                if (
                    widget is not None
                    and widget.task_data.get("name") == PROGRAM_LAUNCH_TASK_NAME
                ):
                    return
        self.add_task_widget(task_data, task_data.get("default_check", False))
        if task_data.get("name") == PROGRAM_LAUNCH_TASK_NAME:
            self.option_list_widget.scrollToTop()
        else:
            self.option_list_widget.scrollToBottom()
        self._apply_option_editing_policy()
        self.check_start_button_state()
        self.save_user_config()

    def reset_task_list(self):
        if not self.task_reset_button.isEnabled():
            return
        self.task_picker.hide()
        # 삭제되는 항목을 참조하는 세부 옵션 컨트롤도 함께 비운다.
        self.clear_sub_cases()
        self.option_list_widget.clear()
        if self.settings_panel.program_launch_task_enabled():
            self.add_task_widget(
                PROGRAM_LAUNCH_TASK, PROGRAM_LAUNCH_TASK["default_check"]
            )
        for task in self.runtime.interface.get("task", []):
            self.add_task_widget(task, task.get("default_check", False))
        self._apply_option_editing_policy()
        self.check_start_button_state()
        self.save_user_config()

    def clear_sub_cases(self):
        layout = self.ui.scrollSettingContents.layout()
        if layout is not None:
            while layout.count():
                child = layout.takeAt(0)
                if child.widget():
                    child.widget().hide()
                    child.widget().deleteLater()

    def show_sub_cases(self, item_widget):
        task_options = item_widget.task_options

        container_widget = self.ui.scrollSettingContents
        layout = container_widget.layout()
        
        if layout is None:
            layout = QVBoxLayout(container_widget)
            layout.setContentsMargins(10, 10, 10, 10)
            
        self.clear_sub_cases()

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
        if self.isRunning:
            self.ui.workStartBtn.setEnabled(
                self.stop_worker is None and not self._close_pending
            )
            return
        if self.stop_worker is not None or self._close_pending:
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
        self._apply_option_editing_policy()

    def _apply_option_editing_policy(self):
        controls_locked = (
            self._options_locked and not self._allow_option_edits_while_running
        )

        if hasattr(self, "option_list_widget"):
            for i in range(self.option_list_widget.count()):
                item = self.option_list_widget.item(i)
                widget = self.option_list_widget.itemWidget(item)
                if widget:
                    widget.set_locked(controls_locked)

            self.option_list_widget.set_locked(controls_locked)

        if hasattr(self, "task_list_actions"):
            self.task_list_actions.setEnabled(not controls_locked)
            self.task_reset_button.setEnabled(not controls_locked)
            self.task_add_button.setEnabled(not controls_locked)
            if controls_locked:
                self.task_picker.hide()

        if hasattr(self.ui, 'minimizeEnableBtn'):
            self.ui.minimizeEnableBtn.setEnabled(not controls_locked)

        if hasattr(self.ui, 'scrollSettingContents'):
            self.ui.scrollSettingContents.setEnabled(not controls_locked)

    def set_runtime_option_editing_enabled(self, enabled: bool):
        """실행 중 전체 옵션 편집 정책을 즉시 적용한다."""
        self._allow_option_edits_while_running = bool(enabled)
        self._apply_option_editing_policy()

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
        program_settings=None,
    ):
        super().__init__()
        self.runtime = runtime
        self.execution_queue = execution_queue
        self.minimize_window = minimize_window
        self.controller_settings = controller_settings
        self.program_settings = program_settings
        self.succeeded = False
        self.result_message = "작업을 시작하지 못했습니다."

    def run(self):
        try:
            initialized, init_message = self.runtime.initialize(
                self.controller_settings,
                program_settings=self.program_settings,
                execution_queue=self.execution_queue,
                cancellation_requested=self.isInterruptionRequested,
            )
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
