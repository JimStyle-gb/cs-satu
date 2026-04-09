# -*- coding: utf-8 -*-
"""
Path: scripts/cs/writer.py

CS Writer — сборка XML/YML и запись файлов.

Роль файла:
- держит только writer-слой: escape, header/footer, FEED_META, build raw/final, запись файла;
- не содержит supplier-specific логики;
- отвечает за стабильный spacing вокруг <offers> и перед </offers>.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Sequence


OUTPUT_ENCODING_DEFAULT = "utf-8"
CURRENCY_ID_DEFAULT = "KZT"


# -----------------------------
# XML / HTML escape helpers
# -----------------------------

def xml_escape_text(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )



def xml_escape_attr(s: str) -> str:
    return xml_escape_text(s).replace('"', "&quot;")



def bool_to_xml(v: bool) -> str:
    return "true" if bool(v) else "false"



def xml_escape(s: str) -> str:
    """Экранирует текст для безопасного HTML/XML вывода."""
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# -----------------------------
# Базовая оболочка YML
# -----------------------------

def make_header(build_time: datetime, *, encoding: str = OUTPUT_ENCODING_DEFAULT) -> str:
    return (
        f'<?xml version="1.0" encoding="{encoding}"?>\n'
        f'<yml_catalog date="{build_time:%Y-%m-%d %H:%M:%S}">\n'
        "<shop><offers>"
    )



def make_footer() -> str:
    return "</offers>\n</shop>\n</yml_catalog>"



def ensure_footer_spacing(xml: str) -> str:
    """Стабилизирует пустые строки перед </offers>, не ломая остальной XML."""
    if not xml:
        return xml
    xml = re.sub(r"\n{3,}</offers>", "\n\n</offers>", xml)
    xml = re.sub(r"</offer>\n</offers>", "</offer>\n\n</offers>", xml)
    return xml


# -----------------------------
# FEED_META
# -----------------------------

def make_feed_meta(
    supplier: str,
    supplier_url: str,
    build_time: datetime,
    next_run: datetime,
    *,
    before: int,
    after: int,
    in_true: int,
    in_false: int,
) -> str:
    lines = [
        "<!--FEED_META",
        f"Поставщик                                  | {supplier}",
        f"URL поставщика                             | {supplier_url}",
        f"Время сборки (Алматы)                      | {build_time:%Y-%m-%d %H:%M:%S}",
        f"Ближайшая сборка (Алматы)                  | {next_run:%Y-%m-%d %H:%M:%S}",
        f"Сколько товаров у поставщика до фильтра    | {before}",
        f"Сколько товаров у поставщика после фильтра | {after}",
        f"Сколько товаров есть в наличии (true)      | {in_true}",
        f"Сколько товаров нет в наличии (false)      | {in_false}",
        "-->",
    ]
    return "\n".join(lines)


# -----------------------------
# Внутренние helper'ы
# -----------------------------

def _build_offers_xml_final(
    offers: Sequence["OfferOut"],
    *,
    currency_id: str,
    public_vendor: str,
    param_priority: Sequence[str] | None,
) -> str:
    if not offers:
        return ""
    return "\n\n".join(
        o.to_xml(
            currency_id=currency_id,
            public_vendor=public_vendor,
            param_priority=param_priority,
        )
        for o in offers
    )



def _build_offers_xml_raw(
    offers: Sequence["OfferOut"],
    *,
    currency_id: str,
) -> str:
    if not offers:
        return ""
    return "\n\n".join(o.to_xml_raw(currency_id=currency_id) for o in offers)



def _build_feed_xml(
    offers_xml: str,
    *,
    supplier: str,
    supplier_url: str,
    build_time: datetime,
    next_run: datetime,
    before: int,
    after: int,
    in_true: int,
    in_false: int,
    encoding: str,
) -> str:
    meta = make_feed_meta(
        supplier=supplier,
        supplier_url=supplier_url,
        build_time=build_time,
        next_run=next_run,
        before=before,
        after=after,
        in_true=in_true,
        in_false=in_false,
    )
    xml = make_header(build_time, encoding=encoding) + "\n" + meta + "\n\n" + offers_xml + "\n\n" + make_footer()
    return ensure_footer_spacing(xml)


# -----------------------------
# Сборка final / raw XML
# -----------------------------

def build_cs_feed_xml(
    offers: Sequence["OfferOut"],
    *,
    supplier: str,
    supplier_url: str,
    build_time: datetime,
    next_run: datetime,
    before: int,
    encoding: str = OUTPUT_ENCODING_DEFAULT,
    public_vendor: str = "CS",
    currency_id: str = CURRENCY_ID_DEFAULT,
    param_priority: Sequence[str] | None = None,
) -> str:
    after = len(offers)
    in_true = sum(1 for o in offers if getattr(o, "available", False))
    in_false = after - in_true
    offers_xml = _build_offers_xml_final(
        offers,
        currency_id=currency_id,
        public_vendor=public_vendor,
        param_priority=param_priority,
    )
    return _build_feed_xml(
        offers_xml,
        supplier=supplier,
        supplier_url=supplier_url,
        build_time=build_time,
        next_run=next_run,
        before=before,
        after=after,
        in_true=in_true,
        in_false=in_false,
        encoding=encoding,
    )



def build_cs_feed_xml_raw(
    offers: Sequence["OfferOut"],
    *,
    supplier: str,
    supplier_url: str,
    build_time: datetime,
    next_run: datetime,
    before: int,
    encoding: str = OUTPUT_ENCODING_DEFAULT,
    currency_id: str = CURRENCY_ID_DEFAULT,
) -> str:
    after = len(offers)
    in_true = sum(1 for o in offers if getattr(o, "available", False))
    in_false = after - in_true
    offers_xml = _build_offers_xml_raw(offers, currency_id=currency_id)
    return _build_feed_xml(
        offers_xml,
        supplier=supplier,
        supplier_url=supplier_url,
        build_time=build_time,
        next_run=next_run,
        before=before,
        after=after,
        in_true=in_true,
        in_false=in_false,
        encoding=encoding,
    )


# -----------------------------
# Запись файла
# -----------------------------

def write_if_changed(path: str, data: str, *, encoding: str = OUTPUT_ENCODING_DEFAULT) -> bool:
    """Перезаписывает файл только если контент реально изменился."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new_bytes = data.encode(encoding, errors="strict")

    if p.exists():
        old_bytes = p.read_bytes()
        if old_bytes == new_bytes:
            return False

    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(new_bytes)
    tmp.replace(p)
    return True
