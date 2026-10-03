"""Run with .venv/Scripts/python.exe -m unittest discover -s UnitTest -v."""

import json
from itertools import permutations
import os
import re
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, MagicMock, call, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))

from PySide6.QtCore import QEvent, QMimeData, QObject, QPoint, QPointF, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSizePolicy,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStyleOptionComboBox,
    QStyleOptionViewItem,
    QTabWidget,
    QToolButton,
    QWidget,
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
    GWL_EXSTYLE,
    LWA_ALPHA,
    LogSinkFocus,
    PROGRAM_WINDOW_POLL_INTERVAL_SECONDS,
    PROGRAM_WINDOW_STABLE_SECONDS,
    WM_SYSCOMMAND,
    SC_MINIMIZE,
    SMTO_BLOCK_ABORTIFHUNG_ERRORONEXIT,
    WINDOW_MINIMIZE_MESSAGE_TIMEOUT_MS,
    WINDOW_MINIMIZE_CHECK_COUNT,
    WS_EX_LAYERED,
    WS_EX_TRANSPARENT,
    WindowPlacement,
)
from app.settingsUI import AssociatedControlLabel, SettingsStore
from app.winUI import (
    COMPACT_SCROLLBAR_WIDTH,
    DARK_CAPTION_COLOR,
    DARK_CAPTION_TEXT_COLOR,
    DARK_QSS_FILENAME,
    DWM_COLOR_DEFAULT,
    DWMWA_CAPTION_COLOR,
    DWMWA_TEXT_COLOR,
    DWMWA_USE_IMMERSIVE_DARK_MODE,
    EXPANDED_SCROLLBAR_WIDTH,
    LOG_ACTION_MENU_CLOSE_DELAY_MS,
    LOG_MENU_COLLAPSE_ICON_PATH,
    LOG_MENU_EXPAND_ICON_PATH,
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
        self.runtime._user32.GetForegroundWindow.return_value = 456

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

    def test_cached_preview_never_posts_capture_and_returns_owned_frame(self):
        controller = MagicMock(connected=True)
        original = np.zeros((20, 30, 3), dtype=np.uint8)
        controller.cached_image = original
        self.runtime.controller = controller
        frame = self.runtime.capture_cached_frame()
        original[:] = 255
        self.assertEqual(frame.max(), 0)
        controller.post_screencap.assert_not_called()
        self.runtime.controller = None
        with self.assertRaises(RuntimeError):
            self.runtime.capture_cached_frame()

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

    def test_standard_win32_interface_runs_without_builtin_launch_or_focus(self):
        # PI v2 기본 필드만 사용한다. 게임명/Task 이름, program 설정, Agent,
        # resource.controller, task.label, Pipeline focus는 필수 의존성이 아니다.
        for capture in ("PrintWindow", "FramePool"):
            for minimize in (False, True):
                for focus in (None, "첫 작업", {"Node.Action.Starting": "첫 작업"}):
                    with self.subTest(capture=capture, minimize=minimize, focus=focus), tempfile.TemporaryDirectory() as temp:
                        interface = {
                            "interface_version": 2,
                            "name": "StandardExample",
                            "controller": [{
                                "name": "ExampleWin32", "type": "Win32",
                                "win32": {"window_regex": "^Example App$", "screencap": capture},
                            }],
                            "resource": [{"name": "Default", "path": ["./resource", "./overrides"]}],
                            "task": [{"name": "ExampleTask", "entry": "Example_Entry"}],
                        }
                        interface_path = Path(temp) / "interface.json"
                        interface_path.write_text(json.dumps(interface), encoding="utf-8")
                        self.runtime.interface_path = interface_path
                        self.runtime.interface = self.runtime._load_interface()
                        self.runtime.resource_config = self.runtime._get_resource_config()
                        self.runtime._resource_loaded = False
                        self.runtime.resource = None
                        user32 = self.runtime._user32
                        user32.reset_mock()
                        user32.GetForegroundWindow.return_value = 456
                        resource = MagicMock(loaded=True)
                        resource.post_bundle.return_value = make_job()
                        controller = MagicMock(connected=True)
                        controller.post_connection.return_value = make_job()
                        controller.post_inactive.return_value = make_job()
                        tasker = MagicMock(inited=True, running=False, stopping=False)
                        job = tasker.post_task.return_value = make_job()

                        def wait():
                            user32.SendMessageTimeoutW.assert_not_called()
                            details = {"name": "Example_Entry", "task_id": 1}
                            if focus is not None:
                                details["focus"] = focus
                            self.runtime.log_sink.on_raw_notification(None, "Node.Action.Starting", details)
                            self.runtime.log_sink.on_raw_notification(None, "Node.Action.Starting", details)

                        job.wait.side_effect = wait
                        with patch("app.runtime.Toolkit.init_option", return_value=True), patch(
                            "app.runtime.Toolkit.find_desktop_windows",
                            return_value=[SimpleNamespace(hwnd=123, window_name="Example App")],
                        ), patch("app.runtime.Resource", return_value=resource), patch(
                            "app.runtime.Win32Controller", return_value=controller
                        ) as create_controller, patch("app.runtime.Tasker", return_value=tasker), patch.object(
                            self.runtime, "_launch_program"
                        ) as launch, patch.object(self.runtime, "_resize_window_for_task", return_value=True):
                            self.assertTrue(self.runtime.initialize()[0])
                            self.assertTrue(self.runtime.run_task(minimize_window=minimize)[0])
                        launch.assert_not_called()
                        tasker.post_task.assert_called_once_with("Example_Entry")
                        self.assertEqual(user32.SendMessageTimeoutW.call_count, int(minimize))
                        user32.ShowWindow.assert_not_called()
                        user32.SetForegroundWindow.assert_not_called()
                        self.assertEqual(create_controller.call_args.kwargs["hWnd"], 123)
                        self.assertEqual(create_controller.call_args.kwargs["screencap_method"].name, capture)
                        self.assertEqual(resource.post_bundle.call_args_list, [
                            call(str((Path(temp) / "resource").resolve())),
                            call(str((Path(temp) / "overrides").resolve())),
                        ])

    def test_standard_class_regex_only_is_not_supported_by_current_runtime(self):
        self.runtime.interface["controller"] = [{
            "name": "ClassOnly", "type": "Win32", "win32": {"class_regex": "ExampleWindowClass"},
        }]
        self.runtime.resource_config.pop("controller", None)
        window, message = self.runtime._find_target_window()
        self.assertIsNone(window)
        self.assertIn("window_regex", message)

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

    def test_controller_stability_resets_when_window_presentation_changes(self):
        game_window = SimpleNamespace(hwnd=333, window_name="Blue Archive")
        with patch(
            "app.runtime.Toolkit.find_desktop_windows",
            return_value=[game_window],
        ) as find_windows, patch.object(
            self.runtime,
            "_get_window_stability_signature",
            side_effect=[("startup",), ("ready",), ("ready",), ("ready",)],
        ), patch(
            "app.runtime.time.monotonic", side_effect=[0, 0, 4, 8, 9]
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

    def test_startup_window_guard_is_transparent_click_through_and_restored(self):
        user32 = self.runtime._user32
        user32.IsWindow.return_value = True
        user32.GetWindowLongPtrW.return_value = 0x100
        user32.SetWindowLongPtrW.return_value = 0x100
        user32.SetLayeredWindowAttributes.return_value = True

        self.assertTrue(self.runtime._apply_startup_window_guard(333))
        user32.SetWindowLongPtrW.assert_called_once_with(
            333,
            GWL_EXSTYLE,
            0x100 | WS_EX_LAYERED | WS_EX_TRANSPARENT,
        )
        user32.SetLayeredWindowAttributes.assert_called_once_with(
            333, 0, 0, LWA_ALPHA
        )

        self.assertTrue(self.runtime._restore_startup_window_guard())
        self.assertEqual(
            user32.SetWindowLongPtrW.call_args_list[-1],
            call(333, GWL_EXSTYLE, 0x100),
        )
        self.assertIsNone(self.runtime._startup_window_guard)

    def test_startup_window_guard_preserves_existing_layered_attributes(self):
        user32 = self.runtime._user32
        original_style = 0x100 | WS_EX_LAYERED
        user32.IsWindow.return_value = True
        user32.GetWindowLongPtrW.return_value = original_style
        user32.SetWindowLongPtrW.return_value = original_style
        user32.SetLayeredWindowAttributes.return_value = True

        def get_layered(_hwnd, color_key, alpha, flags):
            color_key._obj.value = 0x112233
            alpha._obj.value = 192
            flags._obj.value = 3
            return True

        user32.GetLayeredWindowAttributes.side_effect = get_layered
        self.assertTrue(self.runtime._apply_startup_window_guard(333))
        self.assertTrue(self.runtime._restore_startup_window_guard())
        self.assertEqual(
            user32.SetLayeredWindowAttributes.call_args_list[-1],
            call(333, 0x112233, 192, 3),
        )

    def test_session_release_restores_active_startup_window_guard(self):
        user32 = self.runtime._user32
        user32.IsWindow.return_value = True
        user32.GetWindowLongPtrW.return_value = 0x100
        user32.SetWindowLongPtrW.return_value = 0x100
        user32.SetLayeredWindowAttributes.return_value = True
        self.runtime._target_hwnd = 333
        self.assertTrue(self.runtime._apply_startup_window_guard(333))

        succeeded, _message = self.runtime.release_session()

        self.assertTrue(succeeded)
        self.assertIsNone(self.runtime._startup_window_guard)
        self.assertEqual(
            user32.SetWindowLongPtrW.call_args_list[-1],
            call(333, GWL_EXSTYLE, 0x100),
        )

    def test_started_window_guard_retries_until_real_minimize_succeeds(self):
        window = SimpleNamespace(hwnd=333, window_name="Blue Archive")
        self.runtime._user32.IsWindow.return_value = True
        events = []
        with patch.object(
            self.runtime,
            "_apply_startup_window_guard",
            side_effect=lambda _hwnd: events.append("guard") or True,
        ), patch.object(
            self.runtime,
            "_restore_startup_window_guard",
            side_effect=lambda: events.append("restore") or True,
        ), patch.object(
            self.runtime,
            "_minimize_window_for_task",
            side_effect=lambda: events.append("minimize") or events.count("minimize") >= 2,
        ), patch(
            "app.runtime.time.monotonic", side_effect=[0, 1]
        ), patch("app.runtime.time.sleep") as sleep:
            succeeded, _message = (
                self.runtime._prepare_started_window_for_minimized_connection(
                    window,
                    wait_timeout_seconds=30,
                )
            )

        self.assertTrue(succeeded)
        self.assertEqual(
            events,
            ["guard", "minimize", "guard", "minimize", "restore"],
        )
        self.assertEqual(sleep.call_args_list[-1], call(PROGRAM_WINDOW_POLL_INTERVAL_SECONDS))
        self.assertTrue(self.runtime._window_size_prepared)

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
        self.assertTrue(self.runtime._program_started_for_session)

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
        self.assertFalse(self.runtime._program_started_for_session)

    def test_launch_only_waits_for_controller_without_startup_minimize_guard(self):
        self.configure_initialization()
        settings = {
            "resolved_path": "C:/Games/BlueArchive.exe",
            "startup_wait_seconds": 25,
        }
        def launch(_settings):
            self.runtime._program_started_for_session = True
            return True, "started"

        with patch.object(
            self.runtime, "_launch_program", side_effect=launch
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
        self.assertIsNone(create_call.kwargs["window"])

    def test_auto_started_minimized_pipeline_guards_window_before_controller(self):
        self.configure_initialization()
        settings = {
            "resolved_path": "C:/Games/BlueArchive.exe",
            "startup_wait_seconds": 25,
        }
        game_window = SimpleNamespace(hwnd=333, window_name="Blue Archive")

        def launch(_settings):
            self.runtime._program_started_for_session = True
            return True, "started"

        with patch.object(
            self.runtime, "_launch_program", side_effect=launch
        ), patch.object(
            self.runtime,
            "_find_target_window",
            return_value=(game_window, "found"),
        ) as find_window, patch.object(
            self.runtime,
            "_prepare_started_window_for_minimized_connection",
            return_value=(True, "prepared"),
        ) as prepare:
            succeeded, _message = self.runtime.initialize(
                program_settings=settings,
                execution_queue=[(PROGRAM_LAUNCH_ENTRY, {}), ("Login_Main", {})],
                minimize_window=True,
            )

        self.assertTrue(succeeded)
        self.assertEqual(
            find_window.call_args.kwargs["poll_interval_seconds"],
            PROGRAM_WINDOW_POLL_INTERVAL_SECONDS,
        )
        prepare.assert_called_once_with(game_window, 25, None)
        create_call = self.runtime._create_controller.call_args
        self.assertEqual(create_call.kwargs["wait_timeout_seconds"], 0)
        self.assertEqual(create_call.kwargs["stable_window_seconds"], 0)
        self.assertIs(create_call.kwargs["window"], game_window)

    def test_auto_started_pipeline_without_minimize_skips_startup_guard(self):
        self.configure_initialization()
        settings = {
            "resolved_path": "C:/Games/BlueArchive.exe",
            "startup_wait_seconds": 25,
        }

        def launch(_settings):
            self.runtime._program_started_for_session = True
            return True, "started"

        with patch.object(
            self.runtime, "_launch_program", side_effect=launch
        ), patch.object(
            self.runtime, "_prepare_started_window_for_minimized_connection"
        ) as prepare:
            succeeded, _message = self.runtime.initialize(
                program_settings=settings,
                execution_queue=[(PROGRAM_LAUNCH_ENTRY, {}), ("Login_Main", {})],
                minimize_window=False,
            )

        self.assertTrue(succeeded)
        prepare.assert_not_called()
        create_call = self.runtime._create_controller.call_args
        self.assertEqual(create_call.kwargs["wait_timeout_seconds"], 25)
        self.assertEqual(
            create_call.kwargs["stable_window_seconds"],
            PROGRAM_WINDOW_STABLE_SECONDS,
        )
        self.assertIsNone(create_call.kwargs["window"])

    def test_launch_task_binds_immediately_when_program_is_already_running(self):
        self.configure_initialization()
        settings = {
            "resolved_path": "C:/Games/BlueArchive.exe",
            "startup_wait_seconds": 25,
        }
        with patch.object(
            self.runtime, "_launch_program", return_value=(True, "running")
        ):
            succeeded, _message = self.runtime.initialize(
                program_settings=settings,
                execution_queue=[(PROGRAM_LAUNCH_ENTRY, {})],
            )

        self.assertTrue(succeeded)
        create_call = self.runtime._create_controller.call_args
        self.assertEqual(create_call.kwargs["wait_timeout_seconds"], 25)
        self.assertEqual(create_call.kwargs["stable_window_seconds"], 0)
        self.assertIsNone(create_call.kwargs["window"])

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
            self.runtime._user32.SendMessageTimeoutW.assert_not_called()
            posted_entries.append(entry)
            return first_job if entry == "First" else second_job

        def wait_first():
            self.assertEqual(posted_entries, ["First", "Second"])
            self.runtime.log_sink.on_raw_notification(None, "Node.PipelineNode.Starting", {})
            self.runtime.log_sink.on_raw_notification(None, "Node.Recognition.Starting", {})
            self.runtime._user32.SendMessageTimeoutW.assert_not_called()
            self.runtime.log_sink.on_raw_notification(None, "Node.Action.Starting", {})
            # 이후 사용자가 창을 복구해도 다음 노드에서 다시 최소화하지 않는다.
            self.runtime._user32.IsIconic.return_value = False
            self.runtime.log_sink.on_raw_notification(None, "Node.Action.Starting", {})
            self.runtime._user32.SendMessageTimeoutW.assert_called_once()
            return first_job

        tasker.post_task.side_effect = post_task
        first_job.wait.side_effect = wait_first
        self.runtime.tasker = tasker
        self.runtime._target_hwnd = 123
        self.runtime._user32.IsWindow.return_value = True
        self.runtime._user32.IsIconic.return_value = True

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
        self.runtime._user32.SendMessageTimeoutW.assert_called_once_with(
            123, WM_SYSCOMMAND, SC_MINIMIZE, 0,
            SMTO_BLOCK_ABORTIFHUNG_ERRORONEXIT, WINDOW_MINIMIZE_MESSAGE_TIMEOUT_MS, ANY,
        )
        self.runtime._user32.ShowWindow.assert_not_called()

    def test_minimize_sends_once_and_waits_for_window_state(self):
        self.runtime._target_hwnd = 123
        self.runtime._user32.IsWindow.return_value = True
        self.runtime._user32.IsIconic.side_effect = [False, True]

        with patch("app.runtime.time.sleep") as sleep:
            succeeded = self.runtime._minimize_window_for_task()

        self.assertTrue(succeeded)
        self.runtime._user32.SendMessageTimeoutW.assert_called_once()
        self.runtime._user32.ShowWindow.assert_not_called()
        self.assertEqual(sleep.call_count, 1)

    def test_minimize_waits_for_game_thread_to_release_foreground(self):
        self.runtime._target_hwnd = 123
        user32 = self.runtime._user32
        user32.IsIconic.return_value = True
        user32.GetForegroundWindow.side_effect = [123, None, 456]
        with patch("app.runtime.time.sleep"):
            self.assertTrue(self.runtime._minimize_window_for_task())
        user32.SendMessageTimeoutW.assert_called_once()
        user32.ShowWindow.assert_not_called()
        user32.SetForegroundWindow.assert_not_called()

    def test_minimize_keeps_other_foreground_window(self):
        self.runtime._target_hwnd = 123
        self.runtime._user32.GetForegroundWindow.return_value = 789
        with patch("app.runtime.time.sleep"):
            self.assertTrue(self.runtime._minimize_window_for_task())
        self.runtime._user32.SetForegroundWindow.assert_not_called()

    def test_manual_restore_after_system_minimize_is_not_overridden(self):
        self.runtime.tasker = tasker = MagicMock(running=False, stopping=False)
        self.runtime._target_hwnd = 123
        user32 = self.runtime._user32
        user32.GetForegroundWindow.return_value = 123
        user32.IsIconic.side_effect = lambda hwnd: hwnd == 123

        def minimize(*_args):
            user32.GetForegroundWindow.return_value = 456
            return True

        def wait():
            sink = self.runtime.log_sink
            sink.on_raw_notification(None, "Node.Action.Starting", {})
            user32.GetForegroundWindow.return_value = 123
            user32.IsIconic.side_effect = None
            user32.IsIconic.return_value = False
            sink.on_raw_notification(None, "Node.Action.Starting", {})

        user32.SendMessageTimeoutW.side_effect = minimize
        job = tasker.post_task.return_value = make_job()
        job.wait.side_effect = wait
        with patch.object(self.runtime, "_resize_window_for_task", return_value=True), patch("app.runtime.time.sleep"):
            self.assertTrue(self.runtime.run_task([("Login_Main", {})], minimize_window=True)[0])
        user32.SendMessageTimeoutW.assert_called_once()
        user32.ShowWindow.assert_not_called()
        user32.SetForegroundWindow.assert_not_called()
        self.assertEqual(user32.GetForegroundWindow(), 123)

    def test_minimize_delivery_is_not_proof_of_minimized_background_state(self):
        for iconic, foreground in ((False, 456), (True, 123)):
            with self.subTest(iconic=iconic, foreground=foreground):
                self.runtime._target_hwnd = 123
                user32 = self.runtime._user32
                user32.IsIconic.return_value = iconic
                user32.GetForegroundWindow.return_value = foreground
                user32.SendMessageTimeoutW.reset_mock()
                user32.SendMessageTimeoutW.return_value = 1
                with patch("app.runtime.time.sleep") as sleep:
                    self.assertFalse(self.runtime._minimize_window_for_task())
                user32.SendMessageTimeoutW.assert_called_once()
                self.assertEqual(sleep.call_count, WINDOW_MINIMIZE_CHECK_COUNT - 1)
                user32.ShowWindow.assert_not_called()
                user32.SetForegroundWindow.assert_not_called()

    def test_minimize_message_failure_is_not_retried_or_forced(self):
        self.runtime._target_hwnd = 123
        user32 = self.runtime._user32
        user32.SendMessageTimeoutW.return_value = 0
        with patch("app.runtime.time.sleep") as sleep:
            self.assertFalse(self.runtime._minimize_window_for_task())
        sleep.assert_not_called()
        user32.SendMessageTimeoutW.assert_called_once()
        user32.ShowWindow.assert_not_called()
        user32.SetForegroundWindow.assert_not_called()

    def test_minimize_window_closed_during_request_fails(self):
        self.runtime._target_hwnd = 123
        self.runtime._user32.IsWindow.side_effect = [True, False]
        with patch("app.runtime.time.sleep") as sleep:
            self.assertFalse(self.runtime._minimize_window_for_task())
        sleep.assert_not_called()

    def test_minimize_no_window_does_not_send_message(self):
        self.runtime._target_hwnd = None
        self.assertFalse(self.runtime._minimize_window_for_task())
        self.runtime._user32.SendMessageTimeoutW.assert_not_called()

    def test_minimize_accepts_zero_window_procedure_result(self):
        self.runtime._target_hwnd = 123
        user32 = self.runtime._user32

        def delivered(*args):
            args[-1]._obj.value = 0  # WM_SYSCOMMAND's normal WndProc result.
            return 1

        user32.SendMessageTimeoutW.side_effect = delivered
        self.assertTrue(self.runtime._minimize_window_for_task())

    def test_minimized_run_restores_original_window_during_session_release(self):
        tasker = MagicMock(running=False, stopping=False)
        job = tasker.post_task.return_value = make_job()
        def user_restores_window():
            self.runtime.log_sink.on_raw_notification(None, "Node.Action.Starting", {})
            self.runtime._user32.IsIconic.return_value = False
            self.runtime.log_sink.on_raw_notification(None, "Node.Action.Starting", {})
            return job
        job.wait.side_effect = user_restores_window
        self.runtime.tasker = tasker
        self.runtime._target_hwnd = 123
        self.runtime._original_window_placement = WindowPlacement()
        self.runtime._user32.IsWindow.return_value = True
        self.runtime._user32.IsIconic.return_value = True

        with patch.object(self.runtime, "_resize_window_for_task", return_value=True):
            succeeded, _message = self.runtime.run_task(
                [("Login_Main", {})], minimize_window=True
            )

        self.assertTrue(succeeded)
        self.runtime._user32.SendMessageTimeoutW.assert_called_once()
        self.runtime._user32.ShowWindow.assert_not_called()
        self.runtime._user32.SetWindowPlacement.assert_called_once()
        self.assertIsNone(self.runtime._target_hwnd)
        self.assertIsNone(self.runtime._original_window_placement)

    def test_launch_guard_finishes_before_tasker_and_minimize_at_first_action(self):
        for newly_started in (True, False):
            with self.subTest(newly_started=newly_started):
                self.configure_initialization()
                events = []

                def launch(_settings):
                    self.assertIsNone(self.runtime.tasker)
                    self.runtime._program_started_for_session = newly_started
                    events.append("launch")
                    return True, "started"

                def create_controller(_settings, **kwargs):
                    self.assertIsNone(self.runtime.tasker)
                    self.assertIsNone(self.runtime.controller)
                    self.assertEqual(
                        kwargs["wait_timeout_seconds"], 0 if newly_started else 60
                    )
                    self.assertEqual(kwargs["stable_window_seconds"], 0)
                    self.assertEqual(kwargs["window"], game_window if newly_started else None)
                    self.runtime.controller = MagicMock(connected=True)
                    self.runtime.controller.post_connection.return_value = make_job()
                    self.runtime.controller.post_inactive.return_value = make_job()
                    self.runtime._target_hwnd = 123
                    events.append("controller")
                    return True, "created"

                tasker = MagicMock(running=False, stopping=False, inited=True)
                def post_task(*_):
                    events.append("post")
                    self.runtime.log_sink.on_raw_notification(None, "Node.PipelineNode.Starting", {})
                    events.append("first_capture")
                    self.runtime.log_sink.on_raw_notification(None, "Node.Action.Starting", {})
                    events.append("action")
                    return make_job()

                tasker.post_task.side_effect = post_task
                self.runtime._create_controller.side_effect = create_controller
                game_window = SimpleNamespace(hwnd=123, window_name="Blue Archive")
                with patch.object(self.runtime, "_launch_program", side_effect=launch), patch(
                    "app.runtime.Tasker", side_effect=lambda: events.append("tasker") or tasker
                ), patch.object(
                    self.runtime,
                    "_find_target_window",
                    side_effect=lambda *a, **k: events.append("window") or (game_window, "found"),
                ), patch.object(
                    self.runtime,
                    "_prepare_started_window_for_minimized_connection",
                    side_effect=lambda *a, **k: events.append("guard") or (True, "prepared"),
                ), patch.object(
                    self.runtime, "_resize_window_for_task", return_value=True
                ), patch.object(
                    self.runtime, "_minimize_window_for_task",
                    side_effect=lambda: events.append("minimize") or True,
                ):
                    queue = [(PROGRAM_LAUNCH_ENTRY, {}), ("Login_Main", {})]
                    self.assertTrue(
                        self.runtime.initialize(
                            program_settings={},
                            execution_queue=queue,
                            minimize_window=True,
                        )[0]
                    )
                    self.assertTrue(self.runtime.run_task(queue, minimize_window=True)[0])

                expected = ["launch"]
                if newly_started:
                    expected.extend(["window", "guard"])
                self.assertEqual(
                    events,
                    expected + ["controller", "tasker", "post", "first_capture", "minimize", "action"],
                )

    def test_minimize_failure_requests_stop_without_waiting_in_callback(self):
        self.runtime.tasker = tasker = MagicMock(running=False, stopping=False)
        job = tasker.post_task.return_value = make_job()
        job.wait.side_effect = lambda: self.runtime.log_sink.on_raw_notification(None, "Node.Action.Starting", {})
        with patch.object(self.runtime, "_resize_window_for_task", return_value=True), patch.object(
            self.runtime, "_minimize_window_for_task", return_value=False
        ):
            succeeded, message = self.runtime.run_task([("Login_Main", {})], minimize_window=True)
        self.assertFalse(succeeded)
        self.assertIn("최소화", message)
        tasker.post_task.assert_called_once_with("Login_Main")
        tasker.post_stop.assert_called_once_with()
        tasker.post_stop.return_value.wait.assert_not_called()

    def test_launch_only_does_not_minimize_without_pipeline_action(self):
        self.runtime.tasker = tasker = MagicMock(running=False, stopping=False)
        with patch.object(self.runtime, "_resize_window_for_task", return_value=True), patch.object(
            self.runtime, "_minimize_window_for_task", return_value=True
        ) as minimize:
            succeeded, _ = self.runtime.run_task([(PROGRAM_LAUNCH_ENTRY, {})], minimize_window=True)
        self.assertTrue(succeeded)
        minimize.assert_not_called()
        tasker.post_task.assert_not_called()

    def test_first_action_callback_runs_once_before_focus_log(self):
        sink = LogSinkFocus()
        events = []
        sink.set_first_action_callback(lambda: events.append("minimize"))
        sink.set_log_callback(events.append)
        for message in ("Node.PipelineNode.Starting", "Node.Recognition.Starting"):
            sink.on_raw_notification(None, message, {"focus": "작업"})
        self.assertEqual(events, [])
        sink.on_raw_notification(None, "Node.Action.Starting", {"focus": "첫 작업"})
        sink.on_raw_notification(None, "Node.Action.Starting", {"focus": "다음 작업"})
        self.assertEqual(events, ["minimize", "첫 작업", "다음 작업"])

    def test_minimize_exception_is_reported_without_escaping_native_callback(self):
        self.runtime.tasker = tasker = MagicMock(running=False, stopping=False)
        job = tasker.post_task.return_value = make_job()
        job.wait.side_effect = lambda: self.runtime.log_sink.on_raw_notification(None, "Node.Action.Starting", {})
        with patch.object(self.runtime, "_resize_window_for_task", return_value=True), patch.object(
            self.runtime, "_minimize_window_for_task", side_effect=OSError("window unavailable")
        ):
            succeeded, message = self.runtime.run_task([("Login_Main", {})], minimize_window=True)
        self.assertFalse(succeeded)
        self.assertIn("window unavailable", message)
        tasker.post_stop.assert_called_once_with()
        tasker.post_stop.return_value.wait.assert_not_called()

    def test_run_without_action_clears_pending_minimize(self):
        self.runtime.tasker = tasker = MagicMock(running=False, stopping=False)
        tasker.post_task.return_value = make_job(succeeded=False)
        with patch.object(self.runtime, "_resize_window_for_task", return_value=True), patch.object(
            self.runtime, "_minimize_window_for_task"
        ) as minimize:
            succeeded, _ = self.runtime.run_task([("Login_Main", {})], minimize_window=True)
            self.runtime.log_sink.on_raw_notification(None, "Node.Action.Starting", {})
        self.assertFalse(succeeded)
        minimize.assert_not_called()

    def test_cancellation_before_first_action_does_not_minimize(self):
        self.runtime.tasker = tasker = MagicMock(running=False, stopping=False)
        cancelled = False

        def wait():
            nonlocal cancelled
            cancelled = True
            self.runtime.log_sink.on_raw_notification(None, "Node.Action.Starting", {})

        tasker.post_task.return_value = make_job(succeeded=False)
        tasker.post_task.return_value.wait.side_effect = wait
        with patch.object(self.runtime, "_resize_window_for_task", return_value=True), patch.object(
            self.runtime, "_minimize_window_for_task"
        ) as minimize:
            self.runtime.run_task(
                [("Login_Main", {})], minimize_window=True, cancellation_requested=lambda: cancelled
            )
        minimize.assert_not_called()

    def test_disabled_minimize_never_minimizes_for_any_launch_selection(self):
        queues = [
            [(PROGRAM_LAUNCH_ENTRY, {})],
            [("Login_Main", {})],
            [(PROGRAM_LAUNCH_ENTRY, {}), ("Login_Main", {})],
        ]
        for queue in queues:
            with self.subTest(queue=queue):
                self.runtime.tasker = tasker = MagicMock(running=False, stopping=False)
                tasker.post_task.return_value = make_job()
                tasker.post_task.return_value.wait.side_effect = lambda: self.runtime.log_sink.on_raw_notification(
                    None, "Node.Action.Starting", {}
                )
                with patch.object(
                    self.runtime, "_resize_window_for_task", return_value=True
                ), patch.object(self.runtime, "_minimize_window_for_task") as minimize:
                    succeeded, _ = self.runtime.run_task(queue, minimize_window=False)
                self.assertTrue(succeeded)
                minimize.assert_not_called()
                self.assertEqual(
                    tasker.post_task.call_count,
                    sum(entry != PROGRAM_LAUNCH_ENTRY for entry, _ in queue),
                )

    def test_cancelled_startup_does_not_create_tasker(self):
        self.configure_initialization()
        def launch(_settings):
            self.runtime._program_started_for_session = True
            return True, "started"
        self.runtime._create_controller.reset_mock()
        with patch.object(self.runtime, "_launch_program", side_effect=launch):
            succeeded, _ = self.runtime.initialize(
                program_settings={},
                execution_queue=[(PROGRAM_LAUNCH_ENTRY, {})],
                cancellation_requested=lambda: True,
            )
        self.assertFalse(succeeded)
        self.runtime._create_controller.assert_not_called()

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
        self.assertTrue(config["general"]["program_launch_task_enabled"])
        self.assertFalse(
            config["general"]["allow_option_edits_while_running"]
        )
        self.assertTrue(config["general"]["clear_log_on_start"])
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

    def test_negative_program_launch_setting_is_migrated_to_enabled_setting(self):
        self.maa_config_path.parent.mkdir(parents=True)
        self.maa_config_path.write_text(
            json.dumps({"general": {"remove_program_launch_task": True}}),
            encoding="utf-8",
        )

        config = self.store.load(self.controller())

        self.assertFalse(config["general"]["program_launch_task_enabled"])
        saved_general = self.read_json(self.maa_config_path)["general"]
        self.assertNotIn("remove_program_launch_task", saved_general)

    def test_existing_maa_config_wins_during_minimize_migration(self):
        self.maa_config_path.parent.mkdir(parents=True)
        self.maa_config_path.write_text(
            json.dumps(
                {
                    "general": {
                        "minimize_enabled": False,
                        "program_launch_task_enabled": False,
                        "allow_option_edits_while_running": True,
                        "clear_log_on_start": False,
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
        self.assertFalse(config["general"]["program_launch_task_enabled"])
        self.assertTrue(config["general"]["allow_option_edits_while_running"])
        self.assertFalse(config["general"]["clear_log_on_start"])
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
        # Windows offscreen does not enumerate system fonts. Verify real Hangul metrics.
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf"
        if sys.platform == "win32" and font.is_file():
            QFontDatabase.addApplicationFont(str(font))

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

    def wait_for_monitor(self):
        for _ in range(400):
            QTest.qWait(5)
            if not self.window.monitor.busy:
                self.app.processEvents()
                return
        self.fail("모니터 백그라운드 요청이 종료되지 않았습니다.")

    def test_connection_panels_share_discovery_and_connection_status(self):
        from app.monitoring import ConnectionTarget
        monitor = self.window.monitor
        target = ConnectionTarget("10", "Example · PID 123", "Win32", hwnd=10)
        with patch.object(monitor.service, "discover", return_value=[target]):
            monitor.discover()
            self.wait_for_monitor()
        self.assertEqual(monitor.target_key, "10")
        for panel in monitor.panels:
            self.assertEqual(panel.target_combo.currentData(), "10")
            self.assertTrue(panel.connect_button.isEnabled())
        with patch.object(monitor.service, "connect", return_value=target):
            monitor.connect_target()
            self.wait_for_monitor()
        self.assertEqual(monitor.panels[0].status.text(), monitor.panels[1].status.text())
        self.assertIn("연결 성공", monitor.panels[0].status.text())

    def test_monitor_serializes_requests_and_discards_stale_discovery(self):
        from app.monitoring import ConnectionTarget
        monitor = self.window.monitor
        entered, release = threading.Event(), threading.Event()
        calls = []

        def blocked_discovery(*args):
            calls.append(args)
            entered.set()
            release.wait(2)
            return [ConnectionTarget("old", "old", "Win32")]

        with patch.object(monitor.service, "discover", side_effect=blocked_discovery):
            try:
                monitor.discover()
                self.assertTrue(entered.wait(1))
                monitor.discover()
                monitor.select_preset("Win32FramePool")
                self.assertTrue(monitor.busy)
            finally:
                release.set()
                self.wait_for_monitor()
        self.assertEqual(len(calls), 1)
        self.assertEqual(monitor.targets, [])
        self.assertEqual(monitor.target_key, "")

    def test_task_start_waits_for_preflight_controller_cleanup(self):
        monitor = self.window.monitor
        controller = MagicMock()
        controller.post_inactive.return_value.wait.return_value.succeeded = True
        monitor.service.controller = controller
        worker = MagicMock()
        with patch("app.winUI.RuntimeWorker", return_value=worker) as factory:
            self.window.on_task_start()
            factory.assert_not_called()
            self.wait_for_monitor()
            factory.assert_called_once()
        controller.post_inactive.assert_called_once()
        self.assertIsNone(monitor.service.controller)
        self.assertTrue(self.window.isRunning)
        self.assertFalse(monitor.panels[0].discover_button.isEnabled())
        self.window.worker = None
        self.window._finish_run_if_idle()

    def test_failed_preflight_cleanup_cancels_pending_task_start(self):
        monitor = self.window.monitor
        monitor.service.controller = MagicMock()
        monitor.service.controller.post_inactive.return_value.wait.return_value.succeeded = False
        with patch("app.winUI.RuntimeWorker") as factory:
            self.window.on_task_start()
            self.wait_for_monitor()
            factory.assert_not_called()
        self.assertIsNone(monitor.pending_start)
        self.assertIn("해제에 실패", monitor.panels[0].status.text())
        monitor.service.controller = None

    def test_close_waits_for_connection_diagnostic_and_cleanup(self):
        monitor = self.window.monitor
        entered, release = threading.Event(), threading.Event()
        controller = MagicMock()
        controller.post_inactive.return_value.wait.return_value.succeeded = True

        def blocked_connect(*_args):
            entered.set()
            release.wait(2)
            monitor.service.controller = controller
            return SimpleNamespace(label="connected")

        self.window.show()
        with patch.object(monitor.service, "connect", side_effect=blocked_connect):
            try:
                monitor.connect_target()
                self.assertTrue(entered.wait(1))
                self.window.close()
                self.assertTrue(self.window._close_pending)
                self.assertTrue(self.window.isVisible())
            finally:
                release.set()
                self.wait_for_monitor()
        controller.post_inactive.assert_called_once()
        self.assertTrue(monitor.ready_to_close)
        self.assertFalse(self.window.isVisible())

    def test_connection_preferences_persist_without_changing_runtime_preset(self):
        monitor = self.window.monitor
        preset = self.window.settings_panel.controller_settings()
        monitor.change_preferences({"adb_path": " C:/tools/adb.exe ", "address": " localhost:5555 "})
        for panel in monitor.panels:
            self.assertEqual(panel.address.text(), "localhost:5555")
        saved = json.loads((self.window.runtime.user_dir / "config" / "maa_config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["connection"], {"adb_path": "C:/tools/adb.exe", "address": "localhost:5555"})
        self.assertEqual(self.window.settings_panel.controller_settings(), preset)

    def show_screen_panel(self):
        self.window.show()
        sections = self.window.ui.monitorSectionsWidget.sections()
        section = next(s for s in sections if s.property("monitorSectionKey") == "screen")
        section.findChild(QToolButton, "monitorSectionToggle").setChecked(True)
        self.app.processEvents()
        return self.window.monitor.screen

    def configure_screen_capture(self):
        monitor = self.window.monitor
        controller = MagicMock(connected=True)
        controller.post_inactive.return_value.wait.return_value.succeeded = True
        controller.post_screencap.return_value.wait.return_value.succeeded = True
        image = np.zeros((90, 160, 3), dtype=np.uint8)
        image[:, :, 2] = 255
        controller.post_screencap.return_value.wait.return_value.get.return_value = image
        monitor.service.controller = controller
        monitor._sync()
        self.addCleanup(lambda: setattr(monitor.service, "controller", None))
        return controller

    def test_single_screen_test_captures_once_and_owns_correct_color(self):
        screen = self.show_screen_panel()
        controller = self.configure_screen_capture()
        self.window.monitor.toggle_capture()
        self.wait_for_monitor()
        controller.post_screencap.assert_called_once()
        self.assertEqual(screen.preview.image.size(), QSize(160, 90))
        self.assertEqual(screen.preview.image.pixelColor(0, 0).name(), "#ff0000")
        self.assertTrue(screen.status.text().startswith("테스트 캡처"))
        self.assertNotIn("화면 너비", screen.status.text())
        self.assertNotIn("높이", screen.status.text())
        self.assertFalse(self.window.monitor.preview_timer.isActive())
        drawn = screen.preview.image_rect()
        self.assertAlmostEqual(drawn.width() / drawn.height(), 160 / 90, delta=0.03)
        self.assertTrue(screen.preview.rect().contains(drawn))

    def test_running_screen_reads_only_runtime_cache(self):
        self.show_screen_panel()
        self.configure_screen_capture()
        runtime = self.window.runtime
        runtime.capture_cached_frame.return_value = np.zeros((10, 20, 3), dtype=np.uint8)
        self.window.isRunning = True
        with patch.object(self.window.monitor.service, "capture") as capture:
            self.window.monitor.toggle_capture()
            self.wait_for_monitor()
        capture.assert_not_called()
        runtime.capture_cached_frame.assert_called_once()
        self.assertIn("실행 캐시", self.window.monitor.screen.status.text())
        self.window.isRunning = False

    def test_continuous_preview_survives_collapse_and_retains_last_frame(self):
        screen = self.show_screen_panel()
        self.configure_screen_capture()
        screen.mode.setCurrentIndex(screen.mode.findData("continuous"))
        self.window.monitor.toggle_capture()
        self.wait_for_monitor()
        self.assertTrue(self.window.monitor.preview_timer.isActive())
        section = self.window.monitor.screen_content.parentWidget()
        section.findChild(QToolButton, "monitorSectionToggle").setChecked(False)
        self.assertTrue(self.window.monitor.streaming)
        self.assertTrue(self.window.monitor.preview_timer.isActive())
        self.assertFalse(screen.preview.image.isNull())
        self.window.monitor.stop_preview()
        self.wait_for_monitor()

    def test_page_change_preserves_preview_and_screen_preferences(self):
        screen = self.show_screen_panel()
        self.configure_screen_capture()
        screen.mode.setCurrentIndex(screen.mode.findData("continuous"))
        screen.fps.setCurrentIndex(screen.fps.findData(5))
        self.window.monitor.toggle_capture()
        self.wait_for_monitor()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        self.assertTrue(self.window.monitor.streaming)
        self.assertTrue(self.window.monitor.preview_timer.isActive())
        saved = json.loads((self.window.runtime.user_dir / "config" / "maa_config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["monitor"], {"mode": "continuous", "fps": 5})
        store = SettingsStore(self.window.runtime.user_dir / "config" / "maa_config.json")
        self.assertEqual(store.load()["monitor"], saved["monitor"])
        self.window.monitor.stop_preview()
        self.wait_for_monitor()

    def test_preview_capture_error_retries_without_stopping_loop(self):
        screen = self.show_screen_panel()
        controller = self.configure_screen_capture()
        screen.mode.setCurrentIndex(screen.mode.findData("continuous"))
        controller.post_screencap.return_value.wait.return_value.succeeded = False
        self.window.monitor.toggle_capture()
        self.wait_for_monitor()
        self.assertTrue(self.window.monitor.streaming)
        self.assertTrue(self.window.monitor.preview_timer.isActive())
        self.assertIn("캡처에 실패", screen.status.text())
        self.assertTrue(screen.capture_button.isEnabled())
        self.window.monitor.stop_preview()
        self.wait_for_monitor()

    def test_stopping_inflight_frame_discards_result_without_unsafe_termination(self):
        screen = self.show_screen_panel()
        self.configure_screen_capture()
        entered, release = threading.Event(), threading.Event()

        def blocked_capture():
            entered.set()
            release.wait(2)
            return np.zeros((10, 20, 3), dtype=np.uint8)

        screen.mode.setCurrentIndex(screen.mode.findData("continuous"))
        with patch.object(self.window.monitor.service, "capture", side_effect=blocked_capture):
            try:
                self.window.monitor.toggle_capture()
                self.assertTrue(entered.wait(1))
                self.window.monitor.toggle_capture()
                self.assertTrue(self.window.monitor.busy)
                self.assertFalse(self.window.monitor.streaming)
            finally:
                release.set()
                self.wait_for_monitor()
        self.assertTrue(screen.preview.image.isNull())
        self.assertFalse(self.window.monitor.preview_timer.isActive())

    def test_slow_continuous_preview_has_no_overlapping_requests(self):
        screen = self.show_screen_panel()
        self.configure_screen_capture()
        screen.mode.setCurrentIndex(screen.mode.findData("continuous"))
        screen.fps.setCurrentIndex(screen.fps.findData(10))
        entered, release = threading.Event(), threading.Event()
        calls = []

        def blocked_capture():
            calls.append(1)
            entered.set()
            release.wait(2)
            return np.zeros((10, 20, 3), dtype=np.uint8)

        with patch.object(self.window.monitor.service, "capture", side_effect=blocked_capture):
            try:
                self.window.monitor.toggle_capture()
                self.assertTrue(entered.wait(1))
                QTest.qWait(150)
                self.window.monitor.capture_frame()
                self.assertEqual(len(calls), 1)
            finally:
                self.window.monitor.stop_preview()
                release.set()
                self.wait_for_monitor()

    def test_target_change_clears_previous_screenshot(self):
        screen = self.show_screen_panel()
        self.configure_screen_capture()
        self.window.monitor.toggle_capture()
        self.wait_for_monitor()
        self.window.monitor.select_target("different")
        self.wait_for_monitor()
        self.assertTrue(screen.preview.image.isNull())
        self.assertIsNone(self.window.monitor.service.controller)

    def test_continuous_timer_delivers_next_frame_at_selected_rate(self):
        screen = self.show_screen_panel()
        controller = self.configure_screen_capture()
        screen.mode.setCurrentIndex(screen.mode.findData("continuous"))
        screen.fps.setCurrentIndex(screen.fps.findData(10))
        self.window.monitor.toggle_capture()
        self.wait_for_monitor()
        for _ in range(100):
            QTest.qWait(5)
            if controller.post_screencap.call_count >= 2:
                break
        self.assertGreaterEqual(controller.post_screencap.call_count, 2)
        self.window.monitor.stop_preview()
        self.wait_for_monitor()

    def test_program_settings_change_invalidates_preflight_and_image(self):
        self.show_screen_panel()
        self.configure_screen_capture()
        self.window.settings_panel.program_changed.emit({"executable_name": "Other.exe"})
        self.wait_for_monitor()
        self.assertEqual(self.window.monitor.targets, [])
        self.assertEqual(self.window.monitor.target_key, "")
        self.assertIsNone(self.window.monitor.service.controller)

    def test_preview_sync_does_not_rebuild_unchanged_connection_combos(self):
        self.show_screen_panel()
        controller = self.configure_screen_capture()
        combo = self.window.monitor.panels[0].preset_combo
        removed = MagicMock()
        combo.model().rowsRemoved.connect(removed)
        self.window.monitor.toggle_capture()
        self.wait_for_monitor()
        removed.assert_not_called()
        controller.post_screencap.assert_called_once()

    def test_continuous_frames_keep_status_controls_and_panel_geometry_stable(self):
        screen = self.show_screen_panel()
        controller = self.configure_screen_capture()
        monitor = self.window.monitor
        screen.mode.setCurrentIndex(screen.mode.findData("continuous"))
        screen.fps.setCurrentIndex(screen.fps.findData(10))
        with patch.object(monitor, "_sync", wraps=monitor._sync) as sync:
            try:
                monitor.toggle_capture()
                self.wait_for_monitor()
                status = screen.status.text()
                geometry = screen.geometry()
                first_calls = controller.post_screencap.call_count
                self.assertFalse(monitor.panels[0].discover_button.isEnabled())
                for _ in range(100):
                    QTest.qWait(5)
                    if controller.post_screencap.call_count >= first_calls + 2 and not monitor.busy:
                        break
                self.assertGreaterEqual(controller.post_screencap.call_count, first_calls + 2)
                self.assertEqual(screen.status.text(), status)
                self.assertNotIn("가져오고", status)
                self.assertEqual(screen.geometry(), geometry)
                self.assertEqual(sync.call_count, 1)
                self.assertTrue(screen.capture_button.isEnabled())
            finally:
                monitor.stop_preview()
                self.wait_for_monitor()
        self.assertTrue(monitor.panels[0].discover_button.isEnabled())

    def test_new_frames_repaint_only_opaque_image_canvas(self):
        class PaintCounter(QObject):
            def __init__(self, parent):
                super().__init__(parent)
                self.paints = 0

            def eventFilter(self, obj, event):
                if event.type() == QEvent.Type.Paint:
                    self.paints += 1
                return False

        screen = self.show_screen_panel()
        self.configure_screen_capture()
        self.window.monitor.toggle_capture()
        self.wait_for_monitor()
        self.app.processEvents()
        canvas_count = PaintCounter(screen.preview.canvas)
        panel_count = PaintCounter(self.window.ui.monitorSectionsWidget)
        screen.preview.canvas.installEventFilter(canvas_count)
        self.window.ui.monitorSectionsWidget.installEventFilter(panel_count)
        self.assertTrue(screen.preview.canvas.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent))
        for value in (30, 80, 150):
            from app.monitorUI import owned_qimage
            screen.preview.set_image(owned_qimage(np.full((90, 160, 3), value, dtype=np.uint8)))
            self.app.processEvents()
        self.assertGreaterEqual(canvas_count.paints, 3)
        self.assertEqual(panel_count.paints, 0)

    def test_preview_close_waits_for_inflight_capture_then_releases_connection(self):
        screen = self.show_screen_panel()
        controller = self.configure_screen_capture()
        entered, release = threading.Event(), threading.Event()

        def blocked_capture():
            entered.set()
            release.wait(2)
            return np.zeros((10, 20, 3), dtype=np.uint8)

        with patch.object(self.window.monitor.service, "capture", side_effect=blocked_capture):
            try:
                self.window.monitor.toggle_capture()
                self.assertTrue(entered.wait(1))
                self.window.close()
                self.assertTrue(self.window._close_pending)
            finally:
                release.set()
                self.wait_for_monitor()
        self.assertTrue(screen.preview.image.isNull())
        self.assertIsNone(self.window.monitor.service.controller)
        controller.post_inactive.assert_called_once()
        self.assertFalse(self.window.isVisible())

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

    def test_settings_checkbox_labels_share_the_checkbox_hit_area(self):
        panel = self.window.settings_panel
        rows_and_controls = (
            (panel.minimize_checkbox.parentWidget(), panel.minimize_checkbox),
            (panel.program_launch_checkbox.parentWidget(), panel.program_launch_checkbox),
            (panel.runtime_edit_checkbox.parentWidget(), panel.runtime_edit_checkbox),
            (panel.clear_log_checkbox.parentWidget(), panel.clear_log_checkbox),
        )

        self.window.show()
        self.app.processEvents()
        for row, checkbox in rows_and_controls:
            with self.subTest(label=row.title_label.text()):
                was_checked = checkbox.isChecked()
                QTest.mouseClick(row.title_label, Qt.MouseButton.LeftButton)
                self.assertEqual(checkbox.isChecked(), not was_checked)
                QTest.mouseClick(row.description_label, Qt.MouseButton.LeftButton)
                self.assertEqual(checkbox.isChecked(), was_checked)

    def test_dynamic_checkable_labels_share_the_control_hit_area(self):
        task_widget = self.find_task_widget(self.window)
        self.window.show_sub_cases(task_widget)
        option_panel = self.window.ui.scrollSettingContents

        radio_buttons = option_panel.findChildren(QRadioButton)
        checkable_labels = option_panel.findChildren(AssociatedControlLabel)

        self.assertEqual(len(radio_buttons), 2)
        self.assertEqual(len(checkable_labels), 2)
        QTest.mouseClick(checkable_labels[1], Qt.MouseButton.LeftButton)
        self.assertTrue(radio_buttons[1].isChecked())

    def test_keyboard_focus_decorations_are_not_added(self):
        log_view = self.window.ui.logPrintText

        self.assertEqual(log_view.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertNotIn("QCheckBox:focus", self.window._base_style_sheet)
        self.assertNotIn("QPushButton:focus", self.window._base_style_sheet)
        self.assertNotIn("QPushButton:focus", self.window._dark_style_sheet)

    def test_dynamic_typography_uses_shared_qss_selectors(self):
        task_widget = self.find_task_widget(self.window)
        self.assertEqual(task_widget.label.styleSheet(), "")
        self.window.show_sub_cases(task_widget)
        option_title = self.window.ui.scrollSettingContents.findChild(
            QLabel, "optionGroupTitle"
        )
        self.assertIsNotNone(option_title)
        self.assertEqual(option_title.styleSheet(), "")

    def test_option_labels_and_fields_align_with_panel_heading(self):
        self.window.show()
        self.window.show_sub_cases(self.find_task_widget(self.window))
        panel = self.window.centralWidget().findChild(QWidget, "_2_settingWidget")
        heading = panel.findChild(QLabel, "workspaceSectionTitle")
        content = self.window.ui.scrollSettingContents
        group_titles = content.findChildren(QLabel, "optionGroupTitle")
        labels = [*group_titles, *content.findChildren(QLabel, "optionInputLabel")]
        fields = [
            *content.findChildren(QComboBox, "optionSelect"),
            *content.findChildren(QLineEdit, "optionInput"),
        ]
        self.assertTrue(group_titles[0].property("firstGroup"))
        self.assertTrue(all(not title.property("firstGroup") for title in group_titles[1:]))
        self.assertFalse(group_titles[0].text().startswith("["))
        for theme in ("light", "dark"):
            self.window.settings_panel.theme_combo.setCurrentIndex(
                self.window.settings_panel.theme_combo.findData(theme)
            )
            for width in (1000, 1300):
                self.window.resize(width, 750)
                self.app.processEvents()
                self.app.processEvents()
                with self.subTest(theme=theme, width=width):
                    title_x = heading.mapTo(panel, QPoint(0, 0)).x()
                    for widget in (*labels, *fields):
                        self.assertEqual(widget.mapTo(panel, QPoint(0, 0)).x(), title_x + 6)
                    self.assertEqual(heading.font().pointSizeF(), 11)
                    self.assertTrue(all(label.font().pointSizeF() == 10 for label in labels))
                    first_y = group_titles[0].mapTo(panel, QPoint(0, 0)).y()
                    self.assertGreaterEqual(first_y - heading.geometry().bottom(), 8)
                    self.assertLessEqual(first_y - heading.geometry().bottom(), 16)
                    self.assertEqual(content.layout().contentsMargins().left(), 6)
                    self.assertEqual(content.layout().contentsMargins().right(), 4)

    def test_option_groups_have_subtle_separators_and_select_text_is_centered(self):
        self.window.show()
        task = self.find_task_widget(self.window)
        self.window.show_sub_cases(task)
        content = self.window.ui.scrollSettingContents
        separators = content.findChildren(QFrame, "optionGroupSeparator")
        self.assertEqual(len(separators), len(task.task_options) - 1)
        combo = content.findChild(QComboBox, "optionSelect")
        for theme in ("light", "dark"):
            self.window.settings_panel.theme_combo.setCurrentIndex(
                self.window.settings_panel.theme_combo.findData(theme)
            )
            self.app.processEvents()
            option = QStyleOptionComboBox()
            combo.initStyleOption(option)
            text_rect = combo.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox, option,
                QStyle.SubControl.SC_ComboBoxEditField, combo,
            )
            with self.subTest(theme=theme):
                self.assertLessEqual(abs(text_rect.center().y() - combo.rect().center().y()), 1)
                self.assertLessEqual(abs(text_rect.top() - (combo.height() - text_rect.bottom() - 1)), 1)
                for separator in separators:
                    self.assertEqual(separator.height(), 1)
                    image = separator.grab().toImage()
                    self.assertEqual(image.pixelColor(0, 0).name(), "#30415e" if theme == "dark" else "#e2e8f0")

    def test_dynamic_checkable_labels_are_vertically_centered(self):
        task_widget = self.find_task_widget(self.window)
        task_widget.task_options = [
            (
                "Check",
                {
                    "type": "checkbox",
                    "cases": [{"name": "A", "label": "체크 항목"}],
                },
            ),
            (
                "Switch",
                {
                    "type": "switch",
                    "cases": [
                        {"name": "Yes", "label": "스위치 항목"},
                        {"name": "No"},
                    ],
                },
            ),
        ]
        task_widget.selected_options = {"Check": [], "Switch": ["No"]}
        self.window.show_sub_cases(task_widget)

        labels = self.window.ui.scrollSettingContents.findChildren(
            AssociatedControlLabel, "optionChoiceLabel"
        )
        self.assertEqual(len(labels), 2)
        for label in labels:
            with self.subTest(label=label.text()):
                row_layout = label.parentWidget().layout()
                control = label.parentWidget().findChild(QCheckBox)
                self.assertIsNotNone(control)
                self.assertTrue(label.alignment() & Qt.AlignmentFlag.AlignVCenter)
                self.assertTrue(
                    row_layout.itemAt(row_layout.indexOf(label)).alignment()
                    & Qt.AlignmentFlag.AlignVCenter
                )
                self.assertTrue(
                    row_layout.itemAt(row_layout.indexOf(control)).alignment()
                    & Qt.AlignmentFlag.AlignVCenter
                )

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
        self.assertEqual(popup.count(), len(self.window.runtime.interface["task"]))
        self.assertEqual(popup.item(0).data(Qt.UserRole)["name"], "Test")
        self.assertEqual(popup.item(0).toolTip(), "")
        self.assertEqual(popup.currentRow(), -1)
        self.assertEqual(popup.selectedItems(), [])
        self.assertEqual(popup._dismiss_timer.interval(), 80)
        self.assertFalse(popup._dismiss_timer.isSingleShot())
        self.assertTrue(popup._dismiss_timer.isActive())
        popup_position = popup.mapToGlobal(popup.rect().center())
        with patch("app.winUI.QCursor.pos", return_value=popup_position):
            popup._hide_if_pointer_outside()
        self.assertTrue(popup.isVisible())
        with patch("app.winUI.QCursor.pos", return_value=QPoint(-100, -100)):
            popup._hide_if_pointer_outside()
        self.assertFalse(popup.isVisible())
        self.assertFalse(popup._dismiss_timer.isActive())

        add.click()
        self.assertTrue(popup.isVisible())
        add.click()
        self.assertFalse(popup.isVisible())

        add.click()
        self.assertTrue(popup.isVisible())
        below_add = add.mapToGlobal(QPoint(add.width() // 2, add.height() + 2))
        with patch("app.winUI.QCursor.pos", return_value=below_add):
            popup._hide_if_pointer_outside()
        self.assertFalse(popup.isVisible())

        add.click()
        self.assertTrue(popup.isVisible())
        actions._footer_hover_filter._set_hovered(True)
        add.setDown(True)
        anchor_position = add.mapToGlobal(add.rect().center())
        popup_press = MagicMock()
        popup_press.type.return_value = QEvent.Type.MouseButtonPress
        popup_press.globalPosition.return_value = SimpleNamespace(
            toPoint=lambda: anchor_position
        )
        with patch("app.winUI.QCursor.pos", return_value=QPoint(-100, -100)):
            self.assertTrue(popup.eventFilter(popup, popup_press))
        popup_press.accept.assert_called_once_with()
        self.assertFalse(popup.isVisible())
        self.assertFalse(add.isDown())
        self.assertFalse(actions.property("groupHovered"))

        add.click()
        self.assertTrue(popup.isVisible())
        QTest.mouseClick(add, Qt.MouseButton.LeftButton, pos=add.rect().center())
        self.app.processEvents()
        self.assertFalse(popup.isVisible())

        add.click()
        self.app.processEvents()
        QTest.mouseClick(
            popup.viewport(), Qt.MouseButton.LeftButton,
            pos=popup.visualItemRect(popup.item(0)).center(),
        )
        self.assertFalse(popup.isVisible())
        self.assertEqual(task_list.count(), 3)
        self.assertEqual(task_list.item(2).data(Qt.UserRole), "Test")

        add.click()
        QTest.keyClick(popup, Qt.Key.Key_Escape)
        self.assertFalse(popup.isVisible())
        self.assertEqual(task_list.count(), 3)
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
        item = self.find_task_item(self.window)
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
        self.assertEqual(
            line_end,
            task_list.viewport().width() - task_list.DRAG_LINE_RIGHT_MARGIN,
        )
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

    def test_program_launch_task_is_unique_and_pinned_to_top(self):
        task_list = self.window.option_list_widget
        self.window.show()
        self.app.processEvents()
        launch_item = task_list.item(0)
        launch_widget = task_list.itemWidget(launch_item)
        self.assertEqual(launch_widget.task_data["label"], "자동 실행")
        self.assertFalse(launch_item.flags() & Qt.ItemFlag.ItemIsDragEnabled)

        self.window.add_task(launch_widget.task_data)
        self.assertEqual(
            sum(
                task_list.item(row).data(Qt.ItemDataRole.UserRole)
                == PROGRAM_LAUNCH_TASK_NAME
                for row in range(task_list.count())
            ),
            1,
        )

        self.window.add_task(self.window.runtime.interface["task"][0])
        dragged_item = task_list.item(task_list.count() - 1)
        task_list._dragged_item = dragged_item
        dragged_item.setHidden(True)
        task_list._update_drop_target(QPoint(0, 0))
        self.assertIsNot(task_list._drop_before_item, launch_item)
        self.assertGreaterEqual(
            task_list.drag_line_y,
            task_list.visualItemRect(launch_item).bottom() + 1,
        )
        self.assertTrue(task_list._move_dragged_item(launch_item))
        self.app.processEvents()
        self.assertIs(task_list.item(0), launch_item)
        self.assertIs(task_list.item(1), dragged_item)
        dragged_item.setHidden(False)
        task_list._dragged_item = launch_item
        self.assertFalse(task_list._move_dragged_item(None))

    def test_program_launch_setting_disables_and_restores_task(self):
        panel = self.window.settings_panel
        task_list = self.window.option_list_widget
        self.assertTrue(panel.program_launch_checkbox.isChecked())
        launch_widget = self.find_task_widget(self.window, PROGRAM_LAUNCH_TASK_NAME)
        launch_widget.checkbox.setChecked(False)

        panel.program_launch_checkbox.setChecked(False)
        self.assertEqual(
            [task_list.item(row).data(Qt.ItemDataRole.UserRole)
             for row in range(task_list.count())],
            ["Test"],
        )
        config_path = self.window.runtime.user_dir / "config" / "maa_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertFalse(config["general"]["program_launch_task_enabled"])
        self.window.task_reset_button.click()
        self.assertNotIn(
            PROGRAM_LAUNCH_TASK_NAME,
            [
                task_list.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(task_list.count())
            ],
        )

        with patch("app.winUI.AppRuntime", return_value=self.window.runtime):
            restored = MainWindow()
        self.addCleanup(restored.deleteLater)
        restored_names = [
            restored.option_list_widget.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(restored.option_list_widget.count())
        ]
        self.assertNotIn(PROGRAM_LAUNCH_TASK_NAME, restored_names)

        panel.program_launch_checkbox.setChecked(True)
        self.assertEqual(
            task_list.item(0).data(Qt.ItemDataRole.UserRole),
            PROGRAM_LAUNCH_TASK_NAME,
        )
        self.assertFalse(
            self.find_task_widget(
                self.window, PROGRAM_LAUNCH_TASK_NAME
            ).checkbox.isChecked()
        )
        self.assertEqual(
            sum(
                task_list.item(row).data(Qt.ItemDataRole.UserRole)
                == PROGRAM_LAUNCH_TASK_NAME
                for row in range(task_list.count())
            ),
            1,
        )

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
        self.assertEqual(popup.count(), 40)
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
        self.assertEqual(self.window.ui.scrollSettingContents.layout().count(), 1)
        self.assertIn(
            "설정 버튼",
            self.window.ui.scrollSettingContents.findChild(QLabel, "optionEmptyState").text(),
        )
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
                    r"(?s)QWidget#appHeader\s*\{.*?border-bottom:\s*1px solid #30415E;",
                )
                self.assertRegex(
                    dark_stylesheet,
                    r"(?s)QWidget#centralwidget,\s*QWidget#mainTab,\s*"
                    r"QWidget#settingTab\s*\{.*?background-color:\s*#111A2B;",
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
                    r"(?s)QWidget#centralwidget,\s*QWidget#mainTab,\s*"
                    r"QWidget#settingTab\s*\{.*?background-color:\s*#F4F7FB;",
                )
                self.assertRegex(
                    stylesheet,
                    r"(?s)QWidget#_1_settingStartWidget,\s*"
                    r"QWidget#_2_settingWidget,\s*QWidget#_3_logPrintWidget\s*"
                    r"\{.*?border-radius:\s*12px;",
                )
                self.assertRegex(
                    stylesheet,
                    r"(?s)QFrame#line,\s*QFrame#line_4\s*\{.*?"
                    r"min-height:\s*1px;.*?max-height:\s*1px;.*?"
                    r"background-color:\s*#E2E8F0;",
                )
                self.assertRegex(
                    stylesheet,
                    r"(?s)QSplitter#workspaceSplitter::handle:horizontal\s*\{.*?"
                    r"background-color:\s*transparent;",
                )
                self.assertRegex(
                    stylesheet,
                    r"(?s)QListWidget#taskOptionList::item\s*\{.*?"
                    r"border-radius:\s*6px;",
                )
                self.assertEqual(
                    {
                        getattr(window.ui, name).frameShape()
                        for name in ("line", "line_4")
                    },
                    {QFrame.Shape.HLine},
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

    def test_settings_scroll_highlights_topmost_visible_card(self):
        panel = self.window.settings_panel
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        self.app.processEvents()
        first, second = panel._sections[:2]
        for value, expected in (
            (0, 0),
            (first.y() + first.height() - 1, 0),
            (first.y() + first.height(), 1),
            (second.y(), 1),
        ):
            with self.subTest(value=value):
                panel._sync_navigation_to_scroll(value)
                self.assertEqual(panel.navigation.currentRow(), expected)

        scrollbar = panel.detail_scroll.verticalScrollBar()
        panel._sync_navigation_to_scroll(scrollbar.maximum())
        expected = next(
            i for i, section in enumerate(panel._sections)
            if section.y() + section.height() > scrollbar.maximum()
        )
        self.assertEqual(panel.navigation.currentRow(), expected)

    def test_settings_explicit_selection_survives_clamped_scroll(self):
        panel = self.window.settings_panel
        self.window.resize(1000, 600)
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        self.app.processEvents()
        panel.navigation.setCurrentRow(3)
        self.assertEqual(panel.navigation.currentRow(), 3)
        scrollbar = panel.detail_scroll.verticalScrollBar()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())
        self.assertGreater(scrollbar.maximum(), 0)
        scrollbar.setValue(0)
        self.assertEqual(panel.navigation.currentRow(), 0)

    def test_settings_tab_uses_navigation_and_single_scroll_area(self):
        panel = self.window.settings_panel
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        self.app.processEvents()

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
        self.assertIn("자동 실행 작업", row_titles)
        self.assertIn("작업 시작 시 로그 초기화", row_titles)
        self.assertIn("수동 경로", row_titles)
        self.assertNotIn("작업 중 세부 옵션 편집", row_titles)
        general_titles = [
            label.text()
            for label in panel._sections[0].findChildren(QLabel, "settingsRowTitle")
        ]
        self.assertEqual(
            general_titles,
            [
                "실행 시 최소화",
                "자동 실행 작업",
                "작업 중 옵션 편집",
                "작업 시작 시 로그 초기화",
            ],
        )
        general_checkboxes = (
            panel.minimize_checkbox,
            panel.program_launch_checkbox,
            panel.runtime_edit_checkbox,
            panel.clear_log_checkbox,
        )
        general_section = panel._sections[0]
        for index, checkbox in enumerate(general_checkboxes):
            with self.subTest(checkbox_index=index):
                self.assertEqual(checkbox.text(), "")
                row = checkbox.parentWidget()
                row_position = row.mapTo(general_section, QPoint(0, 0))
                self.assertEqual(
                    row_position.x(),
                    general_section.width() - row_position.x() - row.width(),
                )
                option = QStyleOptionButton()
                checkbox.initStyleOption(option)
                indicator = checkbox.style().subElementRect(
                    QStyle.SubElement.SE_CheckBoxIndicator,
                    option,
                    checkbox,
                )
                indicator_right = (
                    checkbox.mapTo(row, indicator.topLeft()).x()
                    + indicator.width()
                )
                self.assertEqual(row.width() - indicator_right, 12)

        rows = panel.findChildren(QFrame, "settingsRow")
        self.assertTrue(rows)
        for row_index, row in enumerate(rows):
            with self.subTest(row_index=row_index):
                self.assertIsInstance(row.layout(), QGridLayout)
                row_margins = row.layout().contentsMargins()
                section_layout = row.parentWidget().layout()
                last_widget = section_layout.itemAt(section_layout.count() - 1).widget()
                expected_bottom = (
                    0 if last_widget is row or row is panel.program_path_row else 12
                )
                expected_right = (
                    12 if row.parentWidget() is general_section else 0
                )
                self.assertEqual(
                    (
                        row_margins.top(),
                        row_margins.right(),
                        row_margins.bottom(),
                    ),
                    (12, expected_right, expected_bottom),
                )
                self.assertIs(
                    row.layout().itemAtPosition(0, 1).widget(),
                    row.layout().itemAtPosition(1, 1).widget(),
                )

        sections = panel.findChildren(QFrame, "settingsSection")
        self.assertTrue(sections)
        for section_index, section in enumerate(sections):
            with self.subTest(section_index=section_index):
                section_margins = section.layout().contentsMargins()
                self.assertEqual(
                    (section_margins.top(), section_margins.bottom()),
                    (12, 12),
                )

    def test_start_page_uses_full_width_three_column_workspace(self):
        self.window.show()
        ui = self.window.ui
        page = ui.mainTab
        columns = [
            self.window.centralWidget().findChild(QWidget, name)
            for name in ("_1_settingStartWidget", "_2_settingWidget", "_3_logPrintWidget")
        ]
        for width in (1000, 1400):
            self.window.resize(width, 800)
            self.app.processEvents()

            def left(widget):
                return widget.mapTo(page, QPoint(0, 0)).x()

            def end(widget):
                return left(widget) + widget.width()

            gaps = (
                left(columns[0]),
                left(columns[1]) - end(columns[0]),
                left(columns[2]) - end(columns[1]),
                page.width() - end(columns[2]),
            )
            with self.subTest(width=width):
                self.assertEqual(gaps, (20, 14, 10, 20))
                self.assertEqual(columns[0].width(), 312)
                self.assertGreaterEqual(columns[1].width(), 300)
                self.assertGreaterEqual(columns[2].width(), 309)

    def test_workspace_navigation_and_monitor_accordion_keep_existing_controls(self):
        self.window.show()
        ui = self.window.ui
        self.assertIsNone(self.window.centralWidget().findChild(QTabWidget))
        self.assertIs(ui.mainPages.currentWidget(), ui.mainTab)
        ui.settingsNavButton.click()
        self.assertIs(ui.mainPages.currentWidget(), ui.settingTab)
        self.assertTrue(ui.settingsNavButton.isChecked())
        ui.dashboardNavButton.click()
        self.assertIs(ui.mainPages.currentWidget(), ui.mainTab)

        sections = ui.monitorSectionsWidget
        self.assertEqual(sections.section_order(), ["connection", "screen", "log"])
        section_by_key = {
            section.property("monitorSectionKey"): section for section in sections.sections()
        }
        for key, expected_open in (("connection", False), ("screen", False), ("log", True)):
            with self.subTest(section=key):
                toggle = section_by_key[key].findChild(QToolButton, "monitorSectionToggle")
                self.assertEqual(toggle.isChecked(), expected_open)
        section_by_key["connection"].findChild(QToolButton, "monitorSectionToggle").click()
        section_by_key["screen"].findChild(QToolButton, "monitorSectionToggle").click()
        self.window.resize(1000, 500)
        self.app.processEvents()
        self.assertGreater(ui.monitorScrollArea.verticalScrollBar().maximum(), 0)
        self.assertFalse(ui.logPrintText.isHidden())
        self.window.append_log("아코디언을 열어도 로그는 유지됩니다.")
        self.assertIn("아코디언을 열어도 로그는 유지됩니다.", ui.logPrintText.toPlainText())

    def test_monitor_reorder_accepts_only_internal_handle_drag_and_persists(self):
        self.window.show()
        sections = self.window.ui.monitorSectionsWidget
        connection = sections.sections()[0]
        handle = connection.findChild(QLabel, "monitorDragHandle")
        mime = QMimeData()
        mime.setData(sections.DRAG_MIME, b"1")
        event = MagicMock()
        event.source.return_value = handle
        event.mimeData.return_value = mime
        event.position.return_value = QPointF(1, sections.height() - 1)

        sections._dragged_section = connection
        sections._drag_source = handle
        self.assertTrue(sections._is_internal_drag(event))
        event.source.return_value = object()
        self.assertFalse(sections._is_internal_drag(event))
        event.source.return_value = handle
        external_mime = QMimeData()
        event.mimeData.return_value = external_mime
        self.assertFalse(sections._is_internal_drag(event))
        event.mimeData.return_value = mime
        connection.hide()
        self.app.processEvents()
        sections.dropEvent(event)
        connection.show()
        sections._dragged_section = None
        sections._drag_source = None
        self.assertEqual(sections.section_order(), ["screen", "log", "connection"])
        config_path = self.window.runtime.user_dir / "config" / "user_config.json"
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["monitor_order"], ["screen", "log", "connection"])
        with patch("app.winUI.AppRuntime", return_value=self.window.runtime):
            restored = MainWindow()
        self.addCleanup(restored.deleteLater)
        self.assertEqual(
            restored.ui.monitorSectionsWidget.section_order(),
            ["screen", "log", "connection"],
        )

    def test_monitor_drop_targets_do_not_paint_insertion_lines(self):
        self.window.show()
        sections = self.window.ui.monitorSectionsWidget
        for dragged in sections.sections():
            sections._dragged_section = dragged
            dragged.hide()
            self.app.processEvents()
            visible = [section for section in sections.sections() if section is not dragged]
            targets = [*visible, None]
            before_image = sections.grab().toImage()
            for target in targets:
                with self.subTest(dragged=dragged.property("monitorSectionKey"), target=target):
                    sections._update_drop_target(target.y() if target else sections.height())
                    self.assertIs(sections._drop_before, target)
                    self.assertEqual(sections.grab().toImage(), before_image)
            sections._clear_drop_target()
            dragged.show()
            sections._dragged_section = None

    def test_expanded_log_fills_available_height_and_collapsed_cards_stay_compact(self):
        self.window.show()
        ui = self.window.ui
        heights = []
        for height in (700, 1000):
            self.window.resize(1100, height)
            self.app.processEvents()
            heights.append([section.height() for section in ui.monitorSectionsWidget.sections()])
            self.assertGreater(ui.logPrintText.height(), 200)
            self.assertEqual(ui.monitorScrollArea.verticalScrollBar().maximum(), 0)
            log_bottom = ui.monitorLogSection.mapTo(ui.monitorSectionsWidget, QPoint()).y() + ui.monitorLogSection.height()
            bottom_gap = ui.monitorSectionsWidget.height() - log_bottom
            self.assertLessEqual(bottom_gap, 5)
        self.assertEqual(heights[0][:2], heights[1][:2])
        self.assertEqual(heights[1][2] - heights[0][2], 300)
        ui.monitorLogSection.findChild(QToolButton, "monitorSectionToggle").click()
        self.app.processEvents()
        self.app.processEvents()
        for section in ui.monitorSectionsWidget.sections():
            self.assertLessEqual(section.height(), 40)
        ui.monitorLogSection.findChild(QToolButton, "monitorSectionToggle").click()
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(ui.monitorLogSection.height(), heights[1][2])
        sections = ui.monitorSectionsWidget
        sections.move_section(ui.monitorLogSection, sections.sections()[0])
        self.app.processEvents()
        self.assertEqual(ui.monitorLogSection.height(), heights[1][2])

    def test_screen_and_log_expand_and_mouse_drag_resizes_without_growing_connection(self):
        screen = self.show_screen_panel()
        self.window.resize(1200, 1100)
        self.app.processEvents()
        sections = self.window.ui.monitorSectionsWidget
        connection, screen_card, log_card = sections.sections()
        before = (connection.height(), screen_card.height(), log_card.height())
        for card in (screen_card, log_card):
            self.assertEqual(card.sizePolicy().verticalPolicy(), QSizePolicy.Policy.Expanding)
        self.assertEqual(screen.preview.sizePolicy().verticalPolicy(), QSizePolicy.Policy.Expanding)
        handle = sections.splitter.handle(2)
        self.assertTrue(handle.isEnabled())
        self.assertFalse(sections.splitter.handle(1).isEnabled())
        center = handle.rect().center()
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=center)
        QTest.mouseMove(handle, center + QPoint(0, 80))
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=center)
        self.app.processEvents()
        self.assertEqual(connection.height(), before[0])
        self.assertGreater(screen_card.height(), before[1] + 50)
        self.assertLess(log_card.height(), before[2] - 50)
        self.assertEqual(screen_card.height() + log_card.height(), before[1] + before[2])
        self.assertGreaterEqual(screen.preview.height(), 120)

    def test_screen_only_fills_height_and_image_grows_proportionally(self):
        from app.monitorUI import owned_qimage
        screen = self.show_screen_panel()
        ui = self.window.ui
        ui.monitorLogSection.findChild(QToolButton, "monitorSectionToggle").setChecked(False)
        screen.preview.set_image(owned_qimage(np.zeros((480, 160, 3), dtype=np.uint8)))
        self.window.resize(1200, 700)
        self.app.processEvents()
        initial_height = screen.preview.height()
        initial_image = screen.preview.image_rect()
        self.window.resize(1200, 1000)
        self.app.processEvents()
        grown_image = screen.preview.image_rect()
        self.assertEqual(screen.preview.height() - initial_height, 300)
        self.assertGreater(grown_image.height(), initial_image.height())
        self.assertAlmostEqual(grown_image.width() / grown_image.height(), 1 / 3, delta=0.01)
        self.assertTrue(screen.preview.rect().contains(grown_image))
        self.assertLessEqual(ui.monitorLogSection.height(), 40)
        self.assertEqual(ui.monitorScrollArea.verticalScrollBar().maximum(), 0)
        self.assertFalse(any(ui.monitorSectionsWidget.splitter.handle(i).isEnabled() for i in (1, 2)))

    def test_vertical_resize_works_with_every_card_order_and_connection_content_height(self):
        self.show_screen_panel()
        sections = self.window.ui.monitorSectionsWidget
        cards = {c.property("monitorSectionKey"): c for c in sections.sections()}
        cards["connection"].findChild(QToolButton, "monitorSectionToggle").setChecked(True)
        self.window.resize(1200, 1500)
        self.app.processEvents()
        connection_height = cards["connection"].height()
        for order in permutations(cards):
            with self.subTest(order=order):
                for index, key in enumerate(order):
                    sections.move_section(cards[key], sections.sections()[index])
                self.app.processEvents()
                self.assertEqual(sections.section_order(), list(order))
                index = next(i for i in (1, 2) if sections.splitter.handle(i).isEnabled())
                before = [cards[key].height() for key in ("screen", "log")]
                sections.splitter.moveSplitter(sections.splitter.handle(index).y() + 20, index)
                self.app.processEvents()
                after = [cards[key].height() for key in ("screen", "log")]
                self.assertNotEqual(before, after)
                self.assertEqual(sum(before), sum(after))
                self.assertEqual(cards["connection"].height(), connection_height)

    def test_monitor_height_distribution_survives_collapse_reorder_and_reload(self):
        self.show_screen_panel()
        self.window.resize(1200, 1100)
        self.app.processEvents()
        sections = self.window.ui.monitorSectionsWidget
        splitter = sections.splitter
        splitter.moveSplitter(splitter.handle(2).y() + 60, 2)
        self.app.processEvents()
        weights = sections.height_weights()
        screen_card = sections.sections()[1]
        toggle = screen_card.findChild(QToolButton, "monitorSectionToggle")
        toggle.setChecked(False)
        self.app.processEvents()
        self.assertLessEqual(screen_card.height(), 40)
        toggle.setChecked(True)
        self.app.processEvents()
        self.assertEqual(sections.height_weights(), weights)
        self.assertAlmostEqual(screen_card.height() / self.window.ui.monitorLogSection.height(),
                               weights["screen"] / weights["log"], delta=0.03)
        sections.move_section(screen_card, sections.sections()[0])
        self.window.save_user_config()
        saved = json.loads((self.window.runtime.user_dir / "config" / "user_config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["monitor_height_weights"], weights)
        with patch("app.winUI.AppRuntime", return_value=self.window.runtime):
            restored = MainWindow()
        self.addCleanup(restored.deleteLater)
        self.assertEqual(restored.ui.monitorSectionsWidget.height_weights(), weights)
        self.assertEqual(restored.ui.monitorSectionsWidget.section_order(), sections.section_order())

    def test_invalid_monitor_height_preferences_are_ignored(self):
        sections = self.window.ui.monitorSectionsWidget
        for value in (None, [], {"screen": 0, "log": 1}, {"screen": True, "log": 1},
                      {"screen": "large", "log": 1}, {"screen": 999999999, "log": 1}):
            sections.restore_height_weights(value)
            self.assertEqual(sections.height_weights(), {"screen": 1, "log": 1})

    def test_vertical_resize_respects_minimums_and_never_collapses_expanded_panels(self):
        self.show_screen_panel()
        self.window.resize(1200, 1100)
        self.app.processEvents()
        sections = self.window.ui.monitorSectionsWidget
        connection, screen, log = sections.sections()
        connection_height = connection.height()
        for position in (-10000, 10000):
            sections.splitter.moveSplitter(position, 2)
            self.app.processEvents()
            self.assertGreaterEqual(screen.height(), screen.minimumHeight())
            self.assertGreaterEqual(log.height(), log.minimumHeight())
            self.assertGreater(screen.height(), 40)
            self.assertGreater(log.height(), 40)
            self.assertEqual(connection.height(), connection_height)
        self.assertFalse(sections.splitter.childrenCollapsible())

    def test_workspace_splitter_divider_is_rounded_and_hover_only(self):
        self.window.show()
        handle = self.window.ui.workspaceSplitter.handle(1)
        for theme in ("light", "dark"):
            self.window.settings_panel.theme_combo.setCurrentIndex(
                self.window.settings_panel.theme_combo.findData(theme)
            )
            self.app.processEvents()
            QApplication.sendEvent(handle, QEvent(QEvent.Type.Leave))
            idle = handle.grab().toImage()
            QApplication.sendEvent(handle, QEvent(QEvent.Type.Enter))
            hovered_pixmap = handle.grab()
            hovered = hovered_pixmap.toImage()
            scale = hovered_pixmap.devicePixelRatio()
            x = round(handle.width() / 2 * scale)
            middle = round(handle.height() / 2 * scale)
            corner = (round((handle.width() / 2 - 2) * scale), round(12 * scale))
            with self.subTest(theme=theme):
                self.assertNotEqual(hovered.pixelColor(x, middle), idle.pixelColor(x, middle))
                self.assertEqual(hovered.pixelColor(x, middle).name(), "#30415e" if theme == "dark" else "#dfe7f0")
                self.assertEqual(hovered.pixelColor(x, round(10 * scale)), idle.pixelColor(x, round(10 * scale)))
                self.assertNotEqual(hovered.pixelColor(*corner), hovered.pixelColor(x, middle))
                QApplication.sendEvent(handle, QEvent(QEvent.Type.Leave))
                self.assertEqual(handle.grab().toImage(), idle)

    def test_log_actions_fit_the_minimum_monitor_width(self):
        self.window.show()
        ui = self.window.ui
        ui.workspaceSplitter.setSizes([700, 309])
        ui.logMenuToggleButton.click()
        for show_latest in (False, True):
            ui.logLatestButton.setVisible(show_latest)
            self.app.processEvents()
            self.app.processEvents()
            with self.subTest(show_latest=show_latest):
                self.assertEqual(ui.monitorScrollArea.horizontalScrollBar().maximum(), 0)
                self.assertFalse(ui.logSaveButton.isHidden())
                self.assertGreaterEqual(ui.logTitleLabel.width(), ui.logTitleLabel.sizeHint().width())

    def test_workspace_splitter_resizes_only_middle_and_right_panels(self):
        self.window.show()
        splitter = self.window.ui.workspaceSplitter
        left = self.window.centralWidget().findChild(QWidget, "_1_settingStartWidget")
        middle, right = splitter.widget(0), splitter.widget(1)
        before = (middle.width(), right.width())
        splitter.setSizes([600, 310])
        self.app.processEvents()
        self.assertEqual(left.width(), 312)
        self.assertNotEqual((middle.width(), right.width()), before)
        self.assertGreaterEqual(middle.width(), 300)
        self.assertGreaterEqual(right.width(), 309)

    def test_start_page_scrollbars_follow_pointer_and_activity(self):
        self.window.show()
        self.window.resize(1000, 500)
        self.window.ui.logPrintText.setPlainText(
            "\n".join(f"scroll line {index}" for index in range(100))
        )
        self.app.processEvents()
        areas = (self.window.option_list_widget, self.window.ui.logPrintText)

        def handle_columns(scroll_bar):
            image = scroll_bar.grab().toImage()
            surface_color = scroll_bar._rounded_paint_filter._surface_color()
            return [
                x
                for x in range(image.width())
                if any(
                    image.pixelColor(x, y) != surface_color
                    for y in range(image.height())
                )
            ]

        for theme in ("light", "dark"):
            self.window.settings_panel.theme_combo.setCurrentIndex(
                self.window.settings_panel.theme_combo.findData(theme)
            )
            self.app.processEvents()
            for area in areas:
                with self.subTest(theme=theme, area=area.objectName()):
                    scroll_bar = area.verticalScrollBar()
                    controller = scroll_bar._contextual_controller
                    scroll_bar.setRange(0, 100)
                    scroll_bar.setPageStep(25)
                    self.app.processEvents()
                    controller._activity_timer.stop()
                    controller._activity_active = False

                    with patch("app.winUI.QCursor.pos", return_value=QPoint(-100, -100)):
                        controller._sync_state()
                    self.assertEqual(scroll_bar.width(), EXPANDED_SCROLLBAR_WIDTH)
                    self.assertFalse(scroll_bar.property("contextualVisible"))
                    self.assertFalse(scroll_bar.property("contextualExpanded"))
                    self.assertEqual(handle_columns(scroll_bar), [])

                    area_position = area.viewport().mapToGlobal(
                        area.viewport().rect().center()
                    )
                    with patch("app.winUI.QCursor.pos", return_value=area_position):
                        controller._sync_state()
                    self.assertTrue(scroll_bar.property("contextualVisible"))
                    self.assertFalse(scroll_bar.property("contextualExpanded"))
                    self.assertEqual(
                        handle_columns(scroll_bar),
                        list(
                            range(
                                EXPANDED_SCROLLBAR_WIDTH
                                - COMPACT_SCROLLBAR_WIDTH
                                - 1,
                                EXPANDED_SCROLLBAR_WIDTH - 1,
                            )
                        ),
                    )

                    scroll_position = scroll_bar.mapToGlobal(
                        scroll_bar.rect().center()
                    )
                    with patch("app.winUI.QCursor.pos", return_value=scroll_position):
                        controller._sync_state()
                    self.assertTrue(scroll_bar.property("contextualExpanded"))
                    self.assertEqual(
                        handle_columns(scroll_bar),
                        list(range(EXPANDED_SCROLLBAR_WIDTH - 1)),
                    )
                    expanded_image = scroll_bar.grab().toImage()
                    handle_color = scroll_bar._rounded_paint_filter._handle_color()
                    self.assertNotEqual(
                        expanded_image.pixelColor(0, 0), handle_color
                    )
                    self.assertEqual(
                        expanded_image.pixelColor(1, 2), handle_color
                    )

                    with patch("app.winUI.QCursor.pos", return_value=QPoint(-100, -100)):
                        controller.eventFilter(
                            area.viewport(), QEvent(QEvent.Type.Wheel)
                        )
                    self.assertTrue(scroll_bar.property("contextualVisible"))
                    self.assertFalse(scroll_bar.property("contextualExpanded"))
                    self.assertTrue(controller._activity_timer.isActive())
                    with patch("app.winUI.QCursor.pos", return_value=QPoint(-100, -100)):
                        controller._finish_activity()
                    self.assertFalse(scroll_bar.property("contextualVisible"))

                    controller.eventFilter(
                        scroll_bar, QEvent(QEvent.Type.MouseButtonPress)
                    )
                    self.assertTrue(scroll_bar.property("contextualVisible"))
                    self.assertTrue(scroll_bar.property("contextualExpanded"))
                    controller.eventFilter(
                        scroll_bar, QEvent(QEvent.Type.MouseButtonRelease)
                    )
                    self.assertFalse(controller._pointer_pressed)

        normal_scrollbars = (
            self.window.ui.scrollSettingWidget.verticalScrollBar(),
            self.window.settings_panel.detail_scroll.verticalScrollBar(),
        )
        for scroll_bar in normal_scrollbars:
            self.assertIsNone(scroll_bar.property("contextual"))
            self.assertEqual(scroll_bar.width(), EXPANDED_SCROLLBAR_WIDTH)
            scroll_bar.setRange(0, 100)
            scroll_bar.setPageStep(25)
            image = scroll_bar.grab().toImage()
            handle_color = scroll_bar._rounded_paint_filter._handle_color()
            self.assertNotEqual(image.pixelColor(0, 0), handle_color)
            self.assertEqual(image.pixelColor(2, 2), handle_color)

        task_scrollbar = self.window.option_list_widget.verticalScrollBar()
        log_scrollbar = self.window.ui.logPrintText.verticalScrollBar()
        task_right_gap = (
            self.window.option_list_widget.width()
            - task_scrollbar.mapTo(
                self.window.option_list_widget,
                QPoint(task_scrollbar.width(), 0),
            ).x()
        )
        log_right_gap = (
            self.window.ui.logPrintText.width()
            - log_scrollbar.mapTo(
                self.window.ui.logPrintText,
                QPoint(log_scrollbar.width(), 0),
            ).x()
        )
        self.assertEqual(task_right_gap, 2)
        self.assertEqual(log_right_gap, task_right_gap)

    def test_task_settings_button_stays_fixed_left_of_scrollbar_slot(self):
        self.window.show()
        self.app.processEvents()
        task_list = self.window.option_list_widget
        task_widget = self.find_task_widget(self.window)
        scroll_bar = task_list.verticalScrollBar()
        controller = scroll_bar._contextual_controller
        scroll_bar.setRange(0, 100)
        self.app.processEvents()

        positions = []
        for visible, expanded in ((False, False), (True, False), (True, True)):
            controller._set_state(visible, expanded)
            self.app.processEvents()
            positions.append(
                task_widget.setting_btn.mapTo(task_list.viewport(), QPoint(0, 0)).x()
            )
        self.assertEqual(len(set(positions)), 1)

        settings_icon_right = (
            positions[0]
            + (
                task_widget.setting_btn.width()
                + task_widget.setting_btn.iconSize().width()
            )
            // 2
        )
        self.assertEqual(
            settings_icon_right,
            task_list.viewport().width() - task_list.DRAG_LINE_RIGHT_MARGIN,
        )

    def test_settings_controls_align_in_both_themes(self):
        panel = self.window.settings_panel
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        for theme in ("light", "dark"):
            panel.theme_combo.setCurrentIndex(panel.theme_combo.findData(theme))
            for width in (1000, 1400):
                self.window.resize(width, 650)
                self.app.processEvents()
                with self.subTest(theme=theme, width=width):
                    controls = (panel.program_path_input, panel.program_browse_button,
                                panel.program_apply_button)
                    self.assertEqual(len({control.height() for control in controls}), 1)
                    self.assertEqual(len({control.y() for control in controls}), 1)
                    self.assertEqual(panel.detail_scroll.horizontalScrollBar().maximum(), 0)
                    self.assertEqual(
                        panel.width() - panel.detail_scroll.geometry().right() - 1, 6
                    )
                    self.assertEqual(panel.detail_layout.contentsMargins().right(), 6)

    def test_settings_without_overflow_highlights_first_card(self):
        self.window.resize(1400, 1600)
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        self.app.processEvents()
        panel = self.window.settings_panel
        self.assertEqual(panel.detail_scroll.verticalScrollBar().maximum(), 0)
        panel._sync_navigation_to_scroll(0)
        self.assertEqual(panel.navigation.currentRow(), 0)

    def test_dark_theme_applies_to_entire_window_and_is_saved(self):
        panel = self.window.settings_panel
        panel.theme_combo.setCurrentIndex(panel.theme_combo.findData("dark"))

        self.assertEqual(self.window._effective_theme, TitleBarTheme.DARK)
        self.assertIn("#111A2B", self.window.styleSheet())
        self.assertIn("QPushButton#dashboardNavButton:checked", self.window.styleSheet())
        config_path = self.window.runtime.user_dir / "config" / "maa_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["appearance"]["theme"], "dark")

        panel.theme_combo.setCurrentIndex(panel.theme_combo.findData("light"))
        self.assertEqual(self.window._effective_theme, TitleBarTheme.LIGHT)
        self.assertNotIn("#111A2B", self.window.styleSheet())

    def test_program_path_can_be_confirmed_from_custom_directory(self):
        panel = self.window.settings_panel
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        self.app.processEvents()
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
        self.assertTrue(panel.program_active_status.isVisible())
        self.assertTrue(panel.program_active_status.property("pathValid"))
        self.assertEqual(panel.program_apply_button.text(), "초기화")
        self.assertNotIn("(수동 경로)", panel.program_active_status.text())
        self.assertTrue(panel.program_active_status.text().startswith("사용 경로: "))
        self.assertTrue(builtin.checkbox.isEnabled())
        builtin.checkbox.setChecked(True)
        self.assertEqual(self.window.build_execution_queue()[0][0], PROGRAM_LAUNCH_ENTRY)
        changed.assert_called_once()
        config_path = self.window.runtime.user_dir / "config" / "maa_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["program"]["manual_path"], str(install_directory))

        panel.program_apply_button.click()
        QTest.qWait(180)
        settings = panel.program_settings()
        self.assertEqual(settings["manual_path"], "")
        self.assertEqual(panel.program_path_input.text(), "")
        self.assertTrue(panel.program_active_status_container.isHidden())
        self.assertEqual(panel.program_apply_button.text(), "확인")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["program"]["manual_path"], "")
        self.assertEqual(changed.call_count, 2)

    def test_invalid_manual_path_switches_to_reset_and_collapses_smoothly(self):
        panel = self.window.settings_panel
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        self.app.processEvents()
        automatic_path = self.window.runtime.user_dir / "AutoGame" / "BlueArchive.exe"
        with patch(
            "app.settingsUI.find_auto_program_executable",
            return_value=automatic_path,
        ), patch(
            "app.settingsUI.find_program_executable",
            return_value=automatic_path,
        ):
            panel._refresh_program_status()
        self.app.processEvents()
        initial_section_height = panel._sections[1].height()
        invalid_path = self.window.runtime.user_dir / "MissingGame"
        panel.program_path_input.setText(str(invalid_path))

        panel.program_apply_button.click()
        QTest.qWait(180)
        expanded_height = panel._sections[1].height()
        self.assertGreater(expanded_height, initial_section_height)
        self.assertEqual(panel.program_apply_button.text(), "초기화")
        self.assertTrue(panel.program_active_status.isVisible())
        self.assertIn("파일을 확인할 수 없습니다", panel.program_active_status.text())
        self.assertEqual(panel.program_settings()["manual_path"], "")

        with patch(
            "app.settingsUI.find_auto_program_executable",
            return_value=automatic_path,
        ), patch(
            "app.settingsUI.find_program_executable",
            return_value=automatic_path,
        ):
            panel.program_apply_button.click()
        self.assertIsNotNone(panel._program_status_animation)
        self.assertEqual(
            bytes(panel._program_status_animation.propertyName()), b"opacity"
        )
        self.assertFalse(panel.program_active_status.property("pathValid"))
        self.assertIn("파일을 확인할 수 없습니다", panel.program_active_status.text())
        height_while_fading = panel._sections[1].height()
        QTest.qWait(70)
        self.assertEqual(panel._sections[1].height(), height_while_fading)
        QTest.qWait(110)
        self.assertTrue(panel.program_active_status_container.isHidden())
        self.assertLess(panel._sections[1].height(), expanded_height)
        self.assertEqual(panel.program_apply_button.text(), "확인")
        self.assertEqual(panel.program_path_input.text(), "")

    def test_automatic_program_path_hides_manual_path_status(self):
        panel = self.window.settings_panel
        automatic_path = self.window.runtime.user_dir / "AutoGame" / "BlueArchive.exe"
        with patch(
            "app.settingsUI.find_auto_program_executable",
            return_value=automatic_path,
        ), patch(
            "app.settingsUI.find_program_executable",
            return_value=automatic_path,
        ):
            panel._refresh_program_status()

        self.assertIn(str(automatic_path), panel.program_auto_status.text())
        self.assertTrue(panel.program_active_status_container.isHidden())
        self.assertEqual(panel.program_apply_button.text(), "확인")
        row_margins = panel.program_path_row.layout().contentsMargins()
        self.assertEqual((row_margins.top(), row_margins.bottom()), (12, 0))

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

    def test_new_run_clears_previous_log_by_default(self):
        self.window.ui.logPrintText.setPlainText("이전 실행 로그")
        worker = MagicMock()
        with patch("app.winUI.RuntimeWorker", return_value=worker):
            self.window.on_task_start()

        log_text = self.window.ui.logPrintText.toPlainText()
        self.assertNotIn("이전 실행 로그", log_text)
        self.assertIn("작업을 시작합니다...", log_text)

    def test_new_run_keeps_previous_log_when_clear_setting_is_disabled(self):
        panel = self.window.settings_panel
        panel.clear_log_checkbox.setChecked(False)
        self.window.ui.logPrintText.setPlainText("이전 실행 로그")
        worker = MagicMock()
        with patch("app.winUI.RuntimeWorker", return_value=worker):
            self.window.on_task_start()

        log_text = self.window.ui.logPrintText.toPlainText()
        self.assertIn("이전 실행 로그", log_text)
        self.assertIn("작업을 시작합니다...", log_text)
        config = json.loads(
            (self.window.runtime.user_dir / "config" / "maa_config.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(config["general"]["clear_log_on_start"])

    def test_new_log_preserves_history_position_until_latest_is_requested(self):
        log_view = self.window.ui.logPrintText
        self.window.show()
        log_view.setPlainText("\n".join(f"기존 로그 {index}" for index in range(200)))
        self.app.processEvents()
        scroll_bar = log_view.verticalScrollBar()
        self.assertGreater(scroll_bar.maximum(), 0)

        history_position = scroll_bar.maximum() // 3
        scroll_bar.setValue(history_position)
        self.window.append_log("새 로그")
        self.app.processEvents()

        self.assertEqual(scroll_bar.value(), history_position)
        self.assertFalse(self.window.ui.logLatestButton.isHidden())

        self.window.ui.logLatestButton.click()
        self.app.processEvents()
        self.assertEqual(scroll_bar.value(), scroll_bar.maximum())
        self.assertTrue(self.window.ui.logLatestButton.isHidden())

    def test_log_toolbar_copies_clears_and_saves_plain_text(self):
        log_view = self.window.ui.logPrintText
        log_view.setPlainText("첫 줄\n둘째 줄")
        self.assertEqual(self.window.ui.logClearButton.text(), "초기화")

        self.window.ui.logCopyButton.click()
        self.assertEqual(QApplication.clipboard().text(), "첫 줄\n둘째 줄")

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "run-log.txt"
            with patch(
                "app.winUI.QFileDialog.getSaveFileName",
                return_value=(str(output_path), "텍스트 파일 (*.txt)"),
            ):
                self.assertTrue(self.window.save_log())
            self.assertEqual(
                output_path.read_text(encoding="utf-8"), "첫 줄\n둘째 줄"
            )

        self.window.ui.logClearButton.click()
        self.assertEqual(log_view.toPlainText(), "")
        self.assertTrue(self.window.ui.logLatestButton.isHidden())

    def test_log_action_menu_expands_left_and_closes_after_pointer_leave(self):
        ui = self.window.ui
        controller = self.window.log_action_menu_controller
        action_buttons = (ui.logCopyButton, ui.logClearButton, ui.logSaveButton)

        self.assertTrue(QSvgRenderer(str(LOG_MENU_EXPAND_ICON_PATH)).isValid())
        self.assertTrue(QSvgRenderer(str(LOG_MENU_COLLAPSE_ICON_PATH)).isValid())
        self.assertFalse(controller.is_expanded())
        self.assertEqual(ui.logMenuToggleButton.text(), "")
        self.assertFalse(ui.logMenuToggleButton.icon().isNull())
        collapsed_icon_key = ui.logMenuToggleButton.icon().cacheKey()
        self.assertTrue(all(button.isHidden() for button in action_buttons))

        self.window.show()
        ui.logMenuToggleButton.click()
        self.app.processEvents()
        self.assertTrue(controller.is_expanded())
        self.assertEqual(ui.logMenuToggleButton.text(), "")
        self.assertNotEqual(
            ui.logMenuToggleButton.icon().cacheKey(), collapsed_icon_key
        )
        self.assertTrue(ui.logMenuToggleButton.property("menuExpanded"))
        self.assertTrue(all(not button.isHidden() for button in action_buttons))
        self.assertLess(ui.logCopyButton.x(), ui.logClearButton.x())
        self.assertLess(ui.logClearButton.x(), ui.logSaveButton.x())
        self.assertLess(ui.logSaveButton.x(), ui.logMenuToggleButton.x())

        with patch("app.winUI.QCursor.pos", return_value=QPoint(-100, -100)):
            QApplication.sendEvent(ui.logActionMenu, QEvent(QEvent.Type.Leave))
            self.app.processEvents()
            self.assertTrue(controller._close_timer.isActive())
            QTest.qWait(LOG_ACTION_MENU_CLOSE_DELAY_MS + 50)

        self.assertFalse(controller.is_expanded())
        self.assertEqual(ui.logMenuToggleButton.text(), "")
        self.assertEqual(ui.logMenuToggleButton.icon().cacheKey(), collapsed_icon_key)
        self.assertFalse(ui.logMenuToggleButton.property("menuExpanded"))
        self.assertTrue(all(button.isHidden() for button in action_buttons))

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

    def test_select_popup_rows_are_centered_highlighted_and_scroll_with_rounded_thumb(self):
        self.window.show()
        self.window.show_sub_cases(self.find_task_widget(self.window))
        combo = self.window.ui.scrollSettingContents.findChild(QComboBox, "optionSelect")
        for index in range(40):
            combo.addItem(f"Option {index}", f"case-{index}")
        popup = combo.view()
        option = QStyleOptionViewItem()
        combo.itemDelegate().initStyleOption(option, combo.model().index(0, 0))
        self.assertTrue(option.displayAlignment & Qt.AlignmentFlag.AlignVCenter)
        for theme in ("light", "dark"):
            self.window.settings_panel.theme_combo.setCurrentIndex(
                self.window.settings_panel.theme_combo.findData(theme)
            )
            combo.showPopup()
            self.app.processEvents()
            self.app.processEvents()
            scroll_bar = popup.verticalScrollBar()
            with self.subTest(theme=theme):
                self.assertGreater(scroll_bar.maximum(), 0)
                self.assertTrue(scroll_bar.isVisible())
                self.assertLessEqual(popup.height(), 8 * 34 + 2)
                paint_filter = scroll_bar._rounded_paint_filter
                self.assertEqual(paint_filter._surface_color().name(), "#1e2d46" if theme == "dark" else "#ffffff")
                self.assertTrue(popup.hasMouseTracking())
                rect = popup.visualRect(combo.model().index(1, 0))
                self.assertGreaterEqual(rect.height(), 34)
                QTest.mouseMove(popup.viewport(), rect.center())
                self.app.processEvents()
                image = popup.viewport().grab().toImage()
                scale = popup.viewport().devicePixelRatioF()
                color = image.pixelColor(round(5 * scale), round(rect.center().y() * scale)).name()
                self.assertIn(color, ("#e6f5fc", "#00aeef") if theme == "light" else ("#283a55", "#168fbe"))
                scroll_bar.setValue(scroll_bar.maximum() // 2)
                slider = paint_filter._slider_rect()
                bar_image = scroll_bar.grab().toImage()
                center = slider.center()
                self.assertEqual(bar_image.pixelColor(round(center.x() * scale), round(center.y() * scale)), paint_filter._handle_color())
                self.assertNotEqual(bar_image.pixelColor(round(slider.left() * scale), round(slider.top() * scale)), paint_filter._handle_color())
            combo.hidePopup()

        combo.showPopup()
        self.app.processEvents()
        QTest.keyClick(popup, Qt.Key.Key_End)
        QTest.keyClick(popup, Qt.Key.Key_Return)
        self.assertEqual(combo.currentData(), "case-39")
        self.assertEqual(self.find_task_widget(self.window).selected_options["Test_Dropdown"], ["case-39"])

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
        self.assertTrue(init_kwargs["minimize_window"])
        self.assertTrue(callable(init_kwargs["cancellation_requested"]))
        runtime.run_task.assert_not_called()
        runtime.release_session.assert_called_once()
        self.assertFalse(worker.succeeded)


if __name__ == "__main__":
    unittest.main()
