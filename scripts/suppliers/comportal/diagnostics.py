# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/comportal/diagnostics.py

ComPortal diagnostics layer.

Что делает:
- печатает стабильный build summary;
- держит diagnostics вне build-оркестратора;

Что не делает:
- не меняет supplier raw/final данные;
- не подменяет builder/source слой.
"""
from __future__ import annotations

from pathlib import Path

from cs.core import OfferOut
from suppliers.comportal.models import BuildStats, SourceOffer

# -----------------------------
# Watch helpers
# -----------------------------

def build_watch_source_map(
    source_offers: list[SourceOffer],
    *,
    prefix: str,
    watch_ids: set[str],
) -> dict[str, dict[str, str]]:
    """Собрать карту source-offers для watch-списка."""
    out: dict[str, dict[str, str]] = {}
    prefix_upper = prefix.upper()

    for src in source_offers:
        vendor_code = src.vendor_code or ""
        oid = vendor_code if vendor_code.upper().startswith(prefix_upper) else f"{prefix}{vendor_code}" if vendor_code else ""
        if oid not in watch_ids:
            continue
        out[oid] = {
            "categoryId": src.category_id,
            "name": src.name,
        }
    return out

def make_watch_messages(
    *,
    watch_ids: set[str],
    watch_source: dict[str, dict[str, str]],
    watch_out: set[str],
) -> list[str]:
    """Построить watch-сообщения по найденным и потерянным товарам."""
    messages: list[str] = []
    for oid in sorted(watch_ids):
        src = watch_source.get(oid)
        if src and oid in watch_out:
            messages.append(f"OK {oid}: in feed | {src.get('categoryId', '')} | {src.get('name', '')}")
        elif src:
            messages.append(f"MISS {oid}: filtered out | {src.get('categoryId', '')} | {src.get('name', '')}")
        else:
            messages.append(f"MISS {oid}: not found in source")
    return messages

def write_watch_report(path: str | Path, lines: list[str]) -> None:
    """Записать watch-report в utf-8."""
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines).strip() + ("\n" if lines else ""), encoding="utf-8")

# -----------------------------
# Summary helpers
# -----------------------------

def summarize_offer_outs(offers: list[OfferOut]) -> dict[str, int]:
    """Посчитать сводку по готовым OfferOut."""
    with_picture = 0
    without_picture = 0
    with_vendor = 0
    without_vendor = 0
    with_native_desc = 0
    without_native_desc = 0
    available_true = 0
    available_false = 0

    for offer in offers:
        if offer.pictures:
            with_picture += 1
        else:
            without_picture += 1

        if (offer.vendor or "").strip():
            with_vendor += 1
        else:
            without_vendor += 1

        if (offer.native_desc or "").strip():
            with_native_desc += 1
        else:
            without_native_desc += 1

        if bool(offer.available):
            available_true += 1
        else:
            available_false += 1

    return {
        "total": len(offers),
        "with_picture": with_picture,
        "without_picture": without_picture,
        "with_vendor": with_vendor,
        "without_vendor": without_vendor,
        "with_native_desc": with_native_desc,
        "without_native_desc": without_native_desc,
        "available_true": available_true,
        "available_false": available_false,
    }

def summarize_source_offers(source_offers: list[SourceOffer]) -> dict[str, int]:
    """Посчитать сводку по source-offers."""
    with_picture = 0
    without_picture = 0
    with_vendor = 0
    without_vendor = 0

    for src in source_offers:
        if src.picture_urls:
            with_picture += 1
        else:
            without_picture += 1

        if (src.vendor or "").strip():
            with_vendor += 1
        else:
            without_vendor += 1

    return {
        "total": len(source_offers),
        "with_picture": with_picture,
        "without_picture": without_picture,
        "with_vendor": with_vendor,
        "without_vendor": without_vendor,
    }

def summarize_build_stats(stats: BuildStats) -> dict[str, int]:
    """Преобразовать supplier build stats в компактную сводку."""
    return {
        "before": int(stats.before),
        "after": int(stats.after),
        "filtered_out": int(stats.filtered_out),
        "missing_picture_count": int(stats.missing_picture_count),
        "placeholder_picture_count": int(stats.placeholder_picture_count),
        "empty_vendor_count": int(stats.empty_vendor_count),
    }
