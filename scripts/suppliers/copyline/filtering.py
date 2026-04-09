# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/copyline/filtering.py

CopyLine filtering layer.

Что делает:
- держит supplier-layer правила фильтрации;
- собирает стабильный filter_report для build summary;

Что не делает:
- не строит final offers;
- не переносит supplier-правила в shared core.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable, Sequence

import yaml

DEFAULT_INCLUDE_PREFIXES: list[str] = [
    "Drum",
    "Девелопер",
    "Драм-картридж",
    "Драм-юниты",
    "Кабель сетевой",
    "Картридж",
    "Картриджи",
    "Термоблок",
    "Тонер-картридж",
    "Чернила",
]

def safe_str(value: object) -> str:
    """Безопасно привести значение к строке."""
    return str(value).strip() if value is not None else ""

def compile_startswith_patterns(prefixes: Sequence[str]) -> list[re.Pattern[str]]:
    """Скомпилировать строгие regex по префиксам названия."""
    out: list[re.Pattern[str]] = []
    for raw in prefixes:
        val = safe_str(raw)
        if not val:
            continue
        out.append(re.compile(r"^\s*" + re.escape(val).replace(r"\ ", " ") + r"(?!\w)", re.I))
    return out

def title_allowed(title: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    """Проверить, разрешён ли title по фильтру префиксов."""
    title = safe_str(title)
    return bool(title) and any(pattern.search(title) for pattern in patterns)

def load_filter_config(path: str | None = None) -> dict:
    """Прочитать filter.yml; если файла нет — вернуть defaults."""
    if not path:
        return {"mode": "include", "include_prefixes": list(DEFAULT_INCLUDE_PREFIXES)}

    config_path = Path(path)
    if not config_path.exists():
        return {"mode": "include", "include_prefixes": list(DEFAULT_INCLUDE_PREFIXES)}

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}

    prefixes = data.get("include_prefixes") or data.get("prefixes") or DEFAULT_INCLUDE_PREFIXES
    return {
        "mode": safe_str(data.get("mode") or "include").lower() or "include",
        "include_prefixes": [safe_str(item) for item in prefixes if safe_str(item)],
    }

def filter_product_index(
    products: Iterable[dict],
    *,
    include_prefixes: Sequence[str] | None = None,
) -> tuple[list[dict], dict[str, object]]:
    """Отфильтровать supplier-index по title-prefix."""
    prefixes = list(include_prefixes or DEFAULT_INCLUDE_PREFIXES)
    patterns = compile_startswith_patterns(prefixes)

    before = 0
    kept: list[dict] = []
    rejected_total = 0
    kept_by_prefix: dict[str, int] = {prefix: 0 for prefix in prefixes}

    for product in products:
        before += 1
        title = safe_str(product.get("title"))
        if not title_allowed(title, patterns):
            rejected_total += 1
            continue

        kept.append(product)
        low = title.lower()
        for prefix in prefixes:
            prefix_low = prefix.lower()
            if low == prefix_low or low.startswith(prefix_low + " ") or low.startswith(prefix_low + "-"):
                kept_by_prefix[prefix] = kept_by_prefix.get(prefix, 0) + 1
                break

    report: dict[str, object] = {
        "mode": "include",
        "before": before,
        "after": len(kept),
        "rejected_total": rejected_total,
        "allowed_prefix_count": len(prefixes),
        "allowed_prefixes": prefixes,
        "kept_by_prefix": {key: value for key, value in kept_by_prefix.items() if value > 0},
        "reject_reasons": {"name_prefix_not_allowed": rejected_total},
    }
    return kept, report
