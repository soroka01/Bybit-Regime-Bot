"""Золотые снимки JSON-ответов API: UPDATE_SNAPSHOTS=1 — записать, иначе сравнить. Ключи отсортированы,
float округлены до 6 знаков; волатильные поля (время расчёта) вырезаются."""
import json
import os
from pathlib import Path

SNAP_DIR = Path(__file__).parent / "snapshots"
VOLATILE = {"computed_at", "elapsed"}


def normalize(o):
    if isinstance(o, dict):
        return {k: normalize(v) for k, v in sorted(o.items()) if k not in VOLATILE}
    if isinstance(o, list):
        return [normalize(v) for v in o]
    if isinstance(o, float):
        return round(o, 6) + 0.0      # +0.0 убирает -0.0
    return o


def check(name: str, payload) -> None:
    text = json.dumps(normalize(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    path = SNAP_DIR / f"{name}.json"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1" or not path.exists():
        SNAP_DIR.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if os.environ.get("UPDATE_SNAPSHOTS") != "1":
            raise AssertionError(f"снимок {name} отсутствовал и записан — перезапустите тесты")
        return
    old = path.read_text(encoding="utf-8")
    if old != text:
        a, b = json.loads(old), json.loads(text)
        raise AssertionError(f"снимок {name} отличается ({_first_diff(a, b) or 'форма'}); "
                             f"если изменение намеренное — UPDATE_SNAPSHOTS=1")


def _first_diff(a, b, path=""):
    if type(a) is not type(b):
        return f"{path}: тип {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}/{k}: ключ {'добавлен' if k in b else 'удалён'}"
            d = _first_diff(a[k], b[k], f"{path}/{k}")
            if d:
                return d
    elif isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: длина {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = _first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
    elif a != b:
        return f"{path}: {a!r} != {b!r}"
    return None
