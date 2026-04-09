# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/comportal/filtering.py

ComPortal Filtering — supplier-layer фильтрация ассортимента.

Что делает:
- читает id-наборы из env и fallback config;
- применяет include по category ids;
- применяет exclude по root ids для supplier-category дерева.

Что не делает:
- не парсит source;
- не меняет offers;
- не переносит supplier-aware правила в shared core.
"""

from __future__ import annotations

import re

from suppliers.comportal.models import SourceOffer

# -----------------------------
# Helper'ы id-наборов
# -----------------------------

def parse_id_set(env_value: str | None, fallback: set[str]) -> set[str]:
    """Прочитать set ids из env или вернуть fallback."""
    if not env_value:
        return set(fallback)

    raw = env_value.strip()
    if not raw:
        return set(fallback)

    parts = re.split(r"[\s,;]+", raw)
    out = {part.strip() for part in parts if part and part.strip()}
    return out or set(fallback)

# -----------------------------
# Public filter API
# -----------------------------

def offer_passes_filter(
    source_offer: SourceOffer,
    include_ids: set[str],
    excluded_root_ids: set[str],
) -> bool:
    """Проверить, проходит ли source-offer supplier-фильтр."""
    if not source_offer.category_id:
        return False
    if include_ids and source_offer.category_id not in include_ids:
        return False
    if excluded_root_ids and source_offer.category_root_id in excluded_root_ids:
        return False
    return True


def filter_source_offers(
    offers: list[SourceOffer],
    include_ids: set[str],
    excluded_root_ids: set[str],
) -> list[SourceOffer]:
    """Отфильтровать source offers по supplier category policy."""
    return [
        source_offer
        for source_offer in offers
        if offer_passes_filter(source_offer, include_ids, excluded_root_ids)
    ]
