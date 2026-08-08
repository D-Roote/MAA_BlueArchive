from pathlib import Path
from datetime import datetime
import json
import cv2

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import (QTextCursor,
                           QColor, QPainter, QPen)
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (QMainWindow, QAbstractItemView, QHBoxLayout, QVBoxLayout, 
                               QListWidget, QListWidgetItem, QWidget, 
                               QButtonGroup, QCheckBox, QLabel, QPushButton, QRadioButton)

from app.runtime import AppRuntime


WINDOW_SIZE = [1200, 800]
WINDOW_TITLE = "MAA_Blue Archive"
UI_FILENAME = "baseUI.ui"
QSS_FILENAME = "style.qss"


# 동적 List 클래스
class OptionItemWidget(QWidget):
    def __init__(self, task_data, task_options, on_setting_clicked_callback, on_checkbox_toggled_callback, parent=None):
        super().__init__(parent)

        self.task_data = task_data      
        self.task_options = task_options 
        
        self.selected_options = {}
        for opt in self.task_options:
            default_val = opt.get("default")
            if default_val:
                self.selected_options[opt["name"]] = [default_val]
            else:
                self.selected_options[opt["name"]] = []
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(8)

        self.checkbox = QCheckBox("")
        self.checkbox.setFixedSize(30, 30)
        self.checkbox.toggled.connect(on_checkbox_toggled_callback)
        layout.addWidget(self.checkbox)

        display_name = task_data.get("label", task_data.get("name", "Unknown Task"))
        self.label = QLabel(display_name)
        self.label.setStyleSheet("background: transparent;")

        layout.addWidget(self.label, 1) 

        self.setting_btn = QPushButton()
        self.setting_btn.setFixedSize(10, 10)
        self.setting_btn.setContentsMargins(1, 1, 1, 1)
        
        if self.task_options:
            layout.addWidget(self.setting_btn)
            self.setting_btn.clicked.connect(lambda: on_setting_clicked_callback(self))
        else:
            self.setting_btn.hide()

    def is_checked(self):
        return self.checkbox.isChecked()

# 커스텀 리스트 위젯
class DragDropListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDropIndicatorShown(False)
        self.drag_line_y = -1

    def dragMoveEvent(self, event):
        super().dragMoveEvent(event)
        if event.isAccepted():
            pos = event.position().toPoint()
            item = self.itemAt(pos)
            if item:
                rect = self.visualItemRect(item)
                if pos.y() < rect.y() + rect.height() / 2:
                    self.drag_line_y = rect.y()
                else:
                    self.drag_line_y = rect.y() + rect.height()
            else:
                if self.count() > 0:
                    last_rect = self.visualItemRect(self.item(self.count() - 1))
                    self.drag_line_y = last_rect.y() + last_rect.height()
                else:
                    self.drag_line_y = 0
            self.viewport().update()
        else:
            self.drag_line_y = -1
            self.viewport().update()

    def dragLeaveEvent(self, event):
        self.drag_line_y = -1
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.drag_line_y = -1
        self.viewport().update()
        super().dropEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        
        if self.drag_line_y != -1:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.Antialiasing)
            
            # 사이 가로줄 색, 두께 하드 코딩
            pen = QPen(QColor("#00AEEF"), 2)
            painter.setPen(pen)
            
            painter.drawLine(0, self.drag_line_y, self.viewport().width(), self.drag_line_y)
            painter.end()

# 메인 윈도우
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        ui_path = Path(__file__).resolve().parent.parent / "pySide6" / UI_FILENAME
        qss_path = Path(__file__).resolve().parent.parent / "pySide6" / QSS_FILENAME
        loader = QUiLoader()
        self.ui = loader.load(str(ui_path), self)

        if qss_path.exists():
            with open(qss_path, "r", encoding="utf-8") as f:
                qss_content = f.read()
                self.setStyleSheet(qss_content)

        self.setCentralWidget(self.ui.centralwidget)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WINDOW_SIZE[0], WINDOW_SIZE[1])

        self.runtime = AppRuntime()
        self.log_sink = self.runtime.log_sink
        self.worker = None
        self.isRunning = False

        self.setup_connections()

        self.setup_dynamic_options()
        self.check_start_button_state()

    def setup_connections(self):
        self.ui.workStartBtn.clicked.connect(self.on_task_start)

    def append_log(self, message):
        current_time = datetime.now().strftime("%H:%M:%S")
        time_text = f"[{current_time}] "
        
        cursor = self.ui.logPrintText.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.ui.logPrintText.setTextCursor(cursor)
        
        block_format = cursor.blockFormat()
        block_format.setAlignment(Qt.AlignLeft)
        
        block_format.setTopMargin(0)
        block_format.setBottomMargin(0)
        
        block_format.setLeftMargin(0)
        block_format.setTextIndent(0)
        cursor.setBlockFormat(block_format)
        cursor.insertText(time_text)
        
        block_format.setLeftMargin(58)    
        block_format.setTextIndent(-58)  
        cursor.setBlockFormat(block_format)
        
        cursor.insertText(message + "\n")
        
        self.ui.logPrintText.ensureCursorVisible()

    def on_task_start(self):
        self.append_log("작업을 시작합니다...")
        self.ui.workStartBtn.setEnabled(False)

        self.save_user_config()

        execution_queue = self.build_execution_queue()

        minimize_window = False
        if hasattr(self.ui, 'minimizeEnableBtn'):
            minimize_window = self.ui.minimizeEnableBtn.isChecked()

        self.worker = RuntimeWorker(self.runtime, execution_queue, minimize_window)

        self.worker.log.connect(self.append_log)
        self.worker.task_finished.connect(self.on_task_finished)

        self.runtime.log_sink.set_log_callback(self.worker.log.emit)

        self.worker.start()

        self.isRunning = True

        self.ui.workStartBtn.setText("작업 중지")
        self.ui.workStartBtn.clicked.disconnect(self.on_task_start)
        self.ui.workStartBtn.clicked.connect(self.on_task_stop)
        self.ui.workStartBtn.setEnabled(True)

    def on_task_stop(self):
        self.ui.workStartBtn.setEnabled(False)

        if not self.worker:
            return
        
        self.worker.stop_runtime()

    def on_task_finished(self):
        self.isRunning = False
        self.runtime.log_sink.set_log_callback(None)
        self.ui.workStartBtn.setText("작업 시작")
        self.ui.workStartBtn.clicked.disconnect(self.on_task_stop)
        self.ui.workStartBtn.clicked.connect(self.on_task_start)
        self.ui.workStartBtn.setEnabled(True)
        
        self.append_log("작업이 종료 되었습니다.\n")

    def setup_dynamic_options(self):
        self.option_list_widget = DragDropListWidget()
        
        self.option_list_widget.setDragDropMode(QAbstractItemView.InternalMove)
        self.option_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)

        target_layout = self.ui.settingStartWidget_1
        if isinstance(target_layout, QWidget):
            if target_layout.layout() is None:
                QVBoxLayout(target_layout)
            target_layout = target_layout.layout()
            
        target_layout.insertWidget(0, self.option_list_widget)
        target_layout.setStretchFactor(self.option_list_widget, 1)

        raw_tasks = self.runtime.interface.get("task", [])
        options_dict = {opt["name"]: opt for opt in self.runtime.interface.get("option", [])}
        
        task_dict = {t["entry"]: t for t in raw_tasks}

        user_config_path = self.runtime.user_dir / "config" / "user_config.json"
        user_config = {}
        if user_config_path.exists():
            try:
                with open(user_config_path, "r", encoding="utf-8") as f:
                    user_config = json.load(f)
            except Exception as e:
                print(f"설정 파일 로드 실패: {e}")

        minimize_enabled = user_config.get("minimize_enabled", False)
        if hasattr(self.ui, 'minimizeEnableBtn'):
            self.ui.minimizeEnableBtn.setChecked(minimize_enabled)

        saved_tasks = user_config.get("tasks", [])
        added_entries = set()

        def add_task_widget(task_data, is_checked, saved_options):
            item = QListWidgetItem()
            item.setFlags(item.flags() & ~Qt.ItemIsDropEnabled)
            self.option_list_widget.addItem(item)
            
            task_options = []
            if "option" in task_data:
                for opt_name in task_data["option"]:
                    if opt_name in options_dict:
                        task_options.append(options_dict[opt_name])

            custom_widget = OptionItemWidget(
                task_data, 
                task_options, 
                self.show_sub_cases, 
                self.check_start_button_state
            )
            
            if saved_options:
                for opt_name in custom_widget.selected_options.keys():
                    if opt_name in saved_options:
                        custom_widget.selected_options[opt_name] = saved_options[opt_name]
            
            custom_widget.checkbox.setChecked(is_checked)
            custom_widget.adjustSize()
            item.setSizeHint(custom_widget.sizeHint())
            
            self.option_list_widget.setItemWidget(item, custom_widget)
            added_entries.add(task_data["entry"])

        for saved_task in saved_tasks:
            entry = saved_task.get("entry")
            if entry in task_dict:
                add_task_widget(
                    task_dict[entry],
                    saved_task.get("checked", True),
                    saved_task.get("selected_options", None)
                )

        for task_data in raw_tasks:
            if task_data["entry"] not in added_entries:
                add_task_widget(task_data, True, None)

        self.check_start_button_state()

    def show_sub_cases(self, item_widget):
        task_options = item_widget.task_options

        container_widget = self.ui.scrollSettingContents
        layout = container_widget.layout()
        
        if layout is None:
            layout = QVBoxLayout(container_widget)
            layout.setContentsMargins(10, 10, 10, 10)
            
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        for opt in task_options:
            opt_name = opt["name"]
            opt_type = opt.get("type", "select")
            
            title_label = QLabel(f"[{opt.get('label', opt['name'])}]")
            title_label.setStyleSheet("font-weight: bold; font-size: 14px; margin-top: 10px;")
            title_label.setWordWrap(True)
            layout.addWidget(title_label)

            cases = opt.get("cases", [])

            if opt_type == "select":
                button_group = QButtonGroup(container_widget)
                
                for case in cases:
                    case_name = case["name"]

                    row_widget = QWidget()
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 2, 0, 2)
                    row_layout.setSpacing(8)

                    radio_btn = QRadioButton("")
                    button_group.addButton(radio_btn)
                    
                    if case_name in item_widget.selected_options.get(opt_name, []):
                        radio_btn.setChecked(True)

                    case_label = QLabel(case.get('label', case_name))
                    case_label.setStyleSheet("background: transparent;")
                    case_label.setWordWrap(True) 
                    
                    def make_radio_slot(w, o_name, c_name):
                        return lambda checked: self.update_widget_option_radio(w, o_name, c_name, checked)
                        
                    radio_btn.toggled.connect(make_radio_slot(item_widget, opt_name, case_name))
                    
                    row_layout.addWidget(radio_btn)
                    row_layout.addWidget(case_label, 1) # 🌟 stretch=1
                    layout.addWidget(row_widget)

            elif opt_type == "checkbox":
                for case in cases:
                    case_name = case["name"]
                    
                    row_widget = QWidget()
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 2, 0, 2)
                    row_layout.setSpacing(8)

                    case_cb = QCheckBox("")
                    
                    if case_name in item_widget.selected_options.get(opt_name, []):
                        case_cb.setChecked(True)
                    
                    case_label = QLabel(case.get('label', case_name))
                    case_label.setStyleSheet("background: transparent;")
                    case_label.setWordWrap(True) 
                    
                    def make_checkbox_slot(w, o_name, c_name):
                        return lambda checked: self.update_widget_option_checkbox(w, o_name, c_name, checked)
                    
                    case_cb.toggled.connect(make_checkbox_slot(item_widget, opt_name, case_name))
                    
                    row_layout.addWidget(case_cb)
                    row_layout.addWidget(case_label, 1)
                    layout.addWidget(row_widget)

            elif opt_type == "switch":
                if cases:
                    case = cases[0]
                    case_name = case["name"]
                    
                    row_widget = QWidget()
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 2, 0, 2)
                    row_layout.setSpacing(8)

                    # 기능은 CheckBox와 동일 (추후 스타일시트로 토글 모양 변경 가능)
                    switch_cb = QCheckBox("")
                    
                    if case_name in item_widget.selected_options.get(opt_name, []):
                        switch_cb.setChecked(True)
                    
                    case_label = QLabel(case.get('label', case_name))
                    case_label.setStyleSheet("background: transparent;")
                    case_label.setWordWrap(True) 
                    
                    def make_switch_slot(w, o_name, c_name):
                        return lambda checked: self.update_widget_option_checkbox(w, o_name, c_name, checked)
                    
                    switch_cb.toggled.connect(make_switch_slot(item_widget, opt_name, case_name))
                    
                    row_layout.addWidget(switch_cb)
                    row_layout.addWidget(case_label, 1)
                    layout.addWidget(row_widget)

            # 필요시 작성
            elif opt_type == "input":
                pass

        layout.addStretch()

    def update_widget_option_radio(self, widget, opt_name, case_name, is_checked):
        if is_checked:
            widget.selected_options[opt_name] = [case_name]

    def update_widget_option_checkbox(self, widget, opt_name, case_name, is_checked):
        if opt_name not in widget.selected_options:
            widget.selected_options[opt_name] = []
            
        if is_checked:
            if case_name not in widget.selected_options[opt_name]:
                widget.selected_options[opt_name].append(case_name)
        else:
            if case_name in widget.selected_options[opt_name]:
                widget.selected_options[opt_name].remove(case_name)

    def check_start_button_state(self):
        any_checked = False
        for i in range(self.option_list_widget.count()):
            item = self.option_list_widget.item(i)
            widget = self.option_list_widget.itemWidget(item)
            if widget and widget.is_checked():
                any_checked = True
                break
        self.ui.workStartBtn.setEnabled(any_checked)

    def build_execution_queue(self):
        execution_queue = []
        
        for i in range(self.option_list_widget.count()):
            item = self.option_list_widget.item(i)
            widget_in_item = self.option_list_widget.itemWidget(item)
            
            if widget_in_item and widget_in_item.is_checked():
                task_entry = widget_in_item.task_data["entry"]
                override_params = {}
                
                for opt in widget_in_item.task_options:
                    opt_name = opt["name"]
                    selected_cases = widget_in_item.selected_options.get(opt_name, [])
                    
                    if not selected_cases:
                        continue
                        
                    for case in opt.get("cases", []):
                        if case["name"] in selected_cases:
                            pipeline_override = case.get("pipeline_override", {})
                            for node_name, node_params in pipeline_override.items():
                                if node_name not in override_params:
                                    override_params[node_name] = {}
                                override_params[node_name].update(node_params)
                
                execution_queue.append((task_entry, override_params))
                
        return execution_queue

    def save_user_config(self):
        config_dir = self.runtime.user_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True) # 폴더가 없으면 자동 생성
        config_path = config_dir / "user_config.json"
        
        tasks_data = []
        
        for i in range(self.option_list_widget.count()):
            item = self.option_list_widget.item(i)
            widget = self.option_list_widget.itemWidget(item)
            if widget:
                tasks_data.append({
                    "entry": widget.task_data["entry"],
                    "checked": widget.is_checked(),
                    "selected_options": widget.selected_options
                })
                
        minimize_enabled = False
        if hasattr(self.ui, 'minimizeEnableBtn'):
            minimize_enabled = self.ui.minimizeEnableBtn.isChecked()
                
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({
                    "minimize_enabled": minimize_enabled,
                    "tasks": tasks_data
                }, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"설정 파일 저장 실패: {e}")

# Tasker 스레드
class RuntimeWorker(QThread):
    log = Signal(str)
    task_finished = Signal()

    def __init__(self, runtime, execution_queue, minimize_window=False):
        super().__init__()
        self.runtime = runtime
        self.execution_queue = execution_queue
        self.minimize_window = minimize_window

    def stop_runtime(self):
        return self.runtime.stop_task()

    def run(self):
        initialized, init_message = self.runtime.initialize(self.minimize_window)
        self.log.emit(init_message)

        if not initialized:
            self.task_finished.emit()
            return
        
        self.log.emit("▶ 작업 시작...")
        tasked, _ = self.runtime.run_task(self.execution_queue, self.minimize_window)

        if not tasked:
            self.task_finished.emit()
            return

        self.task_finished.emit()