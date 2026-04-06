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
from typing import Sequence
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


def next_run_dom_at_hour(build_time: datetime, *, hour: int, doms: Sequence[int]) -> datetime:
    """Следующая сборка по расписанию дней месяца (например 1/10/20) в Алматы."""
    bt = build_time.astimezone(ALMATY_TZ) if build_time.tzinfo else build_time.replace(tzinfo=ALMATY_TZ)
    hour = int(hour)
    doms_sorted = sorted({int(d) for d in doms if 1 <= int(d) <= 31})
    if not doms_sorted:
        return next_run_at_hour(bt, hour=hour)

    def _last_day_of_month(y: int, m: int) -> int:
        if m == 12:
            first_next = datetime(y + 1, 1, 1, tzinfo=ALMATY_TZ)
        else:
            first_next = datetime(y, m + 1, 1, tzinfo=ALMATY_TZ)
        return (first_next - timedelta(days=1)).day

    def _pick_in_month(y: int, m: int, after_dt: datetime | None) -> datetime | None:
        last = _last_day_of_month(y, m)
        for d in doms_sorted:
            if d > last:
                continue
            cand = datetime(y, m, d, hour, 0, 0, tzinfo=ALMATY_TZ)
            if after_dt is None or cand > after_dt:
                return cand
        return None

    cand = _pick_in_month(bt.year, bt.month, bt)
    if cand is not None:
        return cand

    y2, m2 = bt.year, bt.month + 1
    if m2 == 13:
        y2 += 1
        m2 = 1
    cand2 = _pick_in_month(y2, m2, None)
    if cand2 is not None:
        return cand2

    return datetime(y2, m2, 1, hour, 0, 0, tzinfo=ALMATY_TZ)
