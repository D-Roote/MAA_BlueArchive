"""Displayed FPS, exact warning threshold and image-only paint accounting."""
import unittest
from unittest.mock import patch

import test_runtime_lifecycle as ui_fixtures
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.monitorFPS import DisplayFPS


class DisplayFPSTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.stats = DisplayFPS(lambda: self.now)

    def sample(self, count, elapsed=1):
        for _ in range(count):
            self.stats.presented()
        self.now += elapsed
        self.assertTrue(self.stats.sample())

    def test_warmup_does_not_warn(self):
        self.now = 0.9
        self.assertFalse(self.stats.sample())
        self.assertEqual(self.stats.describe(60), "목표 60 FPS  출력 측정 중")

    def test_target_and_actual_both_displayed(self):
        self.sample(60)
        self.assertEqual(self.stats.describe(60), "목표 60 FPS  출력 60.0 FPS")

    def test_exact_fifteen_fps_shortfall_warns_for_target_sixty(self):
        self.sample(45)
        self.assertIn("성능 미달  15.0 FPS 차이", self.stats.describe(60))

    def test_smaller_absolute_gap_does_not_warn(self):
        self.sample(46)
        self.assertNotIn("성능 미달", self.stats.describe(60))

    def test_above_target_also_uses_absolute_gap(self):
        self.sample(75)
        self.assertIn("FPS 편차  15.0 FPS 차이", self.stats.describe(60))

    def test_stalled_display_reports_zero(self):
        self.sample(0)
        self.assertIn("출력 0.0 FPS  성능 미달  60.0 FPS 차이", self.stats.describe(60))

    def test_targets_one_to_fifteen_never_warn_even_when_stalled_or_above_target(self):
        for target in range(1, 16):
            for actual in (0, target / 2, target, target * 3):
                self.stats.actual = actual
                text = self.stats.describe(target)
                self.assertIn(f"목표 {target} FPS", text)
                self.assertIn(f"출력 {actual:.1f} FPS", text)
                self.assertNotIn("성능 미달", text)
                self.assertNotIn("FPS 편차", text)

    def test_fractional_threshold_is_rounded_up_to_whole_fps(self):
        for target, threshold in ((16, 4), (30, 8), (45, 12), (60, 15)):
            self.stats.actual = target - threshold + 0.01
            self.assertNotIn("성능 미달", self.stats.describe(target))
            self.stats.actual = max(0, target - threshold)
            self.assertIn("성능 미달", self.stats.describe(target))

    def test_warning_contains_plain_words_not_icons_or_percentages(self):
        self.sample(0)
        for symbol in ("⚠", "%", "·", "/"):
            self.assertNotIn(symbol, self.stats.describe(60))

    def test_delayed_timer_uses_actual_elapsed_time(self):
        self.sample(60, elapsed=2)
        self.assertEqual(self.stats.actual, 30)

    def test_reset_drops_old_frames_and_warning(self):
        self.sample(20)
        self.stats.reset()
        self.assertIsNone(self.stats.actual)
        self.assertEqual(self.stats.frames, 0)
        self.assertNotIn("성능 미달", self.stats.describe(15))


class DisplayFPSUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui_fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = ui_fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.screen = self.fixture.show_screen_panel()
        self.monitor = self.fixture.window.monitor

    def test_only_new_painted_images_count_and_updates_coalesce(self):
        image = QImage(20, 10, QImage.Format.Format_RGB32)
        image.fill(0)
        with patch.object(self.monitor, "_frame_presented", wraps=self.monitor._frame_presented):
            # Connect a separate observer; the existing bound Qt slot is retained.
            presented = []
            self.screen.preview.canvas.frame_presented.connect(lambda: presented.append(1))
            self.screen.preview.set_image(image)
            self.screen.preview.set_image(image.copy())
            QApplication.processEvents()
            self.assertEqual(len(presented), 1)
            self.screen.preview.canvas.update()
            QApplication.processEvents()
            self.assertEqual(len(presented), 1)
            self.screen.preview.set_image(image.copy())
            QApplication.processEvents()
            self.assertEqual(len(presented), 2)
            self.screen.preview.set_image(QImage())
            QApplication.processEvents()
            self.assertEqual(len(presented), 2)

    def test_status_warns_and_recovers_without_full_panel_sync(self):
        now = [0.0]
        self.monitor.display_fps = DisplayFPS(lambda: now[0])
        self.monitor.streaming = True
        self.monitor.screen_preferences["fps"] = 60
        self.monitor._frame_details = (160, 90, "실행 캐시")
        self.addCleanup(self.monitor.stop_preview)
        with patch.object(self.monitor, "_sync") as sync:
            for _ in range(45):
                self.monitor._frame_presented()
            now[0] = 1.0
            self.monitor._refresh_display_fps()
            self.assertIn("목표 60 FPS  출력 45.0 FPS", self.screen.status.text())
            self.assertIn("성능 미달", self.screen.status.text())
            for _ in range(60):
                self.monitor._frame_presented()
            now[0] = 2.0
            self.monitor._refresh_display_fps()
            self.assertNotIn("성능 미달", self.screen.status.text())
            sync.assert_not_called()

    def test_stop_clears_sampling_and_timer(self):
        self.monitor.streaming = True
        self.monitor.fps_timer.start()
        self.monitor.display_fps.actual = 30
        self.monitor.stop_preview()
        self.assertFalse(self.monitor.fps_timer.isActive())
        self.assertIsNone(self.monitor.display_fps.actual)
