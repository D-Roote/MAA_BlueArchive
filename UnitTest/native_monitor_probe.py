"""Opt-in Windows SDK check against an owned, nonactivating test window only.

Run separately from offscreen unittest: python UnitTest/native_monitor_probe.py
Never discovers game windows or posts game input.
"""
import ctypes
from contextlib import nullcontext
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel
from maa.toolkit import Toolkit
from app.monitoring import ConnectionTarget, MonitoringService, PreviewNotReady, window_process_info
from app.runtime import AppRuntime, WindowPlacement


def main(temp):
    app = QApplication([])
    window = QLabel("Owned SDK capture regression window")
    window.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnBottomHint)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    window.resize(320, 180)
    # Windows deliberately clamps off-screen WindowPlacement on restoration.
    window.move(20, 20)
    window.show()
    app.processEvents()
    hwnd = int(window.winId())
    runtime = AppRuntime()
    api = runtime._user32
    preset = {"name": "NativeProbe", "type": "Win32", "win32": {
        "screencap": "PrintWindow", "mouse": "PostMessage", "keyboard": "PostMessage"}}
    runtime._select_controller_config = lambda settings: (preset, "exact")

    def native(call):
        result, errors = [], []
        def run():
            try:
                result.append(call())
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        deadline = time.monotonic() + 8
        while worker.is_alive():
            app.processEvents()
            if time.monotonic() >= deadline:
                raise TimeoutError("Owned-window SDK operation timed out")
            time.sleep(0.005)
        if errors:
            raise errors[0]
        return result[0]

    def placement():
        state = WindowPlacement()
        state.length = ctypes.sizeof(WindowPlacement)
        assert api.GetWindowPlacement(hwnd, ctypes.byref(state))
        return (state.show_cmd, tuple(getattr(state.normal_position, k)
                for k in ("left", "top", "right", "bottom")))

    try:
        # Parent owns temporary log cleanup after this SDK process has exited.
        with nullcontext(temp):
            assert Toolkit.init_option(temp)
            for originally_minimized in (False, True):
                native(lambda: api.ShowWindow(hwnd, 7 if originally_minimized else 4))
                original = placement()
                assert native(lambda: runtime._create_controller(window=SimpleNamespace(hwnd=hwnd)))[0]
                assert native(runtime._resize_window_for_task)
                assert native(runtime._execute_controller)[0]
                minimized = native(runtime._minimize_window_for_task)
                print(f"Owned minimize result={minimized} iconic={api.IsIconic(hwnd)} foreground={api.GetForegroundWindow()} target={hwnd}")
                assert minimized
                frame = native(lambda: runtime.controller.post_screencap().wait().get())
                assert frame is not None and frame.size
                assert native(runtime.release_session)[0]
                assert placement() == original, (original, placement())
                print(f"SDK task cleanup restored original placement minimized={originally_minimized}")

            pid, path = window_process_info(hwnd)
            service = MonitoringService({"controller": [preset]}, {}, temp)
            target = ConnectionTarget(str(hwnd), "owned probe", "Win32", hwnd, process_path=path, pid=pid)
            service.targets = [target]
            service._discovered_preset = preset["name"]
            original = placement()
            try:
                native(lambda: service.connect(preset["name"], target.key))
                native(service.close)
                assert placement() == original
                try:
                    service.ensure_preview_available(target.key)
                except PreviewNotReady:
                    print("Idle preview correctly waits for the minimized owned window")
                else:
                    raise AssertionError("Idle preview must not unminimize the target")
            finally:
                native(service.close)
    finally:
        native(runtime.release_session)
        window.close()
        app.processEvents()


if __name__ == "__main__":
    if len(sys.argv) == 2:
        main(sys.argv[1])
    else:
        with tempfile.TemporaryDirectory(prefix="maaba-native-monitor-") as temp:
            subprocess.run([sys.executable, str(Path(__file__).resolve()), temp], check=True, timeout=30)
