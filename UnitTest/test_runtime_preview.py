"""Task-safe read-only previews: no second SDK window-state owner."""
import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import test_runtime_lifecycle as ui_fixtures
from app.monitoring import ConnectionTarget, MonitoringDisconnected, PreviewNotReady
from app.runtime import SW_SHOWNOACTIVATE, WindowPlacement


class RuntimePreviewCacheTests(unittest.TestCase):
    def setUp(self):
        fixture = ui_fixtures.RuntimeLifecycleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.runtime = fixture.runtime

    def test_cache_read_never_posts_capture_input_or_inactive(self):
        controller = self.runtime.controller = MagicMock(connected=True)
        frame = controller.cached_image = np.full((10, 20, 3), 50, np.uint8)
        self.runtime._target_hwnd = ctypes.c_void_p(42)
        with patch("app.runtime.Win32Controller") as factory:
            result = self.runtime.capture_cached_frame()
        factory.assert_not_called()
        for method in (controller.post_screencap, controller.post_click, controller.post_inactive):
            method.assert_not_called()
        frame[:] = 255
        self.assertEqual(result[0, 0, 0], 50)
        self.runtime._user32.ShowWindow.assert_not_called()
        self.runtime._user32.SetWindowPlacement.assert_not_called()
        self.runtime._user32.SetWindowLongPtrW.assert_not_called()
        self.runtime._user32.SetLayeredWindowAttributes.assert_not_called()

    def test_initialization_and_release_are_retryable(self):
        for controller in (None, MagicMock(connected=False)):
            self.runtime.controller = controller
            self.runtime._target_hwnd = 42
            self.runtime._user32.IsWindow.return_value = True
            with self.assertRaises(PreviewNotReady):
                self.runtime.capture_cached_frame()

    def test_real_window_loss_is_terminal(self):
        self.runtime.controller = MagicMock(connected=False)
        self.runtime._target_hwnd = 42
        self.runtime._user32.IsWindow.return_value = False
        with self.assertRaises(MonitoringDisconnected):
            self.runtime.capture_cached_frame()

    def test_resume_target_is_metadata_only(self):
        self.runtime.controller = controller = MagicMock(connected=True)
        self.runtime._target_hwnd = 42
        self.runtime.controller_config = {"name": "Window", "type": "Win32"}
        with patch("app.monitoring.window_process_info", return_value=(123, "game.exe")), \
                patch("app.runtime.Win32Controller") as factory:
            name, target = self.runtime.preview_connection_target()
        self.assertEqual((name, target.hwnd, target.pid), ("Window", 42, 123))
        factory.assert_not_called()
        controller.post_connection.assert_not_called()
        controller.post_screencap.assert_not_called()

    def test_cleanup_stops_capture_helper_before_restoring_original_placement(self):
        events = []
        self.runtime.controller = controller = MagicMock(connected=True)
        controller.post_inactive.side_effect = lambda: events.append("inactive") or ui_fixtures.make_job()
        self.runtime._target_hwnd = 42
        placement = self.runtime._original_window_placement = WindowPlacement()
        placement.show_cmd = 1
        self.runtime._user32.IsWindow.return_value = True
        self.runtime._user32.SetWindowPlacement.side_effect = lambda *args: events.append("restore") or True
        self.assertTrue(self.runtime.release_session()[0])
        self.assertEqual(events, ["inactive", "restore"])
        self.assertIsNone(self.runtime._original_window_placement)
        self.runtime._user32.ShowWindow.assert_not_called()

    def test_original_minimized_state_is_preserved_when_restoring(self):
        self.runtime._target_hwnd = 42
        placement = self.runtime._original_window_placement = WindowPlacement()
        placement.show_cmd = 2
        restored = []
        self.runtime._user32.IsWindow.return_value = True
        self.runtime._user32.SetWindowPlacement.side_effect = lambda hwnd, ptr: restored.append(ptr._obj.show_cmd) or True
        self.assertTrue(self.runtime.release_session()[0])
        self.assertEqual(restored, [2])

    def test_manual_stop_finishes_before_capture_release_and_restore(self):
        events = []
        tasker = self.runtime.tasker = MagicMock(running=True, stopping=False)
        def stop():
            events.append("stop")
            tasker.running = False
            return ui_fixtures.make_job()
        tasker.post_stop.side_effect = stop
        self.runtime.controller = controller = MagicMock(connected=True)
        controller.post_inactive.side_effect = lambda: events.append("inactive") or ui_fixtures.make_job()
        self.runtime._target_hwnd = 42
        self.runtime._original_window_placement = WindowPlacement()
        self.runtime._user32.SetWindowPlacement.side_effect = lambda *args: events.append("restore") or True
        self.assertTrue(self.runtime.stop_task()[0])
        self.assertTrue(self.runtime.release_session()[0])
        self.assertEqual(events, ["stop", "inactive", "restore"])

    def test_minimized_restore_failure_retains_original_for_retry(self):
        self.runtime._target_hwnd = 42
        placement = self.runtime._original_window_placement = WindowPlacement()
        placement.show_cmd = 1
        self.runtime._user32.SetWindowPlacement.side_effect = [False, True]
        self.assertFalse(self.runtime.release_session()[0])
        self.assertIs(self.runtime._original_window_placement, placement)
        self.assertEqual(self.runtime._target_hwnd, 42)
        self.assertTrue(self.runtime.release_session()[0])

    def test_snapshot_precedes_sdk_constructor_and_connection_changes(self):
        events = []
        api = self.runtime._user32
        def save(hwnd, ptr):
            events.append("snapshot")
            ptr._obj.show_cmd = 2
            ptr._obj.normal_position.left = 120
            return True
        api.GetWindowPlacement.side_effect = save
        controller = MagicMock(connected=True)
        controller.post_connection.return_value = ui_fixtures.make_job()
        def factory(**kwargs):
            events.append("factory")
            return controller
        with patch("app.runtime.Win32Controller", side_effect=factory):
            self.assertTrue(self.runtime._create_controller(window=SimpleNamespace(hwnd=42))[0])
        controller.post_connection.side_effect = lambda: events.append("connection changes placement") or ui_fixtures.make_job()
        self.assertTrue(self.runtime._execute_controller()[0])
        restored = []
        api.SetWindowPlacement.side_effect = lambda hwnd, ptr: restored.append(
            (ptr._obj.show_cmd, ptr._obj.normal_position.left)) or True
        self.assertTrue(self.runtime.release_session()[0])
        self.assertEqual(events, ["snapshot", "factory", "connection changes placement"])
        self.assertEqual(restored, [(2, 120)])

    def test_resize_precedes_connection_and_is_not_repeated_under_sdk_helper(self):
        events = []
        self.runtime._toolkit_initialized = True
        self.runtime.resource = MagicMock(loaded=True)
        self.runtime._resource_loaded = True
        controller = MagicMock(connected=True)
        controller.post_connection.side_effect = lambda: events.append("connect") or ui_fixtures.make_job()
        tasker = MagicMock(running=False, stopping=False, inited=True)
        tasker.post_task.return_value = ui_fixtures.make_job()
        def resize(*args):
            self.assertEqual(controller.post_connection.call_count, 0)
            events.append("resize")
            return True
        with patch("app.runtime.Win32Controller", return_value=controller), \
                patch("app.runtime.Tasker", return_value=tasker), \
                patch.object(self.runtime, "_find_target_window", return_value=(SimpleNamespace(hwnd=42), "found")), \
                patch.object(self.runtime, "_resize_window_for_task", side_effect=resize):
            self.assertTrue(self.runtime.initialize(execution_queue=[("Task", {})])[0])
            self.assertTrue(self.runtime.run_task([("Task", {})])[0])
        self.assertEqual(events, ["resize", "connect"])
        self.assertFalse(self.runtime._window_size_prepared)

    def test_resize_uses_nonactivating_restore_and_keeps_preconnection_snapshot(self):
        self.runtime._target_hwnd = 42
        api = self.runtime._user32
        def save(hwnd, ptr):
            ptr._obj.show_cmd = 2
            return True
        api.GetWindowPlacement.side_effect = save
        api.IsIconic.return_value = True
        self.assertTrue(self.runtime._save_window_placement())
        original = self.runtime._original_window_placement
        with patch("app.runtime.time.sleep"):
            self.assertTrue(self.runtime._resize_window_for_task())
        api.ShowWindow.assert_called_once_with(42, SW_SHOWNOACTIVATE)
        api.SetForegroundWindow.assert_not_called()
        self.assertIs(self.runtime._original_window_placement, original)

    def test_pointer_handle_does_not_mistake_same_foreground_for_other_window(self):
        self.runtime._target_hwnd = ctypes.c_void_p(42)
        api = self.runtime._user32
        api.IsIconic.return_value = True
        api.GetForegroundWindow.return_value = 42
        with patch("app.runtime.time.sleep"):
            self.assertFalse(self.runtime._minimize_window_for_task())

    def test_iconic_window_without_foreground_is_success_not_task_failure(self):
        self.runtime._target_hwnd = 42
        api = self.runtime._user32
        api.IsIconic.return_value = True
        api.GetForegroundWindow.return_value = None
        self.assertTrue(self.runtime._minimize_window_for_task())
        api.SendMessageTimeoutW.assert_called_once()
        api.SetForegroundWindow.assert_not_called()

    def test_startup_guard_saves_and_resizes_before_minimizing(self):
        events = []
        with patch.object(self.runtime, "_save_window_placement", side_effect=lambda: events.append("save") or True), \
                patch.object(self.runtime, "_apply_startup_window_guard", side_effect=lambda hwnd: events.append("guard") or True), \
                patch.object(self.runtime, "_resize_window_for_task", side_effect=lambda: events.append("resize") or True), \
                patch.object(self.runtime, "_minimize_window_for_task", side_effect=lambda: events.append("minimize") or True), \
                patch.object(self.runtime, "_restore_startup_window_guard", side_effect=lambda: events.append("unguard") or True):
            self.assertTrue(self.runtime._prepare_started_window_for_minimized_connection(
                SimpleNamespace(hwnd=42), 1)[0])
        self.assertEqual(events, ["save", "guard", "resize", "minimize", "unguard"])


class RuntimePreviewUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui_fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = ui_fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.screen = self.fixture.show_screen_panel()
        self.controller = self.fixture.configure_screen_capture()
        self.window.isRunning = True
        self.addCleanup(lambda: setattr(self.window, "isRunning", False))

    def test_running_display_reads_cache_without_creating_native_controller(self):
        runtime = self.window.runtime
        runtime.capture_cached_frame.side_effect = [
            np.full((10, 20, 3), 30, np.uint8), np.full((10, 20, 3), 90, np.uint8)]
        with patch("app.monitoring.Win32Controller") as factory:
            for value in (30, 90):
                self.window.monitor.toggle_capture()
                self.fixture.wait_for_monitor()
                self.assertEqual(self.screen.preview.image.pixelColor(0, 0).red(), value)
        factory.assert_not_called()
        self.assertEqual(runtime.capture_cached_frame.call_count, 2)
        runtime.controller.post_screencap.assert_not_called()
        runtime.controller.post_inactive.assert_not_called()
        self.controller.post_screencap.assert_not_called()
        self.assertIn("실행 캐시", self.screen.status.text())

    def test_metadata_is_optional_and_not_a_second_capture_path(self):
        self.window.runtime.capture_cached_frame.return_value = np.zeros((10, 20, 3), np.uint8)
        self.window.runtime.preview_connection_target.side_effect = PreviewNotReady("connecting")
        self.window.monitor.toggle_capture()
        self.fixture.wait_for_monitor()
        self.window.runtime.capture_cached_frame.assert_called_once()
        self.controller.post_screencap.assert_not_called()
        self.assertIn("실행 캐시", self.screen.status.text())

    def test_qimage_owns_frame_without_an_extra_validation_copy(self):
        from app.monitorUI import owned_qimage
        frame = np.full((10, 20, 3), 50, np.uint8)
        with patch("app.monitorUI.MonitoringService.validate_frame", wraps=self.window.monitor.service.validate_frame) as validate:
            image = owned_qimage(frame)
        validate.assert_called_once_with(frame, copy=False)
        frame[:] = 255
        self.assertEqual(image.pixelColor(0, 0).red(), 50)
