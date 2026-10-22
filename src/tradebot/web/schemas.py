"""Pydantic-модели запросов. Все числовые границы — жёсткие (защита от «убийственных» запросов)."""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from tradebot.core.engine import DEFAULT_PERP_FEE
from tradebot.i18n import tr
from tradebot.market.klines import TIMEFRAMES

SYMBOLS = ("BTCUSDT", "ETHUSDT")
Symbol = Literal["BTCUSDT", "ETHUSDT"]      # зеркало SYMBOLS (проверяется тестом): символ попадает в путь файла данных
MAX_LEVERAGE = 100.0              # максимум Bybit для BTCUSDT; зеркало web/src/leverage.ts (проверяется тестом)
PERP_FEE = DEFAULT_PERP_FEE       # taker деривативов: реальные 0.1 % аккаунта (единый источник — core.engine.DEFAULT_PERP_FEE)
SPOT_FEE = 0.0010                 # taker спота (Bybit)
Market = Literal["perp", "spot"]


def _check_tf(v: str) -> str:
    if v not in TIMEFRAMES:
        raise ValueError(tr("unknown timeframe (allowed: {allowed})").format(allowed=', '.join(TIMEFRAMES)))
    return v


Timeframe = Annotated[str, AfterValidator(_check_tf)]     # только ключи klines.TIMEFRAMES


class _Base(BaseModel):
    """Общая база запросов: NaN/Infinity в числах запрещены (иначе они просачиваются в границы осей и ключи кэша)."""
    model_config = ConfigDict(allow_inf_nan=False)


class Overlays(_Base):
    htf: Optional[int] = Field(None, ge=5, le=400)       # период дневной EMA-фильтра
    stop: Optional[float] = Field(None, gt=0, le=15)     # множитель ATR-стопа


class _Common(_Base):
    symbol: Symbol = "BTCUSDT"
    days: int = Field(365, ge=30, le=1095)
    fee: float = Field(PERP_FEE, ge=0, le=0.01)
    slippage: float = Field(0.0002, ge=0, le=0.01)
    leverage: float = Field(1.0, ge=1, le=MAX_LEVERAGE)
    market: Market = "perp"

    @model_validator(mode="after")
    def _apply_market(self):
        """Спот: плечо 1x, только лонг (у каждой ноги портфеля тоже), комиссия по умолчанию 0.10 %, если не задана явно.
        Нормализация здесь (на границе API), чтобы ключи кэшей (model_dump_json и т.п.) были каноничными: spot с
        плечом 20 и spot с плечом 1 — один и тот же расчёт. Дальше эффективные значения берёт web.backtests.apply_market."""
        if self.market == "spot":
            if "fee" not in self.model_fields_set:
                self.fee = SPOT_FEE
            self.leverage = 1.0
            if "long_only" in type(self).model_fields:
                self.long_only = True
            for leg in getattr(self, "legs", ()):
                leg.long_only = True
        return self


class BacktestReq(_Common):
    tf: Timeframe = "1h"
    strategy: str
    params: dict[str, float] = Field(default_factory=dict)
    long_only: bool = False
    overlays: Overlays = Field(default_factory=Overlays)


class SweepAxis(_Base):
    name: str
    min: float
    max: float
    n: int = Field(10, ge=2, le=25)


class SweepReq(BacktestReq):
    x: SweepAxis
    y: Optional[SweepAxis] = None     # нет y -> одномерный профиль параметра x


class LegReq(_Base):
    strategy: str
    tf: Timeframe
    params: dict[str, float] = Field(default_factory=dict)
    weight: float = Field(0.5, ge=0, le=3)
    long_only: bool = False


class PortfolioReq(_Common):
    legs: list[LegReq] = Field(min_length=1, max_length=8)


class FetchReq(_Base):
    symbol: Symbol
    tf: Timeframe


class OptimizeReq(_Common):
    """Подбор ТОЛЬКО параметров стратегии; всё остальное — как выбрано пользователем."""
    tf: Timeframe = "1h"
    strategy: str
    long_only: bool = False
    overlays: Overlays = Field(default_factory=Overlays)
    criterion: Literal["robust", "calmar", "sharpe", "return"] = "robust"
    top: int = Field(10, ge=1, le=20)


class LbItemReq(_Common):
    tf: Timeframe = "1h"
    strategy: str
    long_only: bool = False
    variants: bool = False          # перебирать фильтры HTF/стоп (выбор по IS)
    force: bool = False             # обойти дисковый кэш и пересчитать


class CachedReq(_Common):
    """Параметры рейтинга для поиска в дисковом кэше (без вычислений)."""
    tf: Timeframe = "1h"
    long_only: bool = False
    variants: bool = False


class ProposalReq(_Base):
    """«В бота» с сайта: тот же текст, что даёт «Копировать для бота» (его разбирает core.legspec)."""
    text: str = Field(min_length=1, max_length=4000)
