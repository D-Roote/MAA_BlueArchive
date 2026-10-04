"""Log popup follows app theme, including when the OS palette is dark."""
import unittest
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPalette, QTextCursor
from app.winUI import TitleBarTheme


class LogContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()
        cls.app = fixtures.UILifecycleTests.app

    def setUp(self):
        fixture = fixtures.UILifecycleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.window = fixture.window
        self.log = self.window.ui.logPrintText
        self.log.setPlainText("첫 로그\n선택해서 복사할 로그")

    def menu(self):
        menu = self.window._create_log_context_menu()
        self.addCleanup(menu.deleteLater)
        menu.ensurePolished()
        menu.resize(menu.sizeHint())
        return menu

    def test_light_theme_popup_remains_light_under_dark_system_palette(self):
        original = QPalette(self.app.palette())
        self.addCleanup(lambda: self.app.setPalette(original))
        dark = QPalette(original)
        dark.setColor(QPalette.Window, QColor("#111111"))
        dark.setColor(QPalette.WindowText, QColor("#EEEEEE"))
        dark.setColor(QPalette.Base, QColor("#111111"))
        dark.setColor(QPalette.Text, QColor("#EEEEEE"))
        self.app.setPalette(dark)
        with patch.object(self.window._style_hints, "colorScheme", return_value=Qt.ColorScheme.Dark):
            self.window.set_title_bar_theme(TitleBarTheme.LIGHT)
        menu = self.menu()
        self.assertEqual(menu.objectName(), "logContextMenu")
        image = menu.grab().toImage()
        self.assertEqual(image.pixelColor(menu.width() - 3, menu.height() // 2), QColor("#FFFFFF"))
        self.assertEqual(menu.palette().color(QPalette.WindowText), QColor("#334155"))
        self.assertTrue(all(action.icon().isNull() for action in menu.actions()))

    def test_dark_and_system_theme_are_resolved_at_each_open(self):
        for theme, system, expected in ((TitleBarTheme.DARK, Qt.ColorScheme.Light, "#172238"),
                                        (TitleBarTheme.SYSTEM, Qt.ColorScheme.Dark, "#172238"),
                                        (TitleBarTheme.SYSTEM, Qt.ColorScheme.Light, "#FFFFFF")):
            with self.subTest(theme=theme, system=system):
                with patch.object(self.window._style_hints, "colorScheme", return_value=system):
                    self.window.set_title_bar_theme(theme)
                menu = self.menu()
                image = menu.grab().toImage()
                self.assertEqual(image.pixelColor(menu.width() - 3, menu.height() // 2), QColor(expected))

    def test_standard_copy_copies_selection_not_entire_log(self):
        cursor = self.log.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(4, QTextCursor.KeepAnchor)
        self.log.setTextCursor(cursor)
        menu = self.menu()
        copy = next(action for action in menu.actions() if action.objectName() == "edit-copy")
        self.assertTrue(copy.isEnabled())
        copy.trigger()
        self.assertEqual(self.app.clipboard().text(), "첫 로그")

    def test_standard_selection_actions_and_disabled_copy_are_preserved(self):
        menu = self.menu()
        copy = next(action for action in menu.actions() if action.objectName() == "edit-copy")
        self.assertFalse(copy.isEnabled())
        select = next(action for action in menu.actions() if action.objectName() == "select-all")
        self.assertTrue(select.isEnabled())
        select.trigger()
        self.assertEqual(self.log.textCursor().selectedText().replace("\u2029", "\n"), self.log.toPlainText())

    def test_unavailable_keyboard_shortcut_hints_are_not_advertised(self):
        menu = self.menu()
        for action in menu.actions():
            with self.subTest(action=action.objectName()):
                self.assertNotIn("\t", action.text())
                self.assertNotIn("Ctrl+", action.text())
                self.assertTrue(action.shortcut().isEmpty())
        names = {action.objectName() for action in menu.actions()}
        self.assertTrue({"edit-copy", "select-all"}.issubset(names))

    def test_hover_highlight_is_painted_in_the_selected_app_theme(self):
        self.log.selectAll()
        for theme, color in ((TitleBarTheme.LIGHT, "#E6F5FC"), (TitleBarTheme.DARK, "#243954")):
            with self.subTest(theme=theme):
                self.window.set_title_bar_theme(theme)
                menu = self.menu()
                copy = next(action for action in menu.actions() if action.objectName() == "edit-copy")
                menu.setActiveAction(copy)
                rect = menu.actionGeometry(copy)
                image = menu.grab().toImage()
                self.assertEqual(image.pixelColor(rect.left() + 2, rect.center().y()), QColor(color))

    def test_popup_uses_viewport_coordinates_and_is_disposed_after_close_or_error(self):
        self.assertEqual(self.log.contextMenuPolicy(), Qt.CustomContextMenu)
        for error in (None, RuntimeError("popup closed")):
            menu = MagicMock()
            menu.exec.side_effect = error
            with patch.object(self.window, "_create_log_context_menu", return_value=menu):
                if error:
                    with self.assertRaises(RuntimeError):
                        self.window.show_log_context_menu(QPoint(10, 12))
                else:
                    self.log.customContextMenuRequested.emit(QPoint(10, 12))
            menu.exec.assert_called_once_with(self.log.viewport().mapToGlobal(QPoint(10, 12)))
            menu.deleteLater.assert_called_once()
