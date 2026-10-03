"""Select mouse highlights must not remain solely because focus is retained."""
import unittest

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QComboBox


class SelectHighlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_mouse_highlight_resets_on_leave_even_when_select_has_focus(self):
        window = self.fixture.window
        window.show()
        window.show_sub_cases(self.fixture.find_task_widget(window))
        combo = window.ui.scrollSettingContents.findChild(QComboBox, "optionSelect")
        for theme, normal, hovered in (("light", "#ffffff", "#f0f8fc"),
                                       ("dark", "#1e2d46", "#283a55")):
            with self.subTest(theme=theme):
                window.set_title_bar_theme(theme)
                window.activateWindow()
                window.windowHandle().requestActivate()
                QApplication.processEvents()
                combo.setFocus(Qt.FocusReason.MouseFocusReason)
                QApplication.processEvents()
                self.assertTrue(combo.hasFocus())
                combo.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
                QApplication.sendEvent(combo, QEvent(QEvent.Type.Enter))
                self.assertEqual(combo.grab().toImage().pixelColor(8, combo.height() // 2).name(), hovered)
                combo.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
                QApplication.sendEvent(combo, QEvent(QEvent.Type.Leave))
                self.assertEqual(combo.grab().toImage().pixelColor(8, combo.height() // 2).name(), normal)
                self.assertTrue(combo.hasFocus())
