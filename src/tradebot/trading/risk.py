"""Риск: kill-switch по просадке от пика и дневному убытку (UTC) — прямо на totalEquity кошелька Bybit."""
from __future__ import annotations


def update_peak(peak: float | None, equity: float) -> float:
    return equity if peak is None else max(peak, equity)


def check_kill(equity: float, peak: float | None, day_start: float | None, kill_dd: float, kill_daily: float) -> str | None:
    """Причина срабатывания kill-switch или None. equity — полная (с нереализованным PnL); peak/day_start — значения
    кошелька из kv (peak_equity, day_start_equity). Пополнение/вывод счёта учитывает только «Сбросить пик»."""
    if peak and peak > 0:
        dd = (peak - equity) / peak
        if dd >= kill_dd:
            return f"просадка от пика {dd:.1%} >= {kill_dd:.0%}"
    if day_start and day_start > 0:
        loss = (day_start - equity) / day_start
        if loss >= kill_daily:
            return f"дневной убыток {loss:.1%} >= {kill_daily:.0%}"
    return None


def stop_risk(stop_pct: float, leverage: float, sum_weights: float) -> float:
    """Доля счёта, которую теряет полная позиция при срабатывании биржевого стопа: стоп * плечо * сумма весов.
    Позиция растёт до sum_weights * (весь счёт) * плечо, поэтому цена, прошедшая stop_pct, съедает эту долю equity."""
    return stop_pct * leverage * sum_weights


def stop_risk_breach(stop_pct: float, leverage: float, sum_weights: float, kill_dd: float,
                     kill_daily: float) -> tuple[float, float, str] | None:
    """(потеря, лимит, какой лимит) если один стоп уже >= меньшего из лимитов kill-switch, иначе None.
    Сначала сработает биржевой стоп, а сразу следом — kill-switch."""
    loss = stop_risk(stop_pct, leverage, sum_weights)
    limit, name = (kill_dd, "просадки от пика") if kill_dd <= kill_daily else (kill_daily, "убытка за сутки")
    return (loss, limit, name) if loss >= limit - 1e-9 else None
