# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/vtt_api/builder.py
Builder для VTT_api.
Максимально переиспользует текущий VTT builder.
"""
from __future__ import annotations

from typing import Any

from suppliers.vtt.builder import build_offer_from_raw
from suppliers.vtt_api.normalize import normalize_api_item


def build_offers_from_api_items(items: list[dict[str, Any]], *, id_prefix: str = "VTA") -> list[Any]:
    offers: list[Any] = []
    for item in items:
        raw = normalize_api_item(item)
        offer = build_offer_from_raw(raw, id_prefix=id_prefix)
        if offer is not None:
            offers.append(offer)
    return offers
