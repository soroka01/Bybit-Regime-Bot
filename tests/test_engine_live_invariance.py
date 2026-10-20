"""Живой бот считает целевую позицию через core.portfolio.exposure + core.strategies и НЕ использует engine/overlays/optimize.
Тест фиксирует это: серия экспозиции ног по умолчанию на фиксированном синтетическом кадре побитно совпадает с эталоном,
посчитанным ДО «backtest semantics v2» (правки движка/оверлеев/индикаторов не должны менять живой сигнал).
Эталон: tests/snapshots/live_exposure_ref.npz (пересоздание — только осознанно: UPDATE_LIVE_REF=1)."""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from _synth import bars  # noqa: E402
from tradebot.core.portfolio import DEFAULT_LEGS, exposure  # noqa: E402

REF = Path(__file__).parent / "snapshots" / "live_exposure_ref.npz"


def _frames() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(77)
    out = {}
    for tf, step, n, seed in (("1h", 1, 3000, 21), ("4h", 4, 1500, 22), ("6h", 6, 1000, 23)):
        df = bars(step, n, seed)
        df["oi"] = 1000 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
        df["lsr"] = np.exp(rng.normal(0, 0.2, n))
        df["funding"] = rng.normal(1e-4, 5e-5, n)
        out[tf] = df
    return out


def _compute() -> dict[str, np.ndarray]:
    expo, sigs = exposure(DEFAULT_LEGS, _frames())
    res = {"expo": expo.to_numpy(dtype="float64")}
    for c in sigs.columns:
        res["sig_" + c] = sigs[c].to_numpy(dtype="float64")
    return res


def test_live_exposure_bit_identical_to_reference():
    cur = _compute()
    if os.environ.get("UPDATE_LIVE_REF") == "1" or not REF.exists():
        np.savez_compressed(REF, **cur)
        assert os.environ.get("UPDATE_LIVE_REF") == "1", "эталон отсутствовал и записан — перезапустите тесты"
        return
    ref = np.load(REF)
    assert sorted(ref.files) == sorted(cur)
    for k in cur:
        assert ref[k].shape == cur[k].shape and ref[k].tobytes() == cur[k].tobytes(), f"изменился живой сигнал: {k}"
    assert np.abs(cur["expo"]).max() > 0 and len(np.unique(cur["expo"])) > 2     # эталон не вырожден (ноль/константа)
