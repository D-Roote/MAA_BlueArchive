"""Opt-in Windows SDK check against an owned test window only.

Run separately from offscreen unittest: python UnitTest/native_monitor_probe.py
Never discovers or controls game windows. Clicks only its own test label.
"""
import ctypes
from contextlib import nullcontext
import json
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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel
from maa.toolkit import Toolkit
from maa.resource import Resource
from app.monitoring import ConnectionTarget, MonitoringService, PreviewNotReady, window_process_info
from app.runtime import AppRuntime, Tasker, WindowPlacement


class ProbeLabel(QLabel):
    clicks = 0

    def mousePressEvent(self, event):
        self.clicks += 1
        print(json.dumps({"clicks": self.clicks}), flush=True)
        super().mousePressEvent(event)


def owned_window():
    """Keep the target outside the SDK process, as with actual game input."""
    app = QApplication([])
    window = ProbeLabel("Owned SDK capture regression window")
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    window.resize(320, 180)
    window.move(20, 20)
    window.show()
    print(json.dumps({"hwnd": int(window.winId())}), flush=True)
    # Even if the SDK probe times out, this owned target exits with its parent.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    parent = kernel.OpenProcess(0x00100000, False, os.getppid())
    assert parent, "Cannot watch owned parent process"
    phase = False
    def tick():
        nonlocal phase
        if kernel.WaitForSingleObject(parent, 0) == 0:
            app.quit()
            return
        phase = not phase
        window.setStyleSheet("background: #204080;" if phase else "background: #e0d090;")
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(250)
    try:
        app.exec()
    finally:
        kernel.CloseHandle(parent)


class OwnedWindowProcess:
    def __init__(self):
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(Path(__file__).resolve()), "--owned-window"],
            stdout=subprocess.PIPE, text=True)
        self.hwnd = json.loads(self.process.stdout.readline())["hwnd"]
        self.clicks = 0
        def read_clicks():
            for line in self.process.stdout:
                self.clicks = json.loads(line)["clicks"]
        self.reader = threading.Thread(target=read_clicks, daemon=True)
        self.reader.start()

    def winId(self):
        return self.hwnd

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()  # Only the Popen-owned fixture process.
        self.process.wait(3)
        self.reader.join(3)
        self.process.stdout.close()


def main(temp):
    app = QApplication([])
    launcher = QLabel("Owned MAA start-window stand-in")
    launcher.resize(250, 100)
    launcher.move(400, 20)
    launcher.show()
    window = OwnedWindowProcess()
    app.processEvents()
    hwnd = int(window.winId())
    runtime = AppRuntime()
    api = runtime._user32
    launcher_hwnd = int(launcher.winId())
    assert api.SetForegroundWindow(launcher_hwnd), "Interactive desktop is required for this probe"
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

            # Restore and restart WITHOUT user clicks/focus between runs.
            # Actual SDK clicks expose its background WM_ACTIVATE/WA_ACTIVE path.
            resource_dir = Path(__file__).resolve().parent / "fixtures" / "native_monitor"
            runtime.resource = Resource()
            assert native(lambda: runtime.resource.post_bundle(resource_dir).wait().succeeded)
            pipeline = json.loads((resource_dir / "pipeline" / "probe.json").read_text(encoding="utf-8"))
            preset["win32"]["mouse"] = "PostMessageWithWindowPos"
            for attempt in range(1, 13):
                # Starting from MAA's active window gives Windows legitimate
                # foreground permission, just as clicking the real Start button.
                assert api.SetForegroundWindow(launcher_hwnd), "Start-window focus was denied"
                native(lambda: api.ShowWindow(hwnd, 4))
                original = placement()
                created = native(lambda: runtime._create_controller(
                    window=SimpleNamespace(hwnd=hwnd), prepare_minimize=True))
                assert created[0], (created, api.GetForegroundWindow(), hwnd, ctypes.get_last_error())
                assert native(runtime._resize_window_for_task)
                assert native(runtime._execute_controller)[0]
                runtime.tasker = Tasker()
                assert native(runtime._bind_tasker)[0]
                outcome, errors = [], []
                clicks_before = window.clicks
                def execute():
                    try:
                        outcome.append(runtime.run_task([("Native_Run", pipeline)], minimize_window=True))
                    except BaseException as error:
                        errors.append(error)
                runner = threading.Thread(target=execute, daemon=True)
                runner.start()
                preview_done = threading.Event()
                preview_frames, preview_errors = [], []
                def preview():
                    while not preview_done.is_set():
                        started = time.monotonic()
                        try:
                            frame = runtime.capture_preview_frame()
                            preview_frames.append(float(frame.mean()))
                        except PreviewNotReady:
                            pass  # Initialization/completion is retryable.
                        except BaseException as error:
                            preview_errors.append(error)
                            return
                        preview_done.wait(max(0.001, 1 / 30 - (time.monotonic() - started)))
                preview_worker = threading.Thread(target=preview, daemon=True)
                preview_worker.start()
                deadline = time.monotonic() + 8
                stopped = False
                checks = 0
                began = time.monotonic()
                while runner.is_alive():
                    app.processEvents()
                    elapsed = time.monotonic() - began
                    if ((checks == 0 and elapsed >= 0.5) or
                            (checks == 1 and elapsed >= 1.0 and window.clicks > clicks_before)):
                        color, alpha, flags = ctypes.c_ulong(), ctypes.c_ubyte(), ctypes.c_ulong()
                        pseudo = api.GetLayeredWindowAttributes(
                            hwnd, ctypes.byref(color), ctypes.byref(alpha), ctypes.byref(flags))
                        assert api.IsIconic(hwnd) or (pseudo and alpha.value == 0 and flags.value & 2), attempt
                        checks += 1
                    if attempt > 6 and not stopped and elapsed >= 1.1:
                        assert native(runtime.stop_task)[0]
                        stopped = True
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Repeated owned task timed out")
                    time.sleep(0.005)
                preview_done.set()
                preview_worker.join(3)
                assert not preview_worker.is_alive() and not preview_errors, preview_errors
                assert len(preview_frames) >= 2 and max(preview_frames) - min(preview_frames) > 1, preview_frames
                assert runtime._preview_capture_idle.is_set()
                if errors:
                    raise errors[0]
                assert outcome and checks == 2, (outcome, attempt, checks)
                assert window.clicks > clicks_before, (attempt, window.clicks, clicks_before)
                if not stopped:
                    assert outcome[0][0], outcome
                # Stopping interrupts the pipeline; a failed task result is expected.
                assert runtime.tasker is None and runtime.controller is None
                assert not runtime._minimize_focus_prepared
                assert placement() == original, (original, placement())
                print(f"Repeated SDK task {attempt} shared preview={len(preview_frames)} fresh frames, minimized before/after SDK click and restored manual_stop={stopped}")

            # Diagnostics are requested from the settings/start window, not
            # from the target window that the SDK just sent input messages to.
            assert api.SetForegroundWindow(launcher_hwnd)
            native(lambda: api.ShowWindow(hwnd, 7))
            pid, path = window_process_info(hwnd)
            service = MonitoringService({"controller": [preset]}, {}, temp)
            target = ConnectionTarget(str(hwnd), "owned probe", "Win32", hwnd, process_path=path, pid=pid)
            service.targets = [target]
            service._discovered_preset = preset["name"]
            original = placement()
            try:
                native(lambda: service.connect(preset["name"], target.key))
                native(service.close)
                assert placement() == original, (original, placement(), api.GetForegroundWindow(), hwnd)
                try:
                    service.ensure_preview_available(target.key)
                except PreviewNotReady:
                    print("Idle preview correctly waits for the minimized owned window")
                else:
                    raise AssertionError("Idle preview must not unminimize the target")
            finally:
                native(service.close)
    finally:
        try:
            native(runtime.release_session)
        finally:
            window.close()
            launcher.close()
            app.processEvents()


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--owned-window":
        owned_window()
    elif len(sys.argv) == 2:
        main(sys.argv[1])
    else:
        with tempfile.TemporaryDirectory(prefix="maaba-native-monitor-") as temp:
            with subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), temp]) as child:
                try:
                    if child.wait(timeout=60):
                        raise RuntimeError("Owned-window probe failed")
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.wait()
