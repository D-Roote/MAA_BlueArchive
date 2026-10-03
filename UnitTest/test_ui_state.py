"""Window/panel persistence without native Maa connections or capture requests."""
import base64
import json
import unittest
from unittest.mock import patch

import test_runtime_lifecycle as fixtures
from PySide6.QtWidgets import QToolButton


class UIStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.path = self.window.runtime.user_dir / "config" / "user_config.json"

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def reopen(self):
        with patch("app.winUI.AppRuntime", return_value=self.window.runtime):
            restored = fixtures.MainWindow()
        self.addCleanup(restored.deleteLater)
        return restored

    def test_close_flushes_window_height_position_and_valid_native_geometry(self):
        self.window.show()
        self.window.resize(1080, 620)
        self.window.move(40, 30)
        fixtures.QApplication.processEvents()
        self.window.close()
        saved = self.read()["ui_state"]
        self.assertGreater(len(base64.b64decode(saved["geometry"], validate=True)), 0)
        restored = self.reopen()
        restored.show()
        fixtures.QApplication.processEvents()
        # Qt may clamp width/position to the 800px offscreen test display.
        self.assertEqual(restored.height(), 620)
        self.assertGreaterEqual(restored.pos().x(), 0)
        self.assertEqual(restored.pos().y(), 30)
        self.assertGreaterEqual(restored.width(), restored.minimumWidth())
        restored.close()

    def test_workspace_ratio_and_accordion_states_survive_startup_without_autorun(self):
        self.window.show()
        self.window.ui.workspaceSplitter.setSizes([460, 360])
        cards = self.window.ui.monitorSectionsWidget.sections()
        for card in cards:
            card.findChild(QToolButton, "monitorSectionToggle").setChecked(card.property("monitorSectionKey") != "log")
        fixtures.QApplication.processEvents()
        self.window.close()
        saved = self.read()
        widths = saved["ui_state"]["workspace_sizes"]
        # Isolate ratio restoration from Qt's offscreen geometry clamping.
        saved["ui_state"]["geometry"] = "invalid"
        self.path.write_text(json.dumps(saved), encoding="utf-8")
        restored = self.reopen()
        self.assertEqual(restored._saved_ui_state["workspace_sizes"], widths)
        restored.show()
        fixtures.QApplication.processEvents()
        actual = restored.ui.workspaceSplitter.sizes()
        self.assertAlmostEqual(actual[0] / actual[1], widths[0] / widths[1], delta=0.02)
        states = {card.property("monitorSectionKey"): card.property("monitorExpanded")
                  for card in restored.ui.monitorSectionsWidget.sections()}
        self.assertEqual(states, {"connection": True, "screen": True, "log": False})
        self.assertFalse(restored.monitor.streaming)
        self.assertIsNone(restored.monitor.worker)
        self.assertIsNone(restored.monitor.service.controller)
        self.assertFalse(restored.isRunning)
        restored.close()

    def test_resize_autosaves_after_debounce_and_close_flushes_latest_change(self):
        self.window.show()
        self.window.resize(1100, 600)
        fixtures.QTest.qWait(360)
        before = self.read()["ui_state"]["geometry"]
        self.window.resize(1100, 640)
        self.window.close()
        self.assertNotEqual(self.read()["ui_state"]["geometry"], before)
        self.assertFalse(self.window._ui_save_timer.isActive())
        restored = self.reopen()
        self.assertEqual(restored.height(), 640)
        restored.close()

    def test_maximized_state_restores_but_minimized_exit_does_not_hide_startup(self):
        self.window.showMaximized()
        fixtures.QApplication.processEvents()
        self.window.close()
        maximized = self.reopen()
        self.assertTrue(maximized.isMaximized())
        maximized.showNormal()
        maximized.showMinimized()
        fixtures.QApplication.processEvents()
        maximized.close()
        restored = self.reopen()
        self.assertFalse(restored.isMinimized())
        restored.close()

    def test_invalid_ui_fields_do_not_crash_or_remove_task_configuration(self):
        saved = self.read()
        tasks = saved["tasks"]
        saved["ui_state"] = {"geometry": "@invalid", "workspace_sizes": [True, -5],
                             "monitor_expanded": {"screen": "yes", "log": None}}
        self.path.write_text(json.dumps(saved), encoding="utf-8")
        restored = self.reopen()
        self.assertEqual(restored.size(), fixtures.QSize(1200, 800))
        self.assertEqual(self.read()["tasks"], tasks)
        self.assertFalse(restored.monitor.screen_content.isVisible())
        restored.close()
