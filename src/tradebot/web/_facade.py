"""Доступ к фасаду `tradebot.web.service` из его же подмодулей — ПО ИМЕНИ В МОМЕНТ ВЫЗОВА.

Тесты (и отладка) подменяют имена на фасаде: service.MARKET, service._optimize, service.invalidate_data и т.п.
Подмодули читают такие имена через `F.<имя>`, поэтому подмена видна везде, как раньше в едином service.py.
Ленивый импорт нужен, чтобы не зависеть от порядка импорта (фасад импортирует подмодули, а они — фасад)."""
from __future__ import annotations


class _Facade:
    def __getattr__(self, name: str):
        from tradebot.web import service
        return getattr(service, name)


F = _Facade()
