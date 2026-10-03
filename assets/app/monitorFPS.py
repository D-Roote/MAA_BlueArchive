"""Measure new frames actually painted, rather than capture requests or repaints."""
from math import ceil, isclose
from time import monotonic


class DisplayFPS:
    def __init__(self, clock=monotonic):
        self.clock = clock
        self.reset()

    def reset(self):
        self.started = self.clock()
        self.frames = 0
        self.actual = None

    def presented(self):
        self.frames += 1

    def sample(self):
        now = self.clock()
        elapsed = now - self.started
        if elapsed < 1:
            return False
        self.actual = self.frames / elapsed
        self.started = now
        self.frames = 0
        return True

    def describe(self, target):
        if self.actual is None:
            return f"목표 {target} FPS  출력 측정 중"
        text = f"목표 {target} FPS  출력 {self.actual:.1f} FPS"
        if target <= 15:
            return text
        difference = abs(self.actual - target)
        # Compare FPS counts, not percentages. Low targets tolerate timer jitter.
        threshold = max(1, ceil(target / 4))
        if difference >= threshold or isclose(difference, threshold, abs_tol=1e-9):
            reason = "성능 미달" if self.actual < target else "FPS 편차"
            text += f"  {reason}  {difference:.1f} FPS 차이"
        return text
