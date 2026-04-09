# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/copyline/pictures.py

CopyLine Pictures — supplier-layer для product images.

Что делает:
- оставляет только реальные product pictures из JShopping img_products;
- убирает мусор, data-URL и дубли;
- даёт приоритет full_* и возвращает clean список URL.

Что не делает:
- не принимает business-решения по ассортименту;
- не подменяет builder и source;
- не нормализует ничего, кроме picture-данных.
"""

from __future__ import annotations

from typing import Iterable


def safe_str(value: object) -> str:
    """Безопасно привести значение к строке."""
    return str(value).strip() if value is not None else ""


def _is_product_picture(url: str) -> bool:
    """Считать реальными только картинки из img_products."""
    val = safe_str(url).replace("\\", "/")
    return "/components/com_jshopping/files/img_products/" in val


def _is_full_picture(url: str) -> bool:
    """Определить full_* картинку."""
    val = safe_str(url)
    base = val.rsplit("/", 1)[-1]
    return base.startswith("full_") or "/full_" in val


def prefer_full_product_pictures(pictures: Iterable[str]) -> list[str]:
    """Оставить только реальные фото товара; full_* поставить в приоритет."""
    cleaned: list[str] = []
    seen: set[str] = set()

    for raw in pictures:
        url = safe_str(raw).replace("&amp;", "&")
        if not url or url.startswith("data:"):
            continue
        if not _is_product_picture(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        cleaned.append(url)

    if not cleaned:
        return []

    fulls = [url for url in cleaned if _is_full_picture(url)]
    other = [url for url in cleaned if not _is_full_picture(url)]
    return fulls if fulls else other


def full_only_if_present(pictures: Iterable[str]) -> list[str]:
    """Если среди уже очищенных картинок есть full_* — оставить только их."""
    pics = [safe_str(item) for item in pictures if safe_str(item)]
    if not pics:
        return []
    fulls = [url for url in pics if _is_full_picture(url)]
    return fulls if fulls else pics
