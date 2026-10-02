"""Settings-only connection layout; monitor card insets are unchanged."""
import unittest

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import QPoint, Qt


class ConnectionLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_settings_heading_controller_target_and_detail_box_share_left_edge(self):
        window = self.fixture.window
        panel = window.monitor.panels[1]
        settings = window.settings_panel
        window.ui.mainPages.setCurrentWidget(window.ui.settingTab)
        window.show()
        for theme in ("light", "dark"):
            window.set_title_bar_theme(theme)
            fixtures.QApplication.processEvents()
            group = settings._connection_group
            settings.detail_scroll.ensureWidgetVisible(group)
            fixtures.QApplication.processEvents()
            widgets = (group.title_label, panel.preset_combo, panel.target_combo, settings.controller_details)
            x = [widget.mapTo(settings, QPoint()).x() for widget in widgets]
            self.assertLessEqual(max(x) - min(x), 1)
            self.assertEqual(panel.preset_combo.width(), panel.target_combo.width())
            self.assertTrue(group.title_label.alignment() & Qt.AlignmentFlag.AlignVCenter)
        self.assertEqual(window.monitor.panels[0].layout().contentsMargins().left(), 10)
