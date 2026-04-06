# -*- coding: utf-8 -*-
"""
CS Meta — время сборки, next-run и вспомогательные функции для FEED_META.

Этап 5: вынос части мета-логики из cs/core.py в отдельный модуль.
Важно: модуль НЕ импортирует cs/core.py (чтобы не ловить циклические импорты).

Сейчас переносим:
- now_almaty()
- next_run_at_hour()
(подготовка к следующему шагу: вынести make_feed_meta/FEED_META полностью из writer)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


ALMATY_TZ = ZoneInfo("Asia/Almaty")


def now_almaty() -> datetime:
    """Текущее время в Алматы (timezone-aware)."""
    return datetime.now(tz=ALMATY_TZ)


def next_run_at_hour(build_time: datetime, *, hour: int) -> datetime:
    """Следующая сборка в Алматы на заданный час (0..23)."""
    bt = build_time.astimezone(ALMATY_TZ)
    target = bt.replace(hour=int(hour), minute=0, second=0, microsecond=0)
    if target <= bt:
        target = target + timedelta(days=1)
    return target

# CS: вычисляет next_run для расписания "в дни месяца" (например 1/10/20) в заданный час (Алматы)
def next_run_dom_at_hour(now: datetime, hour: int, doms: tuple[int, ...] | list[int]) -> datetime:
    hour = int(hour)
    doms_sorted = sorted({int(d) for d in doms if 1 <= int(d) <= 31})
    if not doms_sorted:
        base = (now + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
        return base.replace(hour=hour)

    def _last_day_of_month(y: int, m: int) -> int:
        first_next = datetime(y, m, 28) + timedelta(days=4)
        first_next = datetime(first_next.year, first_next.month, 1)
        return (first_next - timedelta(days=1)).day

    def _pick_in_month(y: int, m: int, after_dt: datetime | None) -> datetime | None:
        last = _last_day_of_month(y, m)
        for d in doms_sorted:
            if d > last:
                continue
            cand = datetime(y, m, d, hour, 0, 0)
            if after_dt is None or cand > after_dt:
                return cand
        return None

    cand = _pick_in_month(now.year, now.month, now)
    if cand:
        return cand

    y2, m2 = now.year, now.month + 1
    if m2 == 13:
        m2 = 1
        y2 += 1
    cand2 = _pick_in_month(y2, m2, None)
    if cand2:
        return cand2

    return datetime(y2, m2, 1, hour, 0, 0)

