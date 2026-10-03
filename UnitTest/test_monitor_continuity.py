"""User-controlled continuous preview survives runtime/FPS/error handoffs."""
import threading
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import test_runtime_lifecycle as ui_fixtures
from PySide6.QtTest import QTest

from app.monitoring import ConnectionTarget, MonitoringDisconnected, PreviewNotReady


class ContinuousPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui_fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = ui_fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.monitor = self.window.monitor
        self.screen = self.fixture.show_screen_panel()
        self.controller = self.fixture.configure_screen_capture()
        self.monitor.service._user32 = MagicMock()
        self.monitor.service._user32.IsIconic.return_value = False
        self.monitor.service.targets = [ConnectionTarget("42", "game", "Win32", hwnd=42, pid=123)]
        self.process = patch("app.monitoring.window_process_info", return_value=(123, "")).start()
        self.addCleanup(patch.stopall)
        self.screen.mode.setCurrentIndex(self.screen.mode.findData("continuous"))
        self.screen.fps.setCurrentIndex(self.screen.fps.findData(1))
        self.addCleanup(self.cleanup_preview)

    def cleanup_preview(self):
        self.monitor.stop_preview()
        self.fixture.wait_for_monitor()
        self.monitor.service.controller = None
        self.window.isRunning = False

    def start_preview(self):
        self.monitor.toggle_capture()
        self.fixture.wait_for_monitor()
        self.assertTrue(self.monitor.streaming)

    def capture_again(self):
        self.monitor.preview_timer.stop()
        self.monitor.capture_frame()
        self.fixture.wait_for_monitor()

    def test_live_fps_change_preserves_session_and_resets_old_measurement(self):
        self.start_preview()
        epoch = self.monitor.preview_generation
        self.monitor.display_fps.actual = 1
        self.screen.fps.setCurrentIndex(self.screen.fps.findData(60))
        self.assertTrue(self.monitor.streaming)
        self.assertEqual(self.monitor.preview_generation, epoch)
        self.assertIsNone(self.monitor.display_fps.actual)
        self.assertIn("목표 60 FPS  출력 측정 중", self.screen.status.text())
        self.assertNotIn("⚠", self.screen.status.text())
        self.assertLessEqual(self.monitor.preview_timer.interval(), 17)
        self.assertFalse(self.screen.mode.isEnabled())
        self.assertTrue(self.screen.fps.isEnabled())

    def test_task_start_and_finish_preserve_intent_and_last_image(self):
        self.start_preview()
        image = self.screen.preview.image.cacheKey()
        for running in (True, False):
            self.window.isRunning = running
            self.monitor.task_state_changed()
            self.assertTrue(self.monitor.streaming)
            self.assertTrue(self.monitor.preview_timer.isActive())
            self.assertEqual(self.screen.preview.image.cacheKey(), image)

    def test_task_handoff_during_capture_discards_stale_frame_and_reschedules(self):
        self.start_preview()
        image = self.screen.preview.image.cacheKey()
        entered, release = threading.Event(), threading.Event()

        def slow_capture():
            entered.set()
            release.wait(2)
            return np.full((10, 20, 3), 77, np.uint8)

        with patch.object(self.monitor.service, "capture", side_effect=slow_capture):
            self.monitor.preview_timer.stop()
            self.monitor.capture_frame()
            try:
                self.assertTrue(entered.wait(1))
                self.window.isRunning = True
                self.monitor.task_state_changed()
                self.monitor.capture_frame()  # Busy is not a user stop.
                self.assertTrue(self.monitor.streaming)
            finally:
                release.set()
                self.fixture.wait_for_monitor()
        self.assertEqual(self.screen.preview.image.cacheKey(), image)
        self.assertTrue(self.monitor.preview_timer.isActive())

    def test_preflight_cleanup_keeps_stream_and_starts_task_once(self):
        self.start_preview()
        self.monitor.target_key = "42"
        self.monitor.service.connected_target = ConnectionTarget("42", "game", "Win32", hwnd=42)
        callback = MagicMock()

        def close():
            self.monitor.service.controller = None

        with patch.object(self.monitor.service, "close", side_effect=close):
            self.assertTrue(self.monitor.prepare_task_start(callback))
            self.assertTrue(self.monitor.streaming)
            self.fixture.wait_for_monitor()
            QTest.qWait(10)
        callback.assert_called_once_with()
        self.assertEqual(self.monitor._resume_connection[1], "42")
        self.assertIsNone(self.monitor.pending_start)
        self.window.isRunning = True
        self.monitor.task_state_changed()
        self.assertTrue(self.monitor.preview_timer.isActive())

    def test_runtime_initialization_retries_then_recovers_without_manual_restart(self):
        self.window.isRunning = True
        self.window.runtime.capture_cached_frame.side_effect = RuntimeError("initializing")
        self.start_preview()
        self.assertIn("재시도 중", self.screen.status.text())
        self.assertTrue(self.monitor.preview_timer.isActive())
        self.window.runtime.capture_cached_frame.side_effect = None
        self.window.runtime.capture_cached_frame.return_value = np.zeros((10, 20, 3), np.uint8)
        self.capture_again()
        self.assertIn("실행 캐시", self.screen.status.text())
        self.assertNotIn("재시도 중", self.screen.status.text())
        self.assertTrue(self.monitor.streaming)

    def test_repeat_error_and_recovery_never_append_continuous_logs(self):
        error = RuntimeError("temporary capture error")
        frame = np.zeros((10, 20, 3), np.uint8)
        with patch.object(self.window, "append_log") as log:
            with patch.object(self.monitor.service, "capture", side_effect=[error, error, frame, error]):
                self.start_preview()
                self.capture_again()
                self.capture_again()
                self.assertNotIn("재시도 중", self.screen.status.text())
                self.capture_again()
            log.assert_not_called()
        self.assertTrue(self.monitor.streaming)
        self.assertIn("temporary capture error", self.screen.status.text())
        self.assertIn("목표 1 FPS", self.screen.status.text())
        self.assertIn("재시도 중", self.screen.status.text())

    def test_actual_disconnection_stops_stream_and_sampling(self):
        def close():
            self.monitor.service.controller = None

        with patch.object(self.monitor.service, "capture", side_effect=MonitoringDisconnected("disconnected")), \
                patch.object(self.monitor.service, "close", side_effect=close):
            self.monitor.toggle_capture()
            self.fixture.wait_for_monitor()
        self.assertFalse(self.monitor.streaming)
        self.assertFalse(self.monitor.preview_timer.isActive())
        self.assertFalse(self.monitor.fps_timer.isActive())
        self.assertIn("disconnected", self.screen.status.text())

    def test_lost_task_connection_is_terminal_without_creating_a_replacement(self):
        self.window.isRunning = True
        self.window.runtime.capture_cached_frame.side_effect = MonitoringDisconnected("task window closed")

        def close():
            self.monitor.service.controller = None

        with patch("app.monitoring.Win32Controller") as factory, \
                patch.object(self.monitor.service, "close", side_effect=close):
            self.monitor.toggle_capture()
            self.fixture.wait_for_monitor()
        factory.assert_not_called()
        self.assertFalse(self.monitor.streaming)
        self.window.runtime.capture_cached_frame.assert_called_once()

    def test_explicit_disconnect_during_task_never_disconnects_task_controller(self):
        self.start_preview()
        self.window.isRunning = True
        self.monitor.task_state_changed()
        self.monitor.disconnect_target()
        self.fixture.wait_for_monitor()
        self.assertFalse(self.monitor.streaming)
        self.assertTrue(self.screen.preview.image.isNull())
        self.window.runtime.controller.post_inactive.assert_not_called()
        self.window.runtime.release_session.assert_not_called()

    def test_failed_task_start_can_restore_preflight_capture(self):
        self.start_preview()
        self.monitor.service.controller = None
        self.monitor._resume_connection = (self.monitor.preset_name, "42", dict(self.monitor.preferences))

        def reconnect(*args):
            self.monitor.service.controller = self.controller

        with patch.object(self.monitor.service, "connect", side_effect=reconnect) as connect:
            self.capture_again()
        connect.assert_called_once_with(*self.monitor._resume_connection)
        self.assertTrue(self.monitor.streaming)
        self.assertFalse(self.screen.preview.image.isNull())

    def test_starting_monitor_during_task_saves_identity_and_reconnects_only_after_finish(self):
        self.window.isRunning = True
        self.monitor.service.controller = None
        # The task may have selected a different actual HWND than preflight.
        self.monitor._resume_connection = (self.monitor.preset_name, "99", dict(self.monitor.preferences))
        target = ConnectionTarget("42", "game", "Win32", hwnd=42, pid=123)
        self.window.runtime.preview_connection_target.return_value = (self.monitor.preset_name, target)
        self.window.runtime.capture_cached_frame.return_value = np.zeros((10, 20, 3), np.uint8)
        with patch.object(self.monitor.service, "connect") as connect:
            self.start_preview()
            self.assertEqual(self.monitor._resume_connection[1], "42")
            self.capture_again()
            connect.assert_not_called()
        self.window.isRunning = False
        self.monitor.task_state_changed()
        def reconnect(*args):
            self.monitor.service.controller = self.controller
        with patch.object(self.monitor.service, "connect", side_effect=reconnect) as connect:
            self.capture_again()
        connect.assert_called_once_with(*self.monitor._resume_connection)
        self.assertTrue(self.monitor.streaming)

    def test_minimized_post_task_target_waits_without_reconnect_then_recovers(self):
        self.start_preview()
        image = self.screen.preview.image.cacheKey()
        self.monitor.service.controller = None
        self.monitor._resume_connection = (self.monitor.preset_name, "42", dict(self.monitor.preferences))
        self.monitor.service._user32.IsIconic.return_value = True
        def connect(*args):
            self.monitor.service.controller = self.controller
        with patch.object(self.monitor.service, "connect", side_effect=connect) as reconnect, \
                patch.object(self.window, "append_log") as log:
            self.capture_again()
            reconnect.assert_not_called()
            self.assertTrue(self.monitor.streaming)
            self.assertEqual(self.screen.preview.image.cacheKey(), image)
            self.assertIn("최소화", self.screen.status.text())
            self.monitor.service._user32.IsIconic.return_value = False
            self.capture_again()
            reconnect.assert_called_once()
            log.assert_not_called()

    def test_terminal_continuous_error_does_not_append_execution_log(self):
        with patch.object(self.monitor.service, "capture", side_effect=MonitoringDisconnected("closed")), \
                patch.object(self.window, "append_log") as log:
            self.monitor.toggle_capture()
            self.fixture.wait_for_monitor()
            log.assert_not_called()
        self.assertFalse(self.monitor.streaming)

    def test_pending_preflight_placement_restore_still_blocks_start_without_controller(self):
        from app.runtime import WindowPlacement
        self.monitor.service.controller = None
        self.monitor.service._original_window_placement = WindowPlacement()
        self.monitor.service._placement_hwnd = 42
        self.monitor.service._user32.SetWindowPlacement.side_effect = [False, True]
        callback = MagicMock()
        self.assertTrue(self.monitor.prepare_task_start(callback))
        self.fixture.wait_for_monitor()
        callback.assert_not_called()
        self.assertTrue(self.monitor.service.needs_cleanup)
        self.assertTrue(self.monitor.prepare_task_start(callback))
        self.fixture.wait_for_monitor()
        QTest.qWait(10)
        callback.assert_called_once()
        self.assertFalse(self.monitor.service.needs_cleanup)
