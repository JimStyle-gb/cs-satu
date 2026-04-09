# -*- coding: utf-8 -*-
"""
Path: scripts/cs/meta.py

CS Meta — build time и next-run helper-ы для FEED_META.

Роль файла:
- держит всё, что связано со временем по Алматы;
- считает следующую сборку для daily и DOM-расписаний;
- не зависит от cs.core.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


ALMATY_TZ = ZoneInfo("Asia/Almaty")


# -----------------------------
# Внутренние helper'ы
# -----------------------------

def _as_almaty(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ALMATY_TZ)
    return dt.astimezone(ALMATY_TZ)



def _last_day_of_month(year: int, month: int) -> int:
    first_next = datetime(year, month, 28, tzinfo=ALMATY_TZ) + timedelta(days=4)
    first_next = datetime(first_next.year, first_next.month, 1, tzinfo=ALMATY_TZ)
    return (first_next - timedelta(days=1)).day



def _candidate_dom(year: int, month: int, day: int, hour: int) -> datetime | None:
    if day < 1 or day > _last_day_of_month(year, month):
        return None
    return datetime(year, month, day, hour, 0, 0, tzinfo=ALMATY_TZ)


# -----------------------------
# Public API
# -----------------------------

def now_almaty() -> datetime:
    """Текущее timezone-aware время в Алматы."""
    return datetime.now(tz=ALMATY_TZ)



def next_run_at_hour(build_time: datetime, *, hour: int) -> datetime:
    """Следующая daily-сборка в Алматы на заданный час (0..23)."""
    bt = _as_almaty(build_time)
    target = bt.replace(hour=int(hour), minute=0, second=0, microsecond=0)
    if target <= bt:
        target = target + timedelta(days=1)
    return target



def next_run_dom_at_hour(now: datetime, hour: int, doms: tuple[int, ...] | list[int]) -> datetime:
    """Следующая сборка в Алматы для расписания по дням месяца (например 1/10/20)."""
    current = _as_almaty(now)
    hour = int(hour)
    doms_sorted = sorted({int(d) for d in doms if 1 <= int(d) <= 31})

    if not doms_sorted:
        return next_run_at_hour(current, hour=hour)

    for day in doms_sorted:
        cand = _candidate_dom(current.year, current.month, day, hour)
        if cand and cand > current:
            return cand

    year = current.year
    month = current.month + 1
    if month == 13:
        month = 1
        year += 1

    for day in doms_sorted:
        cand = _candidate_dom(year, month, day, hour)
        if cand:
            return cand

    return datetime(year, month, 1, hour, 0, 0, tzinfo=ALMATY_TZ)
