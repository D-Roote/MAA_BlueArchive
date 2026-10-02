"""Shared connection panels and serial background diagnostics."""
from time import monotonic

from PySide6.QtCore import QObject, QRect, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from app.monitoring import (
    MonitoringService, normalize_connection_preferences, normalize_screen_preferences,
)


def owned_qimage(frame):
    """SDK BGR buffers must not outlive their owner through a borrowed QImage."""
    frame = MonitoringService.validate_frame(frame)
    height, width, _ = frame.shape
    return QImage(frame.data, width, height, frame.strides[0], QImage.Format.Format_BGR888).copy()


class ScreenPreview(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("screenPreview")
        self.setAccessibleName("스크린샷 미리보기")
        self.setMinimumWidth(0)
        self.setFixedHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.image = QImage()

    def set_image(self, image):
        self.image = image
        self.update()

    def image_rect(self):
        bounds = self.rect().adjusted(8, 8, -8, -8)
        if self.image.isNull() or bounds.isEmpty():
            return QRect()
        result = QRect(bounds.topLeft(), self.image.size().scaled(bounds.size(), Qt.AspectRatioMode.KeepAspectRatio))
        result.moveCenter(bounds.center())
        return result

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.image.isNull():
            painter.setPen(self.palette().text().color())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "연결 확인 후 화면을 테스트하세요")
        else:
            painter.drawImage(self.image_rect(), self.image)


class ScreenPanel(QWidget):
    preferences_changed = Signal(object)
    capture_requested = Signal()

    def __init__(self, preferences, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 4, 10, 10)
        root.setSpacing(6)
        options = QHBoxLayout()
        options.setSpacing(4)
        self.mode = QComboBox()
        self.mode.setAccessibleName("화면 표시 방식")
        self.mode.addItem("단발 테스트", "single")
        self.mode.addItem("연속 모니터링", "continuous")
        self.fps = QComboBox()
        self.fps.setAccessibleName("화면 표시 FPS")
        for fps in (1, 2, 5, 10, 15, 30, 45, 60):
            self.fps.addItem(f"{fps} FPS", fps)
        for combo in (self.mode, self.fps):
            combo.setObjectName("monitorComboBox")
            combo.setMinimumWidth(0)
            options.addWidget(combo)
        self.mode.setCurrentIndex(self.mode.findData(preferences["mode"]))
        self.fps.setCurrentIndex(self.fps.findData(preferences["fps"]))
        root.addLayout(options)
        self.preview = ScreenPreview()
        root.addWidget(self.preview)
        self.capture_button = QPushButton("스크린샷 테스트")
        self.capture_button.setObjectName("monitorActionButton")
        root.addWidget(self.capture_button)
        self.status = QLabel("단발 테스트는 요청할 때만 캡처합니다.")
        self.status.setObjectName("monitorStatus")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.mode.currentIndexChanged.connect(self._emit_preferences)
        self.fps.currentIndexChanged.connect(self._emit_preferences)
        self.capture_button.clicked.connect(self.capture_requested)

    def _emit_preferences(self):
        self.preferences_changed.emit({"mode": self.mode.currentData(), "fps": self.fps.currentData()})

    def sync(self, preferences, streaming, busy, available):
        self.fps.setEnabled(preferences["mode"] == "continuous")
        self.capture_button.setText("모니터링 중지" if streaming else (
            "모니터링 시작" if preferences["mode"] == "continuous" else "스크린샷 테스트"
        ))
        self.capture_button.setEnabled(streaming or (available and not busy))


class ConnectionPanel(QWidget):
    preset_changed = Signal(str)
    target_changed = Signal(str)
    preferences_changed = Signal(object)
    discover_requested = Signal()
    connect_requested = Signal()
    disconnect_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("connectionPanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 4, 10, 10)
        root.setSpacing(6)
        self.preset_combo = QComboBox()
        self.target_combo = QComboBox()
        for combo, name in ((self.preset_combo, "사전 확인 컨트롤러"), (self.target_combo, "연결 대상")):
            combo.setObjectName("monitorComboBox")
            combo.setAccessibleName(name)
            combo.setMinimumWidth(0)
            combo.setMaxVisibleItems(8)
            root.addWidget(combo)
        self.adb_fields = QWidget()
        adb_layout = QVBoxLayout(self.adb_fields)
        adb_layout.setContentsMargins(0, 0, 0, 0)
        adb_layout.setSpacing(4)
        self.adb_path = QLineEdit()
        self.adb_path.setPlaceholderText("ADB 실행 파일 (비우면 자동 탐색)")
        self.adb_path.setAccessibleName("ADB 실행 파일")
        self.address = QLineEdit()
        self.address.setPlaceholderText("기기 주소 · 예: 127.0.0.1:5555")
        self.address.setAccessibleName("ADB 연결 주소")
        path_row = QHBoxLayout()
        path_row.setSpacing(4)
        path_row.addWidget(self.adb_path, 1)
        self.browse_button = QPushButton("찾기")
        self.browse_button.setObjectName("monitorActionButton")
        path_row.addWidget(self.browse_button)
        adb_layout.addLayout(path_row)
        adb_layout.addWidget(self.address)
        root.addWidget(self.adb_fields)
        for editor in (self.adb_path, self.address):
            editor.setObjectName("monitorLineEdit")
            editor.editingFinished.connect(self._emit_preferences)
        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        self.discover_button = QPushButton("대상 탐색")
        self.connect_button = QPushButton("연결 확인")
        self.disconnect_button = QPushButton("해제")
        for button in (self.discover_button, self.connect_button, self.disconnect_button):
            button.setObjectName("monitorActionButton")
            buttons.addWidget(button)
        root.addLayout(buttons)
        self.status = QLabel("대상을 탐색한 후 연결을 확인하세요.")
        self.status.setObjectName("monitorStatus")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.preset_combo.currentIndexChanged.connect(
            lambda: self.preset_changed.emit(self.preset_combo.currentData() or "")
        )
        self.target_combo.currentIndexChanged.connect(
            lambda: self.target_changed.emit(self.target_combo.currentData() or "")
        )
        self.discover_button.clicked.connect(self.discover_requested)
        self.connect_button.clicked.connect(self.connect_requested)
        self.disconnect_button.clicked.connect(self.disconnect_requested)
        self.browse_button.clicked.connect(self._browse_adb)

    def _emit_preferences(self):
        self.preferences_changed.emit({"adb_path": self.adb_path.text(), "address": self.address.text()})

    def _browse_adb(self):
        path, _ = QFileDialog.getOpenFileName(self, "ADB 실행 파일 선택", self.adb_path.text(), "실행 파일 (*.exe);;모든 파일 (*)")
        if path:
            self.adb_path.setText(path)
            self._emit_preferences()

    def sync(self, presets, preset_name, targets, target_key, preferences, status, busy, running, connected):
        items = [
            (self.preset_combo, [(p.get("label") or p["name"], p["name"]) for p in presets], preset_name),
            (self.target_combo, [("대상을 탐색하세요" if not targets else "연결 대상 선택", "")]
             + [(t.label, t.key) for t in targets], target_key),
        ]
        for combo, values, selection in items:
            previous = combo.blockSignals(True)
            current = [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]
            if current != values:
                combo.clear()
                for label, key in values:
                    combo.addItem(label, key)
            combo.setCurrentIndex(max(0, combo.findData(selection)) if values else -1)
            combo.blockSignals(previous)
        is_adb = any(p["name"] == preset_name and p["type"] == "Adb" for p in presets)
        self.adb_fields.setVisible(is_adb)
        for editor, key in ((self.adb_path, "adb_path"), (self.address, "address")):
            if not editor.hasFocus() and editor.text() != preferences[key]:
                editor.setText(preferences[key])
        self.status.setText(status)
        editable = bool(presets) and not busy and not running
        self.preset_combo.setEnabled(editable)
        self.target_combo.setEnabled(editable and bool(targets))
        self.adb_fields.setEnabled(editable)
        self.discover_button.setEnabled(editable)
        self.connect_button.setEnabled(editable and bool(target_key or (is_adb and preferences["adb_path"] and preferences["address"])))
        self.disconnect_button.setEnabled(not busy and connected and not running)


class MonitorOperation(QThread):
    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation
        self.outcome = None

    def run(self):
        try:
            self.outcome = (True, self.operation())
        except Exception as error:
            self.outcome = (False, str(error))


class MonitorCoordinator(QObject):
    busy_changed = Signal()
    shutdown_ready = Signal()

    def __init__(self, window, section_content, screen_content):
        super().__init__(window)
        self.window = window
        runtime = window.runtime
        self.service = MonitoringService(runtime.interface, runtime.resource_config, runtime.user_dir)
        self.preferences = normalize_connection_preferences(window.settings_panel.config.get("connection"))
        initial = window.settings_panel.controller_settings().get("name")
        self.preset_name = initial if any(p["name"] == initial for p in self.service.presets) else (
            self.service.presets[0]["name"] if self.service.presets else ""
        )
        self.target_key = ""
        self.targets = []
        self.status = "대상을 탐색한 후 연결을 확인하세요."
        self.worker = None
        self.generation = 0
        self.pending_reset = False
        self.pending_start = None
        self.shutting_down = False
        self.screen_preferences = normalize_screen_preferences(window.settings_panel.config.get("monitor"))
        self.screen_content = screen_content
        self.screen = ScreenPanel(self.screen_preferences, screen_content)
        screen_layout = screen_content.layout()
        while screen_layout.count():
            item = screen_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        screen_layout.setContentsMargins(0, 0, 0, 0)
        screen_layout.addWidget(self.screen)
        self.streaming = False
        self.preview_generation = 0
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.capture_frame)
        self.screen.preferences_changed.connect(self.change_screen_preferences)
        self.screen.capture_requested.connect(self.toggle_capture)
        self.panels = [ConnectionPanel(section_content), ConnectionPanel(window.settings_panel)]
        layout = section_content.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.panels[0])
        window.settings_panel.add_connection_panel(self.panels[1])
        for panel in self.panels:
            panel.preset_changed.connect(self.select_preset)
            panel.target_changed.connect(self.select_target)
            panel.preferences_changed.connect(self.change_preferences)
            panel.discover_requested.connect(self.discover)
            panel.connect_requested.connect(self.connect_target)
            panel.disconnect_requested.connect(self.disconnect_target)
        window.settings_panel.controller_changed.connect(self._runtime_preset_changed)
        window.settings_panel.program_changed.connect(self._program_settings_changed)
        self._sync()

    @property
    def busy(self):
        return self.worker is not None

    @property
    def ready_to_close(self):
        return not self.busy and self.service.controller is None

    def _sync(self):
        status = "작업 실행 중 · 작업 컨트롤러 사용, 사전 재연결은 종료 후 가능합니다." if self.window.isRunning else self.status
        for panel in self.panels:
            panel.sync(self.service.presets, self.preset_name, self.targets, self.target_key,
                       self.preferences, status, self.busy, self.window.isRunning,
                       self.service.controller is not None)
        available = (not self.shutting_down and self.pending_start is None
                     and (self.window.isRunning or self.service.controller is not None))
        self.screen.sync(self.screen_preferences, self.streaming, self.busy, available)

    def change_screen_preferences(self, preferences):
        self.stop_preview()
        self.screen_preferences = normalize_screen_preferences(preferences)
        self.window.settings_panel.save_monitor_preferences(screen=self.screen_preferences)
        self._sync()

    def stop_preview(self):
        was_active = self.streaming or (self.worker is not None and self.worker.kind == "frame")
        self.preview_timer.stop()
        self.streaming = False
        self.preview_generation += 1
        if was_active:
            self.screen.status.setText("화면 표시를 중지했습니다. 마지막 화면은 유지됩니다.")
        self._sync()

    def toggle_capture(self):
        if self.streaming:
            self.stop_preview()
            self.screen.status.setText("모니터링을 중지했습니다. 마지막 화면은 유지됩니다.")
            return
        if self.busy or self.shutting_down or self.pending_start is not None or not self.screen_content.isVisible():
            return
        self.streaming = self.screen_preferences["mode"] == "continuous"
        self.capture_frame()

    def capture_frame(self):
        if self.busy or self.shutting_down or not self.screen_content.isVisible():
            self.stop_preview()
            return
        running = self.window.isRunning
        if not running and self.service.controller is None:
            self.stop_preview()
            self.screen.status.setText("먼저 연결 확인을 완료하세요.")
            return
        epoch = self.preview_generation

        def capture():
            started = monotonic()
            frame = self.window.runtime.capture_cached_frame() if running else self.service.capture()
            return {"image": owned_qimage(frame), "elapsed": monotonic() - started,
                    "source": "실행 캐시" if running else "테스트 캡처", "epoch": epoch}

        self.screen.status.setText("화면을 가져오고 있습니다…")
        self._request("frame", capture)

    def _runtime_preset_changed(self, preset):
        if preset.get("name") != self.preset_name:
            self.select_preset(preset.get("name", ""))

    def _program_settings_changed(self, _settings):
        self.targets = []
        self.target_key = ""
        self._invalidate()

    def select_preset(self, name):
        if name == self.preset_name or not any(p["name"] == name for p in self.service.presets):
            return
        self.preset_name = name
        self.targets = []
        self.target_key = ""
        self._invalidate()
        combo = self.window.settings_panel.controller_combo
        for index in range(combo.count()):
            data = combo.itemData(index)
            if isinstance(data, dict) and data.get("name") == name:
                combo.setCurrentIndex(index)
                break

    def select_target(self, key):
        if key == self.target_key:
            return
        self.target_key = key
        if any(t.key == key and t.kind == "Adb" for t in self.targets):
            self.preferences["address"] = key
            self.window.settings_panel.save_monitor_preferences(connection=self.preferences)
        self._invalidate()

    def change_preferences(self, preferences):
        preferences = normalize_connection_preferences(preferences)
        if preferences == self.preferences:
            return
        self.preferences = preferences
        self.targets = []
        self.target_key = ""
        self.window.settings_panel.save_monitor_preferences(connection=preferences)
        self._invalidate()

    def _invalidate(self):
        self.stop_preview()
        self.screen.preview.set_image(QImage())
        self.screen.status.setText("연결 설정이 변경되었습니다. 다시 연결 확인 후 테스트하세요.")
        self.generation += 1
        self.status = "설정이 변경되었습니다. 연결을 다시 확인하세요."
        self.pending_reset = True
        if not self.busy:
            if self.service.controller is not None:
                self._request("disconnect", self.service.close)
            else:
                self.pending_reset = False
        self._sync()

    def _request(self, kind, operation):
        if self.busy:
            return False
        worker = MonitorOperation(operation, self)
        worker.kind = kind
        worker.preview_generation = self.preview_generation
        self.worker = worker
        generation = self.generation
        worker.finished.connect(lambda: self._finished(worker, kind, generation))
        self._sync()
        self.busy_changed.emit()
        worker.start()
        return True

    def _finished(self, worker, kind, generation):
        succeeded, result = worker.outcome
        self.worker = None
        worker.deleteLater()
        if kind == "frame":
            if succeeded and result["epoch"] == self.preview_generation and generation == self.generation:
                image = result["image"]
                self.screen.preview.set_image(image)
                self.screen.status.setText(
                    f"{image.width()} × {image.height()} · {result['elapsed'] * 1000:.0f} ms · {result['source']}"
                    + (" · FPS는 최대 요청 빈도" if self.streaming else "")
                )
                if self.streaming and not self.shutting_down and self.pending_start is None:
                    interval = 1 / self.screen_preferences["fps"]
                    self.preview_timer.start(max(1, round((interval - result["elapsed"]) * 1000)))
            elif (not succeeded and generation == self.generation
                  and worker.preview_generation == self.preview_generation):
                self.stop_preview()
                self.screen.status.setText(str(result))
                self.window.append_log(f"화면 확인: {result}")
        elif generation == self.generation or (kind == "disconnect" and not succeeded):
            if not succeeded:
                self.status = str(result)
                self.window.append_log(f"연결 확인: {result}")
            elif kind == "discover":
                self.targets = result
                self.target_key = result[0].key if result else ""
                self.status = f"대상 {len(result)}개 발견. 연결 확인을 눌러주세요." if result else "대상이 없습니다. 프로그램 실행/ADB 연결 상태를 확인하세요."
            elif kind == "connect":
                self.status = f"연결 성공 · {result.label}"
            elif kind == "disconnect":
                self.status = "사전 연결을 해제했습니다."
        if kind == "disconnect" and not succeeded:
            self.pending_start = None
            self.pending_reset = False
        elif self.pending_reset or self.pending_start is not None or self.shutting_down:
            if self.service.controller is not None:
                self._request("disconnect", self.service.close)
                return
            self.pending_reset = False
            if self.shutting_down:
                self.shutdown_ready.emit()
            elif self.pending_start is not None:
                callback = self.pending_start
                self.pending_start = None
                QTimer.singleShot(0, callback)
        self._sync()
        self.busy_changed.emit()

    def discover(self):
        if self.busy or self.window.isRunning or self.shutting_down:
            return
        self.stop_preview()
        self.screen.preview.set_image(QImage())
        name, preferences = self.preset_name, dict(self.preferences)
        program = self.window.settings_panel.program_settings()
        self.status = "대상을 탐색하고 있습니다…"
        self._request("discover", lambda: self.service.discover(name, preferences, program))

    def connect_target(self):
        if self.busy or self.window.isRunning or self.shutting_down:
            return
        self.stop_preview()
        name, target, preferences = self.preset_name, self.target_key, dict(self.preferences)
        self.status = "연결을 확인하고 있습니다…"
        self._request("connect", lambda: self.service.connect(name, target, preferences))

    def disconnect_target(self):
        if not self.window.isRunning:
            self.stop_preview()
            self.screen.preview.set_image(QImage())
            if self.busy:
                self.generation += 1
                self.pending_reset = True
            else:
                self._request("disconnect", self.service.close)

    def task_state_changed(self):
        self.stop_preview()
        self.screen.preview.set_image(QImage())
        self.screen.status.setText("실행 중에는 SDK의 마지막 캐시 화면을 표시합니다." if self.window.isRunning
                                   else "실행이 끝났습니다. 연결 확인 후 다시 테스트하세요.")
        self._sync()

    def prepare_task_start(self, callback):
        self.stop_preview()
        if not self.busy and self.service.controller is None:
            return False
        self.generation += 1
        self.pending_start = callback
        if not self.busy:
            self._request("disconnect", self.service.close)
        self.busy_changed.emit()
        return True

    def shutdown(self):
        self.stop_preview()
        self.shutting_down = True
        self.generation += 1
        if not self.busy and self.service.controller is not None:
            self._request("disconnect", self.service.close)
