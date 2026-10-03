"""Additional preview rates are bounds, not a promise of capture throughput."""
import unittest

import test_runtime_lifecycle as fixtures
from app.monitoring import normalize_screen_preferences


class ExtendedFrameRateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_new_rates_are_selectable_normalized_and_persisted(self):
        window = self.fixture.window
        monitor = window.monitor
        screen = monitor.screen
        for fps in (15, 30, 45, 60):
            with self.subTest(fps=fps):
                self.assertEqual(normalize_screen_preferences({"fps": fps})["fps"], fps)
                screen.fps.setCurrentIndex(screen.fps.findData(fps))
                self.assertEqual(monitor.screen_preferences["fps"], fps)
                stored = window.settings_panel.store.load()
                self.assertEqual(stored["monitor"]["fps"], fps)

    def test_invalid_rates_still_use_safe_default(self):
        for fps in (0, 61, -1, True, 60.0, "60", None):
            self.assertEqual(normalize_screen_preferences({"fps": fps})["fps"], 2)
