# -*- coding: utf-8 -*-
"""
Path: scripts/cs/pricing.py

CS Pricing — shared price calculation layer.

Что делает:
- считает финальную цену по общему CS-правилу;
- не зависит от supplier-specific логики;

Что не делает:
- не строит offers;
- не содержит supplier-specific правил.
"""
from __future__ import annotations

# Source of truth для общей pricing-логики.

CS_PRICE_TIERS = [
    (101, 10_000, 3_000),
    (10_001, 25_000, 4_000),
    (25_001, 50_000, 5_000),
    (50_001, 75_000, 7_000),
    (75_001, 100_000, 10_000),
    (100_001, 150_000, 12_000),
    (150_001, 200_000, 15_000),
    (200_001, 300_000, 20_000),
    (300_001, 500_000, 25_000),
    (500_001, 750_000, 30_000),
    (750_001, 1_000_000, 35_000),
    (1_000_001, 1_500_000, 40_000),
    (1_500_001, 2_000_000, 45_000),
]

# -----------------------------
# Внутренние helper'ы
# -----------------------------

def _safe_price_int(v) -> int | None:
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            return int(v)
        text = str(v).strip()
        if not text:
            return None
        text = text.replace(" ", "").replace(" ", "")
        text = text.split(".")[0]
        return int(text)
    except Exception:
        return None

# -----------------------------
# Public API
# -----------------------------

def compute_price(price_in: int | None) -> int | None:
    p = _safe_price_int(price_in)
    if p is None or p <= 100:
        return None
    if p >= 9_000_000:
        return None

    add = 60_000
    for lo, hi, tier_add in CS_PRICE_TIERS:
        if lo <= p <= hi:
            add = tier_add
            break

    raw = int(p * 1.04 + add)
    out = (raw // 1000) * 1000 + 900

    if out >= 9_000_000:
        return None
    if out <= 100:
        return None
    return out


__all__ = [
    "CS_PRICE_TIERS",
    "compute_price",
]
