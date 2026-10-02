"""No real devices or game windows are touched by these diagnostics tests."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))
from app.monitoring import MonitoringService, supported_presets, normalize_screen_preferences


WIN32 = {"name": "Window", "type": "Win32", "win32": {
    "window_regex": "^Game$", "class_regex": "Unity", "screencap": "PrintWindow",
    "mouse": "PostMessageWithWindowPos", "keyboard": "PostMessage",
}}
ADB = {"name": "Android", "type": "Adb"}


class MonitoringServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = MonitoringService({"controller": [WIN32, ADB]}, {}, self.temp.name)
        self.toolkit = patch("app.monitoring.Toolkit").start()
        self.addCleanup(patch.stopall)
        self.toolkit.init_option.return_value = True
        self.window = SimpleNamespace(hwnd=10, window_name="Game", class_name="UnityWndClass")
        self.toolkit.find_desktop_windows.return_value = [self.window]
        self.process = patch("app.monitoring.window_process_info", return_value=(123, "")).start()

    def test_only_declared_resource_supported_presets_are_exposed(self):
        self.assertEqual(supported_presets({"controller": [WIN32, ADB]}, {"controller": ["Android"]}), [ADB])
        self.assertEqual(supported_presets({"controller": [WIN32]}, {"controller": "bad"}), [])
        with self.assertRaises(ValueError):
            self.service.discover("undeclared")
        self.toolkit.init_option.assert_not_called()

    def test_win32_discovery_matches_title_class_and_program(self):
        self.toolkit.find_desktop_windows.return_value += [
            SimpleNamespace(hwnd=11, window_name="Wrong", class_name="UnityWndClass"),
            SimpleNamespace(hwnd=12, window_name="Game", class_name="Other"),
        ]
        self.process.return_value = (123, str(Path(self.temp.name) / "Game.exe"))
        targets = self.service.discover("Window", program_settings={"executable_name": "Game.exe"})
        self.assertEqual([target.key for target in targets], ["10"])
        self.assertIn("PID 123", targets[0].label)
        self.assertEqual(self.service.discover("Window", program_settings={"executable_name": "Wrong.exe"}), [])

    def test_invalid_regex_is_rejected_before_native_discovery(self):
        self.service.presets[0]["win32"]["window_regex"] = "["
        with self.assertRaises(Exception):
            self.service.discover("Window")
        self.toolkit.find_desktop_windows.assert_not_called()

    def test_connect_requires_current_discovery_and_checks_sdk_success(self):
        with self.assertRaises(ValueError):
            self.service.connect("Window", "10")
        self.service.discover("Window")
        controller = MagicMock()
        controller.post_connection.return_value.wait.return_value.succeeded = True
        controller.connected = True
        with patch("app.monitoring.Win32Controller", return_value=controller) as factory:
            self.assertEqual(self.service.connect("Window", "10").key, "10")
            self.assertEqual(factory.call_args.kwargs["hWnd"], 10)
        controller.post_click.assert_not_called()
        self.service.close()
        controller.post_inactive.assert_called_once()
        self.assertIsNone(self.service.controller)

    def test_failed_connection_releases_owned_controller(self):
        self.service.discover("Window")
        controller = MagicMock()
        controller.post_connection.return_value.wait.return_value.succeeded = False
        controller.connected = False
        with patch("app.monitoring.Win32Controller", return_value=controller):
            with self.assertRaises(RuntimeError):
                self.service.connect("Window", "10")
        self.assertIsNone(self.service.controller)

    def test_reused_window_handle_is_rejected_before_connecting(self):
        self.service.discover("Window")
        self.process.return_value = (999, "")
        with patch("app.monitoring.Win32Controller") as factory:
            with self.assertRaises(ValueError):
                self.service.connect("Window", "10")
        factory.assert_not_called()

    def test_manual_program_directory_matches_resolved_executable(self):
        executable = Path(self.temp.name) / "Game.exe"
        executable.touch()
        self.process.return_value = (123, str(executable))
        targets = self.service.discover("Window", program_settings={
            "executable_name": "Game.exe", "manual_path": self.temp.name,
        })
        self.assertEqual(len(targets), 1)

    def test_adb_discovery_from_other_preset_is_not_reused(self):
        device = SimpleNamespace(name="Emulator", address="localhost:5555", adb_path=Path("adb.exe"))
        self.toolkit.find_adb_devices.return_value = [device]
        self.service.discover("Android")
        self.service.presets.append({"name": "OtherAdb", "type": "Adb"})
        with patch("app.monitoring.AdbController") as factory:
            with self.assertRaises(ValueError):
                self.service.connect("OtherAdb", device.address, {"address": device.address})
        factory.assert_not_called()

    def test_adb_uses_discovered_device_masks_and_configuration(self):
        device = SimpleNamespace(name="Emulator", address="127.0.0.1:5555", adb_path=Path("adb.exe"),
                                 screencap_methods=16, input_methods=2, config={"test": True})
        self.toolkit.find_adb_devices.return_value = [device]
        targets = self.service.discover("Android")
        controller = MagicMock()
        with patch("app.monitoring.AdbController", return_value=controller) as factory:
            self.service.connect("Android", targets[0].key)
        self.assertEqual(factory.call_args.kwargs["config"], device.config)
        self.assertEqual(factory.call_args.kwargs["screencap_methods"], 16)

    def test_manual_adb_requires_valid_path_and_address(self):
        with self.assertRaises(ValueError):
            self.service.connect("Android", "", {"adb_path": "missing", "address": "localhost:5555"})
        with self.assertRaises(ValueError):
            self.service.connect("Android", "", {})

    def test_changed_adb_executable_does_not_reuse_old_device_configuration(self):
        old_path = Path(self.temp.name) / "old.exe"
        new_path = Path(self.temp.name) / "new.exe"
        old_path.touch()
        new_path.touch()
        device = SimpleNamespace(name="Emulator", address="localhost:5555", adb_path=old_path,
                                 screencap_methods=16, input_methods=2, config={"old": True})
        self.toolkit.find_adb_devices.return_value = [device]
        self.service.discover("Android", {"adb_path": str(old_path)})
        controller = MagicMock()
        with patch("app.monitoring.AdbController", return_value=controller) as factory:
            self.service.connect("Android", device.address, {"adb_path": str(new_path), "address": device.address})
        self.assertEqual(factory.call_args.kwargs["adb_path"], str(new_path))
        self.assertNotIn("config", factory.call_args.kwargs)

    def test_capture_copies_bgr_frame_and_rejects_empty_images(self):
        controller = MagicMock()
        image = np.zeros((20, 40, 3), dtype=np.uint8)
        controller.post_screencap.return_value.wait.return_value.get.return_value = image
        self.service.controller = controller
        captured = self.service.capture()
        image[:] = 255
        self.assertEqual(captured.max(), 0)
        controller.post_screencap.return_value.wait.return_value.get.return_value = np.zeros((0, 0, 3), dtype=np.uint8)
        with self.assertRaises(ValueError):
            self.service.capture()

    def test_failed_inactive_keeps_controller_for_safe_retry(self):
        self.service.controller = MagicMock()
        self.service.controller.post_inactive.return_value.wait.return_value.succeeded = False
        with self.assertRaises(RuntimeError):
            self.service.close()
        self.assertIsNotNone(self.service.controller)

    def test_screen_preferences_do_not_accept_unsafe_rates(self):
        self.assertEqual(normalize_screen_preferences({"fps": 999, "mode": "anything"}), {"fps": 2, "mode": "single"})
        self.assertEqual(normalize_screen_preferences({"fps": True})["fps"], 2)


if __name__ == "__main__":
    unittest.main()
