# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/akcent/pictures.py

AkCent supplier layer — сборка и нормализация картинок.

Что делает:
- собирает картинки из source-offer и/или из XML offer_el;
- убирает дубли;
- чистит пробелы и битые ссылки;
- поддерживает supplier-specific замену no-photo ссылок;
- если фото нет — ставит placeholder.

Важно:
- модуль не решает business-логику;
- модуль только возвращает чистый список picture URL для builder.py.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Iterable


_RE_WS = re.compile(r"\s+")
_RE_HTTP = re.compile(r"^https?://", re.IGNORECASE)

# Явные supplier/no-photo заглушки, которые не должны ехать в финал как реальные фото.
_NO_PHOTO_SUBSTRINGS = (
    "nophoto",
    "no-photo",
    "no_photo",
    "noimage",
    "no-image",
    "no_image",
    "placeholder",
    "placehold.it",
    "placehold.co",
    "/notfound/",
)


# -----------------------------
# Базовые helper'ы
# -----------------------------

def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return _RE_WS.sub(" ", str(value).replace("\xa0", " ")).strip()


def _get_field(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj.get(name)
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _normalize_url(value: Any) -> str:
    """Нормализует web-URL картинки и режет не-web значения."""
    url = _clean_text(value)
    if not url:
        return ""

    url = url.replace("\\", "/")
    url = url.replace(" ", "%20")

    if url.startswith("//"):
        url = "https:" + url

    if not _RE_HTTP.match(url):
        return ""
    return url


def _is_no_photo_url(url: str) -> bool:
    text_cf = _clean_text(url).casefold()
    if not text_cf:
        return True
    return any(token in text_cf for token in _NO_PHOTO_SUBSTRINGS)


def _dedupe(urls: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        url = _normalize_url(raw)
        if not url:
            continue
        key = url.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


# -----------------------------
# Сбор картинок
# -----------------------------

def _iter_offer_el_pictures(offer_el: ET.Element | None) -> Iterable[str]:
    if offer_el is None:
        return []

    out: list[str] = []
    for picture_el in offer_el.findall("picture"):
        url = _clean_text("".join(picture_el.itertext()))
        if url:
            out.append(url)
    return out


def _iter_source_pictures(src: Any) -> Iterable[str]:
    out: list[str] = []

    direct_list = _get_field(src, "picture_urls", "pictures", "picture_list")
    if isinstance(direct_list, (list, tuple)):
        for raw in direct_list:
            value = _clean_text(raw)
            if value:
                out.append(value)

    for raw in (
        _get_field(src, "picture_url"),
        _get_field(src, "picture"),
        _get_field(src, "image"),
    ):
        value = _clean_text(raw)
        if value:
            out.append(value)

    offer_el = _get_field(src, "offer_el", "el", "xml_offer")
    if isinstance(offer_el, ET.Element):
        out.extend(_iter_offer_el_pictures(offer_el))

    return out


def collect_picture_urls(
    src_or_urls: Any,
    *,
    placeholder_picture: str = "https://placehold.co/800x800/png?text=No+Photo",
    drop_no_photo: bool = True,
) -> list[str]:
    """
    Возвращает чистый список картинок для builder.py.

    Поддерживает два режима:
    - передали source-offer / dict / dataclass;
    - передали уже готовый список URL.
    """
    if isinstance(src_or_urls, (list, tuple)):
        raw_urls = [str(item) for item in src_or_urls]
    else:
        raw_urls = list(_iter_source_pictures(src_or_urls))

    urls = _dedupe(raw_urls)
    if drop_no_photo:
        urls = [url for url in urls if not _is_no_photo_url(url)]

    placeholder = _normalize_url(placeholder_picture)
    if not urls and placeholder:
        return [placeholder]
    return urls


# -----------------------------
# Короткая диагностика
# -----------------------------

def analyze_pictures(
    src_or_urls: Any,
    *,
    placeholder_picture: str = "https://placehold.co/800x800/png?text=No+Photo",
) -> dict[str, Any]:
    raw_urls = (
        list(src_or_urls)
        if isinstance(src_or_urls, (list, tuple))
        else list(_iter_source_pictures(src_or_urls))
    )
    normalized = _dedupe(raw_urls)
    real_urls = [url for url in normalized if not _is_no_photo_url(url)]
    final_urls = collect_picture_urls(
        src_or_urls,
        placeholder_picture=placeholder_picture,
    )
    normalized_placeholder = _normalize_url(placeholder_picture)

    return {
        "raw_count": len(raw_urls),
        "normalized_count": len(normalized),
        "real_count": len(real_urls),
        "final_count": len(final_urls),
        "used_placeholder": bool(
            final_urls
            and len(final_urls) == 1
            and final_urls[0] == normalized_placeholder
        ),
    }
