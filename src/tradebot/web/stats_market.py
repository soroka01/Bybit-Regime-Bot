"""Рынок для статистики: часовые свечи BTCUSDT + фандинг на сетке 00/08/16 UTC и оплаченный фандинг по сделке."""
from __future__ import annotations

import bisect
import math

import numpy as np

from tradebot.web.stats_base import BAR_S, PRICE_TOL_S, SETTLE_S


# ---------------------------------------------------------------- рынок (свечи + фандинг)
class Market:
    """Часовые свечи BTCUSDT (open/high/low/close, секунды старта) и фандинг на сетке 00/08/16 UTC."""

    def __init__(self, bar_t, o, h, l, c, funding, funding_until: float | None):
        self.t = np.asarray(bar_t, dtype="int64")
        self.o, self.h, self.l, self.c = (np.asarray(x, dtype="float64") for x in (o, h, l, c))
        self.close_t = self.t + BAR_S
        self.funding_until = funding_until
        f = np.nan_to_num(np.asarray(funding, dtype="float64"), nan=0.0)
        m = (self.t % SETTLE_S) == 0
        # расчёт t -> (ставка, open свечи, начинающейся в t)
        self.settle = {int(t): (float(r), float(px)) for t, r, px in zip(self.t[m], f[m], self.o[m])}
        self.candles_until = float(self.close_t[-1]) if len(self.t) else None

    def price_at(self, t: float) -> float | None:
        """Close последней ЗАКРЫТОЙ свечи на момент t; None, если такой свечи нет или она старше PRICE_TOL_S."""
        if not len(self.t):
            return None
        i = int(np.searchsorted(self.close_t, t, side="right")) - 1
        if i < 0 or t - self.close_t[i] > PRICE_TOL_S:
            return None
        return float(self.c[i])

    def bars_between(self, lo: float, hi: float) -> np.ndarray:
        """Индексы свечей, чей СТАРТ в [lo, hi]."""
        return np.flatnonzero((self.t >= lo) & (self.t <= hi))


def market_from_df(df, funding_until: float | None) -> Market:
    """DataFrame из web.data.get_df (индекс — старт свечи UTC; колонка funding — сумма расчётов в свече)."""
    from tradebot.web.serialize import secs
    return Market(secs(df.index), df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy(),
                  df["funding"].to_numpy() if "funding" in df else np.zeros(len(df)), funding_until)


def funding_between(market: Market | None, timeline: list[tuple[float, float]], t0: float, t1: float) -> tuple[float, bool]:
    """(оплаченный фандинг, полнота) за расчёты t в (t0, t1]. timeline = [(ts, позиция со знаком ПОСЛЕ fill)].
    paid = signed_qty*price_t*rate_t (лонг платит при rate>0). Расчёт без данных (нет свечи/ставки, t > funding_until) — неполнота."""
    if t1 <= t0:
        return 0.0, True
    paid, complete = 0.0, True
    times = [x[0] for x in timeline]
    for k in range(int(math.floor(t0 / SETTLE_S)) + 1, int(math.floor(t1 / SETTLE_S)) + 1):
        t = k * SETTLE_S
        rec = market.settle.get(t) if market is not None else None
        if rec is None or market.funding_until is None or t > market.funding_until:
            complete = False
            continue
        i = bisect.bisect_left(times, t) - 1          # позиция ДО fills в момент t (выход ровно в t ещё платит)
        q = timeline[i][1] if i >= 0 else 0.0
        paid += q * rec[1] * rec[0]
    return paid, complete
