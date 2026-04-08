# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/vtt_api/builder.py
Builder для VTT_api.
Максимально переиспользует текущий VTT builder.

v2:
- добавляет supplier-side de-dup offer id только для VTT_api;
- не трогает основной VTT;
- если API даёт несколько товаров с одинаковым stable code,
  добавляет детерминированный suffix из source item id / sku / article;
- если source token не найден, использует порядковый -2 / -3.
"""
from __future__ import annotations

import re
from typing import Any

from suppliers.vtt.builder import build_offer_from_raw
from suppliers.vtt_api.normalize import normalize_api_item


_SUFFIX_CANDIDATE_KEYS = (
    "id", "ID", "itemId", "ItemId", "itemID", "ItemID",
    "productId", "ProductId", "productID", "ProductID",
    "rowId", "RowId", "rowID", "RowID",
    "guid", "GUID", "uuid", "UUID",
    "article", "Article", "sku", "SKU", "code", "Code",
)


def _norm_ws(value: object) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def _sanitize_suffix(value: object) -> str:
    s = _norm_ws(value)
    if not s:
        return ""
    s = re.sub(r"[^A-Za-z0-9._/-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-./_")
    return s[:40]


def _pick_source_suffix(item: dict[str, Any], raw: dict[str, Any], used: set[str]) -> str:
    raw_item = item if isinstance(item, dict) else {}

    for key in _SUFFIX_CANDIDATE_KEYS:
        if key in raw_item:
            tok = _sanitize_suffix(raw_item.get(key))
            if tok and tok.casefold() not in used:
                return tok

    for key in ("sku", "vendor", "name"):
        tok = _sanitize_suffix(raw.get(key))
        if tok and tok.casefold() not in used:
            return tok

    return ""


def _make_unique_oid(base_oid: str, item: dict[str, Any], raw: dict[str, Any], seen: dict[str, int], used_oids: set[str]) -> str:
    if base_oid not in used_oids:
        used_oids.add(base_oid)
        seen.setdefault(base_oid, 1)
        return base_oid

    count = seen.get(base_oid, 1)
    suffixes_used = {oid[len(base_oid) + 1 :].casefold() for oid in used_oids if oid.startswith(base_oid + "-")}

    src_suffix = _pick_source_suffix(item, raw, suffixes_used)
    if src_suffix:
        candidate = f"{base_oid}-{src_suffix}"
        if candidate not in used_oids:
            used_oids.add(candidate)
            seen[base_oid] = count + 1
            return candidate

    n = max(2, count + 1)
    while True:
        candidate = f"{base_oid}-{n}"
        if candidate not in used_oids:
            used_oids.add(candidate)
            seen[base_oid] = n
            return candidate
        n += 1


def build_offers_from_api_items(items: list[dict[str, Any]], *, id_prefix: str = "VTA") -> list[Any]:
    offers: list[Any] = []
    used_oids: set[str] = set()
    seen_base: dict[str, int] = {}

    for item in items:
        raw = normalize_api_item(item)
        offer = build_offer_from_raw(raw, id_prefix=id_prefix)
        if offer is None:
            continue

        base_oid = _norm_ws(getattr(offer, "oid", ""))
        if not base_oid:
            continue

        unique_oid = _make_unique_oid(base_oid, item, raw, seen_base, used_oids)
        if unique_oid != base_oid:
            offer.oid = unique_oid

        offers.append(offer)

    return offers
