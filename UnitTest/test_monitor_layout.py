"""Expanded preview must fit the monitor viewport, including long status text."""
import unittest

import test_runtime_lifecycle as ui_fixtures
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QToolButton


class MonitorPreviewLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui_fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = ui_fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.screen = self.fixture.show_screen_panel()

    def assert_fits_viewport(self):
        QApplication.processEvents()
        QApplication.processEvents()
        scroll = self.window.ui.monitorScrollArea
        self.assertEqual(scroll.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())
        for child in (self.screen.preview, self.screen.status, self.screen.capture_button):
            left = child.mapTo(scroll.viewport(), QPoint()).x()
            self.assertGreaterEqual(left, 0)
            self.assertLessEqual(left + child.width(), scroll.viewport().width())

    def test_long_warning_and_portrait_landscape_images_fit_minimum_width(self):
        self.window.resize(1100, 900)
        self.window.ui.workspaceSplitter.setSizes([900, 309])
        for text in (
                "실행 캐시 작업 화면 갱신 주기에 따라 표시  목표 60 FPS  출력 40.0 FPS\n출력 프레임이 낮습니다. (40.0 FPS) 목표 프레임을 낮추세요.",
                "작업대상연결확인실패" * 60):
            self.screen.status.setText(text)
            for width, height in ((2048, 1024), (512, 1536)):
                image = QImage(width, height, QImage.Format.Format_RGB32)
                image.fill(0)
                self.screen.preview.set_image(image)
                self.assert_fits_viewport()
                self.assertTrue(self.screen.preview.rect().contains(self.screen.preview.image_rect()))

    def test_height_resize_and_card_reorder_keep_horizontal_scroll_zero(self):
        sections = self.window.ui.monitorSectionsWidget
        self.screen.status.setText("연결 준비 재시도 중  목표 60 FPS  출력 0.0 FPS\n출력 프레임이 낮습니다. (0.0 FPS) 목표 프레임을 낮추세요.")
        for order in (("screen", "log", "connection"), ("connection", "screen", "log")):
            cards = {card.property("monitorSectionKey"): card for card in sections.sections()}
            for index, key in enumerate(order):
                sections.splitter.insertWidget(index, cards[key])
            for height in (500, 1000):
                self.window.resize(1100, height)
                self.window.ui.workspaceSplitter.setSizes([900, 309])
                sections.sync_section_layout()
                self.assert_fits_viewport()

    def test_expand_after_long_status_does_not_enlarge_monitor_content(self):
        section = self.screen.parentWidget().parentWidget()
        toggle = section.findChild(QToolButton, "monitorSectionToggle")
        toggle.setChecked(False)
        self.screen.status.setText("테스트 캡처  목표 60 FPS  출력 0.0 FPS\n출력 프레임이 낮습니다. (0.0 FPS) 목표 프레임을 낮추세요.")
        self.window.resize(1100, 650)
        self.window.ui.workspaceSplitter.setSizes([900, 309])
        toggle.setChecked(True)
        self.assert_fits_viewport()
