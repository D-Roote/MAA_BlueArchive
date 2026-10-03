"""Completion actions use fake Windows backends: never lock/suspend/shutdown this PC."""
import ctypes
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
from app.afterActions import (
    AfterActionPreferences, SYSTEM_ACTIONS, WindowTarget, WindowsAfterActionBackend,
    _run_after_exit, capture_window_target, normalize_after_actions,
)
from app.winUI import RuntimeWorker
from app.monitoring import supported_presets
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QStyleOptionButton


class PreferenceTests(unittest.TestCase):
    def test_invalid_values_are_safe_defaults(self):
        for value in (None, [], "bad", {"close_program": "yes", "close_maa": 1, "system_action": []}):
            self.assertEqual(normalize_after_actions(value), {"close_program": False, "close_app": False,
                                                           "close_emulator": False, "close_maa": False, "system_action": ""})

    def test_regular_changes_persist_and_one_system_action_replaces_another(self):
        state = AfterActionPreferences()
        for action in SYSTEM_ACTIONS:
            state.update("system_action", action)
            self.assertEqual(state.saved["system_action"], action)
        state.update("close_program", True)
        state.update("close_maa", True)
        self.assertEqual(state.current, state.saved)
        self.assertIn("대상 프로그램 종료", state.description())

    def test_once_changes_preserve_saved_state_and_consumption_restores_it(self):
        state = AfterActionPreferences({"close_program": True})
        state.set_once(True)
        state.update("close_program", False)
        state.update("system_action", "sleep")
        self.assertTrue(state.saved["close_program"])
        restarted = AfterActionPreferences(state.saved)
        self.assertFalse(restarted.once)
        self.assertEqual(restarted.current["system_action"], "")
        state.consume()
        self.assertFalse(state.once)
        self.assertEqual(state.current, restarted.current)

    def test_unchecking_once_commits_current_selection(self):
        state = AfterActionPreferences()
        state.set_once(True)
        state.update("close_maa", True)
        state.set_once(False)
        self.assertTrue(state.saved["close_maa"])

    def test_clear_removes_saved_and_once_selection(self):
        state = AfterActionPreferences({"close_maa": True, "system_action": "shutdown"})
        state.set_once(True)
        state.clear()
        self.assertFalse(state.once)
        self.assertFalse(any(state.current.values()))
        self.assertEqual(state.current, state.saved)

    def test_emulator_always_implies_app_and_cannot_leave_invalid_saved_state(self):
        state = AfterActionPreferences({"close_emulator": True, "close_app": False})
        self.assertTrue(state.current["close_app"])
        state.update("close_app", False)
        self.assertTrue(state.current["close_app"])
        state.update("close_emulator", False)
        state.update("close_app", False)
        self.assertFalse(state.saved["close_app"])

    def test_controller_projection_preserves_both_modes_but_hides_inactive_actions(self):
        state = AfterActionPreferences({"close_program": True, "close_emulator": True, "close_maa": True})
        self.assertFalse(state.for_controller("Adb")["close_program"])
        self.assertFalse(state.for_controller("Win32")["close_emulator"])
        self.assertIn("앱 종료", state.description("Adb"))
        self.assertNotIn("대상 프로그램", state.description("Adb"))
        self.assertNotIn("에뮬레이터", state.description("Win32"))
        self.assertTrue(state.saved["close_program"])
        self.assertTrue(state.saved["close_emulator"])


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.backend = WindowsAfterActionBackend()
        self.backend.user32 = MagicMock()
        self.backend.kernel32 = MagicMock()
        self.backend.advapi32 = MagicMock()
        self.backend.powrprof = MagicMock()
        self.target = WindowTarget(123, 456, "C:/Game/Game.exe")
        ctypes.set_last_error(0)

    def test_close_revalidates_identity_and_requests_only_that_window(self):
        with patch("app.afterActions.capture_window_target", return_value=self.target):
            self.backend.close_program(self.target)
        self.backend.user32.PostMessageW.assert_called_once_with(123, 0x10, 0, 0)

    def test_missing_closed_replaced_or_unreadable_target_is_not_closed(self):
        with self.assertRaises(RuntimeError):
            self.backend.close_program(None)
        self.backend.user32.IsWindow.return_value = False
        with self.assertRaises(RuntimeError):
            self.backend.close_program(self.target)
        self.backend.user32.IsWindow.return_value = True
        for value in (WindowTarget(123, 789, self.target.path), WindowTarget(123, 456, "C:/Other.exe")):
            with patch("app.afterActions.capture_window_target", return_value=value), self.assertRaises(RuntimeError):
                self.backend.close_program(self.target)
        with patch("app.afterActions.capture_window_target", side_effect=RuntimeError("access denied")), self.assertRaises(RuntimeError):
            self.backend.close_program(self.target)
        self.backend.user32.PostMessageW.assert_not_called()

    def test_target_capture_rejects_own_process_unknown_path_and_invalid_handle(self):
        import os
        for pid, path in ((0, ""), (os.getpid(), "C:/MAA.exe"), (123, "")):
            with patch("app.monitoring.window_process_info", return_value=(pid, path)), self.assertRaises(RuntimeError):
                capture_window_target(123)
        with self.assertRaises(RuntimeError):
            capture_window_target(None)

    def test_lock_does_not_request_power_privilege(self):
        self.backend.system_action("lock")
        self.backend.user32.LockWorkStation.assert_called_once_with()
        self.backend.advapi32.OpenProcessToken.assert_not_called()

    def test_sleep_and_hibernate_are_distinct_and_restore_privilege(self):
        for action, hibernate in (("sleep", False), ("hibernate", True)):
            self.backend.powrprof.reset_mock()
            self.backend.advapi32.reset_mock()
            self.backend.system_action(action)
            self.backend.powrprof.SetSuspendState.assert_called_once_with(hibernate, False, False)
            self.assertEqual(self.backend.advapi32.AdjustTokenPrivileges.call_count, 2)

    def test_shutdown_never_uses_force_flags(self):
        self.backend.system_action("shutdown")
        self.backend.user32.ExitWindowsEx.assert_called_once_with(8, 0x80040000)

    def test_api_failure_restores_privilege_and_closes_token(self):
        self.backend.powrprof.SetSuspendState.return_value = False
        with self.assertRaises(OSError):
            self.backend.system_action("sleep")
        self.assertEqual(self.backend.advapi32.AdjustTokenPrivileges.call_count, 2)
        self.backend.kernel32.CloseHandle.assert_called_once()

    def test_missing_privilege_does_not_invoke_power_api(self):
        self.backend.advapi32.AdjustTokenPrivileges.side_effect = lambda *_args: ctypes.set_last_error(1300) or True
        with self.assertRaises(OSError):
            self.backend.system_action("shutdown")
        self.backend.user32.ExitWindowsEx.assert_not_called()
        self.backend.kernel32.CloseHandle.assert_called_once()

    def test_deferred_helper_inherits_exact_process_handle_and_is_hidden(self):
        self.backend.kernel32.OpenProcess.return_value = 999
        with patch("app.afterActions.subprocess.Popen") as popen:
            self.backend.defer_system_action("lock", Path("after_action.log"))
        args, options = popen.call_args
        self.assertEqual(args[0][2:5], ["--after-exit", "999", "lock"])
        self.assertEqual(options["startupinfo"].lpAttributeList, {"handle_list": [999]})
        self.assertTrue(options["close_fds"])
        self.backend.kernel32.CloseHandle.assert_called_once_with(999)
        self.backend.user32.LockWorkStation.assert_not_called()

    def test_helper_waits_for_parent_exit_before_action_and_records_failure(self):
        self.backend.kernel32.WaitForSingleObject.return_value = 0
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "debug" / "after_action.log"
            self.backend.system_action = MagicMock(side_effect=RuntimeError("unsupported power state"))
            with patch("app.afterActions.WindowsAfterActionBackend", return_value=self.backend):
                _run_after_exit(999, "sleep", path)
            self.backend.kernel32.WaitForSingleObject.assert_called_once_with(999, 0xFFFFFFFF)
            self.backend.kernel32.CloseHandle.assert_called_once_with(999)
            self.backend.system_action.assert_called_once_with("sleep")
            self.assertIn("unsupported power state", path.read_text(encoding="utf-8"))

    def test_helper_wait_failure_never_runs_system_action(self):
        self.backend.kernel32.WaitForSingleObject.return_value = 0xFFFFFFFF
        self.backend.system_action = MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "after_action.log"
            with patch("app.afterActions.WindowsAfterActionBackend", return_value=self.backend):
                _run_after_exit(999, "lock", path)
            self.backend.system_action.assert_not_called()
            self.assertIn("실패", path.read_text(encoding="utf-8"))

    def test_helper_launch_failure_closes_inherited_handle(self):
        self.backend.kernel32.OpenProcess.return_value = 999
        with patch("app.afterActions.subprocess.Popen", side_effect=OSError("launch failed")), self.assertRaises(OSError):
            self.backend.defer_system_action("lock", Path("after_action.log"))
        self.backend.kernel32.CloseHandle.assert_called_once_with(999)


class CompletionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.backend = MagicMock()
        self.window.after_action_backend = self.backend
        self.path = self.window.runtime.user_dir / "config" / "user_config.json"

    def start(self, succeeded=True):
        worker = MagicMock(succeeded=succeeded, result_message="done", completion_target=WindowTarget(123, 456, "C:/Game.exe"))
        with patch("app.winUI.RuntimeWorker", return_value=worker):
            self.window.on_task_start()
        self.assertTrue(self.window.isRunning)
        return worker

    def enable_adb(self):
        self.window.runtime.interface["controller"].append({"name": "Android", "type": "Adb", "label": "Android"})
        self.window.runtime.resource_config["controller"].append("Android")
        self.window.monitor.service.presets = supported_presets(self.window.runtime.interface, self.window.runtime.resource_config)

    def test_selected_interface_preset_switches_visible_controls_without_losing_choices(self):
        self.enable_adb()
        self.window.show_after_actions()
        panel = self.window.after_action_panel
        panel.controls["close_program"].click()
        self.assertTrue(panel.controls["close_app"].isHidden())
        self.window.monitor.select_preset("Android")
        self.assertTrue(panel.controls["close_program"].isHidden())
        self.assertFalse(panel.controls["close_app"].isHidden())
        self.assertFalse(panel.controls["close_emulator"].isHidden())
        self.assertFalse(panel.controls["close_maa"].isHidden())
        self.assertFalse(panel.adb_notice.isHidden())
        panel.controls["close_emulator"].click()
        self.assertTrue(panel.controls["close_app"].isChecked())
        self.assertFalse(panel.controls["close_app"].isEnabled())
        panel.controls["close_app"].click()
        self.assertTrue(panel.controls["close_app"].isChecked())
        self.assertIn("에뮬레이터", self.window.ui.endStatusLabel.text())
        self.window.settings_panel.controller_combo.setCurrentIndex(1)
        self.assertEqual(panel.controller_type, "Win32")
        self.assertTrue(panel.controls["close_emulator"].isHidden())
        self.assertTrue(panel.controls["close_program"].isChecked())
        self.assertNotIn("에뮬레이터", self.window.ui.endStatusLabel.text())
        self.window.monitor.select_preset("Android")
        self.assertTrue(panel.controls["close_emulator"].isChecked())
        panel.controls["close_emulator"].click()
        self.assertTrue(panel.controls["close_app"].isEnabled())

    def test_adb_only_interface_is_detected_at_startup_without_win32_fallback(self):
        self.window.runtime.interface["controller"] = [{"name": "AndroidOnly", "type": "Adb"}]
        self.window.runtime.resource_config["controller"] = ["AndroidOnly"]
        with patch("app.winUI.AppRuntime", return_value=self.window.runtime):
            restarted = fixtures.MainWindow()
        self.addCleanup(restarted.deleteLater)
        restarted.show_after_actions()
        self.assertEqual(restarted._after_action_controller_type(), "Adb")
        self.assertTrue(restarted.after_action_panel.controls["close_program"].isHidden())
        self.assertFalse(restarted.after_action_panel.controls["close_app"].isHidden())
        restarted.after_action_backend = MagicMock()
        restarted.close()

    def test_resource_disallowed_adb_cannot_change_completion_mode(self):
        self.window.runtime.interface["controller"].append({"name": "Disallowed", "type": "Adb"})
        self.window.monitor.service.presets = supported_presets(self.window.runtime.interface, self.window.runtime.resource_config)
        self.window.monitor.select_preset("Disallowed")
        self.assertEqual(self.window._after_action_controller_type(), "Win32")

    def test_adb_choices_save_and_once_changes_do_not_overwrite_them(self):
        self.enable_adb()
        self.window.monitor.select_preset("Android")
        self.window.show_after_actions()
        panel = self.window.after_action_panel
        panel.controls["close_emulator"].click()
        panel.once_checkbox.click()
        panel.controls["close_emulator"].click()
        panel.controls["close_app"].click()
        saved = json.loads(self.path.read_text(encoding="utf-8"))["after_actions"]
        self.assertTrue(saved["close_app"])
        self.assertTrue(saved["close_emulator"])
        self.window.after_actions.consume()
        panel.sync()
        self.assertTrue(panel.controls["close_emulator"].isChecked())
        panel.clear_button.click()
        self.assertFalse(any(self.window.after_actions.saved.values()))

    def test_adb_ui_only_selection_never_closes_win32_program_or_sends_commands(self):
        self.enable_adb()
        self.window.after_actions.update("close_program", True)
        self.window.monitor.select_preset("Android")
        self.window.after_actions.update("close_emulator", True)
        self.start()
        self.window.on_task_finished()
        fixtures.QApplication.processEvents()
        self.backend.close_program.assert_not_called()
        self.backend.system_action.assert_not_called()
        self.backend.defer_system_action.assert_not_called()
        self.assertIn("UI 준비 단계", self.window.ui.logPrintText.toPlainText())

    def test_optical_checkbox_actual_painted_ink_is_centered_in_light_and_dark(self):
        self.window.show_after_actions()
        self.window.show()
        control = self.window.after_action_panel.controls["close_maa"]
        control.setFocusPolicy(fixtures.Qt.FocusPolicy.NoFocus)
        for theme in (fixtures.TitleBarTheme.LIGHT, fixtures.TitleBarTheme.DARK):
            self.window.set_title_bar_theme(theme)
            for text in ("이번에만", "대상 프로그램 종료", "MAA 종료", "에뮬레이터 종료"):
                for checked in (False, True):
                    control.setText(text)
                    control.setChecked(checked)
                    fixtures.QApplication.processEvents()
                    option = QStyleOptionButton()
                    control.initStyleOption(option)
                    indicator = control.style().subElementRect(fixtures.QStyle.SubElement.SE_CheckBoxIndicator, option, control)
                    contents = control.style().subElementRect(fixtures.QStyle.SubElement.SE_CheckBoxContents, option, control)
                    image = control.grab().toImage()
                    ratio = image.devicePixelRatio()
                    background = image.pixelColor(image.width() - 1, 0)
                    x_start = int(contents.x() * ratio)
                    x_end = min(image.width(), int((contents.x() + QFontMetrics(control.font()).horizontalAdvance(text) + 2) * ratio))
                    rows = []
                    for y in range(image.height()):
                        for x in range(x_start, x_end):
                            color = image.pixelColor(x, y)
                            if max(abs(color.red() - background.red()), abs(color.green() - background.green()), abs(color.blue() - background.blue())) > 50:
                                rows.append(y)
                                break
                    self.assertTrue(rows, (theme, text))
                    center = (min(rows) + max(rows)) / 2
                    indicator_center = (indicator.y() + indicator.height() / 2) * ratio - 0.5
                    self.assertLessEqual(abs(center - indicator_center), ratio, (theme, text, checked, rows))

    def test_optical_checkbox_keeps_native_space_key_and_text_click_behavior(self):
        self.window.show_after_actions()
        self.window.show()
        control = self.window.after_action_panel.controls["close_maa"]
        starts = []
        for checkbox in (control, self.window.after_action_panel.once_checkbox):
            option = QStyleOptionButton()
            checkbox.initStyleOption(option)
            starts.append(checkbox.style().subElementRect(fixtures.QStyle.SubElement.SE_CheckBoxContents, option, checkbox).x())
        self.assertEqual(starts[0], starts[1])
        control.setFocus()
        fixtures.QTest.keyClick(control, fixtures.Qt.Key.Key_Space)
        self.assertTrue(control.isChecked())
        fixtures.QTest.mouseClick(control, fixtures.Qt.MouseButton.LeftButton, pos=fixtures.QPoint(40, control.height() // 2))
        self.assertFalse(control.isChecked())

    def test_footer_labels_center_and_icon_vertically_align_in_both_themes(self):
        self.window.show()
        for theme in (fixtures.TitleBarTheme.LIGHT, fixtures.TitleBarTheme.DARK):
            self.window.set_title_bar_theme(theme)
            fixtures.QApplication.processEvents()
            footer = self.window.ui.settingStartWidget_2
            center = footer.mapToGlobal(footer.rect().center())
            for label in (self.window.ui.endWorkStatusLabel, self.window.ui.endStatusLabel):
                self.assertTrue(label.alignment() & fixtures.Qt.AlignmentFlag.AlignHCenter)
                self.assertLessEqual(abs(label.mapToGlobal(label.rect().center()).x() - center.x()), 2)
            icon = self.window.ui.endSettingBtn
            self.assertLessEqual(abs(icon.mapToGlobal(icon.rect().center()).y() - center.y()), 2)

    def test_options_are_exclusive_toggleable_and_clearable(self):
        self.window.ui.endSettingBtn.click()
        panel = self.window.after_action_panel
        self.assertIsNotNone(panel)
        for action in SYSTEM_ACTIONS:
            panel.controls[action].click()
            self.assertEqual(sum(panel.controls[key].isChecked() for key in SYSTEM_ACTIONS), 1)
            self.assertEqual(self.window.after_actions.current["system_action"], action)
        panel.controls["shutdown"].click()
        self.assertEqual(self.window.after_actions.current["system_action"], "")
        panel.controls["close_program"].click()
        panel.once_checkbox.click()
        panel.controls["close_maa"].click()
        panel.clear_button.click()
        self.assertFalse(any(self.window.after_actions.saved.values()))
        self.assertFalse(panel.once_checkbox.isChecked())

    def test_footer_gear_keeps_right_inset_when_summary_grows(self):
        self.window.show()
        fixtures.QApplication.processEvents()
        footer = self.window.ui.settingStartWidget_2
        self.assertEqual(footer.width(), 292)
        before = self.window.ui.endSettingBtn.mapToGlobal(self.window.ui.endSettingBtn.rect().center()).x()
        for key, value in (("close_program", True), ("close_maa", True), ("system_action", "hibernate")):
            self.window.after_actions.update(key, value)
        self.window._refresh_after_action_summary()
        fixtures.QApplication.processEvents()
        after = self.window.ui.endSettingBtn.mapToGlobal(self.window.ui.endSettingBtn.rect().center()).x()
        self.assertEqual(before, after)

    def test_persistent_and_once_settings_survive_other_configuration_saves(self):
        self.window.show_after_actions()
        panel = self.window.after_action_panel
        panel.controls["close_program"].click()
        panel.once_checkbox.click()
        panel.controls["close_program"].click()
        panel.controls["sleep"].click()
        self.window.save_user_config()
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertTrue(saved["after_actions"]["close_program"])
        self.assertEqual(saved["after_actions"]["system_action"], "")
        self.assertNotIn("once", saved["after_actions"])
        with patch("app.winUI.AppRuntime", return_value=self.window.runtime):
            restarted = fixtures.MainWindow()
        self.addCleanup(restarted.deleteLater)
        self.assertFalse(restarted.after_actions.once)
        self.assertTrue(restarted.after_actions.current["close_program"])
        self.assertIn("대상 프로그램 종료", restarted.ui.endStatusLabel.text())
        restarted.after_action_backend = MagicMock()
        restarted.close()

    def test_actions_wait_for_both_callbacks_and_execute_exactly_once(self):
        self.window.after_actions.update("close_program", True)
        self.start()
        self.window.stop_worker = MagicMock(succeeded=True, result_message="stopped")
        self.window.on_task_finished()
        fixtures.QApplication.processEvents()
        self.backend.close_program.assert_not_called()
        self.window.on_stop_worker_finished()
        self.assertFalse(self.window.ui.workStartBtn.isEnabled())
        self.window.check_start_button_state()
        self.assertFalse(self.window.ui.workStartBtn.isEnabled())
        fixtures.QApplication.processEvents()
        self.backend.close_program.assert_called_once_with(WindowTarget(123, 456, "C:/Game.exe"))
        self.window.on_task_finished()
        self.window.on_stop_worker_finished()
        fixtures.QApplication.processEvents()
        self.backend.close_program.assert_called_once()

    def test_failure_does_not_consume_once_or_run_actions(self):
        self.window.after_actions.set_once(True)
        self.window.after_actions.update("system_action", "lock")
        self.start(succeeded=False)
        self.window.on_task_finished()
        fixtures.QApplication.processEvents()
        self.backend.system_action.assert_not_called()
        self.assertTrue(self.window.after_actions.once)

    def test_manual_stop_suppresses_even_a_late_success_result(self):
        self.window.after_actions.update("system_action", "lock")
        self.start()
        with patch("app.winUI.StopWorker", return_value=MagicMock(succeeded=True, result_message="stopped")):
            self.window.on_task_stop()
        self.window.on_stop_worker_finished()
        self.window.on_task_finished()
        fixtures.QApplication.processEvents()
        self.backend.system_action.assert_not_called()

    def test_window_close_cancels_already_queued_completion(self):
        self.window.after_actions.update("system_action", "lock")
        self.start()
        self.window.on_task_finished()
        self.window.close()
        fixtures.QApplication.processEvents()
        self.backend.system_action.assert_not_called()

    def test_launch_only_does_not_trigger_actions(self):
        self.window.after_actions.update("system_action", "shutdown")
        with patch.object(self.window, "build_execution_queue", return_value=[(fixtures.PROGRAM_LAUNCH_ENTRY, {})]):
            self.start()
        self.window.on_task_finished()
        fixtures.QApplication.processEvents()
        self.backend.system_action.assert_not_called()

    def test_success_consumes_once_before_system_request_and_uses_snapshot(self):
        self.window.after_actions.set_once(True)
        self.window.after_actions.update("system_action", "lock")
        self.window.show_after_actions()
        self.start()
        self.assertFalse(self.window.ui.endSettingBtn.isEnabled())
        self.assertFalse(self.window.after_action_panel.isEnabled())
        self.window.on_task_finished()
        fixtures.QApplication.processEvents()
        self.backend.system_action.assert_called_once_with("lock")
        self.assertFalse(self.window.after_actions.once)
        self.assertEqual(self.window.after_actions.current["system_action"], "")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["after_actions"]["system_action"], "")

    def test_run_snapshot_does_not_change_with_later_preferences(self):
        self.window.after_actions.update("system_action", "lock")
        self.start()
        self.window.after_actions.update("system_action", "sleep")
        self.window.on_task_finished()
        fixtures.QApplication.processEvents()
        self.backend.system_action.assert_called_once_with("lock")

    def test_runtime_editing_setting_cannot_enable_completion_controls_during_run(self):
        self.window.show_after_actions()
        self.start()
        self.window.set_runtime_option_editing_enabled(True)
        self.assertFalse(self.window.after_action_panel.isEnabled())
        self.assertFalse(self.window.ui.endSettingBtn.isEnabled())
        self.window.on_task_finished()

    def test_maa_close_and_power_combination_defers_power_until_process_exit(self):
        self.window.after_actions.update("close_maa", True)
        self.window.after_actions.update("system_action", "hibernate")
        self.start()
        self.window.on_task_finished()
        fixtures.QApplication.processEvents()
        self.backend.system_action.assert_not_called()
        self.backend.defer_system_action.assert_called_once_with("hibernate", self.window.runtime.user_dir / "debug" / "after_action.log")
        self.assertTrue(self.window._closing)

    def test_action_failure_is_logged_and_does_not_block_maa_close(self):
        self.backend.close_program.side_effect = RuntimeError("target changed")
        self.window.after_actions.update("close_program", True)
        self.window.after_actions.update("close_maa", True)
        self.start()
        self.window.on_task_finished()
        fixtures.QApplication.processEvents()
        self.assertIn("target changed", self.window.ui.logPrintText.toPlainText())
        self.assertTrue(self.window._closing)

    def test_runtime_worker_captures_actual_execution_window_before_cleanup(self):
        runtime = MagicMock(_target_hwnd=123)
        runtime.initialize.return_value = (True, "initialized")
        runtime.run_task.return_value = (True, "done")
        runtime.release_session.return_value = (True, "released")
        target = WindowTarget(123, 456, "C:/Game.exe")
        worker = RuntimeWorker(runtime, [("Task", {})], completion_actions={"close_program": True})
        with patch("app.winUI.capture_window_target", return_value=target) as capture:
            worker.run()
        capture.assert_called_once_with(123)
        self.assertEqual(worker.completion_target, target)
        self.assertTrue(worker.succeeded)
        runtime.release_session.assert_called_once()

    def test_initialization_and_cleanup_failure_are_not_successful_completions(self):
        for initialize, release in (((False, "init failed"), (True, "released")), ((True, "initialized"), (False, "cleanup failed"))):
            runtime = MagicMock(_target_hwnd=123)
            runtime.initialize.return_value = initialize
            runtime.run_task.return_value = (True, "done")
            runtime.release_session.return_value = release
            worker = RuntimeWorker(runtime, [("Task", {})], completion_actions={"close_program": True})
            with patch("app.winUI.capture_window_target", return_value=WindowTarget(123, 456, "C:/Game.exe")):
                worker.run()
            self.assertFalse(worker.succeeded)
            runtime.release_session.assert_called_once()
