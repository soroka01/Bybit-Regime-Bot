"""Общие мелочи веб-бэкенда."""
from __future__ import annotations

import time


class Deadline:
    def __init__(self, seconds: float):
        self.t = time.monotonic() + seconds

    def check(self):
        if time.monotonic() > self.t:
            raise TimeoutError("превышено время расчёта")
