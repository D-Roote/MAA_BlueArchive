"""Painted FPS sampling and sticky ten-observation shortfall warnings."""
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
        self.stats.set_target(60)

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

    def test_ninth_sample_is_quiet_and_tenth_latches_plain_warning(self):
        for _ in range(9):
            self.sample(45)
        self.assertNotIn("낮습니다", self.stats.describe(60))
        for _ in range(50):
            self.stats.describe(60)
        self.assertEqual(self.stats.below_target_samples, 9)
        self.sample(45)
        self.assertEqual(self.stats.describe(60),
                         "목표 60 FPS  출력 45.0 FPS\n출력 프레임이 낮습니다. (45.0 FPS) 목표 프레임을 낮추세요.")

    def test_smaller_absolute_gap_and_above_target_do_not_count(self):
        for _ in range(12):
            self.sample(46)
            self.sample(75)
        self.assertEqual(self.stats.below_target_samples, 0)
        self.assertNotIn("낮습니다", self.stats.describe(60))

    def test_stalled_display_reports_zero_after_ten_samples(self):
        for _ in range(10):
            self.sample(0)
        self.assertIn("출력 0.0 FPS\n출력 프레임이 낮습니다. (0.0 FPS)", self.stats.describe(60))

    def test_targets_one_to_fifteen_never_warn(self):
        for target in range(1, 16):
            self.stats.set_target(target)
            for actual in (0, target // 2, target, target * 3):
                for _ in range(10):
                    self.sample(actual)
                text = self.stats.describe(target)
                self.assertIn(f"목표 {target} FPS", text)
                self.assertIn(f"출력 {actual:.1f} FPS", text)
                self.assertNotIn("낮습니다", text)
            self.assertEqual(self.stats.below_target_samples, 0)

    def test_fractional_threshold_is_rounded_up_to_whole_fps(self):
        for target, threshold in ((16, 4), (30, 8), (45, 12), (60, 15)):
            self.stats.set_target(target)
            for _ in range(10):
                self.sample(target - threshold + 1)
            self.assertEqual(self.stats.below_target_samples, 0)
            for _ in range(10):
                self.sample(target - threshold)
            self.assertIn("낮습니다", self.stats.describe(target))

    def test_warning_survives_recovery_and_sample_reset(self):
        for _ in range(10):
            self.sample(20)
        self.sample(60)
        self.assertIn("출력 60.0 FPS\n출력 프레임이 낮습니다. (20.0 FPS)", self.stats.describe(60))
        self.stats.reset()
        self.assertIsNone(self.stats.actual)
        self.assertIn("낮습니다", self.stats.describe(60))

    def test_samples_accumulate_across_recovery_not_necessarily_consecutive(self):
        for _ in range(10):
            self.sample(45)
            self.sample(60)
        self.assertIn("낮습니다", self.stats.describe(60))

    def test_target_change_alone_clears_latched_warning_and_count(self):
        for _ in range(10):
            self.sample(0)
        self.stats.set_target(60)
        self.assertIn("낮습니다", self.stats.describe(60))
        self.stats.set_target(30)
        self.assertIsNone(self.stats.actual)
        self.assertEqual(self.stats.below_target_samples, 0)
        self.assertNotIn("낮습니다", self.stats.describe(30))

    def test_warning_contains_plain_words_not_icons_or_percentages(self):
        for _ in range(10):
            self.sample(0)
        for symbol in ("⚠", "%", "·", "/"):
            self.assertNotIn(symbol, self.stats.describe(60))

    def test_delayed_timer_uses_actual_elapsed_time(self):
        self.sample(60, elapsed=2)
        self.assertEqual(self.stats.actual, 30)
        self.assertEqual(self.stats.below_target_samples, 1)


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

    def test_sticky_warning_source_first_no_dimensions_and_no_full_panel_sync(self):
        now = [0.0]
        self.monitor.display_fps = DisplayFPS(lambda: now[0])
        self.monitor.display_fps.set_target(60)
        self.monitor.streaming = True
        self.monitor.screen_preferences["fps"] = 60
        self.monitor._frame_details = (160, 90, "실행 캐시")
        self.addCleanup(self.monitor.stop_preview)
        with patch.object(self.monitor, "_sync") as sync:
            for sample in range(10):
                for _ in range(45):
                    self.monitor._frame_presented()
                now[0] += 1
                self.monitor._refresh_display_fps()
                self.assertEqual("낮습니다" in self.screen.status.text(), sample == 9)
            self.assertTrue(self.screen.status.text().startswith("실행 캐시  목표 60 FPS  출력 45.0 FPS"))
            self.assertNotIn("너비", self.screen.status.text())
            self.assertNotIn("높이", self.screen.status.text())
            for _ in range(60):
                self.monitor._frame_presented()
            now[0] += 1
            self.monitor._refresh_display_fps()
            self.assertIn("출력 60.0 FPS\n출력 프레임이 낮습니다.", self.screen.status.text())
            sync.assert_not_called()

    def test_hidden_task_handoff_and_stop_preserve_warning_until_fps_change(self):
        self.monitor.display_fps.set_target(60)
        self.monitor.display_fps.warning_fps = 10
        self.monitor.streaming = True
        self.monitor.screen_preferences["fps"] = 60
        self.addCleanup(self.monitor.stop_preview)
        with patch.object(self.screen.preview.canvas, "isVisible", return_value=False):
            self.monitor._refresh_display_fps()
        self.monitor.task_state_changed()
        self.monitor.stop_preview()
        self.assertIn("낮습니다", self.monitor.display_fps.describe(60))
        self.monitor.change_screen_preferences({"mode": "continuous", "fps": 30})
        self.assertNotIn("낮습니다", self.monitor.display_fps.describe(30))

    def test_stop_clears_sampling_and_timer(self):
        self.monitor.streaming = True
        self.monitor.fps_timer.start()
        self.monitor.display_fps.actual = 30
        self.monitor.stop_preview()
        self.assertFalse(self.monitor.fps_timer.isActive())
        self.assertIsNone(self.monitor.display_fps.actual)
