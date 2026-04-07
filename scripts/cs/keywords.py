# -*- coding: utf-8 -*-
"""
CS Keywords — общий сборщик <keywords>.

Файл не зависит от cs.core.py и использует единые shared-хелперы из cs.util.
Роли модуля:
- сборка итоговой строки <keywords>
- дедупликация токенов со стабильным порядком
- ограничение длины keywords
- общий гео-хвост по Казахстану

Важно:
- fix_mixed_cyr_lat и norm_ws импортируются из cs.util — это единый источник правды;
- имена импортов сохранены на уровне модуля для backward compatibility.
"""

from __future__ import annotations

import os

from .util import fix_mixed_cyr_lat, norm_ws


CS_KEYWORDS_MAX_LEN = int((os.getenv("CS_KEYWORDS_MAX_LEN", "380") or "380").strip() or "380")

# Города Казахстана — хвост для локального поиска внутри маркетплейса
CS_KEYWORDS_CITIES = (
    "Казахстан",
    "Алматы",
    "Астана",
    "Шымкент",
    "Караганда",
    "Актобе",
    "Павлодар",
    "Костанай",
    "Атырау",
    "Актау",
    "Усть-Каменогорск",
    "Семей",
    "Тараз",
)

# Общие коммерческие фразы
CS_KEYWORDS_PHRASES = (
    "доставка",
    "доставка по Казахстану",
    "отправка в регионы",
)


def _dedup_keep_order(items: list[str]) -> list[str]:
    """Дедупликация со стабильным порядком без сортировки."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_keywords(
    vendor: str | None,
    offer_name: str,
    extra: list[str] | None = None,
    **_kwargs,
) -> str:
    """Собрать итоговую строку <keywords> для CS-фида."""
    parts: list[str] = []

    # В keywords запятая — разделитель токенов, поэтому чистим её из vendor/name.
    vendor_clean = (vendor or "").replace(",", " ") or None
    offer_name_clean = (offer_name or "").replace(",", " ")

    if vendor_clean:
        parts.append(norm_ws(vendor_clean))
    if offer_name_clean:
        parts.append(norm_ws(offer_name_clean))

    if extra:
        for value in extra:
            value_norm = norm_ws(value)
            if value_norm:
                parts.append(value_norm)

    parts.extend(CS_KEYWORDS_PHRASES)
    parts.extend(CS_KEYWORDS_CITIES)

    parts = _dedup_keep_order([norm_ws(part) for part in parts if norm_ws(part)])

    # Если есть "доставка по Казахстану" — отдельный токен "доставка" убираем.
    lowered = [part.casefold() for part in parts]
    if "доставка по казахстану" in lowered and "доставка" in lowered:
        parts = [part for part in parts if part.casefold() != "доставка"]

    # Ограничение длины: сначала уходят города, потому что они добавлены в хвост.
    max_len = int(CS_KEYWORDS_MAX_LEN or 380)
    joined = ", ".join(parts)
    while len(joined) > max_len and len(parts) > 2:
        parts.pop()
        joined = ", ".join(parts)

    return joined


__all__ = [
    "CS_KEYWORDS_MAX_LEN",
    "CS_KEYWORDS_CITIES",
    "CS_KEYWORDS_PHRASES",
    "build_keywords",
    "fix_mixed_cyr_lat",
    "norm_ws",
]
