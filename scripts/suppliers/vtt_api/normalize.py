# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/vtt_api/normalize.py
Нормализация SOAP payload -> raw dict, максимально похожий на текущий VTT raw.
"""
from __future__ import annotations

from typing import Any


def norm_ws(value: object) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def _pick(d: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in d and norm_ws(d.get(name)):
            return norm_ws(d.get(name))
    low = {str(k).casefold(): v for k, v in d.items()}
    for name in names:
        if name.casefold() in low and norm_ws(low[name.casefold()]):
            return norm_ws(low[name.casefold()])
    return ""


def _bool_from_any(value: object) -> bool:
    s = norm_ws(value).lower()
    if s in {"", "0", "false", "no", "нет"}:
        return False
    if s in {"1", "true", "yes", "да", "есть"}:
        return True
    try:
        return float(str(value)) > 0
    except Exception:
        return bool(value)


def _extract_params(item: dict[str, Any]) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = []
    raw = item.get("params") or item.get("Params") or item.get("attributes") or item.get("Attributes") or []
    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, list):
        for p in raw:
            if not isinstance(p, dict):
                continue
            key = _pick(p, "name", "Name", "key", "Key")
            val = _pick(p, "value", "Value", "val", "Val")
            if key and val:
                params.append((key, val))
    compat = _pick(item, "compat", "Compatibility", "compatibility")
    if compat:
        params.append(("Совместимость", compat))
    part = _pick(item, "part_number", "PartNumber", "oem", "OEM")
    if part:
        params.append(("Партномер", part))
    color = _pick(item, "color", "Color")
    if color:
        params.append(("Цвет", color))
    tech = _pick(item, "technology", "Technology", "print_technology")
    if tech:
        params.append(("Технология печати", tech))
    resource = _pick(item, "resource", "Resource", "yield")
    if resource:
        params.append(("Ресурс", resource))
    return params


def _extract_pictures(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("pictures", "Pictures", "images", "Images"):
        val = item.get(key)
        if isinstance(val, list):
            for x in val:
                s = norm_ws(x if not isinstance(x, dict) else x.get("url") or x.get("Url"))
                if s:
                    out.append(s)
    single = _pick(item, "picture", "Picture", "image", "Image")
    if single:
        out.append(single)
    dedup: list[str] = []
    seen: set[str] = set()
    for x in out:
        if x not in seen:
            seen.add(x)
            dedup.append(x)
    return dedup


def normalize_api_item(item: dict[str, Any]) -> dict[str, Any]:
    title = _pick(item, "name", "Name", "title", "Title")
    sku = _pick(item, "sku", "SKU", "article", "Article", "id", "ID", "code", "Code")
    vendor = _pick(item, "vendor", "Vendor", "brand", "Brand", "manufacturer", "Manufacturer")
    desc = _pick(item, "description", "Description", "desc", "Desc")
    price = _pick(item, "price", "Price", "priceIn", "PriceIn")
    qty = _pick(item, "qty", "Qty", "quantity", "Quantity", "stock", "Stock")

    return {
        "name": title,
        "sku": sku,
        "vendor": vendor,
        "description_body": desc,
        "description_meta": "",
        "price_in": price,
        "qty": qty,
        "available": _bool_from_any(item.get("available") if "available" in item else qty),
        "params": _extract_params(item),
        "pictures": _extract_pictures(item),
        "raw_item": item,
    }
