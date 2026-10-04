"""Opt-in real SDK stop race; custom actions only, no game/window input.

Run: .venv/Scripts/python.exe UnitTest/native_controller_stop_probe.py
The parent removes temporary SDK logs only after the SDK process exits.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))
from maa.controller import Controller, CustomController
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit
from app.runtime import AppRuntime, ControllerActivitySink


class SlowController(CustomController):
    def __init__(self):
        self.entered = threading.Event()
        self.finish = threading.Event()
        self.exited = threading.Event()
        self.slow = None
        self.inactive_during_action = False
        super().__init__()

    def connect(self):
        return True

    def request_uuid(self):
        return "owned-stop-race-probe"

    def get_features(self):
        return 0

    def delay(self, action):
        if self.slow == action:
            self.entered.set()
            if not self.finish.wait(3):
                raise RuntimeError("Probe action release timed out")
            self.exited.set()

    def screencap(self):
        self.delay("capture")
        return np.zeros((180, 320, 3), dtype=np.uint8)

    def click(self, x, y):
        self.delay("click")
        return True

    def inactive(self):
        self.inactive_during_action |= self.entered.is_set() and not self.exited.is_set()
        return True


def main(log_dir):
    assert Toolkit.init_option(log_dir)
    resource = Resource()
    bundle = Path(__file__).resolve().parent / "fixtures" / "native_monitor"
    assert resource.post_bundle(bundle).wait().succeeded
    for attempt in range(6):
        owned = SlowController()
        controller = Controller(handle=owned._handle)
        runtime = AppRuntime()
        runtime.controller = controller
        runtime._controller_activity = ControllerActivitySink()
        runtime._controller_sink_id = controller.add_sink(runtime._controller_activity)
        assert runtime._controller_sink_id is not None
        assert controller.post_connection().wait().succeeded
        runtime.tasker = Tasker()
        assert runtime.tasker.bind(resource, controller)
        # Tasker is waiting in a pipeline delay while a native action is running.
        runtime.tasker.post_task("Native_Run", {"Native_Run": {"pre_delay": 2000}})
        assert runtime.tasker.running
        owned.slow = "capture" if attempt % 2 == 0 else "click"
        pending = controller.post_screencap() if owned.slow == "capture" else controller.post_click(20, 20)
        assert owned.entered.wait(2)
        result = runtime.stop_task()
        assert result[0], result
        assert not owned.exited.is_set(), "Probe must stop before native action ends"
        outcome, errors = [], []

        def cleanup():
            try:
                outcome.append(runtime.release_session())
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=cleanup)
        worker.start()
        try:
            time.sleep(0.08)
            assert worker.is_alive(), "Cleanup overtook native action"
        finally:
            owned.finish.set()
            worker.join(6)
        assert not worker.is_alive() and not errors, errors
        assert outcome and outcome[0][0], outcome
        assert owned.exited.is_set() and not owned.inactive_during_action
        assert runtime.tasker is None and runtime.controller is None
        # pending is deliberately not waited: SDK stop invalidates queued ids.
        del pending, controller, owned
        print(f"Real SDK rapid-stop {attempt + 1}: native action drained, cleanup succeeded")


if __name__ == "__main__":
    if len(sys.argv) == 2:
        main(sys.argv[1])
    else:
        with tempfile.TemporaryDirectory(prefix="maaba-controller-stop-") as log_dir:
            subprocess.run([sys.executable, str(Path(__file__).resolve()), log_dir], check=True, timeout=45)
