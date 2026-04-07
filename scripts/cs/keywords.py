# -*- coding: utf-8 -*-
"""
Path: scripts/cs/keywords.py

CS Keywords — общий сборщик <keywords>.

Роль файла:
- собирает единый keywords-хвост для всех поставщиков;
- делает мягкую нормализацию и дедуп токенов;
- не зависит от cs.core и не содержит supplier-specific ветвления.
"""

from __future__ import annotations

import os
import re

from .util import fix_mixed_cyr_lat

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

# Базовые regex-хелперы
_RE_WS = re.compile(r"\s+")

def _dedup_keep_order(items: list[str]) -> list[str]:
    """Дедупликация со стабильным порядком (без сортировки)."""
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if not x:
            continue
        k = x.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def build_keywords(
    vendor: str | None,
    offer_name: str,
    extra: list[str] | None = None,
    **_kwargs,
) -> str:
    parts: list[str] = []
    # В keywords запятая — это разделитель токенов, поэтому убираем запятые из vendor/name
    vendor = (vendor or "").replace(",", " ") or None
    offer_name = (offer_name or "").replace(",", " ")
    if vendor:
        parts.append(norm_ws(vendor))
    if offer_name:
        parts.append(norm_ws(offer_name))

    if extra:
        for x in extra:
            x = norm_ws(x)
            if x:
                parts.append(x)

    parts.extend(CS_KEYWORDS_PHRASES)
    parts.extend(CS_KEYWORDS_CITIES)

    parts = _dedup_keep_order([norm_ws(p) for p in parts if norm_ws(p)])

    # анти-дубль: если есть "доставка по Казахстану" — убираем отдельный токен "доставка"
    low = [p.casefold() for p in parts]
    if "доставка по казахстану" in low and "доставка" in low:
        parts = [p for p in parts if p.casefold() != "доставка"]

    # лимит длины: сначала уходят города (они добавлены в конец)
    max_len = int(CS_KEYWORDS_MAX_LEN or 380)
    joined = ", ".join(parts)
    while len(joined) > max_len and len(parts) > 2:
        parts.pop()
        joined = ", ".join(parts)

    return joined
