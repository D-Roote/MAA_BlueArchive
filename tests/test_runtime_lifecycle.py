"""Run with .venv/Scripts/python.exe -m unittest discover -s tests -v."""

import os
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))

from PySide6.QtWidgets import QApplication

from app.runtime import AppRuntime, LogSinkFocus, WindowPlacement
from app.winUI import MainWindow, RuntimeWorker


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

        def create_controller(minimize):
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
        runtime.interface = {"task": [{"name": "Test", "entry": "Test_Main", "default_check": True}]}
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
        worker = RuntimeWorker(runtime, [])
        with patch.object(worker, "isInterruptionRequested", return_value=True):
            worker.run()
        runtime.run_task.assert_not_called()
        runtime.release_session.assert_called_once()
        self.assertFalse(worker.succeeded)


if __name__ == "__main__":
    unittest.main()
