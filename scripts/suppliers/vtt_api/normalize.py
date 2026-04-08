# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/vtt_api/normalize.py
Нормализация SOAP payload -> raw dict, максимально похожий на текущий VTT raw.

v2:
- поддерживает больше английских и русских ключей;
- даёт fallback по title/sku/vendor/price/qty;
- пишет поля именно в том виде, который ждёт текущий VTT builder.
"""
from __future__ import annotations

from typing import Any


def norm_ws(value: object) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def _index(item: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in item.items():
        key = norm_ws(k)
        if not key:
            continue
        out[key.casefold()] = v
    return out


def _pick(d: dict[str, Any], *names: str) -> str:
    idx = _index(d)
    for name in names:
        key = name.casefold()
        if key in idx and norm_ws(idx[key]):
            return norm_ws(idx[key])
    return ""


def _pick_contains(d: dict[str, Any], needles: tuple[str, ...]) -> str:
    idx = _index(d)
    for key, value in idx.items():
        if any(n in key for n in needles) and norm_ws(value):
            return norm_ws(value)
    return ""


def _bool_from_any(value: object) -> bool:
    s = norm_ws(value).lower()
    if s in {"", "0", "false", "no", "нет"}:
        return False
    if s in {"1", "true", "yes", "да", "есть"}:
        return True
    try:
        return float(str(value).replace(",", ".")) > 0
    except Exception:
        return bool(value)


def _extract_params(item: dict[str, Any], used_keys: set[str]) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = []
    raw = (
        item.get("params")
        or item.get("Params")
        or item.get("attributes")
        or item.get("Attributes")
        or item.get("Характеристики")
        or []
    )
    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, list):
        for p in raw:
            if not isinstance(p, dict):
                continue
            key = _pick(p, "name", "Name", "key", "Key", "Наименование")
            val = _pick(p, "value", "Value", "val", "Val", "Значение")
            if key and val:
                params.append((key, val))

    # Если SOAP отдаёт плоскую структуру, тянем полезные scalar-поля в params.
    skip_contains = (
        "name", "title", "caption", "наименование", "description", "описание",
        "price", "цена", "cost", "стоимость", "qty", "quantity", "остат",
        "available", "налич", "image", "picture", "photo", "vendor", "brand",
        "manufacturer", "sku", "article", "артикул", "code", "код", "id",
    )
    for k, v in item.items():
        key = norm_ws(k)
        if not key or key.casefold() in used_keys:
            continue
        if any(x in key.casefold() for x in skip_contains):
            continue
        if isinstance(v, (dict, list, tuple, set)):
            continue
        val = norm_ws(v)
        if not val:
            continue
        params.append((key, val))
    return params


def _extract_pictures(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("pictures", "Pictures", "images", "Images", "photos", "Photos", "Фото"):
        val = item.get(key)
        if isinstance(val, list):
            for x in val:
                if isinstance(x, dict):
                    s = _pick(x, "url", "Url", "href", "Href", "src", "Src")
                else:
                    s = norm_ws(x)
                if s:
                    out.append(s)
    for key in ("picture", "Picture", "image", "Image", "photo", "Photo", "Фото"):
        s = _pick(item, key)
        if s:
            out.append(s)
    dedup: list[str] = []
    seen: set[str] = set()
    for x in out:
        if x not in seen:
            seen.add(x)
            dedup.append(x)
    return dedup


def normalize_api_item(item: dict[str, Any]) -> dict[str, Any]:
    used_keys: set[str] = set()

    title = _pick(
        item,
        "name", "Name", "title", "Title", "caption", "Caption",
        "Наименование", "Название", "Товар", "Номенклатура",
    ) or _pick_contains(item, ("name", "title", "caption", "наименование", "название", "товар", "номенклатур"))
    if title:
        used_keys.add("name")

    sku = _pick(
        item,
        "sku", "SKU", "article", "Article", "Артикул", "Код товара",
        "productCode", "ProductCode", "code", "Code", "PartNumber", "partNumber",
        "id", "ID",
    ) or _pick_contains(item, ("sku", "article", "артикул", "код", "partnumber", "productcode", "code"))

    vendor = _pick(
        item,
        "vendor", "Vendor", "brand", "Brand", "manufacturer", "Manufacturer",
        "Бренд", "Производитель", "Вендор", "Марка",
    ) or _pick_contains(item, ("brand", "vendor", "manufacturer", "бренд", "производител", "вендор", "марка"))

    desc = _pick(
        item,
        "description", "Description", "desc", "Desc", "body", "Body", "Описание",
    ) or _pick_contains(item, ("description", "desc", "описан"))

    price = _pick(
        item,
        "price", "Price", "priceIn", "PriceIn", "Цена", "Стоимость", "cost", "Cost",
    ) or _pick_contains(item, ("price", "цена", "стоим", "cost"))

    qty = _pick(
        item,
        "qty", "Qty", "quantity", "Quantity", "stock", "Stock", "Остаток", "Наличие", "Balance",
    ) or _pick_contains(item, ("qty", "quantity", "stock", "остат", "налич", "balance"))

    source_categories = []
    for key in ("category", "Category", "Категория", "subcategory", "SubCategory", "Подкатегория"):
        s = _pick(item, key)
        if s:
            source_categories.append(s)

    params = _extract_params(item, used_keys)
    pictures = _extract_pictures(item)

    return {
        "name": title,
        "sku": sku,
        "vendor": vendor,
        "description_body": desc,
        "description_meta": "",
        "price_rub_raw": price,
        "qty": qty,
        "available": _bool_from_any(item.get("available") if "available" in item else qty),
        "params": params,
        "pictures": pictures,
        "source_categories": source_categories,
        "raw_item": item,
    }
