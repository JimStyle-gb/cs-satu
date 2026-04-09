# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/comportal/models.py

ComPortal models layer.

Что делает:
- держит carrier-структуры supplier-layer;
- задаёт контракт между source, filtering, builder и diagnostics;

Что не делает:
- не содержит бизнес-логики;
- не хранит regex или repair-эвристики.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass(slots=True)
class ParamItem:
    """Один исходный param."""
    name: str
    value: str
    source: str = "xml"

@dataclass(slots=True)
class CategoryRecord:
    """Одна source category."""
    category_id: str
    name: str
    parent_id: str = ""
    path: str = ""
    root_id: str = ""

@dataclass(slots=True)
class SourceOffer:
    """Один сырой offer из source YML ComPortal."""
    raw_id: str
    vendor_code: str
    category_id: str
    category_name: str
    category_path: str
    category_root_id: str
    name: str
    available_attr: str
    available_tag: str
    vendor: str
    description: str
    price_text: str
    currency_id: str
    url: str
    active: str
    delivery: str
    picture_urls: list[str] = field(default_factory=list)
    params: list[ParamItem] = field(default_factory=list)
    offer_el: Any | None = None

@dataclass(slots=True)
class BuildStats:
    """Базовая supplier-статистика сборки."""
    before: int = 0
    after: int = 0
    filtered_out: int = 0
    missing_picture_count: int = 0
    placeholder_picture_count: int = 0
    empty_vendor_count: int = 0

__all__ = [
    "ParamItem",
    "CategoryRecord",
    "SourceOffer",
    "BuildStats",
]
