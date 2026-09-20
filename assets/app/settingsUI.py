from copy import deepcopy
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.runtime import WIN32_METHOD_DEFAULTS, WIN32_METHOD_PRIORITY
from app.program import (
    DEFAULT_PROGRAM_CONFIG,
    find_auto_program_executable,
    find_program_executable,
    normalize_program_config,
    resolve_manual_program_path,
)


DEFAULT_MAA_CONFIG = {
    "general": {
        "minimize_enabled": False,
        "allow_option_edits_while_running": False,
    },
    "controller": {},
    "program": DEFAULT_PROGRAM_CONFIG,
    "appearance": {
        "theme": "light",
    },
}


class SettingsStore:

    def __init__(self, config_path, legacy_user_config_path=None):
        self.config_path = Path(config_path)
        self.legacy_user_config_path = (
            Path(legacy_user_config_path) if legacy_user_config_path else None
        )

    @staticmethod
    def _read_json(path):
        if path is None or not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _write_json(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=4)
            file.flush()
        temp_path.replace(path)

    def load(self, default_controller=None):
        raw_config = self._read_json(self.config_path)
        config = deepcopy(DEFAULT_MAA_CONFIG)

        if raw_config is not None:
            raw_general = raw_config.get("general")
            if isinstance(raw_general, dict):
                for key in (
                    "minimize_enabled",
                    "allow_option_edits_while_running",
                ):
                    if isinstance(raw_general.get(key), bool):
                        config["general"][key] = raw_general[key]

            raw_controller = raw_config.get("controller")
            if isinstance(raw_controller, dict):
                config["controller"] = deepcopy(raw_controller)

            config["program"] = normalize_program_config(raw_config.get("program"))

            raw_appearance = raw_config.get("appearance")
            if isinstance(raw_appearance, dict):
                theme = raw_appearance.get("theme")
                if theme in {"system", "light", "dark"}:
                    config["appearance"]["theme"] = theme

        if not config["controller"] and isinstance(default_controller, dict):
            config["controller"] = self.serialize_controller(default_controller)

        legacy_config = self._read_json(self.legacy_user_config_path)
        migrated_legacy = None
        if legacy_config is not None and "minimize_enabled" in legacy_config:
            raw_general = raw_config.get("general") if raw_config else None
            has_new_value = isinstance(raw_general, dict) and isinstance(
                raw_general.get("minimize_enabled"), bool
            )
            legacy_value = legacy_config.get("minimize_enabled")
            if not has_new_value and isinstance(legacy_value, bool):
                config["general"]["minimize_enabled"] = legacy_value

            migrated_legacy = deepcopy(legacy_config)
            migrated_legacy.pop("minimize_enabled", None)

        self.save(config)
        if migrated_legacy is not None:
            self._write_json(self.legacy_user_config_path, migrated_legacy)
        return config

    def save(self, config):
        self._write_json(self.config_path, config)

    @staticmethod
    def serialize_controller(controller):
        if not isinstance(controller, dict):
            return {}
        return {
            key: deepcopy(controller[key])
            for key in ("name", "type", "win32")
            if key in controller
        }


class SettingsPanel(QWidget):
    minimize_changed = Signal(bool)
    runtime_option_editing_changed = Signal(bool)
    theme_changed = Signal(str)
    controller_changed = Signal(object)
    program_changed = Signal(object)

    def __init__(
        self,
        config_path,
        legacy_user_config_path,
        controllers,
        supported_controller_names=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("settingsPanel")
        self._syncing_navigation = False
        self._sections = []
        self.controllers = self._filter_controllers(
            controllers, supported_controller_names
        )
        self.store = SettingsStore(config_path, legacy_user_config_path)
        default_controller = self.controllers[0] if self.controllers else None
        self.config = self.store.load(default_controller)

        self._build_ui()
        self._apply_config()
        self._connect_controls()
        QTimer.singleShot(0, lambda: self._sync_navigation_to_scroll(0))

    @staticmethod
    def _filter_controllers(controllers, supported_controller_names):
        if not isinstance(controllers, list):
            return []
        supported = (
            set(supported_controller_names)
            if isinstance(supported_controller_names, list)
            else None
        )
        result = []
        for controller in controllers:
            if not isinstance(controller, dict) or controller.get("type") != "Win32":
                continue
            name = controller.get("name")
            if not isinstance(name, str) or not name:
                continue
            if not isinstance(controller.get("win32"), dict):
                continue
            if supported is not None and name not in supported:
                continue
            result.append(controller)
        return result

    def _build_ui(self):
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(16)

        self.navigation = QListWidget()
        self.navigation.setObjectName("settingsNavigation")
        self.navigation.setMinimumWidth(170)
        self.navigation.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        for label in ("일반", "프로그램", "컨트롤러", "외관"):
            item = QListWidgetItem(label)
            self.navigation.addItem(item)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setObjectName("settingsDetailScroll")
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.detail_contents = QWidget()
        self.detail_contents.setObjectName("settingsDetailContents")
        self.detail_layout = QVBoxLayout(self.detail_contents)
        self.detail_layout.setContentsMargins(0, 0, 8, 0)
        self.detail_layout.setSpacing(14)

        general, general_layout = self._create_section(
            "일반", "작업 실행과 편집 동작을 설정합니다."
        )
        self.minimize_checkbox = QCheckBox("사용")
        self._add_setting_row(
            general_layout,
            "실행 시 최소화",
            "작업을 시작할 때 대상 프로그램 창을 최소화합니다.",
            self.minimize_checkbox,
        )
        self.runtime_edit_checkbox = QCheckBox("허용")
        self._add_setting_row(
            general_layout,
            "작업 중 옵션 편집",
            (
                "실행 중에도 작업 활성화, 순서, 세부 옵션 등 모든 실행 옵션을 "
                "편집합니다. 변경 사항은 다음 실행부터 적용됩니다."
            ),
            self.runtime_edit_checkbox,
        )

        program, program_layout = self._create_section(
            "프로그램", "자동 실행에 사용할 대상 프로그램을 확인하거나 직접 지정합니다."
        )
        self.program_auto_status = QLabel()
        self.program_auto_status.setObjectName("programPathStatus")
        self.program_auto_status.setWordWrap(True)
        program_layout.addWidget(self.program_auto_status)
        program_layout.addSpacing(8)

        self.program_path_input = QLineEdit()
        self.program_path_input.setObjectName("programPathInput")
        self.program_path_input.setPlaceholderText(
            "실행 파일 또는 실행 파일이 있는 폴더 경로"
        )
        self.program_path_input.setMinimumWidth(260)
        self.program_browse_button = QPushButton("찾아보기")
        self.program_browse_button.setObjectName("programBrowseButton")
        self.program_apply_button = QPushButton("확인")
        self.program_apply_button.setObjectName("programApplyButton")

        program_path_controls = QWidget()
        program_path_controls.setObjectName("programPathControls")
        program_path_layout = QHBoxLayout(program_path_controls)
        program_path_layout.setContentsMargins(0, 0, 0, 0)
        program_path_layout.setSpacing(6)
        program_path_layout.addWidget(self.program_path_input, 1)
        program_path_layout.addWidget(self.program_browse_button)
        program_path_layout.addWidget(self.program_apply_button)
        self._add_setting_row(
            program_layout,
            "수동 경로",
            "비워 두면 자동 검색 결과를 사용합니다. 폴더 또는 실행 파일을 지정할 수 있습니다.",
            program_path_controls,
        )
        self.program_active_status = QLabel()
        self.program_active_status.setObjectName("programPathStatus")
        self.program_active_status.setWordWrap(True)
        program_layout.addWidget(self.program_active_status)

        controller, controller_layout = self._create_section(
            "컨트롤러", "실행에 사용할 컨트롤러를 선택합니다."
        )
        self.controller_combo = QComboBox()
        self.controller_combo.setObjectName("settingsComboBox")
        self.controller_combo.setMinimumWidth(240)
        if self.controllers:
            for controller_config in self.controllers:
                label = controller_config.get("label") or controller_config["name"]
                self.controller_combo.addItem(label, controller_config)
        else:
            self.controller_combo.addItem("사용 가능한 컨트롤러 없음", None)
            self.controller_combo.setEnabled(False)
        self._add_setting_row(
            controller_layout,
            "사용할 컨트롤러",
            "선택한 캡처 및 입력 방식은 다음 실행부터 적용됩니다.",
            self.controller_combo,
        )
        controller_layout.addSpacing(8)
        self.controller_details = QLabel()
        self.controller_details.setObjectName("controllerDetails")
        self.controller_details.setWordWrap(True)
        controller_layout.addWidget(self.controller_details)

        appearance, appearance_layout = self._create_section(
            "외관", "애플리케이션의 표시 방식을 설정합니다."
        )
        self.theme_combo = QComboBox()
        self.theme_combo.setObjectName("settingsComboBox")
        self.theme_combo.setMinimumWidth(180)
        for label, value in (
            ("시스템 설정", "system"),
            ("라이트", "light"),
            ("다크", "dark"),
        ):
            self.theme_combo.addItem(label, value)
        self._add_setting_row(
            appearance_layout,
            "색상 모드",
            "애플리케이션 전체와 Windows 제목 표시줄의 색상을 변경합니다.",
            self.theme_combo,
        )

        self.detail_layout.addStretch(1)
        self.detail_scroll.setWidget(self.detail_contents)

        root_layout.addWidget(self.navigation, 2)
        root_layout.addWidget(self.detail_scroll, 8)

    def _create_section(self, title, description):
        section = QFrame()
        section.setObjectName("settingsSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(0)

        title_label = QLabel(title)
        title_label.setObjectName("settingsSectionTitle")
        layout.addWidget(title_label)
        layout.addSpacing(3)

        description_label = QLabel(description)
        description_label.setObjectName("settingsSectionDescription")
        description_label.setWordWrap(True)
        layout.addWidget(description_label)
        layout.addSpacing(6)

        self.detail_layout.addWidget(section)
        self._sections.append(section)
        return section, layout

    @staticmethod
    def _add_setting_row(parent_layout, title, description, control):
        row = QFrame()
        row.setObjectName("settingsRow")
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        row_layout = QGridLayout(row)
        row_layout.setContentsMargins(0, 12, 0, 12)
        row_layout.setHorizontalSpacing(16)
        row_layout.setVerticalSpacing(2)
        row_layout.setColumnStretch(0, 1)

        title_label = QLabel(title)
        title_label.setObjectName("settingsRowTitle")
        description_label = QLabel(description)
        description_label.setObjectName("settingsRowDescription")
        description_label.setWordWrap(True)

        control.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row_layout.addWidget(title_label, 0, 0)
        row_layout.addWidget(description_label, 1, 0)
        row_layout.addWidget(
            control,
            0,
            1,
            2,
            1,
            Qt.AlignmentFlag.AlignVCenter,
        )
        parent_layout.addWidget(row)

    def _apply_config(self):
        general = self.config["general"]
        self.minimize_checkbox.setChecked(general["minimize_enabled"])
        self.runtime_edit_checkbox.setChecked(
            general["allow_option_edits_while_running"]
        )

        self._confirmed_manual_path = self.config["program"]["manual_path"]
        self.program_path_input.setText(self._confirmed_manual_path)
        self._refresh_program_status()

        theme = self.config["appearance"]["theme"]
        theme_index = self.theme_combo.findData(theme)
        self.theme_combo.setCurrentIndex(theme_index if theme_index >= 0 else 1)

        controller_index = self._find_controller_index(self.config["controller"])
        if controller_index >= 0:
            self.controller_combo.setCurrentIndex(controller_index)
        self._update_controller_details()

    def _find_controller_index(self, saved_controller):
        if not isinstance(saved_controller, dict):
            return 0 if self.controllers else -1

        saved_name = saved_controller.get("name")
        if isinstance(saved_name, str):
            for index, controller in enumerate(self.controllers):
                if controller.get("name") == saved_name:
                    return index

        saved_win32 = saved_controller.get("win32")
        if not isinstance(saved_win32, dict):
            return 0 if self.controllers else -1
        requested = {
            method: saved_win32[method]
            for method in WIN32_METHOD_PRIORITY
            if isinstance(saved_win32.get(method), str) and saved_win32[method]
        }
        for index, controller in enumerate(self.controllers):
            win32_config = controller["win32"]
            if requested and all(
                win32_config.get(method, WIN32_METHOD_DEFAULTS[method]) == value
                for method, value in requested.items()
            ):
                return index
        return 0 if self.controllers else -1

    def _connect_controls(self):
        self.navigation.currentRowChanged.connect(self._scroll_to_section)
        self.detail_scroll.verticalScrollBar().valueChanged.connect(
            self._sync_navigation_to_scroll
        )
        self.minimize_checkbox.toggled.connect(self._on_minimize_changed)
        self.runtime_edit_checkbox.toggled.connect(self._on_runtime_edit_changed)
        self.program_browse_button.clicked.connect(self._browse_program_path)
        self.program_apply_button.clicked.connect(self._apply_program_path)
        self.controller_combo.currentIndexChanged.connect(
            self._on_controller_changed
        )
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.navigation.setCurrentRow(0)

    def _scroll_to_section(self, row):
        if self._syncing_navigation or not 0 <= row < len(self._sections):
            return
        section = self._sections[row]
        self.detail_scroll.verticalScrollBar().setValue(section.y())

    def _sync_navigation_to_scroll(self, value):
        if not self._sections:
            return
        scrollbar = self.detail_scroll.verticalScrollBar()
        if value >= scrollbar.maximum() - 2:
            active_row = len(self._sections) - 1
        else:
            active_row = 0
            for index, section in enumerate(self._sections):
                if section.y() <= value + 24:
                    active_row = index
                else:
                    break

        if self.navigation.currentRow() == active_row:
            return
        self._syncing_navigation = True
        self.navigation.setCurrentRow(active_row)
        self._syncing_navigation = False

    def _collect_config(self):
        return {
            "general": {
                "minimize_enabled": self.minimize_checkbox.isChecked(),
                "allow_option_edits_while_running": (
                    self.runtime_edit_checkbox.isChecked()
                ),
            },
            "controller": SettingsStore.serialize_controller(
                self.controller_combo.currentData()
            ),
            "program": {
                **normalize_program_config(self.config.get("program")),
                "manual_path": self._confirmed_manual_path,
            },
            "appearance": {
                "theme": self.theme_combo.currentData() or "light",
            },
        }

    def _save(self):
        self.config = self._collect_config()
        self.store.save(self.config)

    def _on_minimize_changed(self, enabled):
        self._save()
        self.minimize_changed.emit(enabled)

    def _on_runtime_edit_changed(self, enabled):
        self._save()
        self.runtime_option_editing_changed.emit(enabled)

    def _browse_program_path(self):
        initial_path = self.program_path_input.text().strip()
        selected = QFileDialog.getExistingDirectory(
            self,
            "프로그램 설치 폴더 선택",
            initial_path if Path(initial_path).is_dir() else "",
        )
        if selected:
            self.program_path_input.setText(selected)

    def _apply_program_path(self):
        manual_path = self.program_path_input.text().strip()
        config = normalize_program_config(self.config.get("program"))
        if manual_path and resolve_manual_program_path(
            manual_path, config["executable_name"]
        ) is None:
            self.program_active_status.setProperty("pathValid", False)
            self.program_active_status.setText(
                f"{config['executable_name']} 파일을 확인할 수 없습니다."
            )
            self._refresh_status_style(self.program_active_status)
            return

        self._confirmed_manual_path = manual_path
        self._save()
        self._refresh_program_status()
        self.program_changed.emit(self.program_settings())

    @staticmethod
    def _refresh_status_style(label):
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()

    def _refresh_program_status(self):
        config = {
            **normalize_program_config(self.config.get("program")),
            "manual_path": self._confirmed_manual_path,
        }
        auto_path = find_auto_program_executable(config)
        if auto_path is None:
            self.program_auto_status.setText("자동 검색: 설치 위치를 찾지 못했습니다.")
            self.program_auto_status.setProperty("pathValid", False)
        else:
            self.program_auto_status.setText(f"자동 검색: {auto_path}")
            self.program_auto_status.setProperty("pathValid", True)

        active_path = find_program_executable(config)
        if active_path is None:
            self.program_active_status.setText(
                "사용할 실행 파일이 없습니다. 작업 목록의 자동 실행 작업이 비활성화됩니다."
            )
            self.program_active_status.setProperty("pathValid", False)
        else:
            source = "수동 경로" if self._confirmed_manual_path else "자동 경로"
            self.program_active_status.setText(f"사용 경로 ({source}): {active_path}")
            self.program_active_status.setProperty("pathValid", True)
        self._refresh_status_style(self.program_auto_status)
        self._refresh_status_style(self.program_active_status)

    def _on_controller_changed(self, _index):
        self._update_controller_details()
        self._save()
        self.controller_changed.emit(self.controller_settings())

    def _on_theme_changed(self, _index):
        self._save()
        self.theme_changed.emit(self.theme())

    def _update_controller_details(self):
        controller = self.controller_combo.currentData()
        if not isinstance(controller, dict):
            self.controller_details.setText("선택할 수 있는 Win32 컨트롤러가 없습니다.")
            return
        win32_config = controller.get("win32", {})
        values = {
            method: win32_config.get(method, WIN32_METHOD_DEFAULTS[method])
            for method in WIN32_METHOD_PRIORITY
        }
        self.controller_details.setText(
            f"ID: {controller['name']}  ·  캡처: {values['screencap']}  ·  "
            f"마우스: {values['mouse']}  ·  키보드: {values['keyboard']}"
        )

    def minimize_enabled(self):
        return self.minimize_checkbox.isChecked()

    def set_minimize_enabled(self, enabled):
        self.minimize_checkbox.setChecked(bool(enabled))

    def runtime_option_editing_enabled(self):
        return self.runtime_edit_checkbox.isChecked()

    def theme(self):
        return self.theme_combo.currentData() or "light"

    def controller_settings(self):
        return SettingsStore.serialize_controller(
            self.controller_combo.currentData()
        )

    def program_settings(self):
        config = {
            **normalize_program_config(self.config.get("program")),
            "manual_path": self._confirmed_manual_path,
        }
        resolved_path = find_program_executable(config)
        config["resolved_path"] = str(resolved_path) if resolved_path else ""
        return config
