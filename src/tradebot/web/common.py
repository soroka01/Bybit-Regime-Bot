"""Общие мелочи веб-бэкенда."""
from __future__ import annotations

import time

from tradebot.market import guard


class Deadline:
    def __init__(self, seconds: float):
        self.t = time.monotonic() + seconds

    def check(self):
        guard.check_cancel()          # остановка приложения прерывает расчёт так же, как исчерпанный срок
        if time.monotonic() > self.t:
            raise TimeoutError("превышено время расчёта")
