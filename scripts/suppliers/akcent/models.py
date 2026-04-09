# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/akcent/models.py

AkCent supplier layer — внутренние модели.

Задача файла:
- держать компактные dataclass-модели supplier-layer;
- не тащить business-логику сюда;
- дать единый типовой контракт для source / filtering / builder / diagnostics.

Важно:
- models.py не должен знать про core-эвристики;
- models.py не должен тянуть supplier-specific regex;
- здесь живут только структуры данных.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# -----------------------------
# Source / params carriers
# -----------------------------

@dataclass(slots=True)
class ParamItem:
    """Родной param поставщика с указанием источника."""

    name: str
    value: str
    source: str = "xml"


@dataclass(slots=True)
class SourceOffer:
    """Честный source-object без нормализации бизнес-полей."""

    raw_id: str
    offer_id: str = ""
    article: str = ""
    category_id: str = ""
    name: str = ""
    type_text: str = ""
    available_attr: str = ""
    available_tag: str = ""
    vendor: str = ""
    model: str = ""
    description: str = ""
    manufacturer_warranty: str = ""
    stock_text: str = ""
    dealer_price_text: str = ""
    rrp_price_text: str = ""
    price_text: str = ""
    url: str = ""
    picture_urls: list[str] = field(default_factory=list)
    raw_params: list[tuple[str, str]] = field(default_factory=list)
    offer_el: Any | None = None


# -----------------------------
# Normalized / builder carriers
# -----------------------------

@dataclass(slots=True)
class NormalizedOfferBasics:
    """Нормализованная базовая часть supplier-offer до сборки OfferOut."""

    oid: str
    article: str = ""
    name: str = ""
    vendor: str = ""
    model: str = ""
    available: bool = True
    price_in: float = 0.0
    warranty: str = ""
    dealer_price_text: str = ""
    rrp_price_text: str = ""
    source_price_text: str = ""


@dataclass(slots=True)
class ParamBuildResult:
    """Небольшой carrier для supplier-side результата params pipeline."""

    kind: str = ""
    params: list[tuple[str, str]] = field(default_factory=list)
    extra_info: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BuildStats:
    """Сводная статистика supplier-layer."""

    before: int = 0
    after: int = 0
    filtered_out: int = 0
    placeholder_picture_count: int = 0
    desc_params_added: int = 0
    extra_info_items: int = 0
    watch_hits: int = 0


@dataclass(slots=True)
class WatchMessage:
    """Маленький container под watched-offer сообщения."""

    oid: str = ""
    name: str = ""
    kind: str = ""
    message: str = ""
