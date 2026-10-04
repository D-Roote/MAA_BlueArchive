"""Deduplicate terminal diagnostics, not recurring task/progress messages."""
import unittest
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
from app.winUI import RuntimeWorker, StopWorker, distinct_result_message


FAILURE = "정리 중 컨트롤러 비활성화에 실패했습니다."


class RuntimeResultTests(unittest.TestCase):
    def runtime(self):
        value = MagicMock()
        value.initialize.return_value = (True, "connected")
        value.run_task.return_value = (True, "completed")
        value.release_session.return_value = (True, "released")
        return value

    def run_worker(self, runtime):
        worker = RuntimeWorker(runtime, [("Task", {})])
        worker.run()
        return worker

    def test_reported_cleanup_exception_and_retry_failure_are_one_diagnostic(self):
        runtime = self.runtime()
        runtime.run_task.side_effect = RuntimeError(FAILURE)
        runtime.release_session.return_value = (False, FAILURE)
        worker = self.run_worker(runtime)
        self.assertEqual(worker.result_message.count(FAILURE), 1)
        self.assertFalse(worker.succeeded)
        runtime.release_session.assert_called_once()

    def test_initialization_failure_and_same_cleanup_failure_are_not_repeated(self):
        runtime = self.runtime()
        runtime.initialize.return_value = (False, FAILURE)
        runtime.release_session.return_value = (False, FAILURE)
        worker = self.run_worker(runtime)
        self.assertEqual(worker.result_message, FAILURE)
        runtime.run_task.assert_not_called()

    def test_manual_startup_cancel_adds_stop_result_without_discarding_reason(self):
        runtime = self.runtime()
        runtime.initialize.return_value = (False, "자동 실행 후 대기가 취소되었습니다.")
        worker = RuntimeWorker(runtime, [("Task", {})])
        with patch.object(worker, "isInterruptionRequested", return_value=True):
            worker.run()
        self.assertEqual(worker.result_message.splitlines(), [
            "자동 실행 후 대기가 취소되었습니다.", "작업이 중지되었습니다."])
        self.assertFalse(worker.succeeded)
        runtime.run_task.assert_not_called()
        runtime.release_session.assert_called_once()

    def test_manual_startup_cancel_keeps_cleanup_failure_and_stop_only_once(self):
        runtime = self.runtime()
        runtime.initialize.return_value = (False, "자동 실행 후 대기가 취소되었습니다.")
        runtime.release_session.return_value = (False, FAILURE)
        worker = RuntimeWorker(runtime, [("Task", {})])
        with patch.object(worker, "isInterruptionRequested", return_value=True):
            worker.run()
        self.assertEqual(worker.result_message.splitlines(), [
            "자동 실행 후 대기가 취소되었습니다.", "작업이 중지되었습니다.", FAILURE])

    def test_failed_task_result_and_same_cleanup_failure_are_not_repeated(self):
        runtime = self.runtime()
        runtime.run_task.return_value = (False, FAILURE)
        runtime.release_session.return_value = (False, FAILURE)
        self.assertEqual(self.run_worker(runtime).result_message, FAILURE)

    def test_retry_success_does_not_turn_original_failure_into_success(self):
        runtime = self.runtime()
        runtime.run_task.side_effect = RuntimeError(FAILURE)
        worker = self.run_worker(runtime)
        self.assertFalse(worker.succeeded)
        self.assertEqual(worker.result_message.count(FAILURE), 1)

    def test_unrelated_failure_and_cleanup_details_are_both_kept(self):
        runtime = self.runtime()
        runtime.run_task.side_effect = RuntimeError("리소스 읽기 실패")
        runtime.release_session.return_value = (False, FAILURE)
        worker = self.run_worker(runtime)
        self.assertIn("리소스 읽기 실패", worker.result_message)
        self.assertIn(FAILURE, worker.result_message)
        self.assertEqual(len(worker.result_message.splitlines()), 2)

    def test_cleanup_exception_is_reported_instead_of_escaping_thread(self):
        runtime = self.runtime()
        runtime.run_task.side_effect = RuntimeError("device gone")
        runtime.release_session.side_effect = RuntimeError("device gone")
        worker = self.run_worker(runtime)
        self.assertEqual(worker.result_message.count("device gone"), 1)
        self.assertFalse(worker.succeeded)

    def test_cleanup_exception_after_success_marks_worker_failed(self):
        runtime = self.runtime()
        runtime.release_session.side_effect = RuntimeError("cleanup busy")
        worker = self.run_worker(runtime)
        self.assertFalse(worker.succeeded)
        self.assertIn("completed", worker.result_message)
        self.assertIn("cleanup busy", worker.result_message)

    def test_stop_worker_exception_produces_a_final_diagnostic(self):
        runtime = self.runtime()
        runtime.stop_task.side_effect = RuntimeError("stop busy")
        worker = StopWorker(runtime)
        worker.run()
        self.assertFalse(worker.succeeded)
        self.assertIn("stop busy", worker.result_message)

    def test_multiline_results_are_deduplicated_with_context_and_order_preserved(self):
        message = f"Runtime에서 예기치 않은 오류가 발생했습니다: {FAILURE}\n{FAILURE}\n별도 오류\n별도 오류"
        self.assertEqual(distinct_result_message(message).splitlines(), [message.splitlines()[0], "별도 오류"])

    def test_substring_is_not_treated_as_duplicate_error(self):
        self.assertEqual(distinct_result_message("실패: 연결\n연결\n연결 대상 A\n연결 대상 B").count("\n"), 3)


class RuntimeResultUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        fixture = fixtures.UILifecycleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture, self.window = fixture, fixture.window

    def test_stop_and_runtime_callbacks_in_either_order_show_same_failure_once(self):
        for stop_first in (True, False):
            with self.subTest(stop_first=stop_first):
                self.window.clear_log()
                self.window._run_result_keys = set()
                self.window.worker = MagicMock(succeeded=False,
                    result_message="Runtime에서 예기치 않은 오류가 발생했습니다: 정리 중 Tasker 중지에 실패했습니다.")
                self.window.stop_worker = MagicMock(succeeded=False, result_message="Tasker 중지에 실패했습니다.")
                callbacks = [self.window.on_stop_worker_finished, self.window.on_task_finished]
                if not stop_first:
                    callbacks.reverse()
                for callback in callbacks:
                    callback()
                self.assertEqual(self.window.ui.logPrintText.toPlainText().count("Tasker 중지에 실패했습니다."), 1)

    def test_legitimate_repeated_pipeline_logs_are_still_visible(self):
        self.window.append_log(FAILURE)
        self.window.append_log(FAILURE)
        self.assertEqual(self.window.ui.logPrintText.toPlainText().count(FAILURE), 2)

    def test_stop_results_do_not_insert_blank_lines_between_entries(self):
        self.window.clear_log()
        self.window._append_run_result("작업 중지 중입니다...")
        self.window._append_run_result("작업이 중지되었습니다.")
        lines = self.window.ui.logPrintText.toPlainText().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith("작업 중지 중입니다..."))
        self.assertTrue(lines[1].endswith("작업이 중지되었습니다."))

    def test_startup_cancel_callbacks_show_reason_and_stop_without_blank_lines(self):
        for stop_first in (True, False):
            with self.subTest(stop_first=stop_first):
                self.window.clear_log()
                self.window._run_result_keys = set()
                self.window.worker = MagicMock(succeeded=False, result_message=
                    "자동 실행 후 대기가 취소되었습니다.\n작업이 중지되었습니다.")
                self.window.stop_worker = MagicMock(succeeded=False,
                    result_message="실행 중인 Tasker가 없습니다.")
                callbacks = [self.window.on_stop_worker_finished, self.window.on_task_finished]
                if not stop_first:
                    callbacks.reverse()
                for callback in callbacks:
                    callback()
                lines = self.window.ui.logPrintText.toPlainText().splitlines()
                self.assertEqual(len(lines), 2)
                self.assertTrue(lines[0].endswith("자동 실행 후 대기가 취소되었습니다."))
                self.assertTrue(lines[1].endswith("작업이 중지되었습니다."))

    def test_multiline_error_and_next_result_have_no_extra_empty_paragraph(self):
        self.window.clear_log()
        self.window._append_run_result("첫 오류\n별도 오류\n첫 오류")
        self.window._append_run_result("모든 작업을 완료했습니다.", "▶ ")
        lines = self.window.ui.logPrintText.toPlainText().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[1], "별도 오류")
        self.assertTrue(lines[2].endswith("▶ 모든 작업을 완료했습니다."))

    def test_new_run_resets_terminal_dedup_even_when_log_history_is_kept(self):
        self.window.settings_panel.clear_log_checkbox.setChecked(False)
        self.window._append_run_result(FAILURE)
        worker = MagicMock()
        with patch("app.winUI.RuntimeWorker", return_value=worker):
            self.window.on_task_start()
        self.window._append_run_result(FAILURE)
        self.assertEqual(self.window.ui.logPrintText.toPlainText().count(FAILURE), 2)
