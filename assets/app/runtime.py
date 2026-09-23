from pathlib import Path
from typing import Callable
import csv
import json
import re
import subprocess
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

from app.pg_init import build_program_launch_command


WIN32_METHOD_PRIORITY = ("screencap", "mouse", "keyboard")
WIN32_METHOD_DEFAULTS = {
    "screencap": MaaWin32ScreencapMethodEnum.Background.name,
    "mouse": MaaWin32InputMethodEnum.PostMessageWithWindowPos.name,
    "keyboard": MaaWin32InputMethodEnum.PostMessage.name,
}
PROGRAM_LAUNCH_ENTRY = "__LaunchProgram"
PROGRAM_WINDOW_STABLE_SECONDS = 5.0
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x00000002
PROGRAM_WINDOW_POLL_INTERVAL_SECONDS = 0.05
SW_RESTORE = 9
WM_SYSCOMMAND = 0x0112
SC_MINIMIZE = 0xF020
SMTO_BLOCK_ABORTIFHUNG_ERRORONEXIT = 0x0001 | 0x0002 | 0x0020
WINDOW_MINIMIZE_MESSAGE_TIMEOUT_MS = 1500
WINDOW_MINIMIZE_CHECK_COUNT = 10
WINDOW_MINIMIZE_CHECK_INTERVAL_SECONDS = 0.1


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
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
    ]
    user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetLayeredWindowAttributes.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetLayeredWindowAttributes.restype = wintypes.BOOL
    user32.SetLayeredWindowAttributes.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_ubyte,
        wintypes.DWORD,
    ]
    user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
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

        self.interface = self._load_interface()
        self.resource_config = self._get_resource_config()
        self.controller_config, self._controller_selection_status = (
            self._select_controller_config()
        )

        self.resource = None
        self.tasker = None
        self.controller = None
        self.log_sink = LogSinkFocus()
        self._context_sink_id = None
        self._toolkit_initialized = False
        self._resource_loaded = False
        self._task_post_lock = threading.Lock()
        self._user32 = create_user32()

        self._target_hwnd = None
        self._original_window_placement = None
        self._preserve_minimized_window = False
        self._program_started_for_session = False
        self._startup_window_guard = None

    def _load_interface(self):
        with self.interface_path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _get_controller_config(self, controller_settings: dict | None = None):
        controller, _ = self._select_controller_config(controller_settings)
        return controller

    def _get_task_label(self, entry):
        for task in self.interface.get("task", []):
            if not isinstance(task, dict) or task.get("entry") != entry:
                continue
            label = task.get("label")
            if isinstance(label, str) and label.strip():
                return label.strip()
            break
        return entry

    def _select_controller_config(self, controller_settings: dict | None = None):
        controllers = self.interface.get("controller", [])
        if not isinstance(controllers, list) or not controllers:
            raise ValueError("interface.json의 controller는 비어 있지 않은 목록이어야 합니다.")

        supported_controllers = self.resource_config.get("controller")
        if supported_controllers is not None and not isinstance(supported_controllers, list):
            raise ValueError("리소스의 controller 설정은 목록이어야 합니다.")

        win32_controllers = []
        for controller in controllers:
            if not isinstance(controller, dict) or controller.get("type") != "Win32":
                continue

            controller_name = controller.get("name")
            win32_config = controller.get("win32")
            if not isinstance(controller_name, str) or not controller_name.strip():
                continue
            if not isinstance(win32_config, dict):
                continue
            if (
                supported_controllers is not None
                and controller_name not in supported_controllers
            ):
                continue
            win32_controllers.append(controller)

        if not win32_controllers:
            raise ValueError("현재 리소스에서 사용할 수 있는 Win32 컨트롤러가 없습니다.")

        if not isinstance(controller_settings, dict):
            return win32_controllers[0], "default"

        nested_settings = controller_settings.get("win32")
        if isinstance(nested_settings, dict):
            controller_settings = nested_settings

        requested_methods = {
            method: controller_settings[method]
            for method in WIN32_METHOD_PRIORITY
            if isinstance(controller_settings.get(method), str)
            and controller_settings[method]
        }
        if not requested_methods:
            return win32_controllers[0], "default"

        for controller in win32_controllers:
            win32_config = controller["win32"]
            if all(
                win32_config.get(method, WIN32_METHOD_DEFAULTS[method]) == value
                for method, value in requested_methods.items()
            ):
                return controller, "exact"

        def match_score(controller):
            win32_config = controller["win32"]
            return tuple(
                int(
                    method in requested_methods
                    and win32_config.get(method, WIN32_METHOD_DEFAULTS[method])
                    == requested_methods[method]
                )
                for method in WIN32_METHOD_PRIORITY
            )

        scores = [match_score(controller) for controller in win32_controllers]
        best_score = max(scores)
        if any(best_score):
            return win32_controllers[scores.index(best_score)], "partial"

        return win32_controllers[0], "fallback"

    def _get_resource_config(self):
        resources = self.interface.get("resource", [])
        if not resources:
            raise ValueError("interface.json에 리소스 항목이 없습니다.")

        resource = resources[0]
        paths = resource.get("path", [])
        if not isinstance(paths, list) or not paths:
            raise ValueError("설정된 리소스 경로 목록이 비어 있습니다.")
        if not all(isinstance(path, str) and path for path in paths):
            raise ValueError("모든 리소스 경로는 비어 있지 않은 문자열이어야 합니다.")

        return resource

    def _load_resource(self):
        if self._resource_loaded and self.resource is not None and self.resource.loaded:
            return True, "리소스가 이미 로드되어 있습니다."

        # 실패했던 Resource에 일부 노드가 남아 있을 수 있으므로 재시도마다 새로 만든다.
        self.resource = Resource()
        resource_paths = [
            (self.interface_path.parent / configured_path).resolve()
            for configured_path in self.resource_config["path"]
        ]

        for resource_path in resource_paths:
            job = self.resource.post_bundle(str(resource_path)).wait()
            if not job.succeeded:
                self.resource = None
                return False, f"리소스 로드에 실패했습니다: {resource_path}"

        if not self.resource.loaded:
            self.resource = None
            return False, "리소스 로드가 완료되지 않았습니다."

        self._resource_loaded = True
        return True, "리소스를 불러왔습니다."

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

    def _get_controller_log_message(self):
        screencap_name = self._get_screencap_method().name
        selection_suffix = {
            "partial": " / 설정 일부 일치",
            "fallback": " / 폴백",
        }.get(self._controller_selection_status, "")
        return f"[화면 캡처: {screencap_name}{selection_suffix}]"


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
            self._user32.ShowWindow(self._target_hwnd, SW_RESTORE)
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

    def _minimize_window_for_task(self):
        target_hwnd = self._target_hwnd
        if not target_hwnd or not self._user32.IsWindow(target_hwnd):
            return False

        # 외부 스레드에서 ShowWindow/SetForegroundWindow를 조합하지 않는다.
        # 시스템 메뉴의 최소화 요청을 게임의 창 프로시저가 처리하게 한다.
        # SendMessageTimeout의 반환값은 전달 성공, message_result는 WndProc 결과다.
        message_result = ctypes.c_size_t()
        delivered = self._user32.SendMessageTimeoutW(
            target_hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0,
            SMTO_BLOCK_ABORTIFHUNG_ERRORONEXIT, WINDOW_MINIMIZE_MESSAGE_TIMEOUT_MS,
            ctypes.byref(message_result),
        )
        if not delivered:
            return False

        for attempt in range(WINDOW_MINIMIZE_CHECK_COUNT):
            if not self._user32.IsWindow(target_hwnd):
                return False
            iconic = bool(self._user32.IsIconic(target_hwnd))
            foreground = self._user32.GetForegroundWindow()
            if iconic and foreground and foreground != target_hwnd:
                return True
            if attempt + 1 < WINDOW_MINIMIZE_CHECK_COUNT:
                time.sleep(WINDOW_MINIMIZE_CHECK_INTERVAL_SECONDS)
        return False

    def _restore_window(self):
        placement = self._original_window_placement
        target_hwnd = self._target_hwnd

        if not target_hwnd or placement is None:
            return False
        # 창이 닫혔다면 복원할 대상이 없고, API 실패 시에는 재시도할 원본을 남긴다.
        if self._user32.IsWindow(target_hwnd) and not self._user32.SetWindowPlacement(
            target_hwnd, ctypes.byref(placement)
        ):
            return False

        self._original_window_placement = None
        self._target_hwnd = None
        return True

    def _apply_startup_window_guard(self, hwnd):
        """Make a newly launched window invisible and click-through while it starts."""
        if not hwnd or not self._user32.IsWindow(hwnd):
            return False

        guard = self._startup_window_guard
        if guard is not None:
            if guard["hwnd"] != hwnd:
                if not self._restore_startup_window_guard():
                    return False
                guard = None

        if guard is None:
            ctypes.set_last_error(0)
            original_ex_style = int(
                self._user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            )
            if original_ex_style == 0 and ctypes.get_last_error() != 0:
                return False

            had_layered_style = bool(original_ex_style & WS_EX_LAYERED)
            color_key = wintypes.DWORD()
            alpha = ctypes.c_ubyte(255)
            flags = wintypes.DWORD(LWA_ALPHA)
            if had_layered_style and not self._user32.GetLayeredWindowAttributes(
                hwnd,
                ctypes.byref(color_key),
                ctypes.byref(alpha),
                ctypes.byref(flags),
            ):
                return False

            guard = {
                "hwnd": hwnd,
                "original_ex_style": original_ex_style,
                "had_layered_style": had_layered_style,
                "color_key": color_key.value,
                "alpha": alpha.value,
                "flags": flags.value,
            }
            self._startup_window_guard = guard

        ctypes.set_last_error(0)
        result = self._user32.SetWindowLongPtrW(
            hwnd,
            GWL_EXSTYLE,
            guard["original_ex_style"] | WS_EX_LAYERED | WS_EX_TRANSPARENT,
        )
        if result == 0 and ctypes.get_last_error() != 0:
            return False

        if not self._user32.SetLayeredWindowAttributes(hwnd, 0, 0, LWA_ALPHA):
            self._user32.SetWindowLongPtrW(
                hwnd, GWL_EXSTYLE, guard["original_ex_style"]
            )
            return False
        return True

    def _restore_startup_window_guard(self):
        guard = self._startup_window_guard
        if guard is None:
            return True

        hwnd = guard["hwnd"]
        if not self._user32.IsWindow(hwnd):
            self._startup_window_guard = None
            return True

        ctypes.set_last_error(0)
        result = self._user32.SetWindowLongPtrW(
            hwnd, GWL_EXSTYLE, guard["original_ex_style"]
        )
        if result == 0 and ctypes.get_last_error() != 0:
            return False

        if guard["had_layered_style"] and not self._user32.SetLayeredWindowAttributes(
            hwnd,
            guard["color_key"],
            guard["alpha"],
            guard["flags"],
        ):
            return False

        self._startup_window_guard = None
        return True

    def _prepare_started_window_for_minimized_connection(
        self,
        window,
        wait_timeout_seconds,
        cancellation_requested=None,
    ):
        self._target_hwnd = window.hwnd
        deadline = time.monotonic() + max(0, wait_timeout_seconds)
        while True:
            if cancellation_requested is not None and cancellation_requested():
                self._restore_startup_window_guard()
                return False, "프로그램 창 최소화 준비가 취소되었습니다."
            if not self._user32.IsWindow(window.hwnd):
                self._restore_startup_window_guard()
                return False, "최소화 준비 중 대상 프로그램 창이 종료되었습니다."
            if not self._apply_startup_window_guard(window.hwnd):
                self._restore_startup_window_guard()
                return False, "자동 실행 창의 입력 방지 상태를 유지하지 못했습니다."
            if self._minimize_window_for_task():
                if not self._restore_startup_window_guard():
                    return False, "최소화된 창의 원래 표시 상태를 복원하지 못했습니다."
                return True, "자동 실행 창을 최소화 준비했습니다."
            if time.monotonic() >= deadline:
                self._restore_startup_window_guard()
                return False, "대상 프로그램 창이 제한 시간 안에 최소화 가능한 상태가 되지 않았습니다."
            time.sleep(PROGRAM_WINDOW_POLL_INTERVAL_SECONDS)

    def _get_window_stability_signature(self, window):
        hwnd = window.hwnd
        if not hwnd or not self._user32.IsWindow(hwnd):
            return None

        window_rect = wintypes.RECT()
        client_rect = wintypes.RECT()
        if not self._user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
            return None
        if not self._user32.GetClientRect(hwnd, ctypes.byref(client_rect)):
            return None

        return (
            hwnd,
            getattr(window, "class_name", "") or "",
            getattr(window, "window_name", "") or "",
            int(self._user32.GetWindowLongPtrW(hwnd, GWL_STYLE)),
            int(self._user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)),
            (window_rect.left, window_rect.top, window_rect.right, window_rect.bottom),
            (client_rect.left, client_rect.top, client_rect.right, client_rect.bottom),
        )


    def _find_target_window(
        self,
        controller_settings: dict | None = None,
        wait_timeout_seconds: float = 0,
        stable_window_seconds: float = 0,
        poll_interval_seconds: float = 0.25,
        cancellation_requested: Callable[[], bool] | None = None,
    ):
        try:
            self.controller_config, self._controller_selection_status = (
                self._select_controller_config(controller_settings)
            )
        except ValueError as error:
            return None, str(error)

        window_keyword = self._get_window_keyword()
        if not window_keyword:
            return None, "Win32 window_regex 설정이 비어 있습니다."

        try:
            window_pattern = re.compile(window_keyword, re.IGNORECASE)
        except re.error as error:
            return None, f"Win32 window_regex가 올바르지 않습니다: {error}"

        deadline = time.monotonic() + max(0, wait_timeout_seconds)
        stable_signature = None
        stable_since = None
        window = None
        while True:
            if cancellation_requested is not None and cancellation_requested():
                return None, "프로그램 창 확인이 취소되었습니다."
            now = time.monotonic()
            windows = Toolkit.find_desktop_windows() or []
            candidates = [
                window
                for window in windows
                if window_pattern.search(window.window_name or "")
            ]
            if candidates:
                candidate = candidates[0]
                if stable_window_seconds <= 0:
                    window = candidate
                    break
                signature = self._get_window_stability_signature(candidate)
                if signature is None:
                    stable_signature = None
                    stable_since = None
                elif signature != stable_signature:
                    stable_signature = signature
                    stable_since = now
                elif now - stable_since >= stable_window_seconds:
                    window = candidate
                    break
            else:
                stable_signature = None
                stable_since = None
            if now >= deadline:
                return None, "대상 프로그램 창을 찾지 못했습니다."
            time.sleep(max(0.01, poll_interval_seconds))

        return window, "프로그램 창을 확인했습니다."

    def _create_controller(
        self,
        controller_settings: dict | None = None,
        wait_timeout_seconds: float = 0,
        stable_window_seconds: float = 0,
        window=None,
        cancellation_requested: Callable[[], bool] | None = None,
    ):
        if window is None:
            window, message = self._find_target_window(
                controller_settings,
                wait_timeout_seconds=wait_timeout_seconds,
                stable_window_seconds=stable_window_seconds,
                cancellation_requested=cancellation_requested,
            )
            if window is None:
                return False, message
        else:
            try:
                self.controller_config, self._controller_selection_status = (
                    self._select_controller_config(controller_settings)
                )
            except ValueError as error:
                return False, str(error)
        self._target_hwnd = window.hwnd

        controller = Win32Controller(
            hWnd=window.hwnd,
            screencap_method=self._get_screencap_method(),
            mouse_method=self._get_mouse_method(),
            keyboard_method=self._get_keyboard_method(),
        )

        self.controller = controller

        return True, "컨트롤러를 생성했습니다."

    @staticmethod
    def _program_is_running(process_name):
        try:
            result = subprocess.run(
                [
                    "tasklist",
                    "/FI",
                    f"IMAGENAME eq {process_name}",
                    "/FO",
                    "CSV",
                    "/NH",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0:
            return False
        return any(
            row and row[0].casefold() == process_name.casefold()
            for row in csv.reader(result.stdout.splitlines())
        )

    def _launch_program(self, program_settings):
        self._program_started_for_session = False
        if not isinstance(program_settings, dict):
            return False, "프로그램 실행 설정이 없습니다."
        resolved_path = program_settings.get("resolved_path")
        if not isinstance(resolved_path, str) or not resolved_path.strip():
            return False, "실행할 프로그램 경로가 확인되지 않았습니다."
        executable_path = Path(resolved_path).resolve()
        if not executable_path.is_file():
            return False, f"실행 파일을 찾을 수 없습니다: {executable_path}"

        if self._program_is_running(executable_path.name):
            return True, "대상 프로그램이 이미 실행 중입니다."

        try:
            launch_command, working_directory = build_program_launch_command(
                executable_path
            )
            subprocess.Popen(
                launch_command,
                cwd=str(working_directory),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            return False, f"프로그램 실행에 실패했습니다: {error}"
        self._program_started_for_session = True
        return True, "대상 프로그램을 실행했습니다."
        
    def _execute_controller(self):
        if self.controller is None:
            return False, "컨트롤러가 생성되지 않았습니다."

        job = self.controller.post_connection().wait()
        if not job.succeeded or not self.controller.connected:
            return False, "컨트롤러 연결에 실패했습니다."

        return True, "컨트롤러를 연결했습니다."

    def _bind_tasker(self):
        if self.controller is None or self.resource is None:
            return False, "리소스 또는 컨트롤러가 생성되지 않았습니다."

        if not self.tasker.bind(self.resource, self.controller):
            return False, "Tasker 연결에 실패했습니다."

        if not self.tasker.inited:
            return False, "연결 후 Tasker가 초기화되지 않았습니다."

        if self._context_sink_id is None:
            sink_id = self.tasker.add_context_sink(self.log_sink)
            if sink_id is None:
                return False, "로그 콜백 연결에 실패했습니다."
            self._context_sink_id = sink_id

        return True, "Tasker를 연결했습니다."
    
    

    # winUI.py에서 호출하는 함수
    def initialize(
        self,
        controller_settings: dict | None = None,
        program_settings: dict | None = None,
        execution_queue: list[tuple[str, dict]] | None = None,
        minimize_window: bool = False,
        cancellation_requested: Callable[[], bool] | None = None,
    ):
        released, release_message = self.release_session()
        if not released:
            return False, release_message

        initialized = False
        try:
            if not self._toolkit_initialized:
                if not Toolkit.init_option(str(self.user_dir)):
                    return False, "MaaFW Toolkit 초기화에 실패했습니다."
                self._toolkit_initialized = True

            loaded, load_message = self._load_resource()
            if not loaded:
                return False, load_message

            program_launch_requested = any(
                entry == PROGRAM_LAUNCH_ENTRY
                for entry, _override in (execution_queue or [])
            )
            pipeline_task_requested = any(
                entry != PROGRAM_LAUNCH_ENTRY
                for entry, _override in (execution_queue or [])
            )
            wait_timeout_seconds = 0
            stable_window_seconds = 0
            prepared_window = None
            if program_launch_requested:
                started, start_message = self._launch_program(program_settings)
                if not started:
                    return False, start_message
                wait_timeout_seconds = float(program_settings.get("startup_wait_seconds", 60))
                if self._program_started_for_session:
                    stable_window_seconds = PROGRAM_WINDOW_STABLE_SECONDS
                if (
                    self._program_started_for_session
                    and minimize_window
                    and pipeline_task_requested
                ):
                    prepared_window, window_message = self._find_target_window(
                        controller_settings,
                        wait_timeout_seconds=wait_timeout_seconds,
                        poll_interval_seconds=PROGRAM_WINDOW_POLL_INTERVAL_SECONDS,
                        cancellation_requested=cancellation_requested,
                    )
                    if prepared_window is None:
                        return False, window_message
                    prepared, prepare_message = (
                        self._prepare_started_window_for_minimized_connection(
                            prepared_window,
                            wait_timeout_seconds,
                            cancellation_requested,
                        )
                    )
                    if not prepared:
                        return False, prepare_message
                    wait_timeout_seconds = 0
                    stable_window_seconds = 0

            if cancellation_requested is not None and cancellation_requested():
                return False, "작업 시작이 취소되었습니다."

            # 시작 최소화 창은 클릭을 받지 않는 상태에서 실제 최소화가 확인된 뒤
            # 연결한다. MaaFW는 연결 중 첫 캡처에서 자체 pseudo-minimize를 이어받는다.
            created, create_message = self._create_controller(
                controller_settings,
                wait_timeout_seconds=wait_timeout_seconds,
                stable_window_seconds=stable_window_seconds,
                window=prepared_window,
                cancellation_requested=cancellation_requested,
            )
            if not created:
                return False, create_message

            executed, execute_message = self._execute_controller()
            if not executed:
                return False, execute_message

            # 프로그램 시작/대기가 끝난 뒤에만 새 Tasker를 생성한다.
            self.tasker = Tasker()
            bound, bind_message = self._bind_tasker()
            if not bound:
                return False, bind_message
            initialized = True
        except (KeyError, TypeError, ValueError, RuntimeError, OSError) as error:
            return False, f"Runtime 초기화 중 오류가 발생했습니다: {error}"
        finally:
            if not initialized:
                released, release_message = self.release_session()
                if not released:
                    raise RuntimeError(release_message)

        return True, self._get_controller_log_message()

    def run_task(
        self,
        execution_queue: list[tuple[str, dict]] | None = None,
        minimize_window: bool = False,
        cancellation_requested: Callable[[], bool] | None = None,
    ):
        if self.tasker is None:
            return False, "Tasker가 생성되지 않았습니다."

        # 명시적인 빈 목록은 실행하지 않고, None일 때만 기본 작업 목록을 사용한다.
        if execution_queue is None:
            tasks = self.interface.get("task", [])
            if not tasks:
                return False, "interface.json에 작업 항목이 없습니다."
            
            execution_queue = []
            for task_data in tasks:
                entry = task_data.get("entry", "")
                if entry:
                    execution_queue.append((entry, {}))

        execution_queue = [
            (entry, override_data)
            for entry, override_data in execution_queue
            if entry != PROGRAM_LAUNCH_ENTRY
        ]

        if not execution_queue:
            self.release_session()
            return True, "프로그램 실행 작업을 완료했습니다."
        
        minimize_error = None

        def minimize_on_first_action():
            nonlocal minimize_error
            if cancellation_requested is not None and cancellation_requested():
                return
            try:
                if self._minimize_window_for_task():
                    self._preserve_minimized_window = True
                    return
                minimize_error = "대상 창의 최소화 또는 전경 전환을 완료하지 못했습니다."
            except Exception as error:
                minimize_error = f"대상 창 최소화 중 오류가 발생했습니다: {error}"
            # 콜백에서 wait/실행 잠금을 사용하면 Tasker 스레드와 교착할 수 있다.
            try:
                self.tasker.post_stop()
            except Exception as error:
                minimize_error += f" 작업 중지 요청에 실패했습니다: {error}"

        try:
            if not self._resize_window_for_task(1280, 720):
                return False, "대상 창의 내부 영역을 1280x720으로 조정하지 못했습니다."

            prepared_tasks = []
            for entry, override_data in execution_queue:
                if isinstance(override_data, dict):
                    override_param = override_data if override_data else None
                elif isinstance(override_data, str) and override_data.strip() and override_data != "{}":
                    try:
                        override_param = json.loads(override_data)
                    except json.JSONDecodeError as error:
                        return False, f"{entry}의 파이프라인 오버라이드가 올바르지 않습니다: {error}"
                    if not isinstance(override_param, dict):
                        return False, f"{entry}의 파이프라인 오버라이드는 JSON 객체여야 합니다."
                else:
                    override_param = None

                prepared_tasks.append((entry, override_param))

            jobs = []
            with self._task_post_lock:
                if cancellation_requested is not None and cancellation_requested():
                    return False, "작업 실행이 취소되었습니다."
                # 첫 캡처/인식 이후, 작업 로그가 출력되는 Action.Starting에서 한 번만 적용한다.
                # post_task 직후 콜백이 올 수 있으므로 제출 전에 등록한다.
                self.log_sink.set_first_action_callback(
                    minimize_on_first_action if minimize_window else None
                )

                # MaaFW는 큐가 비면 controller를 자동으로 inactive 처리한다. 모든 작업을
                # 먼저 등록해야 최소화한 Win32 창이 중간 작업에서 복원되지 않는다.
                for entry, override_param in prepared_tasks:
                    if minimize_error:
                        return False, minimize_error
                    if cancellation_requested is not None and cancellation_requested():
                        return False, "작업 실행이 취소되었습니다."
                    if override_param:
                        job = self.tasker.post_task(entry, override_param)
                    else:
                        job = self.tasker.post_task(entry)
                    jobs.append((entry, job))

            failed_labels = []
            for entry, job in jobs:
                job.wait()
                if not job.succeeded:
                    failed_labels.append(self._get_task_label(entry))

            if minimize_error:
                return False, minimize_error
            if failed_labels:
                return False, f"일부 작업에 실패했습니다: {', '.join(failed_labels)}"

            return True, "모든 작업을 완료했습니다."
        finally:
            self.log_sink.set_first_action_callback(None)
            released, release_message = self.release_session()
            if not released:
                raise RuntimeError(release_message)

    def release_session(self):
        """정지 완료 후 Tasker, 컨트롤러, 창 상태 순서로 실행 상태를 정리한다."""
        self.log_sink.set_first_action_callback(None)
        with self._task_post_lock:
            try:
                if self.tasker is not None:
                    if self.tasker.running or self.tasker.stopping:
                        if not self.tasker.post_stop().wait().succeeded:
                            return False, "정리 중 Tasker 중지에 실패했습니다."
                    if self._context_sink_id is not None:
                        self.tasker.remove_context_sink(self._context_sink_id)
                        self._context_sink_id = None

                cleanup_errors = []

                # Tasker가 보유하는 controller 참조부터 해제한다.
                self.tasker = None
                if self.controller is not None and self.controller.connected:
                    try:
                        if not self.controller.post_inactive().wait().succeeded:
                            cleanup_errors.append("정리 중 컨트롤러 비활성화에 실패했습니다.")
                    except Exception as error:
                        cleanup_errors.append(f"컨트롤러 비활성화에 실패했습니다: {error}")
                self.controller = None

                if not self._restore_startup_window_guard():
                    cleanup_errors.append("자동 실행 창의 입력 방지 상태를 해제하지 못했습니다.")

                if self._preserve_minimized_window:
                    # 사용자가 요청한 최소화 상태는 세션 정리 후에도 유지한다.
                    self._original_window_placement = None
                    self._target_hwnd = None
                    self._preserve_minimized_window = False
                elif self._original_window_placement is not None:
                    if not self._restore_window():
                        cleanup_errors.append("창을 원래 상태로 복원하지 못했습니다.")
                else:
                    self._target_hwnd = None
                self._program_started_for_session = False
                if cleanup_errors:
                    return False, " ".join(cleanup_errors)
                return True, "Runtime 실행 상태를 정리했습니다."
            except Exception as error:
                return False, f"Runtime 정리 중 오류가 발생했습니다: {error}"
        
    def stop_task(self):
        try:
            with self._task_post_lock:
                if self.tasker is None or not self.tasker.running:
                    return False, "실행 중인 Tasker가 없습니다."
                stop_job = self.tasker.post_stop()
                # 정지 완료 전에는 cleanup/다음 실행이 같은 Tasker에 접근하지 못하게 한다.
                stop_job.wait()
                if not stop_job.succeeded:
                    return False, "Tasker 중지에 실패했습니다."
            return True, "작업 중지 중입니다..."
        except Exception as error:
            return False, f"Tasker 중지 중 오류가 발생했습니다: {error}"
        

# Focus 콜백, 기타 콜백이 필요하면 수정해서 추가
class LogSinkFocus(ContextEventSink):
    def __init__(self):
        super().__init__()
        self.log_callback: Callable[[str], None] | None = None
        self._first_action_callback: Callable[[], None] | None = None
        self._first_action_lock = threading.Lock()

    def set_first_action_callback(self, callback: Callable[[], None] | None):
        with self._first_action_lock:
            self._first_action_callback = callback

    def set_log_callback(self, log_callback: Callable[[str], None] | None):
        self.log_callback = log_callback

    def _emit(self, message: str):
        if self.log_callback is not None:
            self.log_callback(message)

    def on_raw_notification(self, context, msg: str, details: dict):
        if msg == "Node.Action.Starting":
            with self._first_action_lock:
                callback = self._first_action_callback
                self._first_action_callback = None
            if callback is not None:
                callback()

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
