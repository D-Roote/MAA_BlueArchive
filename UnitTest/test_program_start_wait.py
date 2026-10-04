"""Newly launched programs wait after detection, without delaying reuse."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_runtime_lifecycle as fixtures
from app.runtime import AppRuntime, PROGRAM_LAUNCH_ENTRY, PROGRAM_POST_WINDOW_WAIT_SECONDS


class ProgramStartupWaitTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RuntimeLifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.runtime = self.fixture.runtime
        self.runtime._target_hwnd = 42
        self.runtime._user32.IsWindow.return_value = True

    def wait(self, cancelled=None):
        return self.runtime._wait_for_started_program_ready(cancelled)

    def test_waits_twenty_seconds_after_detection_with_one_log(self):
        self.assertEqual(PROGRAM_POST_WINDOW_WAIT_SECONDS, 20)
        now, sleeps, logs = [100.0], [], []
        self.runtime.log_sink.set_log_callback(logs.append)
        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds
        with patch("app.runtime.time.monotonic", side_effect=lambda: now[0]), \
                patch("app.runtime.time.sleep", side_effect=sleep):
            self.assertTrue(self.wait()[0])
        self.assertAlmostEqual(sum(sleeps), 20)
        self.assertLessEqual(max(sleeps), 0.05)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0], "다음 작업 시작까지 대기합니다.")
        self.assertIsNone(self.runtime.tasker)
        self.runtime._user32.ShowWindow.assert_not_called()

    def test_mid_wait_cancel_does_not_sleep_until_deadline(self):
        now = [0.0]
        with patch("app.runtime.time.monotonic", side_effect=lambda: now[0]), \
                patch("app.runtime.time.sleep", side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)):
            result = self.wait(lambda: now[0] >= 0.1)
        self.assertFalse(result[0])
        self.assertIn("취소", result[1])
        self.assertAlmostEqual(now[0], 0.1)

    def test_closed_or_missing_window_aborts_before_sleep(self):
        for hwnd in (42, None):
            with self.subTest(hwnd=hwnd), patch("app.runtime.time.sleep") as sleep:
                self.runtime._target_hwnd = hwnd
                self.runtime._user32.IsWindow.return_value = False
                result = self.wait()
            self.assertFalse(result[0])
            self.assertIn("창이 종료", result[1])
            sleep.assert_not_called()

    def test_window_loss_during_wait_aborts(self):
        self.runtime._user32.IsWindow.side_effect = [True, False]
        with patch("app.runtime.time.monotonic", return_value=0), \
                patch("app.runtime.time.sleep") as sleep:
            self.assertFalse(self.wait()[0])
        sleep.assert_called_once_with(0.05)


class ProgramStartupWaitIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RuntimeLifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.configure_initialization()
        self.runtime = self.fixture.runtime
        self.queue = [(PROGRAM_LAUNCH_ENTRY, {}), ("Login_Main", {})]

    def launch(self, _settings):
        self.runtime._program_started_for_session = True
        return True, "started"

    def test_new_normal_and_minimized_windows_wait_before_connect_and_tasker(self):
        for minimized in (False, True):
            with self.subTest(minimized=minimized):
                self.runtime._wait_for_started_program_ready.reset_mock()
                def wait(_cancelled):
                    self.assertEqual(self.runtime._target_hwnd, 123)
                    self.assertIsNotNone(self.runtime.controller)
                    self.assertIsNone(self.runtime.tasker)
                    self.runtime.controller.post_connection.assert_not_called()
                    return True, "ready"
                self.runtime._wait_for_started_program_ready.side_effect = wait
                with patch.object(self.runtime, "_launch_program", side_effect=self.launch), \
                        patch.object(self.runtime, "_find_target_window", return_value=(SimpleNamespace(hwnd=123), "found")), \
                        patch.object(self.runtime, "_prepare_started_window_for_minimized_connection", return_value=(True, "prepared")):
                    self.assertTrue(self.runtime.initialize(
                        program_settings={}, execution_queue=self.queue, minimize_window=minimized)[0])
                self.runtime._wait_for_started_program_ready.assert_called_once_with(None)
                self.assertEqual(self.runtime._create_controller.call_args.kwargs["stable_window_seconds"], 0)
                self.assertEqual(self.runtime._create_controller.call_args.kwargs["wait_timeout_seconds"], 0)
                self.assertIsNotNone(self.runtime._create_controller.call_args.kwargs["window"])
                self.runtime.controller.post_connection.assert_called_once()
                self.assertIsNotNone(self.runtime.tasker)

    def test_already_running_program_does_not_wait(self):
        with patch.object(self.runtime, "_launch_program", return_value=(True, "already running")):
            self.assertTrue(self.runtime.initialize(program_settings={}, execution_queue=self.queue)[0])
        self.runtime._wait_for_started_program_ready.assert_not_called()

    def test_new_normal_window_is_detected_without_rolling_stability_delay(self):
        window = SimpleNamespace(hwnd=42, window_name="Owned Game")
        self.runtime._find_target_window.side_effect = lambda *a, **k: AppRuntime._find_target_window(
            self.runtime, *a, **k)
        with patch.object(self.runtime, "_launch_program", side_effect=self.launch), \
                patch.object(self.runtime, "_get_window_keyword", return_value="Owned Game"), \
                patch("app.runtime.Toolkit.find_desktop_windows", return_value=[window]) as find, \
                patch.object(self.runtime, "_get_window_stability_signature", side_effect=AssertionError("No rolling wait")), \
                patch.object(self.runtime, "_resize_window_for_task", return_value=True), \
                patch("app.runtime.time.sleep") as sleep:
            self.assertTrue(self.runtime.initialize(program_settings={}, execution_queue=self.queue)[0])
        find.assert_called_once()
        sleep.assert_not_called()
        self.assertIs(self.runtime._create_controller.call_args.kwargs["window"], window)
        self.runtime._wait_for_started_program_ready.assert_called_once()

    def test_manual_connection_does_not_wait(self):
        self.assertTrue(self.runtime.initialize(execution_queue=[("Login_Main", {})])[0])
        self.runtime._wait_for_started_program_ready.assert_not_called()

    def test_launch_only_without_next_pipeline_does_not_wait(self):
        with patch.object(self.runtime, "_launch_program", side_effect=self.launch):
            self.assertTrue(self.runtime.initialize(
                program_settings={}, execution_queue=[(PROGRAM_LAUNCH_ENTRY, {})])[0])
        self.runtime._wait_for_started_program_ready.assert_not_called()

    def test_failed_wait_cleans_up_without_connecting_or_creating_tasker(self):
        self.runtime._wait_for_started_program_ready.return_value = (False, "대기가 취소되었습니다.")
        with patch.object(self.runtime, "_launch_program", side_effect=self.launch), \
                patch.object(self.runtime, "_execute_controller", wraps=self.runtime._execute_controller) as connect:
            result = self.runtime.initialize(program_settings={}, execution_queue=self.queue)
        self.assertFalse(result[0])
        self.assertIn("취소", result[1])
        connect.assert_not_called()
        self.assertFalse(self.fixture.taskers)
        self.assertIsNone(self.runtime.controller)
        self.assertIsNone(self.runtime.tasker)


if __name__ == "__main__":
    unittest.main()
