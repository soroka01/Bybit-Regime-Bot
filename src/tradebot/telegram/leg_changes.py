"""Расчёт изменений ног (общий для подтверждения и применения): LegChange и миксин LegChanges для Controller."""
from __future__ import annotations

import math
from dataclasses import dataclass

from tradebot.core.portfolio import Leg


@dataclass
class LegChange:
    """Одно изменение ноги: было (w0, en0) -> станет (w1, en1)."""
    i: int
    leg: Leg
    w0: float
    en0: bool
    w1: float
    en1: bool

    @property
    def noop(self) -> bool:
        return self.en0 == self.en1 and abs(self.w0 - self.w1) < 1e-9

    @property
    def delta(self) -> float:
        """Вклад в сумму весов включённых: нужен для порядка применения (сначала уменьшения)."""
        return (self.w1 if self.en1 else 0.0) - (self.w0 if self.en0 else 0.0)


class LegChanges:
    """Миксин: нужны self.st (хранилище)."""

    # ---------- стратегии ----------
    def _target(self, cfg: list[tuple[Leg, float, bool]], extra: dict[str, float | None] | None = None) -> float | None:
        """Цель = Σ сигнал × вес по включённым ногам с весом > 0; None, если сигнал какой-то из них неизвестен.
        extra — сигналы, которых ещё нет в журнале (новая нога: берётся из проверки окна)."""
        sigs, t = self.st.latest_signals(), 0.0
        for leg, w, en in cfg:
            if not en or w <= 0:
                continue
            sg = extra[leg.key] if extra and leg.key in extra else sigs.get(leg.key, (None,))[0]
            if sg is None:
                return None
            t += sg * w
        return t

    def _leg_idx(self, raw: str) -> int:
        i = int(raw)
        if i >= len(self.st.leg_cfg()):
            raise ValueError("нет такой стратегии")
        return i

    # --- расчёт изменений ног (общий для подтверждения и применения) ---
    def _leg_index(self, p: tuple[str, ...]) -> int | None:
        """Индекс ноги из callback (новый lg:<i>… и старый lg:t:<i>/lg:w:<i>:<p|m>); None для lg / lg:eq."""
        if len(p) < 2 or p[0] != "lg" or p[1] == "eq":
            return None
        return self._leg_idx(p[2] if p[1] in ("t", "w") else p[1])

    @staticmethod
    def _is_leg_change(p: tuple[str, ...]) -> bool:
        if p[0] != "lg" or len(p) < 2:
            return False
        return p[1] in ("eq", "t", "w") or (len(p) >= 3 and p[2] in ("t", "w"))

    def _leg_changes(self, p: tuple[str, ...]) -> list[LegChange]:
        """Что изменится по callback (без :y). Проверяет индекс и границы веса, но не сумму."""
        cfg = self.st.leg_cfg()
        if p[1] == "eq":
            act = [(i, l, w, en) for i, (l, w, en) in enumerate(cfg) if en and w > 0]
            if not act:
                raise ValueError("нет включённых стратегий с весом больше 0% — сначала задайте вес")
            each = math.floor(100 / len(act) + 1e-9) / 100          # целые проценты вниз: сумма ≤ 100%
            return [LegChange(i, l, w, en, each, en) for i, l, w, en in act]
        i = self._leg_index(p)
        leg, w, en = cfg[i]
        if p[1] == "t":                                              # старый lg:t:<i>
            return [LegChange(i, leg, w, en, w, not en)]
        if p[1] == "w":                                              # старый lg:w:<i>:<p|m>
            return [LegChange(i, leg, w, en, round(w + (0.1 if p[3] == "p" else -0.1), 2), en)]
        if p[2] == "t":
            return [LegChange(i, leg, w, en, w, not en)]
        return [LegChange(i, leg, w, en, int(p[3]) / 100, en)]       # lg:<i>:w:<pct>

    def _plan_legs(self, p: tuple[str, ...]) -> list[LegChange]:
        """_leg_changes + проверка правил state.set_leg с понятным сообщением (что уменьшить)."""
        changes = self._leg_changes(p)
        for c in changes:
            if c.w1 < -1e-9:
                raise ValueError("вес не может быть меньше 0%")
            if c.w1 > 1.0 + 1e-9:
                raise ValueError("вес не может быть больше 100%")
        cfg = self.st.leg_cfg()
        new = {c.leg.key: (c.w1, c.en1) for c in changes}
        total = sum(new.get(l.key, (w, en))[0] for l, w, en in cfg if new.get(l.key, (w, en))[1])
        if total > 1.0 + 1e-9:
            over = math.ceil((total - 1.0) * 100 - 1e-6)
            c = changes[0]
            others = sum(w for l, w, en in cfg if en and l.key != c.leg.key)
            cap = max(0, math.floor((1.0 - others) * 100 + 1e-6))
            nm = f"{c.leg.strategy} · {c.leg.tf}"
            if not c.en0 and c.en1:
                raise ValueError(f"сумма весов стала бы {total:.0%} (максимум 100%). Чтобы включить «{nm}», "
                                 f"уменьшите вес других стратегий на {over}%")
            raise ValueError(f"сумма весов стала бы {total:.0%} (максимум 100%). Уменьшите вес других стратегий на "
                             f"{over}% или задайте «{nm}» не больше {cap}%")
        return changes

    def _apply_legs(self, changes: list[LegChange]) -> None:
        """Сначала уменьшения суммы, потом увеличения: промежуточная сумма никогда не превышает 100%."""
        for c in sorted(changes, key=lambda c: c.delta):
            if c.noop:
                continue
            self.st.set_leg(c.leg.key, weight=None if abs(c.w0 - c.w1) < 1e-9 else c.w1,
                            enabled=None if c.en0 == c.en1 else c.en1)
