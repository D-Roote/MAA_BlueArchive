"""All selection boxes require explicit choice, not a closed-combo wheel."""
import unittest
from copy import deepcopy
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QScrollArea, QVBoxLayout, QWidget

from app.controls import PopupOnlyWheelComboBox


def wheel(widget, delta=-120, pixels=0):
    position = widget.rect().center()
    event = QWheelEvent(QPointF(position), QPointF(widget.mapToGlobal(position)),
                        QPoint(0, pixels), QPoint(0, delta), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    QApplication.processEvents()
    return event


class ControllerWheelControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.combo = PopupOnlyWheelComboBox()
        self.combo.addItems([f"Controller {i}" for i in range(60)])
        self.combo.setCurrentIndex(1)
        self.combo.show()
        self.combo.activateWindow()
        QApplication.processEvents()
        self.addCleanup(self.combo.close)
        self.addCleanup(self.combo.deleteLater)

    def test_closed_wheel_never_changes_selection_with_or_without_focus(self):
        changes = []
        self.combo.currentIndexChanged.connect(changes.append)
        for focused in (True, False):
            if focused:
                self.combo.setFocus()
            else:
                self.combo.clearFocus()
            self.assertEqual(self.combo.hasFocus(), focused)
            for delta in (120, -120, 360, -360):
                with self.subTest(focused=focused, delta=delta):
                    self.assertFalse(wheel(self.combo, delta).isAccepted())
                    self.assertEqual(self.combo.currentIndex(), 1)
        self.assertFalse(changes)

    def test_closed_trackpad_pixel_scroll_does_not_change_selection(self):
        self.combo.setFocus()
        self.assertFalse(wheel(self.combo, delta=0, pixels=-80).isAccepted())
        self.assertEqual(self.combo.currentIndex(), 1)

    def test_keyboard_selection_is_unchanged(self):
        self.combo.setFocus()
        QTest.keyClick(self.combo, Qt.Key.Key_Down)
        self.assertEqual(self.combo.currentIndex(), 2)
        QTest.keyClick(self.combo, Qt.Key.Key_Up)
        self.assertEqual(self.combo.currentIndex(), 1)

    def test_popup_view_scroll_and_click_selection_are_unchanged(self):
        self.combo.setStyleSheet("QComboBox { combobox-popup: 0; }")
        self.combo.setMaxVisibleItems(5)
        self.combo.showPopup()
        QApplication.processEvents()
        self.addCleanup(self.combo.hidePopup)
        view = self.combo.view()
        self.assertTrue(view.isVisible())
        scroll = view.verticalScrollBar()
        self.assertGreater(scroll.maximum(), 0)
        previous = scroll.value()
        wheel(view.viewport())
        self.assertGreater(scroll.value(), previous)
        index = self.combo.model().index(10, 0)
        view.scrollTo(index)
        QApplication.processEvents()
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(index).center())
        self.assertEqual(self.combo.currentIndex(), 10)

    def test_wheel_is_blocked_again_after_popup_closes(self):
        self.combo.showPopup()
        self.combo.hidePopup()
        self.combo.setFocus()
        wheel(self.combo)
        self.assertEqual(self.combo.currentIndex(), 1)

    def test_closed_combo_allows_enclosing_area_to_scroll(self):
        area = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(self.combo)
        layout.addStretch()
        content.setMinimumHeight(2000)
        area.setWidget(content)
        area.setWidgetResizable(True)
        area.resize(400, 250)
        area.show()
        QApplication.processEvents()
        self.addCleanup(area.close)
        self.addCleanup(area.deleteLater)
        scroll = area.verticalScrollBar()
        previous = scroll.value()
        QTest.wheelEvent(area.windowHandle(), self.combo.mapTo(area, self.combo.rect().center()), QPoint(0, -120))
        QApplication.processEvents()
        self.assertGreater(scroll.value(), previous)
        self.assertEqual(self.combo.currentIndex(), 1)


class ControllerWheelIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window

    def test_all_controller_selectors_ignore_closed_wheel_without_saving_or_disconnect(self):
        combos = [self.window.settings_panel.controller_combo]
        combos.extend(panel.preset_combo for panel in self.window.monitor.panels)
        self.assertEqual(len(combos), 3)
        with patch.object(self.window.settings_panel.store, "save") as save, \
                patch.object(self.window.monitor, "_invalidate") as invalidate:
            for combo in combos:
                with self.subTest(combo=combo.accessibleName()):
                    self.assertIsInstance(combo, PopupOnlyWheelComboBox)
                    original = combo.currentData()
                    changed = MagicMock()
                    combo.currentIndexChanged.connect(changed)
                    for delta in (-120, 120):
                        wheel(combo, delta)
                    self.assertEqual(combo.currentData(), original)
                    changed.assert_not_called()
            save.assert_not_called()
            invalidate.assert_not_called()

    def test_monitor_and_theme_selectors_also_ignore_closed_wheel(self):
        for combo in (self.window.monitor.screen.fps, self.window.monitor.screen.mode,
                      *(panel.target_combo for panel in self.window.monitor.panels),
                      self.window.settings_panel.theme_combo):
            self.assertIsInstance(combo, QComboBox)
            self.assertIsInstance(combo, PopupOnlyWheelComboBox)
            previous = combo.currentIndex()
            changed = MagicMock()
            combo.currentIndexChanged.connect(changed)
            for delta in (120, -120):
                wheel(combo, delta)
            self.assertEqual(combo.currentIndex(), previous)
            changed.assert_not_called()

    def test_dynamic_task_selects_block_wheel_without_changing_saved_options(self):
        self.window.show()
        task = self.fixture.find_task_widget(self.window)
        for _ in range(2):
            self.window.show_sub_cases(task)
            combos = self.window.ui.scrollSettingContents.findChildren(QComboBox, "optionSelect")
            self.assertTrue(combos)
            previous = deepcopy(task.selected_options)
            with patch.object(self.window, "save_user_config") as save:
                for combo in combos:
                    self.assertIsInstance(combo, PopupOnlyWheelComboBox)
                    combo.setFocus()
                    changed = MagicMock()
                    combo.currentIndexChanged.connect(changed)
                    wheel(combo, 120)
                    wheel(combo, -120)
                    changed.assert_not_called()
                save.assert_not_called()
            self.assertEqual(task.selected_options, previous)
        self.assertTrue(all(isinstance(combo, PopupOnlyWheelComboBox)
                            for combo in self.window.findChildren(QComboBox)))

    def test_fps_wheel_does_not_reset_or_change_continuous_monitoring(self):
        self.fixture.show_screen_panel()
        self.fixture.configure_screen_capture()
        monitor = self.window.monitor
        screen = monitor.screen
        screen.mode.setCurrentIndex(screen.mode.findData("continuous"))
        screen.fps.setCurrentIndex(screen.fps.findData(1))
        monitor.toggle_capture()
        self.fixture.wait_for_monitor()
        try:
            previous = deepcopy(monitor.screen_preferences)
            epoch = monitor.preview_generation
            with patch.object(self.window.settings_panel, "save_monitor_preferences") as save:
                screen.fps.setFocus()
                wheel(screen.fps, 120)
                wheel(screen.fps, -120)
                self.assertTrue(monitor.streaming)
                self.assertEqual(monitor.preview_generation, epoch)
                self.assertEqual(monitor.screen_preferences, previous)
                save.assert_not_called()
        finally:
            monitor.stop_preview()
            self.fixture.wait_for_monitor()


if __name__ == "__main__":
    unittest.main()
