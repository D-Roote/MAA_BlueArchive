"""Run with .venv/Scripts/python.exe -m unittest discover -s RegressionTest -v."""

import json
import os
import re
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
)
from PySide6.QtSvg import QSvgRenderer
from maa.define import MaaWin32ScreencapMethodEnum

from app.runtime import AppRuntime, LogSinkFocus, WindowPlacement
from app.settingsUI import SettingsStore
from app.winUI import (
    DARK_CAPTION_COLOR,
    DARK_CAPTION_TEXT_COLOR,
    DARK_QSS_FILENAME,
    DWM_COLOR_DEFAULT,
    DWMWA_CAPTION_COLOR,
    DWMWA_TEXT_COLOR,
    DWMWA_USE_IMMERSIVE_DARK_MODE,
    MainWindow,
    RuntimeWorker,
    TitleBarTheme,
    UI_DIR,
    UI_RESOURCE_DIR,
    WINDOW_TITLE,
    apply_windows_title_bar_theme,
    resolve_effective_theme,
)


def make_job(succeeded=True):
    job = MagicMock()
    job.succeeded = succeeded
    job.wait.return_value = job
    return job


class RuntimeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.runtime = AppRuntime()
        self.runtime._user32 = MagicMock()

    def configure_initialization(self):
        resource = MagicMock(loaded=True)
        self.runtime.resource = resource
        self.runtime._resource_loaded = True
        self.runtime._toolkit_initialized = True
        self.taskers = []

        def make_tasker():
            tasker = MagicMock(running=False, stopping=False, inited=True)
            tasker.add_context_sink.return_value = 7
            self.taskers.append(tasker)
            return tasker

        def create_controller(controller_settings):
            self.runtime.controller = MagicMock(connected=True)
            self.runtime.controller.post_connection.return_value = make_job()
            self.runtime.controller.post_inactive.return_value = make_job()
            self.runtime._target_hwnd = 123
            return True, "created"

        self.contexts.enter_context(patch("app.runtime.Tasker", side_effect=make_tasker))
        self.contexts.enter_context(patch.object(self.runtime, "_create_controller", side_effect=create_controller))

    def test_native_initialization_is_deferred(self):
        with patch("app.runtime.Toolkit.init_option") as toolkit, patch("app.runtime.Tasker") as tasker:
            runtime = AppRuntime()
        toolkit.assert_not_called()
        tasker.assert_not_called()
        self.assertIsNone(runtime.resource)
        self.assertIsNone(runtime.tasker)

    def test_window_search_ignores_app_and_uses_controller_settings(self):
        windows = [
            SimpleNamespace(hwnd=111, window_name=WINDOW_TITLE),
            SimpleNamespace(hwnd=222, window_name="Blue Archive - Notes"),
            SimpleNamespace(hwnd=333, window_name="Blue Archive"),
        ]
        for settings, controller_name, capture in (
            (
                {
                    "screencap": "FramePool",
                    "mouse": "PostMessageWithWindowPos",
                    "keyboard": "PostMessage",
                },
                "Win32FramePool",
                MaaWin32ScreencapMethodEnum.FramePool,
            ),
            (
                {
                    "name": "SavedController",
                    "win32": {
                        "screencap": "PrintWindow",
                        "mouse": "PostMessageWithWindowPos",
                        "keyboard": "PostMessage",
                    },
                },
                "Win32PrintWindow",
                MaaWin32ScreencapMethodEnum.PrintWindow,
            ),
        ):
            with self.subTest(settings=settings), patch(
                "app.runtime.Toolkit.find_desktop_windows", return_value=windows
            ), patch("app.runtime.Win32Controller") as controller:
                self.assertTrue(self.runtime._create_controller(settings)[0])
                self.assertEqual(self.runtime.controller_config["name"], controller_name)
                self.assertEqual(controller.call_args.kwargs["hWnd"], 333)
                self.assertEqual(controller.call_args.kwargs["screencap_method"], capture)

    def test_interface_controller_profiles_are_linked_to_resource(self):
        controllers = {
            controller["name"]: controller for controller in self.runtime.interface["controller"]
        }
        self.assertEqual(
            controllers["Win32FramePool"]["win32"]["screencap"], "FramePool"
        )
        self.assertEqual(
            controllers["Win32PrintWindow"]["win32"]["screencap"], "PrintWindow"
        )
        self.assertEqual(
            self.runtime.interface["controller"][0]["name"], "Win32PrintWindow"
        )
        self.assertEqual(
            self.runtime.resource_config["controller"],
            ["Win32PrintWindow", "Win32FramePool"],
        )

    def test_no_controller_settings_uses_first_supported_win32_controller(self):
        controller, status = self.runtime._select_controller_config()

        self.assertEqual(controller["name"], "Win32PrintWindow")
        self.assertEqual(status, "default")

    def test_omitted_controller_methods_match_runtime_defaults(self):
        self.runtime.interface["controller"] = [
            {
                "name": "DefaultMethods",
                "type": "Win32",
                "win32": {"window_regex": "^Blue Archive$"},
            }
        ]
        self.runtime.resource_config.pop("controller", None)
        settings = {
            "screencap": "Background",
            "mouse": "PostMessageWithWindowPos",
            "keyboard": "PostMessage",
        }

        controller, status = self.runtime._select_controller_config(settings)

        self.assertEqual(controller["name"], "DefaultMethods")
        self.assertEqual(status, "exact")

    def test_controller_selection_uses_screencap_mouse_keyboard_priority(self):
        self.runtime.interface["controller"] = [
            {
                "name": "KeyboardMatch",
                "type": "Win32",
                "win32": {
                    "window_regex": "^Blue Archive$",
                    "screencap": "GDI",
                    "mouse": "SendMessage",
                    "keyboard": "PostMessage",
                },
            },
            {
                "name": "ScreencapMatch",
                "type": "Win32",
                "win32": {
                    "window_regex": "^Blue Archive$",
                    "screencap": "FramePool",
                    "mouse": "SendMessage",
                    "keyboard": "SendMessage",
                },
            },
            {
                "name": "ScreencapMouseMatch",
                "type": "Win32",
                "win32": {
                    "window_regex": "^Blue Archive$",
                    "screencap": "FramePool",
                    "mouse": "PostMessageWithWindowPos",
                    "keyboard": "SendMessage",
                },
            },
        ]
        self.runtime.resource_config.pop("controller", None)
        settings = {
            "screencap": "FramePool",
            "mouse": "PostMessageWithWindowPos",
            "keyboard": "PostMessage",
        }

        controller, status = self.runtime._select_controller_config(settings)

        self.assertEqual(controller["name"], "ScreencapMouseMatch")
        self.assertEqual(status, "partial")

    def test_unmatched_controller_settings_fall_back_to_first_controller(self):
        settings = {
            "screencap": "GDI",
            "mouse": "SendMessage",
            "keyboard": "SendMessage",
        }

        controller, status = self.runtime._select_controller_config(settings)

        self.assertEqual(controller["name"], "Win32PrintWindow")
        self.assertEqual(status, "fallback")

    def test_controller_log_is_one_line_for_all_selection_results(self):
        expected_suffixes = {
            "default": "",
            "exact": "",
            "partial": " / 설정 일부 일치",
            "fallback": " / 폴백",
        }
        for status, suffix in expected_suffixes.items():
            with self.subTest(status=status):
                self.runtime._controller_selection_status = status
                message = self.runtime._get_controller_log_message()
                self.assertEqual(message, f"[화면 캡처: PrintWindow{suffix}]")
                self.assertNotIn("\n", message)

    def test_app_window_alone_is_not_a_game_window(self):
        windows = [SimpleNamespace(hwnd=111, window_name=WINDOW_TITLE)]
        with patch("app.runtime.Toolkit.find_desktop_windows", return_value=windows), patch(
            "app.runtime.Win32Controller"
        ) as controller:
            self.assertFalse(self.runtime._create_controller()[0])
        controller.assert_not_called()

    def test_all_tasks_are_queued_before_first_wait(self):
        tasker = MagicMock()
        first_job = make_job()
        second_job = make_job()
        posted_entries = []

        def post_task(entry, *args):
            posted_entries.append(entry)
            return first_job if entry == "First" else second_job

        def wait_first():
            self.assertEqual(posted_entries, ["First", "Second"])
            return first_job

        tasker.post_task.side_effect = post_task
        first_job.wait.side_effect = wait_first
        self.runtime.tasker = tasker
        self.runtime._target_hwnd = 123

        with patch.object(self.runtime, "_resize_window_for_task", return_value=True), patch.object(
            self.runtime, "release_session", return_value=(True, "released")
        ):
            succeeded, message = self.runtime.run_task(
                [("First", {}), ("Second", {})], minimize_window=True
            )

        self.assertTrue(succeeded)
        self.assertEqual(message, "모든 작업을 완료했습니다.")
        self.assertEqual(tasker.post_task.call_count, 2)
        first_job.wait.assert_called_once()
        second_job.wait.assert_called_once()
        self.runtime._user32.ShowWindow.assert_called_once_with(123, 6)

    def test_reinitialize_releases_old_binding_and_sink(self):
        self.configure_initialization()
        self.assertTrue(self.runtime.initialize()[0])
        old_controller = self.runtime.controller
        self.assertTrue(self.runtime.initialize()[0])
        self.assertIsNot(self.runtime.tasker, self.taskers[0])
        self.taskers[0].remove_context_sink.assert_called_once_with(7)
        old_controller.post_inactive.assert_called_once()
        self.assertTrue(self.runtime.release_session()[0])
        self.assertIsNone(self.runtime.controller)
        self.assertIsNone(self.runtime.tasker)

    def test_failed_binding_clears_partial_session_and_can_retry(self):
        self.configure_initialization()
        with patch.object(self.runtime, "_bind_tasker", return_value=(False, "bind failed")):
            self.assertEqual(self.runtime.initialize(), (False, "bind failed"))
        self.assertIsNone(self.runtime.controller)
        self.assertIsNone(self.runtime.tasker)
        self.assertIsNone(self.runtime._target_hwnd)
        self.assertTrue(self.runtime.initialize()[0])
        self.assertTrue(self.runtime.release_session()[0])

    def test_initialization_exception_clears_partial_session(self):
        self.configure_initialization()
        with patch.object(self.runtime, "_execute_controller", side_effect=RuntimeError("connection error")):
            succeeded, message = self.runtime.initialize()
        self.assertFalse(succeeded)
        self.assertIn("connection error", message)
        self.assertIsNone(self.runtime.controller)
        self.assertIsNone(self.runtime.tasker)
        self.assertIsNone(self.runtime._target_hwnd)

    def test_stop_completion_precedes_session_release(self):
        entered_wait = threading.Event()
        allow_stop = threading.Event()
        cleanup_started = threading.Event()
        cleanup_done = threading.Event()
        tasker = MagicMock(running=True, stopping=False)
        self.runtime.tasker = tasker
        stop_job = make_job()

        def wait_for_stop():
            entered_wait.set()
            if not allow_stop.wait(3):
                raise RuntimeError("test stop timed out")
            tasker.running = False
            return stop_job

        stop_job.wait.side_effect = wait_for_stop
        tasker.post_stop.return_value = stop_job
        results = []

        def cleanup():
            cleanup_started.set()
            results.append(self.runtime.release_session())
            cleanup_done.set()

        stopper = threading.Thread(target=lambda: results.append(self.runtime.stop_task()))
        cleaner = threading.Thread(target=cleanup)
        stopper.start()
        try:
            self.assertTrue(entered_wait.wait(2))
            cleaner.start()
            self.assertTrue(cleanup_started.wait(2))
            self.assertFalse(cleanup_done.wait(0.05))
            self.assertIs(self.runtime.tasker, tasker)
        finally:
            allow_stop.set()
            stopper.join(3)
            if cleaner.ident is not None:
                cleaner.join(3)
        self.assertFalse(stopper.is_alive())
        self.assertFalse(cleaner.is_alive())
        self.assertTrue(all(result[0] for result in results))
        self.assertIsNone(self.runtime.tasker)
        tasker.post_stop.assert_called_once()

    def test_failed_stop_keeps_session_for_retry(self):
        tasker = MagicMock(running=True, stopping=False)
        tasker.post_stop.return_value = make_job(False)
        self.runtime.tasker = tasker
        controller = self.runtime.controller = MagicMock()
        self.assertFalse(self.runtime.release_session()[0])
        self.assertIs(self.runtime.tasker, tasker)
        self.assertIs(self.runtime.controller, controller)

    def test_window_restore_failure_retains_original_placement(self):
        placement = self.runtime._original_window_placement = WindowPlacement()
        self.runtime._target_hwnd = 123
        self.runtime._user32.IsWindow.return_value = True
        self.runtime._user32.SetWindowPlacement.side_effect = [False, True]
        self.assertFalse(self.runtime.release_session()[0])
        self.assertIs(self.runtime._original_window_placement, placement)
        self.assertEqual(self.runtime._target_hwnd, 123)
        self.assertTrue(self.runtime.release_session()[0])
        self.assertIsNone(self.runtime._original_window_placement)

    def test_deactivation_failure_still_restores_window_and_allows_retry(self):
        controller = self.runtime.controller = MagicMock(connected=True)
        controller.post_inactive.return_value = make_job(False)
        self.runtime._original_window_placement = WindowPlacement()
        self.runtime._target_hwnd = 123
        self.assertFalse(self.runtime.release_session()[0])
        self.runtime._user32.SetWindowPlacement.assert_called_once()
        self.assertIsNone(self.runtime.controller)
        self.assertIsNone(self.runtime._target_hwnd)
        self.assertTrue(self.runtime.release_session()[0])


class TitleBarThemeTests(unittest.TestCase):
    def test_light_theme_forces_white_caption_and_dark_text(self):
        dwmapi = MagicMock()
        dwmapi.DwmSetWindowAttribute.return_value = 0

        self.assertTrue(apply_windows_title_bar_theme(123, TitleBarTheme.LIGHT, dwmapi))

        calls = dwmapi.DwmSetWindowAttribute.call_args_list
        self.assertEqual(
            [call.args[1] for call in calls],
            [DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR],
        )
        self.assertEqual([call.args[2]._obj.value for call in calls], [0, 0x00FFFFFF, 0])

    def test_dark_theme_uses_navy_caption_and_light_text(self):
        dwmapi = MagicMock()
        dwmapi.DwmSetWindowAttribute.return_value = 0

        self.assertTrue(apply_windows_title_bar_theme(123, TitleBarTheme.DARK, dwmapi))

        calls = dwmapi.DwmSetWindowAttribute.call_args_list
        self.assertEqual(
            [call.args[2]._obj.value for call in calls],
            [1, DARK_CAPTION_COLOR, DARK_CAPTION_TEXT_COLOR],
        )

    def test_system_theme_restores_default_caption_colors(self):
        dwmapi = MagicMock()
        dwmapi.DwmSetWindowAttribute.return_value = 0

        self.assertTrue(
            apply_windows_title_bar_theme(
                123,
                TitleBarTheme.SYSTEM,
                dwmapi,
                Qt.ColorScheme.Dark,
            )
        )

        calls = dwmapi.DwmSetWindowAttribute.call_args_list
        self.assertEqual(
            [call.args[2]._obj.value for call in calls],
            [1, DWM_COLOR_DEFAULT, DWM_COLOR_DEFAULT],
        )

    def test_system_theme_resolves_to_current_color_scheme(self):
        self.assertEqual(
            resolve_effective_theme(TitleBarTheme.SYSTEM, Qt.ColorScheme.Dark),
            TitleBarTheme.DARK,
        )
        self.assertEqual(
            resolve_effective_theme(TitleBarTheme.SYSTEM, Qt.ColorScheme.Light),
            TitleBarTheme.LIGHT,
        )


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        config_dir = Path(temp_dir.name) / "config"
        self.maa_config_path = config_dir / "maa_config.json"
        self.user_config_path = config_dir / "user_config.json"
        self.store = SettingsStore(
            self.maa_config_path,
            self.user_config_path,
        )

    @staticmethod
    def controller(name="Win32PrintWindow", screencap="PrintWindow"):
        return {
            "name": name,
            "label": f"Win32 ({screencap})",
            "type": "Win32",
            "win32": {
                "window_regex": "^Example$",
                "screencap": screencap,
                "mouse": "PostMessageWithWindowPos",
                "keyboard": "PostMessage",
            },
        }

    @staticmethod
    def read_json(path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_first_load_writes_defaults_and_first_controller(self):
        controller = self.controller()

        config = self.store.load(controller)

        self.assertFalse(config["general"]["minimize_enabled"])
        self.assertFalse(
            config["general"]["allow_option_edits_while_running"]
        )
        self.assertEqual(config["appearance"]["theme"], "light")
        self.assertEqual(config["controller"]["name"], controller["name"])
        self.assertNotIn("label", config["controller"])
        self.assertEqual(self.read_json(self.maa_config_path), config)
        self.assertFalse(self.user_config_path.exists())

    def test_only_minimize_enabled_is_migrated_from_user_config(self):
        tasks = [
            {
                "name": "Example Task",
                "entry": "ExampleTask",
                "checked": True,
                "selected_options": {"Mode": ["Default"]},
            }
        ]
        legacy_config = {
            "minimize_enabled": True,
            "tasks": tasks,
            "task_schema_version": 2,
        }
        self.user_config_path.parent.mkdir(parents=True)
        self.user_config_path.write_text(
            json.dumps(legacy_config, ensure_ascii=False),
            encoding="utf-8",
        )

        config = self.store.load(self.controller())

        self.assertTrue(config["general"]["minimize_enabled"])
        self.assertNotIn("tasks", self.read_json(self.maa_config_path))
        self.assertEqual(
            self.read_json(self.user_config_path),
            {"tasks": tasks, "task_schema_version": 2},
        )

    def test_existing_maa_config_wins_during_minimize_migration(self):
        self.maa_config_path.parent.mkdir(parents=True)
        self.maa_config_path.write_text(
            json.dumps(
                {
                    "general": {
                        "minimize_enabled": False,
                        "allow_option_edits_while_running": True,
                    },
                    "appearance": {"theme": "dark"},
                }
            ),
            encoding="utf-8",
        )
        self.user_config_path.write_text(
            json.dumps({"minimize_enabled": True, "tasks": []}),
            encoding="utf-8",
        )

        config = self.store.load(self.controller())

        self.assertFalse(config["general"]["minimize_enabled"])
        self.assertTrue(config["general"]["allow_option_edits_while_running"])
        self.assertEqual(config["appearance"]["theme"], "dark")
        self.assertEqual(self.read_json(self.user_config_path), {"tasks": []})


class UILifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp = temp_dir.name
        runtime = MagicMock()
        runtime.user_dir = Path(temp)
        runtime.interface = {
            "controller": [
                {
                    "name": "Win32PrintWindow",
                    "label": "Win32 (PrintWindow)",
                    "type": "Win32",
                    "win32": {
                        "window_regex": "^Example$",
                        "screencap": "PrintWindow",
                        "mouse": "PostMessageWithWindowPos",
                        "keyboard": "PostMessage",
                    },
                },
                {
                    "name": "Win32FramePool",
                    "label": "Win32 (FramePool)",
                    "type": "Win32",
                    "win32": {
                        "window_regex": "^Example$",
                        "screencap": "FramePool",
                        "mouse": "PostMessageWithWindowPos",
                        "keyboard": "PostMessage",
                    },
                },
            ],
            "task": [
                {
                    "name": "Test",
                    "entry": "Test_Main",
                    "default_check": True,
                    "option": ["Test_Mode", "Test_Dropdown", "Test_Input"],
                }
            ],
            "option": {
                "Test_Mode": {
                    "type": "radio",
                    "default_case": "A",
                    "cases": [
                        {
                            "name": "A",
                            "pipeline_override": {"Test_Node": {"next": ["A"]}},
                        },
                        {
                            "name": "B",
                            "pipeline_override": {"Test_Node": {"next": ["B"]}},
                        },
                    ],
                },
                "Test_Dropdown": {
                    "type": "select",
                    "cases": [
                        {
                            "name": "First",
                            "label": "첫 번째 선택지",
                            "pipeline_override": {
                                "Dropdown_Node": {"next": ["First"]}
                            },
                        },
                        {
                            "name": "Second",
                            "label": "두 번째 선택지",
                            "pipeline_override": {
                                "Dropdown_Node": {"next": ["Second"]}
                            },
                        },
                    ],
                },
                "Test_Input": {
                    "type": "input",
                    "inputs": [
                        {
                            "name": "Chapter",
                            "label": "Chapter",
                            "default": "4",
                            "pipeline_type": "string",
                            "verify": "^\\d+$",
                            "pattern_msg": "숫자만 입력해 주세요.",
                        },
                        {
                            "name": "Timeout",
                            "label": "Timeout",
                            "default": "20000",
                            "pipeline_type": "int",
                            "verify": "^\\d+$",
                        },
                        {
                            "name": "Enabled",
                            "label": "Enabled",
                            "default": "true",
                            "pipeline_type": "bool",
                        },
                    ],
                    "pipeline_override": {
                        "Input_Node": {
                            "next": "Chapter_{Chapter}",
                            "timeout": "{Timeout}",
                            "enabled": "{Enabled}",
                        }
                    },
                },
            },
        }
        runtime.resource_config = {
            "controller": ["Win32PrintWindow", "Win32FramePool"]
        }
        runtime.log_sink = LogSinkFocus()
        with patch("app.winUI.AppRuntime", return_value=runtime):
            self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)

    def start_mock_run(self):
        worker = MagicMock(succeeded=True, result_message="done")
        with patch("app.winUI.RuntimeWorker", return_value=worker):
            self.window.on_task_start()
        stop_worker = MagicMock(succeeded=True, result_message="stopped")
        self.window.stop_worker = stop_worker
        return worker

    def test_relocated_ui_and_svg_assets_load_outside_project_directory(self):
        original_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp)
                with patch("app.winUI.AppRuntime", return_value=self.window.runtime):
                    window = MainWindow()
                self.addCleanup(window.deleteLater)
                self.assertTrue((UI_DIR / DARK_QSS_FILENAME).is_file())
                dark_stylesheet = (UI_DIR / DARK_QSS_FILENAME).read_text(
                    encoding="utf-8"
                )
                self.assertRegex(
                    dark_stylesheet,
                    r"(?s)QTabWidget::pane\s*\{.*?border:\s*1px solid #30415E;",
                )
                self.assertRegex(
                    dark_stylesheet,
                    r"(?s)QTabBar::tab\s*\{.*?border:\s*1px solid #30415E;",
                )
                widget = window.option_list_widget.itemWidget(window.option_list_widget.item(0))
                self.assertFalse(widget.setting_btn.icon().pixmap(20, 20).isNull())
                stylesheet = (UI_DIR / "style.qss").read_text(encoding="utf-8")
                referenced_icons = re.findall(r'maabaicons:([^"\s)]+)', stylesheet)
                self.assertTrue(referenced_icons)
                for relative_path in referenced_icons:
                    with self.subTest(icon=relative_path):
                        self.assertTrue((UI_RESOURCE_DIR / "icons" / relative_path).is_file())
                        self.assertTrue(QSvgRenderer("maabaicons:" + relative_path).isValid())
            finally:
                os.chdir(original_directory)

    def test_settings_tab_uses_navigation_and_single_scroll_area(self):
        panel = self.window.settings_panel

        self.assertEqual(panel.navigation.count(), 3)
        self.assertEqual(
            [panel.navigation.item(index).text() for index in range(3)],
            ["일반", "컨트롤러", "외관"],
        )
        self.assertIs(panel.detail_scroll.widget(), panel.detail_contents)
        self.assertEqual(panel.layout().stretch(0), 2)
        self.assertEqual(panel.layout().stretch(1), 8)
        self.assertEqual(
            panel.controller_settings()["name"],
            "Win32PrintWindow",
        )

        rows = panel.findChildren(QFrame, "settingsRow")
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(row=row):
                self.assertIsInstance(row.layout(), QGridLayout)
                row_margins = row.layout().contentsMargins()
                self.assertEqual((row_margins.top(), row_margins.bottom()), (12, 12))
                self.assertIs(
                    row.layout().itemAtPosition(0, 1).widget(),
                    row.layout().itemAtPosition(1, 1).widget(),
                )

        sections = panel.findChildren(QFrame, "settingsSection")
        self.assertTrue(sections)
        for section in sections:
            with self.subTest(section=section):
                section_margins = section.layout().contentsMargins()
                self.assertEqual(
                    (section_margins.top(), section_margins.bottom()),
                    (12, 12),
                )

    def test_dark_theme_applies_to_entire_window_and_is_saved(self):
        panel = self.window.settings_panel
        panel.theme_combo.setCurrentIndex(panel.theme_combo.findData("dark"))
        self.window.update_tab_widths()

        self.assertEqual(self.window._effective_theme, TitleBarTheme.DARK)
        self.assertIn("#111A2B", self.window.styleSheet())
        tab_bar_style = self.window.ui.tabWidget.tabBar().styleSheet()
        self.assertNotIn("background-color", tab_bar_style)
        self.assertNotIn("border", tab_bar_style)
        config_path = self.window.runtime.user_dir / "config" / "maa_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["appearance"]["theme"], "dark")

        panel.theme_combo.setCurrentIndex(panel.theme_combo.findData("light"))
        self.assertEqual(self.window._effective_theme, TitleBarTheme.LIGHT)
        self.assertNotIn("#111A2B", self.window.styleSheet())

    def test_minimize_setting_is_saved_only_to_maa_config(self):
        self.window.ui.minimizeEnableBtn.setChecked(True)
        self.window.save_user_config()

        config_dir = self.window.runtime.user_dir / "config"
        maa_config = json.loads(
            (config_dir / "maa_config.json").read_text(encoding="utf-8")
        )
        user_config = json.loads(
            (config_dir / "user_config.json").read_text(encoding="utf-8")
        )
        self.assertTrue(maa_config["general"]["minimize_enabled"])
        self.assertNotIn("minimize_enabled", user_config)
        self.assertIn("tasks", user_config)

    def test_selected_controller_is_passed_to_next_runtime_worker(self):
        self.window.settings_panel.controller_combo.setCurrentIndex(1)
        worker = MagicMock()

        with patch("app.winUI.RuntimeWorker", return_value=worker) as worker_type:
            self.window.on_task_start()

        args, kwargs = worker_type.call_args
        self.assertIs(args[0], self.window.runtime)
        self.assertEqual(args[2], self.window.ui.minimizeEnableBtn.isChecked())
        self.assertEqual(
            kwargs["controller_settings"]["name"],
            "Win32FramePool",
        )
        worker.start.assert_called_once()

    def test_task_details_remain_viewable_while_option_values_are_locked(self):
        item = self.window.option_list_widget.item(0)
        task_widget = self.window.option_list_widget.itemWidget(item)
        queued_before_start = self.window.build_execution_queue()

        self.start_mock_run()
        self.assertFalse(task_widget.checkbox.isEnabled())
        self.assertTrue(task_widget.setting_btn.isEnabled())

        task_widget.setting_btn.click()
        self.assertFalse(self.window.ui.scrollSettingContents.isEnabled())

        self.window.settings_panel.runtime_edit_checkbox.setChecked(True)
        self.assertTrue(self.window.ui.scrollSettingContents.isEnabled())
        task_widget.selected_options["Test_Mode"] = ["B"]
        queued_after_change = self.window.build_execution_queue()
        self.assertEqual(queued_before_start[0][1]["Test_Node"]["next"], ["A"])
        self.assertEqual(queued_after_change[0][1]["Test_Node"]["next"], ["B"])

        self.window.settings_panel.runtime_edit_checkbox.setChecked(False)
        self.assertFalse(self.window.ui.scrollSettingContents.isEnabled())
        self.window.on_task_finished()
        self.window.on_stop_worker_finished()

    def test_input_option_renders_and_builds_typed_pipeline_override(self):
        item = self.window.option_list_widget.item(0)
        task_widget = self.window.option_list_widget.itemWidget(item)
        task_widget.setting_btn.click()

        input_widgets = {
            widget.property("inputName"): widget
            for widget in self.window.ui.scrollSettingContents.findChildren(QLineEdit)
        }
        self.assertEqual(set(input_widgets), {"Chapter", "Timeout", "Enabled"})
        self.assertEqual(input_widgets["Chapter"].text(), "4")

        input_widgets["Chapter"].setText("12")
        input_widgets["Timeout"].setText("3500")
        input_widgets["Enabled"].setText("false")

        override = self.window.build_execution_queue()[0][1]["Input_Node"]
        self.assertEqual(override["next"], "Chapter_12")
        self.assertEqual(override["timeout"], 3500)
        self.assertIs(override["enabled"], False)

    def test_standard_select_uses_dropdown_and_radio_extension_stays_separate(self):
        item = self.window.option_list_widget.item(0)
        task_widget = self.window.option_list_widget.itemWidget(item)
        task_widget.setting_btn.click()

        radio_buttons = self.window.ui.scrollSettingContents.findChildren(QRadioButton)
        self.assertEqual(len(radio_buttons), 2)

        combo_boxes = self.window.ui.scrollSettingContents.findChildren(QComboBox)
        self.assertEqual(len(combo_boxes), 1)
        combo_box = combo_boxes[0]
        self.assertEqual(combo_box.property("optionName"), "Test_Dropdown")
        self.assertEqual(combo_box.currentData(), "First")
        self.assertEqual(combo_box.itemText(1), "두 번째 선택지")

        combo_box.setCurrentIndex(1)

        self.assertEqual(task_widget.selected_options["Test_Dropdown"], ["Second"])
        override = self.window.build_execution_queue()[0][1]
        self.assertEqual(override["Dropdown_Node"]["next"], ["Second"])

    def test_invalid_input_disables_start_and_displays_pattern_message(self):
        item = self.window.option_list_widget.item(0)
        task_widget = self.window.option_list_widget.itemWidget(item)
        task_widget.setting_btn.click()
        chapter_input = next(
            widget
            for widget in self.window.ui.scrollSettingContents.findChildren(QLineEdit)
            if widget.property("inputName") == "Chapter"
        )

        chapter_input.setText("invalid")

        self.assertFalse(self.window.ui.workStartBtn.isEnabled())
        self.assertFalse(chapter_input.property("inputValid"))
        error_labels = self.window.ui.scrollSettingContents.findChildren(
            QLabel, "optionInputError"
        )
        self.assertIn("숫자만 입력해 주세요.", [label.text() for label in error_labels])

        chapter_input.setText("7")
        self.assertTrue(self.window.ui.workStartBtn.isEnabled())
        self.assertTrue(chapter_input.property("inputValid"))

    def test_restart_waits_for_both_finished_callbacks(self):
        self.start_mock_run()
        self.window.on_task_finished()
        self.assertTrue(self.window.isRunning)
        self.assertFalse(self.window.ui.workStartBtn.isEnabled())
        self.window.on_task_start()
        self.assertIsNone(self.window.worker)
        self.window.on_stop_worker_finished()
        self.assertFalse(self.window.isRunning)
        self.assertTrue(self.window.ui.workStartBtn.isEnabled())

    def test_stop_callback_first_keeps_run_active(self):
        worker = self.start_mock_run()
        self.window.on_stop_worker_finished()
        self.assertTrue(self.window.isRunning)
        self.assertIs(self.window.worker, worker)
        self.window.on_task_finished()
        self.assertFalse(self.window.isRunning)
        self.assertTrue(self.window.ui.workStartBtn.isEnabled())

    def test_close_waits_for_callbacks_even_when_thread_has_finished(self):
        worker = self.start_mock_run()
        worker.isRunning.return_value = False
        self.window.stop_worker.isRunning.return_value = False
        event = MagicMock()
        self.window.closeEvent(event)
        event.ignore.assert_called_once()
        self.assertTrue(self.window._close_pending)
        self.window.on_task_finished()
        self.window.on_stop_worker_finished()
        self.assertFalse(self.window.ui.workStartBtn.isEnabled())

    def test_cancelled_initialization_always_releases_session(self):
        runtime = MagicMock()
        runtime.initialize.return_value = (True, "initialized")
        runtime.release_session.return_value = (True, "released")
        controller_settings = {
            "screencap": "FramePool",
            "mouse": "PostMessageWithWindowPos",
            "keyboard": "PostMessage",
        }
        worker = RuntimeWorker(
            runtime,
            [],
            minimize_window=True,
            controller_settings=controller_settings,
        )
        with patch.object(worker, "isInterruptionRequested", return_value=True):
            worker.run()
        runtime.initialize.assert_called_once_with(controller_settings)
        runtime.run_task.assert_not_called()
        runtime.release_session.assert_called_once()
        self.assertFalse(worker.succeeded)


if __name__ == "__main__":
    unittest.main()
