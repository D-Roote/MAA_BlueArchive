"""Fast-stop cleanup must drain native actions before restoring the window."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
from app.runtime import AppRuntime, ControllerActivitySink, WindowPlacement


class ControllerStopTests(unittest.TestCase):
    def setUp(self):
        self.runtime = AppRuntime()
        self.runtime._user32 = MagicMock()
        self.controller = self.runtime.controller = MagicMock(connected=True)
        self.controller.post_inactive.return_value = fixtures.make_job()
        self.runtime._original_window_placement = WindowPlacement()
        self.runtime._target_hwnd = 123

    def notify(self, state, ctrl_id=1):
        self.runtime._controller_activity.on_raw_notification(
            self.controller, "Controller.Action." + state, {"ctrl_id": ctrl_id})

    def test_tasker_stop_does_not_allow_cleanup_to_overtake_running_capture(self):
        self.runtime._controller_sink_id = 7
        tasker = self.runtime.tasker = MagicMock(running=True, stopping=False)
        job = fixtures.make_job()
        job.wait.side_effect = lambda: (setattr(tasker, "running", False) or job)
        tasker.post_stop.return_value = job
        self.notify("Starting")
        self.assertTrue(self.runtime.stop_task()[0])
        entered, done = threading.Event(), threading.Event()
        results = []

        def cleanup():
            entered.set()
            results.append(self.runtime.release_session())
            done.set()

        cleaner = threading.Thread(target=cleanup)
        cleaner.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(done.wait(0.05))
            self.controller.post_inactive.assert_not_called()
            self.runtime._user32.SetWindowPlacement.assert_not_called()
        finally:
            self.notify("Succeeded")
            cleaner.join(2)
        self.assertFalse(cleaner.is_alive())
        self.assertTrue(results[0][0], results)
        self.controller.post_inactive.assert_called_once()
        self.controller.remove_sink.assert_called_once_with(7)
        self.runtime._user32.SetWindowPlacement.assert_called_once()
        self.assertIsNone(self.runtime.controller)

    def test_failed_native_action_is_also_terminal(self):
        self.notify("Starting")
        self.notify("Failed")
        self.assertTrue(self.runtime._controller_activity.wait_quiet(time.monotonic() + 1))

    def test_tracker_waits_for_every_started_action_ignores_unrelated_events(self):
        tracker = ControllerActivitySink()
        for identifier in (1, 2):
            tracker.on_raw_notification(None, "Controller.Action.Starting", {"ctrl_id": identifier})
        tracker.on_raw_notification(None, "Node.Action.Succeeded", {"ctrl_id": 2})
        tracker.on_raw_notification(None, "Controller.Action.Succeeded", {"ctrl_id": 1})
        self.assertFalse(tracker.wait_quiet(time.monotonic() + 0.03))
        tracker.on_raw_notification(None, "Controller.Action.Failed", {"ctrl_id": 2})
        self.assertTrue(tracker.wait_quiet(time.monotonic() + 1))

    def test_rejected_post_retries_without_waiting_on_invalid_job(self):
        rejected = fixtures.make_job(False)
        rejected.job_id = 0
        accepted = fixtures.make_job()
        accepted.job_id = 8
        self.controller.post_inactive.side_effect = [rejected, accepted]
        with patch("app.runtime.time.sleep") as sleep:
            self.assertTrue(self.runtime.release_session()[0])
        rejected.wait.assert_not_called()
        accepted.wait.assert_called_once()
        sleep.assert_called_once_with(0.05)

    def test_executed_deactivation_failure_is_not_silently_retried(self):
        failed = fixtures.make_job(False)
        failed.job_id = 8
        self.controller.post_inactive.return_value = failed
        self.assertFalse(self.runtime.release_session()[0])
        self.controller.post_inactive.assert_called_once()
        self.runtime._user32.SetWindowPlacement.assert_not_called()
        self.assertIs(self.runtime.controller, self.controller)

    def test_busy_timeout_preserves_controller_and_window_for_safe_retry(self):
        self.runtime._controller_sink_id = 7
        self.notify("Starting")
        with patch("app.runtime.CONTROLLER_DRAIN_TIMEOUT_SECONDS", 0.02):
            result = self.runtime.release_session()
        self.assertFalse(result[0])
        self.assertIn("완료를 기다리는 시간이 초과", result[1])
        self.controller.post_inactive.assert_not_called()
        self.controller.remove_sink.assert_not_called()
        self.runtime._user32.SetWindowPlacement.assert_not_called()
        self.assertIs(self.runtime.controller, self.controller)
        self.notify("Failed")
        self.assertTrue(self.runtime.release_session()[0])

    def test_permanently_rejected_post_is_bounded_and_preserves_session(self):
        rejected = fixtures.make_job(False)
        rejected.job_id = 0
        self.controller.post_inactive.return_value = rejected
        with patch("app.runtime.CONTROLLER_DRAIN_TIMEOUT_SECONDS", 0.01):
            result = self.runtime.release_session()
        self.assertFalse(result[0])
        self.assertIn("중지 상태 해제를 기다리는 시간이 초과", result[1])
        rejected.wait.assert_not_called()
        self.assertIs(self.runtime.controller, self.controller)
        self.runtime._user32.SetWindowPlacement.assert_not_called()

    def test_create_registers_activity_sink_before_connect_and_releases_it(self):
        controller = self.controller
        controller.add_sink.return_value = 7
        with patch("app.runtime.Win32Controller", return_value=controller), patch.object(
                self.runtime, "_save_window_placement", return_value=True):
            self.assertTrue(self.runtime._create_controller(window=SimpleNamespace(hwnd=123))[0])
        controller.add_sink.assert_called_once_with(self.runtime._controller_activity)
        sink_id = self.runtime._controller_sink_id
        self.assertTrue(self.runtime.release_session()[0])
        controller.remove_sink.assert_called_once_with(sink_id)

    def test_sink_registration_failure_retains_partial_controller_for_cleanup(self):
        self.controller.add_sink.return_value = None
        with patch("app.runtime.Win32Controller", return_value=self.controller), patch.object(
                self.runtime, "_save_window_placement", return_value=True):
            self.assertFalse(self.runtime._create_controller(window=SimpleNamespace(hwnd=123))[0])
        self.assertIs(self.runtime.controller, self.controller)
        self.assertTrue(self.runtime.release_session()[0])
