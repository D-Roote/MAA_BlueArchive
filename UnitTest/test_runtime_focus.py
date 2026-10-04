"""Real activation before SDK ownership, never synthetic game clicks."""
import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
from app.runtime import AppRuntime, PROGRAM_LAUNCH_ENTRY, SW_SHOWNOACTIVATE, WINDOW_MINIMIZE_CHECK_COUNT


class MinimizeFocusTests(unittest.TestCase):
    def setUp(self):
        self.runtime = AppRuntime()
        self.runtime._target_hwnd = 42
        self.runtime._user32 = self.api = MagicMock()
        self.api.IsWindow.return_value = True
        self.api.IsIconic.return_value = False
        self.api.GetForegroundWindow.return_value = 789

    def activate(self, hwnd):
        self.api.GetForegroundWindow.return_value = hwnd
        return True

    def test_automatic_restore_without_user_focus_is_prepared_on_each_restart(self):
        events = []
        def activate(hwnd):
            events.append("focus")
            return self.activate(hwnd)
        def factory(**kwargs):
            self.assertEqual(self.api.GetForegroundWindow(), 42)
            self.assertTrue(self.runtime._minimize_focus_prepared)
            events.append("controller")
            controller = MagicMock(connected=True)
            controller.post_inactive.return_value = fixtures.make_job()
            return controller
        def restore(hwnd, ptr):
            self.assertEqual(ptr._obj.show_cmd, SW_SHOWNOACTIVATE)
            events.append("restore")
            # Restore displays the game without the user's real focus/click.
            self.api.GetForegroundWindow.return_value = 789
            return True
        self.api.SetForegroundWindow.side_effect = activate
        self.api.SetWindowPlacement.side_effect = restore
        def save(hwnd, ptr):
            ptr._obj.show_cmd = 1
            return True
        self.api.GetWindowPlacement.side_effect = save
        with patch("app.runtime.Win32Controller", side_effect=factory):
            for attempt in range(6):
                ok, message = self.runtime._create_controller(
                    window=SimpleNamespace(hwnd=42), prepare_minimize=True)
                self.assertTrue(ok, message)
                # Once per session, not on each node/capture.
                self.assertTrue(self.runtime._prepare_minimize_focus())
                self.assertTrue(self.runtime.release_session()[0])
                self.assertFalse(self.runtime._minimize_focus_prepared)
        self.assertEqual(events, ["focus", "controller", "restore"] * 6)
        self.assertEqual(self.api.SetForegroundWindow.call_count, 6)
        self.api.SendMessageTimeoutW.assert_not_called()

    def test_already_foreground_pointer_handle_needs_no_second_activation(self):
        self.runtime._target_hwnd = ctypes.c_void_p(42)
        self.api.GetForegroundWindow.return_value = ctypes.c_void_p(42)
        self.assertTrue(self.runtime._prepare_minimize_focus())
        self.api.SetForegroundWindow.assert_not_called()

    def test_originally_minimized_window_is_shown_without_activation_before_focus(self):
        self.api.IsIconic.return_value = True
        self.api.SetForegroundWindow.side_effect = self.activate
        self.assertTrue(self.runtime._prepare_minimize_focus())
        self.api.ShowWindow.assert_called_once_with(42, SW_SHOWNOACTIVATE)
        self.api.SetForegroundWindow.assert_called_once_with(42)

    def test_focus_denied_prevents_sdk_creation_and_keeps_original_for_cleanup(self):
        self.api.SetForegroundWindow.return_value = False
        with patch("app.runtime.Win32Controller") as factory:
            ok, message = self.runtime._create_controller(
                window=SimpleNamespace(hwnd=42), prepare_minimize=True)
        self.assertFalse(ok)
        self.assertIn("포커스", message)
        self.assertIsNotNone(self.runtime._original_window_placement)
        self.assertFalse(self.runtime._minimize_focus_prepared)
        factory.assert_not_called()
        self.api.SendMessageTimeoutW.assert_not_called()

    def test_foreground_request_success_is_not_actual_focus_confirmation(self):
        self.api.SetForegroundWindow.return_value = True
        with patch("app.runtime.time.sleep") as sleep:
            self.assertFalse(self.runtime._prepare_minimize_focus())
        self.api.SetForegroundWindow.assert_called_once_with(42)
        self.assertEqual(sleep.call_count, WINDOW_MINIMIZE_CHECK_COUNT - 1)
        self.assertFalse(self.runtime._minimize_focus_prepared)

    def test_delayed_foreground_is_waited_for(self):
        self.api.GetForegroundWindow.side_effect = [789, 789, ctypes.c_void_p(42)]
        with patch("app.runtime.time.sleep") as sleep:
            self.assertTrue(self.runtime._prepare_minimize_focus())
        sleep.assert_called_once()

    def test_cancellation_or_missing_window_does_not_activate(self):
        self.assertFalse(self.runtime._prepare_minimize_focus(lambda: True))
        self.api.IsWindow.return_value = False
        self.assertFalse(self.runtime._prepare_minimize_focus())
        self.api.SetForegroundWindow.assert_not_called()

    def test_cancellation_during_foreground_wait_does_not_mark_prepared(self):
        with patch("app.runtime.time.sleep"):
            cancelled = MagicMock(side_effect=[False, False, True])
            self.assertFalse(self.runtime._prepare_minimize_focus(cancelled))
        self.assertFalse(self.runtime._minimize_focus_prepared)

    def test_nonminimized_controller_does_not_touch_focus(self):
        controller = MagicMock()
        with patch("app.runtime.Win32Controller", return_value=controller):
            self.assertTrue(self.runtime._create_controller(window=SimpleNamespace(hwnd=42))[0])
        self.api.SetForegroundWindow.assert_not_called()
        self.api.ShowWindow.assert_not_called()

    def test_initialize_prepares_focus_only_for_minimized_pipeline_execution(self):
        for queue, minimize, expected in (([("Task", {})], True, True),
                                          (None, True, True),
                                          ([("Task", {})], False, False),
                                          ([(PROGRAM_LAUNCH_ENTRY, {})], True, False)):
            with self.subTest(queue=queue, minimize=minimize):
                fixture = fixtures.RuntimeLifecycleTests()
                fixture.setUp()
                try:
                    fixture.configure_initialization()
                    with patch.object(fixture.runtime, "_launch_program", return_value=(True, "already running")), \
                            patch.object(fixture.runtime, "_resize_window_for_task", return_value=True):
                        self.assertTrue(fixture.runtime.initialize(
                            program_settings={}, execution_queue=queue, minimize_window=minimize)[0])
                    self.assertEqual(fixture.runtime._create_controller.call_args.kwargs["prepare_minimize"], expected)
                finally:
                    fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
