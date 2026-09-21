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

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView,
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

from app.pg_init import (
    DEFAULT_PROGRAM_CONFIG,
    find_auto_program_executable,
    find_program_executable,
    normalize_program_config,
    resolve_manual_program_path,
)
from app.runtime import (
    AppRuntime,
    LogSinkFocus,
    PROGRAM_WINDOW_STABLE_SECONDS,
    WindowPlacement,
)
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
    PROGRAM_LAUNCH_ENTRY,
    PROGRAM_LAUNCH_TASK_NAME,
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

        def create_controller(controller_settings, **_kwargs):
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

    def test_controller_waits_for_launched_program_window(self):
        game_window = SimpleNamespace(hwnd=333, window_name="Blue Archive")
        with patch(
            "app.runtime.Toolkit.find_desktop_windows",
            side_effect=[[], [game_window]],
        ) as find_windows, patch("app.runtime.Win32Controller"), patch(
            "app.runtime.time.sleep"
        ):
            succeeded, _message = self.runtime._create_controller(
                wait_timeout_seconds=5
            )

        self.assertTrue(succeeded)
        self.assertEqual(find_windows.call_count, 2)

    def test_controller_ignores_transient_window_until_target_is_stable(self):
        transient_window = SimpleNamespace(hwnd=222, window_name="Blue Archive")
        game_window = SimpleNamespace(hwnd=333, window_name="Blue Archive")
        with patch(
            "app.runtime.Toolkit.find_desktop_windows",
            side_effect=[[transient_window], [], [game_window], [game_window]],
        ) as find_windows, patch(
            "app.runtime.time.monotonic", side_effect=[0, 0, 1, 2, 7]
        ), patch("app.runtime.Win32Controller") as controller, patch(
            "app.runtime.time.sleep"
        ):
            succeeded, _message = self.runtime._create_controller(
                wait_timeout_seconds=30,
                stable_window_seconds=PROGRAM_WINDOW_STABLE_SECONDS,
            )

        self.assertTrue(succeeded)
        self.assertEqual(find_windows.call_count, 4)
        self.assertEqual(controller.call_args.kwargs["hWnd"], game_window.hwnd)

    def test_program_launch_starts_configured_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "BlueArchive.exe"
            executable.write_bytes(b"")
            process = MagicMock()
            settings = {
                "resolved_path": str(executable),
                "startup_wait_seconds": 45,
            }

            with patch.object(
                self.runtime, "_program_is_running", return_value=False
            ), patch(
                "app.runtime.subprocess.Popen", return_value=process
            ) as popen:
                succeeded, _message = self.runtime._launch_program(settings)

        self.assertTrue(succeeded)
        command = popen.call_args.args[0]
        self.assertEqual(command, [str(executable.resolve())])
        self.assertEqual(popen.call_args.kwargs["cwd"], str(executable.parent.resolve()))

    def test_program_launch_uses_steam_url_for_steam_install(self):
        with tempfile.TemporaryDirectory() as temp:
            steam_root = Path(temp) / "Steam"
            launcher = steam_root / "steam.exe"
            executable = (
                steam_root
                / "steamapps"
                / "common"
                / "BlueArchive"
                / "BlueArchive.exe"
            )
            launcher.parent.mkdir(parents=True, exist_ok=True)
            executable.parent.mkdir(parents=True, exist_ok=True)
            launcher.write_bytes(b"")
            executable.write_bytes(b"")
            manifest = steam_root / "steamapps" / "appmanifest_3557620.acf"
            manifest.write_text(
                '"AppState"\n{\n'
                '\t"appid"\t\t"3557620"\n'
                f'\t"LauncherPath"\t\t"{launcher}"\n'
                '\t"installdir"\t\t"BlueArchive"\n'
                '}\n',
                encoding="utf-8",
            )
            settings = {"resolved_path": str(executable)}

            with patch.object(
                self.runtime, "_program_is_running", return_value=False
            ), patch("app.runtime.subprocess.Popen") as popen:
                succeeded, _message = self.runtime._launch_program(settings)

        self.assertTrue(succeeded)
        self.assertEqual(
            popen.call_args.args[0],
            [str(launcher.resolve()), "steam://run/3557620"],
        )
        self.assertEqual(popen.call_args.kwargs["cwd"], str(steam_root.resolve()))

    def test_program_launch_skips_executable_when_process_is_running(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "BlueArchive.exe"
            executable.write_bytes(b"")
            settings = {"resolved_path": str(executable)}

            with patch.object(
                self.runtime, "_program_is_running", return_value=True
            ), patch("app.runtime.subprocess.Popen") as popen:
                succeeded, message = self.runtime._launch_program(settings)

        self.assertTrue(succeeded)
        self.assertIn("이미 실행", message)
        popen.assert_not_called()

    def test_launch_task_prepares_program_before_waiting_for_controller(self):
        self.configure_initialization()
        settings = {
            "resolved_path": "C:/Games/BlueArchive.exe",
            "startup_wait_seconds": 25,
        }
        with patch.object(
            self.runtime, "_launch_program", return_value=(True, "started")
        ) as launch_program:
            succeeded, _message = self.runtime.initialize(
                program_settings=settings,
                execution_queue=[(PROGRAM_LAUNCH_ENTRY, {})],
            )

        self.assertTrue(succeeded)
        launch_program.assert_called_once_with(settings)
        create_call = self.runtime._create_controller.call_args
        self.assertEqual(create_call.kwargs["wait_timeout_seconds"], 25)
        self.assertEqual(
            create_call.kwargs["stable_window_seconds"],
            PROGRAM_WINDOW_STABLE_SECONDS,
        )

    def test_launch_task_rejects_missing_program_path_before_controller_creation(self):
        self.configure_initialization()

        succeeded, message = self.runtime.initialize(
            program_settings={"resolved_path": ""},
            execution_queue=[(PROGRAM_LAUNCH_ENTRY, {})],
        )

        self.assertFalse(succeeded)
        self.assertIn("경로", message)
        self.runtime._create_controller.assert_not_called()

    def test_launch_task_is_not_posted_to_tasker(self):
        tasker = MagicMock()
        tasker.post_task.return_value = make_job()
        self.runtime.tasker = tasker
        self.runtime._target_hwnd = 123

        with patch.object(
            self.runtime, "_resize_window_for_task", return_value=True
        ), patch.object(
            self.runtime, "release_session", return_value=(True, "released")
        ):
            succeeded, _message = self.runtime.run_task(
                [(PROGRAM_LAUNCH_ENTRY, {}), ("Login_Main", {})]
            )

        self.assertTrue(succeeded)
        tasker.post_task.assert_called_once_with("Login_Main")

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

    def test_failed_task_message_uses_interface_label_without_queue_summary(self):
        tasker = MagicMock()
        tasker.post_task.return_value = make_job(False)
        self.runtime.tasker = tasker
        self.runtime._target_hwnd = 123

        with patch.object(
            self.runtime, "_resize_window_for_task", return_value=True
        ), patch.object(
            self.runtime, "release_session", return_value=(True, "released")
        ):
            succeeded, message = self.runtime.run_task([("Login_Main", {})])

        self.assertFalse(succeeded)
        self.assertEqual(message, "일부 작업에 실패했습니다: 게임 로그인")
        self.assertNotIn("전체 실행 작업", message)

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
        self.assertEqual(config["program"], DEFAULT_PROGRAM_CONFIG)
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

    def test_program_config_is_normalized_and_saved_at_top_level(self):
        self.maa_config_path.parent.mkdir(parents=True)
        self.maa_config_path.write_text(
            json.dumps(
                {
                    "program": {
                        "executable_name": "Example.exe",
                        "manual_path": " C:/Games/Example ",
                        "search_paths": ["Games/Example/Example.exe"],
                        "startup_wait_seconds": 12.8,
                        "recognition_timeout_seconds": 90,
                        "poll_interval_seconds": 0.5,
                    }
                }
            ),
            encoding="utf-8",
        )

        config = self.store.load(self.controller())

        self.assertEqual(config["program"]["executable_name"], "Example.exe")
        self.assertEqual(config["program"]["manual_path"], "C:/Games/Example")
        self.assertEqual(config["program"]["startup_wait_seconds"], 12)
        self.assertEqual(config["program"]["poll_interval_seconds"], 0.5)
        self.assertEqual(self.read_json(self.maa_config_path)["program"], config["program"])


class ProgramLocatorTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name)

    def test_auto_search_checks_each_configured_drive_relative_path(self):
        executable = (
            self.root
            / "SteamLibrary"
            / "steamapps"
            / "common"
            / "BlueArchive"
            / "BlueArchive.exe"
        )
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"")

        detected = find_auto_program_executable(
            DEFAULT_PROGRAM_CONFIG, drive_roots=[self.root]
        )

        self.assertEqual(detected, executable.resolve())

    def test_manual_directory_has_priority_and_requires_target_executable(self):
        manual_directory = self.root / "CustomBlueArchive"
        manual_directory.mkdir()
        executable = manual_directory / "BlueArchive.exe"
        executable.write_bytes(b"")
        config = normalize_program_config(
            {"manual_path": str(manual_directory)}
        )

        self.assertEqual(
            resolve_manual_program_path(
                str(manual_directory), config["executable_name"]
            ),
            executable.resolve(),
        )
        self.assertEqual(
            find_program_executable(config, drive_roots=[]), executable.resolve()
        )
        self.assertIsNone(
            resolve_manual_program_path(
                str(manual_directory / "Other.exe"), config["executable_name"]
            )
        )


class UILifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp = temp_dir.name
        auto_search_patch = patch(
            "app.settingsUI.find_auto_program_executable", return_value=None
        )
        resolved_path_patch = patch(
            "app.settingsUI.find_program_executable",
            side_effect=lambda config: find_program_executable(config, drive_roots=[]),
        )
        auto_search_patch.start()
        resolved_path_patch.start()
        self.addCleanup(auto_search_patch.stop)
        self.addCleanup(resolved_path_patch.stop)
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

    @staticmethod
    def find_task_item(window, task_name="Test"):
        task_list = window.option_list_widget
        for row in range(task_list.count()):
            item = task_list.item(row)
            widget = task_list.itemWidget(item)
            if widget is not None and widget.task_data.get("name") == task_name:
                return item
        raise AssertionError(f"작업 목록에서 {task_name!r} 항목을 찾지 못했습니다.")

    @classmethod
    def find_task_widget(cls, window, task_name="Test"):
        item = cls.find_task_item(window, task_name)
        return window.option_list_widget.itemWidget(item)

    def test_task_picker_opens_above_full_width_and_appends_on_click(self):
        self.window.show()
        self.app.processEvents()
        task_list = self.window.option_list_widget
        actions = self.window.task_list_actions
        self.assertEqual(actions.width(), task_list.width())
        self.assertEqual(
            actions.mapToGlobal(QPoint(0, 0)).x(),
            task_list.mapToGlobal(QPoint(0, 0)).x(),
        )
        reset = self.window.task_reset_button
        add = self.window.task_add_button
        self.assertIs(task_list, self.window.ui.taskOptionList)
        self.assertIs(actions, self.window.ui.taskListFooter)
        self.assertIs(reset, self.window.ui.taskResetButton)
        self.assertIs(add, self.window.ui.taskAddButton)
        self.assertEqual(add.x() - (reset.x() + reset.width()), 0)
        self.assertEqual(reset.width(), 34)
        self.assertEqual(add.width() + reset.width(), actions.width())
        self.assertEqual(add.height(), 34)
        separator = self.window.ui.taskListSeparator
        self.assertEqual(separator.height(), 1)
        self.assertGreaterEqual(separator.y(), task_list.y() + task_list.height())
        self.assertGreaterEqual(
            self.window.ui.taskListActionsStack.y(),
            separator.y() + separator.height(),
        )
        self.assertEqual(reset.toolTip(), "")
        self.assertFalse(reset.icon().isNull())
        self.assertEqual(reset.iconSize(), QSize(18, 18))
        QApplication.sendEvent(reset, QEvent(QEvent.Type.Enter))
        self.assertEqual(reset.iconSize(), QSize(22, 22))
        QApplication.sendEvent(reset, QEvent(QEvent.Type.Leave))
        self.assertEqual(reset.iconSize(), QSize(18, 18))

        add.click()
        self.app.processEvents()
        popup = self.window.task_picker
        self.assertTrue(popup.isVisible())
        container = self.window.ui.taskListContainer
        self.assertEqual(popup.width(), container.width())
        self.assertEqual(popup.x(), container.mapToGlobal(QPoint(0, 0)).x())
        self.assertEqual(popup.sizeHintForRow(0), task_list.sizeHintForRow(0))
        popup_chrome = popup.height() - popup.viewport().height()
        self.assertEqual(
            popup.height(),
            popup.sizeHintForRow(0) * popup.count() + popup_chrome,
        )
        self.assertLess(popup.height(), task_list.height())
        self.assertEqual(popup.y() + popup.height(), actions.mapToGlobal(QPoint(0, 0)).y())
        self.assertTrue(popup.windowFlags() & Qt.WindowType.NoDropShadowWindowHint)
        self.assertEqual(popup.count(), len(self.window.runtime.interface["task"]) + 1)
        self.assertEqual(popup.item(0).data(Qt.UserRole)["name"], PROGRAM_LAUNCH_TASK_NAME)
        self.assertEqual(popup.item(0).toolTip(), "")
        self.assertEqual(popup._dismiss_timer.interval(), 80)
        popup_position = popup.mapToGlobal(popup.rect().center())
        with patch("app.winUI.QCursor.pos", return_value=popup_position):
            popup._hide_if_pointer_outside()
        self.assertTrue(popup.isVisible())
        with patch("app.winUI.QCursor.pos", return_value=QPoint(-100, -100)):
            popup._hide_if_pointer_outside()
        self.assertFalse(popup.isVisible())
        add.click()
        self.app.processEvents()
        QTest.mouseClick(
            popup.viewport(), Qt.MouseButton.LeftButton,
            pos=popup.visualItemRect(popup.item(0)).center(),
        )
        self.assertFalse(popup.isVisible())
        self.assertEqual(task_list.count(), 2)
        self.assertEqual(task_list.item(1).data(Qt.UserRole), "Test")

        add.click()
        QTest.keyClick(popup, Qt.Key.Key_Escape)
        self.assertFalse(popup.isVisible())
        self.assertEqual(task_list.count(), 2)
        self.window.hide()

    def test_duplicate_tasks_keep_independent_options_on_reload_and_execution(self):
        task_list = self.window.option_list_widget
        first = self.find_task_widget(self.window)
        first.selected_options["Test_Mode"] = ["B"]
        self.window.add_task(self.window.runtime.interface["task"][0])
        second = task_list.itemWidget(task_list.item(task_list.count() - 1))
        self.assertEqual(second.selected_options["Test_Mode"], ["A"])
        second.checkbox.setChecked(False)
        self.assertNotEqual(
            self.find_task_item(self.window).data(Qt.UserRole + 1),
            task_list.item(task_list.count() - 1).data(Qt.UserRole + 1),
        )

        with patch("app.winUI.AppRuntime", return_value=self.window.runtime):
            restored = MainWindow()
        self.addCleanup(restored.deleteLater)
        restored_list = restored.option_list_widget
        self.assertEqual(restored_list.count(), 3)
        widgets = [
            restored_list.itemWidget(restored_list.item(i))
            for i in range(restored_list.count())
            if restored_list.item(i).data(Qt.UserRole) == "Test"
        ]
        self.assertEqual([w.selected_options["Test_Mode"] for w in widgets], [["B"], ["A"]])
        self.assertEqual([w.is_checked() for w in widgets], [True, False])
        self.assertEqual(len(restored.build_execution_queue()), 1)
        widgets[1].checkbox.setChecked(True)
        queue = restored.build_execution_queue()
        self.assertEqual([entry for entry, _ in queue], ["Test_Main", "Test_Main"])
        self.assertEqual([override["Test_Node"]["next"] for _, override in queue], [["B"], ["A"]])

    def test_dragging_replaces_actions_with_delete_zone_and_removes_dropped_task(self):
        task_list = self.window.option_list_widget
        stack = self.window.task_list_actions_stack
        delete_page = self.window.task_delete_page
        delete_zone = self.window.task_delete_drop_zone
        self.window.show()
        self.app.processEvents()

        self.assertIs(stack.currentWidget(), self.window.task_list_actions)
        self.window.on_task_drag_started()
        self.app.processEvents()
        self.assertIs(stack.currentWidget(), delete_page)
        self.assertTrue(self.window.ui.taskListSeparator.isHidden())
        self.assertLess(delete_zone.width(), stack.width())
        self.assertLess(delete_zone.height(), stack.height())
        margins = delete_page.layout().contentsMargins()
        self.assertEqual(
            (margins.left(), margins.top(), margins.right(), margins.bottom()),
            (4, 4, 4, 4),
        )
        self.assertEqual(self.window.ui.taskDeleteLabel.text(), "제거")
        self.assertFalse(
            (UI_RESOURCE_DIR / "icons/actions/trash.svg").exists()
        )

        dragged_item = self.find_task_item(self.window)
        drop_position = delete_zone.mapToGlobal(delete_zone.rect().center())
        self.window.on_task_drag_finished(dragged_item, drop_position)
        self.assertIs(stack.currentWidget(), self.window.task_list_actions)
        self.assertFalse(self.window.ui.taskListSeparator.isHidden())
        self.assertEqual(task_list.count(), 1)
        self.assertFalse(self.window.ui.workStartBtn.isEnabled())
        config_path = self.window.runtime.user_dir / "config" / "user_config.json"
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [task["name"] for task in saved["tasks"]],
            [PROGRAM_LAUNCH_TASK_NAME],
        )
        self.window.hide()

    def test_drag_release_outside_delete_zone_keeps_task(self):
        task_list = self.window.option_list_widget
        dragged_item = self.find_task_item(self.window)
        self.window.on_task_drag_started()
        outside = self.window.option_list_widget.mapToGlobal(QPoint(2, 2))
        self.window.on_task_drag_finished(dragged_item, outside)
        self.assertEqual(task_list.count(), 2)
        self.assertFalse(self.window.ui.taskListSeparator.isHidden())
        self.assertIs(
            self.window.task_list_actions_stack.currentWidget(),
            self.window.task_list_actions,
        )

    def test_drag_preview_hides_source_and_uses_text_only_compact_pixmap(self):
        task_list = self.window.option_list_widget
        self.window.show()
        self.app.processEvents()
        item = task_list.item(0)
        task_list.setCurrentItem(item)
        drag = MagicMock()

        def inspect_drag_state(*_args):
            self.assertTrue(item.isHidden())
            self.assertTrue(self.window.ui.taskListSeparator.isHidden())
            self.assertIs(
                self.window.task_list_actions_stack.currentWidget(),
                self.window.task_delete_page,
            )
            return Qt.DropAction.IgnoreAction

        drag.exec.side_effect = inspect_drag_state
        cursor_position = task_list.viewport().mapToGlobal(QPoint(20, 20))
        with patch("app.winUI.QDrag", return_value=drag), patch(
            "app.winUI.QCursor.pos", return_value=cursor_position
        ):
            task_list.startDrag(Qt.DropAction.MoveAction)

        preview = drag.setPixmap.call_args.args[0]
        self.assertLess(preview.width(), task_list.viewport().width())
        self.assertLess(preview.height(), item.sizeHint().height())
        preview_alpha = preview.toImage().pixelColor(2, 2).alpha()
        self.assertGreater(preview_alpha, 0)
        self.assertLess(preview_alpha, 255)
        self.assertFalse(item.isHidden())
        self.assertFalse(self.window.ui.taskListSeparator.isHidden())
        self.assertIs(
            self.window.task_list_actions_stack.currentWidget(),
            self.window.task_list_actions,
        )
        self.window.hide()

    def test_custom_drag_reorders_item_widget_and_top_line_keeps_full_width(self):
        task_list = self.window.option_list_widget
        task_data = self.window.runtime.interface["task"][0]
        self.window.add_task(task_data)
        self.window.add_task(task_data)
        self.window.show()
        self.app.processEvents()
        source = task_list.item(task_list.count() - 1)
        source_widget = task_list.itemWidget(source)
        first = self.find_task_item(self.window)
        task_list._dragged_item = source
        source.setHidden(True)

        first_rect = task_list.visualItemRect(first)
        task_list._update_drop_target(first_rect.topLeft())
        self.assertIs(task_list._drop_before_item, first)
        self.assertGreaterEqual(task_list._bounded_drag_line_y(), 1)
        line_start, line_end = task_list._drag_line_span()
        self.assertEqual(task_list.DRAG_LINE_MARGIN, 5)
        self.assertEqual(line_start, task_list.DRAG_LINE_MARGIN)
        self.assertEqual(line_end, task_list.viewport().width() - task_list.DRAG_LINE_MARGIN)
        first_widget = task_list.itemWidget(first)
        checkbox_left = first_widget.checkbox.mapTo(task_list.viewport(), QPoint(0, 0)).x()
        settings_icon_right = (
            first_widget.setting_btn.mapTo(task_list.viewport(), QPoint(0, 0)).x()
            + (first_widget.setting_btn.width() + first_widget.setting_btn.iconSize().width()) // 2
        )
        self.assertEqual(line_start, checkbox_left)
        self.assertEqual(line_end, settings_icon_right)
        with patch.object(task_list, "removeItemWidget", wraps=task_list.removeItemWidget) as remove:
            self.assertTrue(task_list._move_dragged_item(first))
        remove.assert_not_called()
        self.app.processEvents()
        self.assertIs(task_list.item(1), source)
        self.assertIs(task_list.itemWidget(source), source_widget)
        self.assertTrue(task_list._move_dragged_item(None))
        self.app.processEvents()
        self.assertIs(task_list.item(task_list.count() - 1), source)
        self.assertIs(task_list.itemWidget(source), source_widget)
        source.setHidden(False)
        self.assertFalse(source.isHidden())
        self.window.hide()

    def test_reset_and_footer_hover_keep_icon_scale_and_group_background(self):
        reset = self.window.task_reset_button
        footer = self.window.task_list_actions
        QApplication.sendEvent(reset, QEvent(QEvent.Type.Enter))
        self.assertTrue(footer.property("groupHovered"))
        self.assertEqual(reset.iconSize(), QSize(22, 22))
        stylesheet = self.window.styleSheet()
        self.assertIn('QWidget#taskListFooter[groupHovered="true"]', stylesheet)

    def test_long_task_picker_scrolls_and_supports_keyboard_selection(self):
        self.window.runtime.interface["task"] = [
            {"name": f"Task{i}", "label": "Long task label " * 10, "entry": f"Entry{i}"}
            for i in range(40)
        ]
        self.window.show()
        self.app.processEvents()
        self.window.task_add_button.click()
        self.app.processEvents()
        popup = self.window.task_picker
        self.assertEqual(popup.count(), 41)
        self.assertEqual(popup.width(), self.window.ui.taskListContainer.width())
        self.assertEqual(
            popup.sizeHintForRow(0),
            self.window.option_list_widget.sizeHintForRow(0),
        )
        self.assertEqual(popup.height(), self.window.option_list_widget.height())
        self.assertGreater(popup.verticalScrollBar().maximum(), 0)
        QTest.keyClick(popup, Qt.Key.Key_End)
        QTest.keyClick(popup, Qt.Key.Key_Return)
        self.assertFalse(popup.isVisible())
        task_list = self.window.option_list_widget
        self.assertEqual(task_list.item(task_list.count() - 1).data(Qt.UserRole), "Task39")
        self.window.hide()

    def test_reset_restores_interface_defaults_and_clears_detail_controls(self):
        self.window.runtime.interface["task"].append({
            "name": "Other", "entry": "Other_Main", "default_check": False,
        })
        task_list = self.window.option_list_widget
        first = self.find_task_widget(self.window)
        first.selected_options["Test_Mode"] = ["B"]
        first.checkbox.setChecked(False)
        self.window.show_sub_cases(first)
        self.window.add_task(self.window.runtime.interface["task"][0])
        self.window.task_reset_button.click()

        self.assertEqual(task_list.count(), 3)
        self.assertEqual(
            [task_list.item(i).data(Qt.UserRole) for i in range(3)],
            [PROGRAM_LAUNCH_TASK_NAME, "Test", "Other"],
        )
        widgets = [task_list.itemWidget(task_list.item(i)) for i in range(1, 3)]
        self.assertEqual([w.is_checked() for w in widgets], [True, False])
        self.assertEqual(widgets[0].selected_options["Test_Mode"], ["A"])
        self.assertEqual(self.window.ui.scrollSettingContents.layout().count(), 0)
        path = self.window.runtime.user_dir / "config" / "user_config.json"
        saved = json.loads(path.read_text(encoding="utf-8"))["tasks"]
        self.assertEqual(
            [task["name"] for task in saved],
            [PROGRAM_LAUNCH_TASK_NAME, "Test", "Other"],
        )
        self.assertEqual(saved[1]["selected_options"]["Test_Mode"], ["A"])

    def test_task_list_actions_follow_runtime_edit_policy_without_changing_active_queue(self):
        worker = MagicMock()
        expected_queue = self.window.build_execution_queue()
        with patch("app.winUI.RuntimeWorker", return_value=worker) as worker_type:
            self.window.on_task_start()
        active_queue = worker_type.call_args.args[1]
        original_count = self.window.option_list_widget.count()
        self.assertFalse(self.window.task_reset_button.isEnabled())
        self.assertFalse(self.window.task_add_button.isEnabled())
        self.window.add_task(self.window.runtime.interface["task"][0])
        self.assertEqual(self.window.option_list_widget.count(), original_count)

        self.window.set_runtime_option_editing_enabled(True)
        self.assertTrue(self.window.task_reset_button.isEnabled())
        self.assertTrue(self.window.task_add_button.isEnabled())
        with patch("app.winUI.RuntimeWorker") as worker_type:
            self.window.add_task(self.window.runtime.interface["task"][0])
            self.assertEqual(self.window.option_list_widget.count(), original_count + 1)
            self.window.task_reset_button.click()
            self.assertEqual(self.window.option_list_widget.count(), original_count)
        worker_type.assert_not_called()
        self.assertIs(self.window.worker, worker)
        self.assertEqual(active_queue, expected_queue)

    def test_empty_interface_reset_keeps_disabled_builtin_program_task(self):
        self.window.runtime.interface["task"] = []
        self.window.task_reset_button.click()
        self.assertEqual(self.window.option_list_widget.count(), 1)
        builtin = self.find_task_widget(self.window, PROGRAM_LAUNCH_TASK_NAME)
        self.assertFalse(builtin.checkbox.isEnabled())
        self.assertTrue(self.window.task_add_button.isEnabled())
        self.assertFalse(self.window.ui.workStartBtn.isEnabled())

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
                    r"(?s)QTabWidget::pane\s*\{.*?"
                    r"background-color:\s*#111A2B;",
                )
                self.assertRegex(
                    dark_stylesheet,
                    r"(?s)QTabBar::tab\s*\{.*?border:\s*1px solid #30415E;",
                )
                self.assertRegex(
                    dark_stylesheet,
                    r"(?s)QFrame#line,.*?QFrame#line_4\s*\{.*?"
                    r"background-color:\s*#30415E;",
                )
                self.assertEqual(window.option_list_widget.objectName(), "taskOptionList")
                widget = self.find_task_widget(window)
                task_settings_icon = widget.setting_btn.icon().pixmap(20, 20)
                end_settings_icon = window.ui.endSettingBtn.icon().pixmap(20, 20)
                self.assertFalse(task_settings_icon.isNull())
                self.assertFalse(end_settings_icon.isNull())
                self.assertEqual(
                    task_settings_icon.toImage(),
                    end_settings_icon.toImage(),
                )
                for button in (widget.setting_btn, window.ui.endSettingBtn):
                    with self.subTest(button=button.objectName()):
                        QApplication.sendEvent(
                            button,
                            QEvent(QEvent.Type.Enter),
                        )
                        self.assertEqual(button.iconSize(), QSize(24, 24))
                        QApplication.sendEvent(
                            button,
                            QEvent(QEvent.Type.Leave),
                        )
                        self.assertEqual(button.iconSize(), QSize(20, 20))
                stylesheet = (UI_DIR / "style.qss").read_text(encoding="utf-8")
                self.assertRegex(
                    stylesheet,
                    r"(?s)QScrollArea#scrollSettingWidget\s*\{.*?"
                    r"border:\s*1px solid transparent;",
                )
                self.assertRegex(
                    stylesheet,
                    r"(?s)QTabWidget::pane\s*\{.*?"
                    r"background-color:\s*#F4F7FB;.*?"
                    r"border-bottom-left-radius:\s*8px;.*?"
                    r"border-bottom-right-radius:\s*8px;",
                )
                self.assertRegex(
                    stylesheet,
                    r"(?s)QTabWidget#tabWidget > QStackedWidget,\s*"
                    r"QWidget#mainTab,\s*QWidget#settingTab\s*\{.*?"
                    r"background-color:\s*transparent;",
                )
                self.assertRegex(
                    stylesheet,
                    r"(?s)QFrame#line,\s*QFrame#line_4\s*\{.*?"
                    r"min-height:\s*1px;.*?max-height:\s*1px;.*?"
                    r"background-color:\s*#E2E8F0;",
                )
                self.assertRegex(
                    stylesheet,
                    r"(?s)QFrame#line_2,\s*QFrame#line_3\s*\{.*?"
                    r"min-width:\s*1px;.*?max-width:\s*1px;.*?"
                    r"background-color:\s*#E2E8F0;",
                )
                self.assertRegex(
                    stylesheet,
                    r"(?s)QListWidget#taskOptionList::item\s*\{.*?"
                    r"border-radius:\s*6px;",
                )
                self.assertEqual(
                    {
                        getattr(window.ui, name).frameShape()
                        for name in ("line", "line_2", "line_3", "line_4")
                    },
                    {QFrame.Shape.HLine, QFrame.Shape.VLine},
                )
                referenced_icons = re.findall(r'maabaicons:([^"\s)]+)', stylesheet)
                self.assertTrue(referenced_icons)
                for relative_path in referenced_icons:
                    with self.subTest(icon=relative_path):
                        self.assertTrue((UI_RESOURCE_DIR / "icons" / relative_path).is_file())
                        self.assertTrue(QSvgRenderer("maabaicons:" + relative_path).isValid())

                for filename in (
                    "checkbox-unchecked.svg",
                    "checkbox-unchecked-hover.svg",
                    "checkbox-unchecked-disabled.svg",
                    "radio-unchecked.svg",
                    "radio-unchecked-hover.svg",
                    "radio-unchecked-disabled.svg",
                    "radio-checked.svg",
                    "radio-checked-hover.svg",
                    "radio-checked-disabled.svg",
                ):
                    control_svg = (
                        UI_RESOURCE_DIR / "icons" / "controls" / filename
                    ).read_text(encoding="utf-8")
                    with self.subTest(icon=filename):
                        self.assertIn('fill="none"', control_svg)
                        self.assertNotIn('fill="#FFFFFF"', control_svg)
                        self.assertNotIn('fill="#E2E8F0"', control_svg)
            finally:
                os.chdir(original_directory)

    def test_settings_tab_uses_navigation_and_single_scroll_area(self):
        panel = self.window.settings_panel

        self.assertEqual(panel.navigation.count(), 4)
        self.assertEqual(
            [panel.navigation.item(index).text() for index in range(4)],
            ["일반", "프로그램", "컨트롤러", "외관"],
        )
        self.assertIs(panel.detail_scroll.widget(), panel.detail_contents)
        self.assertEqual(panel.layout().stretch(0), 2)
        self.assertEqual(panel.layout().stretch(1), 8)
        self.assertEqual(
            panel.controller_settings()["name"],
            "Win32PrintWindow",
        )
        row_titles = {
            label.text()
            for label in panel.findChildren(QLabel, "settingsRowTitle")
        }
        self.assertIn("작업 중 옵션 편집", row_titles)
        self.assertIn("수동 경로", row_titles)
        self.assertNotIn("작업 중 세부 옵션 편집", row_titles)

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

    def test_program_path_can_be_confirmed_from_custom_directory(self):
        panel = self.window.settings_panel
        task_list = self.window.option_list_widget
        self.assertEqual(task_list.item(0).data(Qt.UserRole), PROGRAM_LAUNCH_TASK_NAME)
        builtin = self.find_task_widget(self.window, PROGRAM_LAUNCH_TASK_NAME)
        self.assertFalse(builtin.checkbox.isEnabled())
        install_directory = self.window.runtime.user_dir / "CustomGame"
        install_directory.mkdir()
        executable = install_directory / "BlueArchive.exe"
        executable.write_bytes(b"")
        changed = MagicMock()
        panel.program_changed.connect(changed)

        panel.program_path_input.setText(str(install_directory))
        panel._apply_program_path()

        settings = panel.program_settings()
        self.assertEqual(settings["manual_path"], str(install_directory))
        self.assertEqual(settings["resolved_path"], str(executable.resolve()))
        self.assertTrue(panel.program_active_status.property("pathValid"))
        self.assertTrue(builtin.checkbox.isEnabled())
        builtin.checkbox.setChecked(True)
        self.assertEqual(self.window.build_execution_queue()[0][0], PROGRAM_LAUNCH_ENTRY)
        changed.assert_called_once()
        config_path = self.window.runtime.user_dir / "config" / "maa_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["program"]["manual_path"], str(install_directory))

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
        self.assertEqual(
            kwargs["program_settings"],
            self.window.settings_panel.program_settings(),
        )
        worker.start.assert_called_once()

    def test_runtime_editing_policy_controls_all_execution_options(self):
        task_widget = self.find_task_widget(self.window)
        queued_before_start = self.window.build_execution_queue()

        self.start_mock_run()
        self.window.stop_worker = None
        self.assertFalse(task_widget.checkbox.isEnabled())
        self.assertTrue(task_widget.setting_btn.isEnabled())
        self.assertEqual(
            self.window.option_list_widget.dragDropMode(),
            QAbstractItemView.DragDropMode.NoDragDrop,
        )
        self.assertFalse(self.window.ui.minimizeEnableBtn.isEnabled())
        self.assertTrue(self.window.ui.workStartBtn.isEnabled())

        task_widget.setting_btn.click()
        self.assertFalse(self.window.ui.scrollSettingContents.isEnabled())

        self.window.settings_panel.runtime_edit_checkbox.setChecked(True)
        self.assertTrue(task_widget.checkbox.isEnabled())
        self.assertEqual(
            self.window.option_list_widget.dragDropMode(),
            QAbstractItemView.DragDropMode.InternalMove,
        )
        self.assertTrue(self.window.ui.minimizeEnableBtn.isEnabled())
        self.assertTrue(self.window.ui.scrollSettingContents.isEnabled())

        task_widget.checkbox.setChecked(False)
        self.assertTrue(self.window.ui.workStartBtn.isEnabled())
        task_widget.checkbox.setChecked(True)
        self.assertTrue(self.window.ui.workStartBtn.isEnabled())

        task_widget.selected_options["Test_Mode"] = ["B"]
        queued_after_change = self.window.build_execution_queue()
        self.assertEqual(queued_before_start[0][1]["Test_Node"]["next"], ["A"])
        self.assertEqual(queued_after_change[0][1]["Test_Node"]["next"], ["B"])

        self.window.settings_panel.runtime_edit_checkbox.setChecked(False)
        self.assertFalse(task_widget.checkbox.isEnabled())
        self.assertEqual(
            self.window.option_list_widget.dragDropMode(),
            QAbstractItemView.DragDropMode.NoDragDrop,
        )
        self.assertFalse(self.window.ui.minimizeEnableBtn.isEnabled())
        self.assertFalse(self.window.ui.scrollSettingContents.isEnabled())
        self.window.on_task_finished()
        self.assertTrue(task_widget.checkbox.isEnabled())
        self.assertEqual(
            self.window.option_list_widget.dragDropMode(),
            QAbstractItemView.DragDropMode.InternalMove,
        )
        self.assertTrue(self.window.ui.minimizeEnableBtn.isEnabled())
        self.assertTrue(self.window.ui.scrollSettingContents.isEnabled())

    def test_input_option_renders_and_builds_typed_pipeline_override(self):
        task_widget = self.find_task_widget(self.window)
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
        task_widget = self.find_task_widget(self.window)
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
        task_widget = self.find_task_widget(self.window)
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
        runtime.initialize.assert_called_once()
        init_args, init_kwargs = runtime.initialize.call_args
        self.assertEqual(init_args, (controller_settings,))
        self.assertIsNone(init_kwargs["program_settings"])
        self.assertEqual(init_kwargs["execution_queue"], [])
        self.assertTrue(callable(init_kwargs["cancellation_requested"]))
        runtime.run_task.assert_not_called()
        runtime.release_session.assert_called_once()
        self.assertFalse(worker.succeeded)


if __name__ == "__main__":
    unittest.main()
