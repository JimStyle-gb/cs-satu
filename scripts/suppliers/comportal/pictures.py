# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/comportal/pictures.py

ComPortal pictures layer.

Что делает:
- собирает и нормализует picture URL;
- возвращает готовый список картинок для builder.py;

Что не делает:
- не меняет business-логику товаров;
- не управляет ассортиментной фильтрацией.
"""
from __future__ import annotations

from cs.util import norm_ws

# -----------------------------
# Normalize helpers
# -----------------------------

def _normalize_picture_url(url: str) -> str:
    """Нормализовать один picture url."""
    picture_url = norm_ws(url).replace(" ", "%20")
    if not picture_url:
        return ""

    lower_url = picture_url.casefold()
    if lower_url.startswith("http://www.comportal.kz/"):
        return "https://www.comportal.kz/" + picture_url[len("http://www.comportal.kz/"):]
    if lower_url.startswith("http://comportal.kz/"):
        return "https://comportal.kz/" + picture_url[len("http://comportal.kz/"):]
    return picture_url

# -----------------------------
# Public API
# -----------------------------

def collect_picture_urls(urls: list[str], *, placeholder_picture: str) -> list[str]:
    """Собрать чистый список picture urls для builder."""
    out: list[str] = []
    seen: set[str] = set()

    for raw_url in urls or []:
        picture_url = _normalize_picture_url(raw_url)
        if not picture_url:
            continue
        signature = picture_url.casefold()
        if signature in seen:
            continue
        seen.add(signature)
        out.append(picture_url)

    if not out:
        return [placeholder_picture]
    return out
