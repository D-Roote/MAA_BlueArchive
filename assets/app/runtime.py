from pathlib import Path
from typing import Callable
import json
import re
import threading
import time

import ctypes
from ctypes import wintypes

from maa.context import ContextEventSink
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit


class WindowPlacement(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("show_cmd", wintypes.UINT),
        ("min_position", wintypes.POINT),
        ("max_position", wintypes.POINT),
        ("normal_position", wintypes.RECT),
    ]


def create_user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.MoveWindow.argtypes = [
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.BOOL,
    ]
    user32.MoveWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WindowPlacement)]
    user32.GetWindowPlacement.restype = wintypes.BOOL
    user32.SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WindowPlacement)]
    user32.SetWindowPlacement.restype = wintypes.BOOL
    return user32


class AppRuntime:
    def __init__(self):
        self.base_dir = Path(__file__).resolve().parent.parent
        self.resource_dir = self.base_dir / "resources"
        self.user_dir = self.base_dir / "user"
        self.interface_path = self.resource_dir / "interface.json"

        Toolkit.init_option(str(self.user_dir))

        self.interface = self._load_interface()
        self.controller_config = self._get_controller_config()

        self.resource = Resource()
        self.tasker = Tasker()
        self.controller = None
        self.log_sink = LogSinkFocus()
        self._sink_bound = False
        self._resource_loaded = False
        self._task_post_lock = threading.Lock()
        self._user32 = create_user32()

        self._target_hwnd = None
        self._original_window_placement = None

    def _load_interface(self):
        with self.interface_path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _get_controller_config(self):
        controllers = self.interface.get("controller", [])
        if not controllers:
            raise ValueError("No controller entry found in interface.json.")

        controller = controllers[0]
        if controller.get("type") != "Win32":
            raise ValueError("The configured controller is not a Win32 controller.")

        return controller

    def _load_resource(self):
        if self._resource_loaded and self.resource.loaded:
            return True, "Resource already loaded."

        job = self.resource.post_bundle(str(self.resource_dir)).wait()
        if not job.succeeded or not self.resource.loaded:
            return False, f"Resource loading failed: {self.resource_dir}"

        self._resource_loaded = True
        return True, "Resource loaded successfully."

    def _get_window_keyword(self):
        win32_config = self.controller_config.get("win32", {})
        return win32_config.get("window_regex", "")

    def _get_screencap_method(self):
        win32_config = self.controller_config.get("win32", {})
        return MaaWin32ScreencapMethodEnum[win32_config.get("screencap", "Background")]

    def _get_mouse_method(self):
        win32_config = self.controller_config.get("win32", {})
        return MaaWin32InputMethodEnum[win32_config.get("mouse", "PostMessageWithWindowPos")]

    def _get_keyboard_method(self):
        win32_config = self.controller_config.get("win32", {})
        return MaaWin32InputMethodEnum[win32_config.get("keyboard", "PostMessage")]


    def _resize_window_for_task(self, target_client_w=1280, target_client_h=720):
        if not self._target_hwnd or not self._user32.IsWindow(self._target_hwnd):
            return False

        placement = WindowPlacement()
        placement.length = ctypes.sizeof(WindowPlacement)
        if not self._user32.GetWindowPlacement(self._target_hwnd, ctypes.byref(placement)):
            return False

        if self._original_window_placement is None:
            self._original_window_placement = placement

        # 최소화 또는 최대화 상태에서는 먼저 일반 창으로 전환해야 client 크기를 맞출 수 있다.
        if self._user32.IsIconic(self._target_hwnd) or placement.show_cmd == 3:
            self._user32.ShowWindow(self._target_hwnd, 9)
            time.sleep(0.1)

        window_rect = wintypes.RECT()
        client_rect = wintypes.RECT()
        if not self._user32.GetWindowRect(self._target_hwnd, ctypes.byref(window_rect)):
            return False
        if not self._user32.GetClientRect(self._target_hwnd, ctypes.byref(client_rect)):
            return False

        current_window_w = window_rect.right - window_rect.left
        current_window_h = window_rect.bottom - window_rect.top
        current_client_w = client_rect.right - client_rect.left
        current_client_h = client_rect.bottom - client_rect.top

        border_width = current_window_w - current_client_w
        border_height = current_window_h - current_client_h
        final_window_w = target_client_w + border_width
        final_window_h = target_client_h + border_height

        return bool(
            self._user32.MoveWindow(
                self._target_hwnd,
                window_rect.left,
                window_rect.top,
                final_window_w,
                final_window_h,
                True,
            )
        )

    def _restore_window(self):
        placement = self._original_window_placement
        self._original_window_placement = None
        target_hwnd = self._target_hwnd
        self._target_hwnd = None

        if not target_hwnd or placement is None:
            return False
        if not self._user32.IsWindow(target_hwnd):
            return False

        return bool(self._user32.SetWindowPlacement(target_hwnd, ctypes.byref(placement)))


    def _create_controller(self, minimize_window: bool = False):
        windows = Toolkit.find_desktop_windows()
        if not windows:
            return False, "No desktop windows found."
        
        window_keyword = self._get_window_keyword()
        if not window_keyword:
            return False, "The Win32 window_regex must not be empty."

        try:
            window_pattern = re.compile(window_keyword, re.IGNORECASE)
        except re.error as error:
            return False, f"Invalid Win32 window_regex: {error}"

        candidates = []
        for w in windows:
            title = w.window_name or ""
            if window_pattern.search(title):
                candidates.append(w)

        if not candidates:
            return False, f" ᓀ‸ᓂ \n블루 아카이브가 실행 중이 아닙니다."
        
        window = candidates[0]
        self._target_hwnd = window.hwnd

        if minimize_window:
            screencap_mode = MaaWin32ScreencapMethodEnum.PrintWindow
            # screencap_mode = MaaWin32ScreencapMethodEnum.FramePool
            # 블루 아카이브는 최소화 모드에서 FramePool 캡쳐가 작동하지 않는듯 싶다
        else:
            screencap_mode = MaaWin32ScreencapMethodEnum.FramePool

        controller = Win32Controller(
            hWnd=window.hwnd,
            screencap_method=screencap_mode, # self._get_screencap_method() 대신 변수 사용
            mouse_method=self._get_mouse_method(),
            keyboard_method=self._get_keyboard_method(),
        )

        self.controller = controller

        return True, "Controller created successfully."
        
    def _execute_controller(self):
        if self.controller is None:
            return False, "Controller is not created."

        job = self.controller.post_connection().wait()
        if not job.succeeded or not self.controller.connected:
            return False, "Controller connection failed."

        return True, "Controller connected successfully."

    def _bind_tasker(self):
        if self.controller is None:
            return False, "Controller is not created."

        if not self.tasker.bind(self.resource, self.controller):
            return False, "Tasker binding failed."

        if not self.tasker.inited:
            return False, "Tasker is not initialized after binding."

        if not self._sink_bound:
            sink_id = self.tasker.add_context_sink(self.log_sink)
            if sink_id is None:
                return False, "Context sink binding failed."
            self._sink_bound = True

        return True, "Tasker bound successfully."
    
    

    # winUI.py에서 호출하는 함수
    def initialize(self, minimize_window: bool = False):
        try:
            loaded, load_message = self._load_resource()
            if not loaded:
                return False, load_message

            created, create_message = self._create_controller(minimize_window)
            if not created:
                return False, create_message

            executed, execute_message = self._execute_controller()
            if not executed:
                self.controller = None
                return False, execute_message

            bound, bind_message = self._bind_tasker()
            if not bound:
                self.controller = None
                return False, bind_message
        except (KeyError, TypeError, ValueError, RuntimeError, OSError) as error:
            self.controller = None
            return False, f"AppRuntime initialization failed: {error}"

        # 146 라인 수정시 같이 수정할 것
        screencap_name = "PrintWindow" if minimize_window else "FramePool"
        # mouse_name = self._get_mouse_method().name
        # keyboard_name = self._get_keyboard_method().name

        return (
            True, 
            # f"AppRuntime initialized successfully.\n"
            f"[Screen Capture : {screencap_name}]"
        )

    def run_task(
        self,
        execution_queue: list[tuple[str, dict]] | None = None,
        minimize_window: bool = False,
        cancellation_requested: Callable[[], bool] | None = None,
    ):
        if self.tasker is None:
            return False, "Tasker is not created."

        # 인수가 비어있거나 None이면 interface.json에서 백업으로 가져옴
        if execution_queue is None:
            tasks = self.interface.get("task", [])
            if not tasks:
                return False, "No task entry found in interface.json."
            
            execution_queue = []
            for task_data in tasks:
                entry = task_data.get("entry", "")
                if entry:
                    execution_queue.append((entry, {}))

        if not execution_queue:
            return False, "No valid tasks to execute."
        
        try:
            if not self._resize_window_for_task(1280, 720):
                return False, "Failed to resize the target window to a 1280x720 client area."

            if minimize_window and self._target_hwnd:
                self._user32.SetForegroundWindow(self._target_hwnd)
                time.sleep(0.1)

                self._user32.ShowWindow(self._target_hwnd, 6)

            executed_entries = []
            failed_entries = []

            for entry, override_data in execution_queue:
                if isinstance(override_data, dict):
                    override_param = override_data if override_data else None
                elif isinstance(override_data, str) and override_data.strip() and override_data != "{}":
                    try:
                        override_param = json.loads(override_data)
                    except json.JSONDecodeError as error:
                        return False, f"Invalid pipeline override for {entry}: {error}"
                    if not isinstance(override_param, dict):
                        return False, f"Pipeline override for {entry} must be a JSON object."
                else:
                    override_param = None

                with self._task_post_lock:
                    if cancellation_requested is not None and cancellation_requested():
                        return False, "Task execution cancelled."
                    if override_param:
                        job = self.tasker.post_task(entry, override_param)
                    else:
                        job = self.tasker.post_task(entry)

                executed_entries.append(entry)
                job.wait()
                if not job.succeeded:
                    failed_entries.append(entry)

            if failed_entries:
                return (
                    False,
                    f"Some tasks failed: {', '.join(failed_entries)} "
                    f"(all executed: {', '.join(executed_entries)})"
                )

            return True, f"All tasks finished: {', '.join(executed_entries)}"
        finally:
            if self.controller is not None:
                try:
                    self.controller.post_inactive().wait()
                except Exception:
                    pass
            self._restore_window()
        
    def stop_task(self):
        if self.tasker is None:
            return False, "Tasker is not created."

        try:
            with self._task_post_lock:
                if not self.tasker.running:
                    return False, "Tasker is not running."
                stop_job = self.tasker.post_stop()
            stop_job.wait()
            if not stop_job.succeeded:
                return False, "Tasker stop failed."
            self.log_sink.set_log_callback(None)
            return True, "Tasker stop requested."
        except Exception as error:
            return False, f"Tasker stop failed: {error}"
        

# Focus 콜백, 기타 콜백이 필요하면 수정해서 추가
class LogSinkFocus(ContextEventSink):
    def __init__(self):
        super().__init__()
        self.log_callback: Callable[[str], None] | None = None

    def set_log_callback(self, log_callback: Callable[[str], None] | None):
        self.log_callback = log_callback

    def _emit(self, message: str):
        if self.log_callback is not None:
            self.log_callback(message)

    def on_raw_notification(self, context, msg: str, details: dict):
        focus = details.get("focus")
        if focus is None:
            return

        if isinstance(focus, dict):
            template = focus.get(msg)
            if template is None:
                return

            if isinstance(template, dict):
                content = template.get("content", "")
                display = template.get("display", "log")
                if isinstance(display, str):
                    display_channels = [display]
                elif isinstance(display, list):
                    display_channels = display
                else:
                    display_channels = []
                if "log" not in display_channels:
                    return
            else:
                content = str(template)
        else:
            # 기존 문자열 focus는 Action 시작 시 한 번만 출력한다.
            if msg != "Node.Action.Starting":
                return
            content = str(focus)

        if not content:
            return

        try:
            rendered = content.format(**details)
        except Exception:
            # focus 템플릿에 details에 없는 플레이스홀더가 있는 등 포맷 문법 오류.
            # 콜백 스레드로 예외를 전파시키지 않고, 원본 템플릿을 그대로 표시한다.
            rendered = content

        self._emit(rendered)
