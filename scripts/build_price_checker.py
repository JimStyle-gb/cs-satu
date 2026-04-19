# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from zoneinfo import ZoneInfo
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ALMATY_TZ = ZoneInfo("Asia/Almaty")
ROOT = Path(__file__).resolve().parents[1]
PRICE_FILE = ROOT / "docs" / "Price.yml"
DOCS_DIR = ROOT / "docs"
RAW_DIR = DOCS_DIR / "raw"
SUMMARY_REPORT_FILE = RAW_DIR / "price_checker_report.txt"
DETAILS_REPORT_FILE = RAW_DIR / "price_checker_details.txt"
LAST_SUCCESS_FILE = RAW_DIR / "price_checker_last_success.json"
UNRESOLVED_FILE = RAW_DIR / "category_id_unresolved.txt"

EXPECTED_SUPPLIERS = ("AkCent", "AlStyle", "ComPortal", "CopyLine", "VTT")
PLACEHOLDER_URL = "https://placehold.co/800x800/png?text=No+Photo"
DETAIL_LIMIT_DEFAULT = 300
DETAIL_LIMIT_CRITICAL = 1000

WARN_TOTAL_DELTA_PCT = 5.0
WARN_SUPPLIER_DELTA_PCT = 10.0
WARN_READY_TO_SHIP_NO_PRICE_DELTA_ABS = 50
WARN_READY_TO_SHIP_NO_PRICE_DELTA_PCT = 10.0
WARN_PLACEHOLDER_DELTA_ABS = 50
WARN_PLACEHOLDER_DELTA_PCT = 10.0
WARN_FALSE_DELTA_PCT = 20.0

FAIL_TOTAL_DROP_PCT = 15.0
FAIL_SUPPLIER_DROP_PCT = 25.0

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


@dataclass
class OfferEntry:
    supplier: str
    offer_id: str
    vendor_code: str
    name: str
    category_id: str = ""
    portal_category_id: str = ""
    price: str = ""
    in_stock: bool = False
    picture: str = ""


@dataclass
class SupplierSummary:
    total: int = 0
    available: int = 0
    unavailable: int = 0
    ready_to_ship_no_price: int = 0
    placeholder: int = 0
    excluded_no_categoryid: int = 0
    no_satu_category: int = 0

    def to_json(self) -> dict:
        return {
            "total": self.total,
            "available": self.available,
            "unavailable": self.unavailable,
            "ready_to_ship_no_price": self.ready_to_ship_no_price,
            "placeholder": self.placeholder,
            "excluded_no_categoryid": self.excluded_no_categoryid,
            "no_satu_category": self.no_satu_category,
        }

    @classmethod
    def from_json(cls, data: dict) -> "SupplierSummary":
        return cls(
            total=int(data.get("total", 0)),
            available=int(data.get("available", 0)),
            unavailable=int(data.get("unavailable", 0)),
            ready_to_ship_no_price=int(data.get("ready_to_ship_no_price", data.get("price100", 0))),
            placeholder=int(data.get("placeholder", 0)),
            excluded_no_categoryid=int(data.get("excluded_no_categoryid", 0)),
            no_satu_category=int(data.get("no_satu_category", 0)),
        )


@dataclass
class DuplicateGroup:
    key: str
    entries: List[OfferEntry] = field(default_factory=list)


@dataclass
class Metrics:
    build_time: str = ""
    total: int = 0
    available_true: int = 0
    available_false: int = 0
    ready_to_ship_no_price: int = 0
    placeholder: int = 0
    empty_category: int = 0
    invalid_category: int = 0
    unknown_satu: int = 0
    excluded_unmapped_total: int = 0
    duplicate_offer_id_groups: int = 0
    duplicate_vendorcode_groups: int = 0
    supplier_summary: Dict[str, SupplierSummary] = field(default_factory=dict)
    excluded_unmapped_by_supplier: Dict[str, int] = field(default_factory=dict)
    excluded_details: List[str] = field(default_factory=list)
    no_satu_category_details: List[OfferEntry] = field(default_factory=list)
    ready_to_ship_no_price_details: List[OfferEntry] = field(default_factory=list)
    placeholder_details: List[OfferEntry] = field(default_factory=list)
    duplicate_offer_id_details: List[DuplicateGroup] = field(default_factory=list)
    duplicate_vendorcode_details: List[DuplicateGroup] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "build_time": self.build_time,
            "total": self.total,
            "available_true": self.available_true,
            "available_false": self.available_false,
            "ready_to_ship_no_price": self.ready_to_ship_no_price,
            "placeholder": self.placeholder,
            "empty_category": self.empty_category,
            "invalid_category": self.invalid_category,
            "unknown_satu": self.unknown_satu,
            "excluded_unmapped_total": self.excluded_unmapped_total,
            "duplicate_offer_id_groups": self.duplicate_offer_id_groups,
            "duplicate_vendorcode_groups": self.duplicate_vendorcode_groups,
            "excluded_unmapped_by_supplier": self.excluded_unmapped_by_supplier,
            "supplier_summary": {k: v.to_json() for k, v in self.supplier_summary.items()},
        }

    @classmethod
    def from_json(cls, data: dict) -> "Metrics":
        supplier_summary = {
            k: SupplierSummary.from_json(v)
            for k, v in (data.get("supplier_summary") or {}).items()
        }
        return cls(
            build_time=str(data.get("build_time", "")),
            total=int(data.get("total", 0)),
            available_true=int(data.get("available_true", 0)),
            available_false=int(data.get("available_false", 0)),
            ready_to_ship_no_price=int(data.get("ready_to_ship_no_price", data.get("price100", 0))),
            placeholder=int(data.get("placeholder", 0)),
            empty_category=int(data.get("empty_category", 0)),
            invalid_category=int(data.get("invalid_category", 0)),
            unknown_satu=int(data.get("unknown_satu", 0)),
            excluded_unmapped_total=int(data.get("excluded_unmapped_total", 0)),
            duplicate_offer_id_groups=int(data.get("duplicate_offer_id_groups", 0)),
            duplicate_vendorcode_groups=int(data.get("duplicate_vendorcode_groups", 0)),
            excluded_unmapped_by_supplier={
                k: int(v) for k, v in (data.get("excluded_unmapped_by_supplier") or {}).items()
            },
            supplier_summary=supplier_summary,
        )


def now_almaty() -> datetime:
    return datetime.now(ALMATY_TZ)


def fmt_dt(dt: datetime) -> str:
    dt = dt.astimezone(ALMATY_TZ)
    return f"{dt.day} {MONTHS_RU[dt.month]} {dt.year} г. {dt:%H:%M:%S}"


def fmt_delta(old: int | None, new: int) -> str:
    if old is None:
        return "(нет базы)"
    diff = new - old
    if diff > 0:
        return f"(+{diff})"
    if diff < 0:
        return f"({diff})"
    return "(0)"


def pct_change(old: int, new: int) -> float:
    if old <= 0:
        return 0.0 if new <= 0 else 100.0
    return abs(new - old) / old * 100.0


def has_warn_abs_pct(old: int, new: int, abs_threshold: int, pct_threshold: float) -> bool:
    return (new - old) >= abs_threshold or pct_change(old, new) >= pct_threshold


def parse_build_time_from_feed_meta(text: str) -> str:
    match = re.search(r"Price\s*\n.*?Время сборки \(Алматы\)\s*\|\s*([^\n\r]+)", text, re.S)
    if not match:
        return ""
    raw = match.group(1).strip()
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ALMATY_TZ)
        return fmt_dt(dt)
    except Exception:
        return raw


def load_xml_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def normalize_spaces(value: str) -> str:
    return " ".join((value or "").split())


def extract_offer_supplier(offer_id: str) -> str:
    oid = (offer_id or "").upper()
    if oid.startswith("AC"):
        return "AkCent"
    if oid.startswith("AS"):
        return "AlStyle"
    if oid.startswith("CP"):
        return "ComPortal"
    if oid.startswith("CL"):
        return "CopyLine"
    if oid.startswith("VT"):
        return "VTT"
    return ""


def make_offer_entry(offer: ET.Element, supplier_hint: str = "") -> OfferEntry:
    offer_id = (offer.get("id") or "").strip()
    supplier = supplier_hint or extract_offer_supplier(offer_id) or "Неизвестно"
    return OfferEntry(
        supplier=supplier,
        offer_id=offer_id,
        vendor_code=(offer.findtext("vendorCode") or "").strip(),
        name=normalize_spaces(offer.findtext("name") or ""),
        category_id=(offer.findtext("categoryId") or "").strip(),
        portal_category_id=(offer.findtext("portal_category_id") or "").strip(),
        price=(offer.findtext("price") or "").strip(),
        in_stock=str(offer.get("in_stock", "")).strip().lower() == "true",
        picture=next(((pic.text or "").strip() for pic in offer.findall("picture") if (pic.text or "").strip()), ""),
    )


def parse_unresolved_blocks(path: Path) -> Tuple[Dict[str, int], List[str]]:
    counts = {name: 0 for name in EXPECTED_SUPPLIERS}
    details: List[str] = []
    if not path.exists():
        return counts, details

    text = path.read_text(encoding="utf-8", errors="ignore")
    for supplier in EXPECTED_SUPPLIERS:
        block_re = re.compile(rf"(?ms)^## START {re.escape(supplier)}\n(.*?)^## END {re.escape(supplier)}\s*$")
        match = block_re.search(text)
        if not match:
            continue
        block = match.group(1)
        count_match = re.search(r"Товаров без categoryId:\s*(\d+)", block)
        if count_match:
            counts[supplier] = int(count_match.group(1))
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            if stripped.startswith("Поставщик:"):
                continue
            if stripped.startswith("Товаров без categoryId:"):
                continue
            details.append(f"{supplier} | excluded_no_categoryId | {stripped}")
    return counts, details


def build_top_suppliers(metrics_by_supplier: Dict[str, int]) -> str:
    pairs = [(supplier, count) for supplier, count in metrics_by_supplier.items() if count > 0]
    if not pairs:
        return "нет"
    pairs.sort(key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{supplier} {count}" for supplier, count in pairs[:3])


def limit_lines(lines: List[str], limit: int) -> List[str]:
    if len(lines) <= limit:
        return lines
    limited = list(lines[:limit])
    limited.append(f"... показаны первые {limit} строк, всего записей: {len(lines)}")
    return limited


def format_entry_line(entry: OfferEntry, issue: str) -> str:
    parts = [
        entry.supplier,
        issue,
        f"id={entry.offer_id or '-'}",
        f"vendorCode={entry.vendor_code or '-'}",
    ]
    if entry.category_id:
        parts.append(f"categoryId={entry.category_id}")
    if entry.portal_category_id:
        parts.append(f"portal_category_id={entry.portal_category_id}")
    if entry.price:
        parts.append(f"price={entry.price}")
    if entry.in_stock:
        parts.append("in_stock=true")
    if entry.picture:
        parts.append(f"picture={entry.picture}")
    parts.append(f"name={entry.name or '-'}")
    return " | ".join(parts)


def format_duplicate_groups(groups: List[DuplicateGroup], kind: str) -> List[str]:
    if not groups:
        return ["нет"]
    lines: List[str] = []
    for group in groups:
        lines.append(f"{kind}={group.key} | count={len(group.entries)}")
        for entry in group.entries:
            lines.append(f"- {entry.supplier} | id={entry.offer_id or '-'} | vendorCode={entry.vendor_code or '-'} | name={entry.name or '-'}")
    return lines


def collect_metrics(price_path: Path) -> Metrics:
    if not price_path.exists():
        raise FileNotFoundError("Файл Price.yml не найден.")
    text = load_xml_text(price_path)
    if not text.strip():
        raise ValueError("Файл Price.yml пустой.")

    try:
        root = ET.fromstring(text)
    except Exception as exc:
        raise ValueError(f"XML в Price повреждён: {exc}") from exc

    shop = root.find("shop")
    if shop is None:
        raise ValueError("В Price отсутствует блок shop.")
    categories = shop.find("categories")
    offers = shop.find("offers")
    if categories is None:
        raise ValueError("В Price отсутствует блок categories.")
    if offers is None:
        raise ValueError("В Price отсутствует блок offers.")

    category_portal_ids = {
        str(cat.get("id", "")).strip(): str(cat.get("portal_id", "")).strip()
        for cat in categories.findall("category")
        if str(cat.get("id", "")).strip()
    }

    supplier_summary = {name: SupplierSummary() for name in EXPECTED_SUPPLIERS}
    excluded_by_supplier, excluded_details = parse_unresolved_blocks(UNRESOLVED_FILE)
    for supplier, count in excluded_by_supplier.items():
        supplier_summary.setdefault(supplier, SupplierSummary()).excluded_no_categoryid = count

    offer_id_map: Dict[str, List[OfferEntry]] = {}
    vendor_code_map: Dict[str, List[OfferEntry]] = {}
    no_satu_category_details: List[OfferEntry] = []
    ready_to_ship_no_price_details: List[OfferEntry] = []
    placeholder_details: List[OfferEntry] = []

    total = 0
    available_true = 0
    available_false = 0
    ready_to_ship_no_price = 0
    placeholder = 0
    empty_category = 0
    invalid_category = 0
    unknown_satu = 0

    for offer in offers.findall("offer"):
        entry = make_offer_entry(offer)
        supplier = entry.supplier if entry.supplier in supplier_summary else ""
        if supplier:
            supplier_summary[supplier].total += 1

        total += 1
        avail = str(offer.get("available", "")).strip().lower()
        if avail == "true":
            available_true += 1
            if supplier:
                supplier_summary[supplier].available += 1
        elif avail == "false":
            available_false += 1
            if supplier:
                supplier_summary[supplier].unavailable += 1

        if entry.offer_id:
            offer_id_map.setdefault(entry.offer_id, []).append(entry)
        if entry.vendor_code:
            vendor_code_map.setdefault(entry.vendor_code, []).append(entry)

        cid = entry.category_id
        if not cid:
            empty_category += 1
        elif cid not in category_portal_ids:
            invalid_category += 1

        has_satu_category = bool(entry.portal_category_id)
        if cid and not has_satu_category:
            has_satu_category = bool(category_portal_ids.get(cid, ""))
        if not has_satu_category:
            unknown_satu += 1
            no_satu_category_details.append(entry)
            if supplier:
                supplier_summary[supplier].no_satu_category += 1

        if not entry.price and entry.in_stock:
            ready_to_ship_no_price += 1
            ready_to_ship_no_price_details.append(entry)
            if supplier:
                supplier_summary[supplier].ready_to_ship_no_price += 1

        pictures = [(pic.text or "").strip() for pic in offer.findall("picture") if (pic.text or "").strip()]
        if any(pic == PLACEHOLDER_URL for pic in pictures):
            placeholder += 1
            placeholder_details.append(entry)
            if supplier:
                supplier_summary[supplier].placeholder += 1

    duplicate_offer_groups = [DuplicateGroup(key=k, entries=v) for k, v in offer_id_map.items() if len(v) > 1]
    duplicate_vendorcode_groups = [DuplicateGroup(key=k, entries=v) for k, v in vendor_code_map.items() if len(v) > 1]
    duplicate_offer_groups.sort(key=lambda group: (group.key or ""))
    duplicate_vendorcode_groups.sort(key=lambda group: (group.key or ""))

    missing_suppliers = [name for name, info in supplier_summary.items() if info.total <= 0]
    if missing_suppliers:
        raise ValueError(
            "В Price отсутствует один или несколько поставщиков: " + ", ".join(missing_suppliers)
        )

    return Metrics(
        build_time=parse_build_time_from_feed_meta(text),
        total=total,
        available_true=available_true,
        available_false=available_false,
        ready_to_ship_no_price=ready_to_ship_no_price,
        placeholder=placeholder,
        empty_category=empty_category,
        invalid_category=invalid_category,
        unknown_satu=unknown_satu,
        excluded_unmapped_total=sum(excluded_by_supplier.values()),
        duplicate_offer_id_groups=len(duplicate_offer_groups),
        duplicate_vendorcode_groups=len(duplicate_vendorcode_groups),
        supplier_summary=supplier_summary,
        excluded_unmapped_by_supplier=excluded_by_supplier,
        excluded_details=excluded_details,
        no_satu_category_details=no_satu_category_details,
        ready_to_ship_no_price_details=ready_to_ship_no_price_details,
        placeholder_details=placeholder_details,
        duplicate_offer_id_details=duplicate_offer_groups,
        duplicate_vendorcode_details=duplicate_vendorcode_groups,
    )


def load_baseline() -> Metrics | None:
    if not LAST_SUCCESS_FILE.exists():
        return None
    try:
        data = json.loads(LAST_SUCCESS_FILE.read_text(encoding="utf-8"))
        return Metrics.from_json(data)
    except Exception:
        return None


def save_baseline(metrics: Metrics) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    LAST_SUCCESS_FILE.write_text(
        json.dumps(metrics.to_json(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def evaluate(metrics: Metrics, baseline: Metrics | None) -> Tuple[str, str]:
    if metrics.empty_category > 0:
        return "НЕУСПЕШНО", "В итоговом Price есть товары без categoryId."
    if metrics.invalid_category > 0:
        return "НЕУСПЕШНО", "В итоговом Price есть товары с categoryId, которого нет в списке categories."
    if metrics.unknown_satu > 0:
        return "НЕУСПЕШНО", "В итоговом Price есть товары без категории Satu."
    if metrics.duplicate_offer_id_groups > 0:
        return "НЕУСПЕШНО", "В итоговом Price обнаружены дубли offer id."
    if metrics.duplicate_vendorcode_groups > 0:
        return "НЕУСПЕШНО", "В итоговом Price обнаружены дубли vendorCode."

    if baseline is None:
        if metrics.excluded_unmapped_total > 0:
            return "ТРЕБУЕТ ВНИМАНИЯ", "Есть товары, которые не вошли в final из-за отсутствия categoryId."
        return "УСПЕШНО", "Критичных проблем не обнаружено."

    if baseline.total > 0 and metrics.total < baseline.total and pct_change(baseline.total, metrics.total) > FAIL_TOTAL_DROP_PCT:
        return "НЕУСПЕШНО", "Общее количество товаров в Price просело сильнее допустимого порога."

    for supplier in EXPECTED_SUPPLIERS:
        old_total = baseline.supplier_summary.get(supplier, SupplierSummary()).total
        new_total = metrics.supplier_summary.get(supplier, SupplierSummary()).total
        if old_total > 0 and new_total < old_total and pct_change(old_total, new_total) > FAIL_SUPPLIER_DROP_PCT:
            return "НЕУСПЕШНО", f"У поставщика {supplier} количество товаров просело сильнее допустимого порога."

    if metrics.excluded_unmapped_total > 0:
        return "ТРЕБУЕТ ВНИМАНИЯ", "Есть новые товары, которые не вошли в final из-за отсутствия categoryId."

    if pct_change(baseline.total, metrics.total) > WARN_TOTAL_DELTA_PCT:
        return "ТРЕБУЕТ ВНИМАНИЯ", "Общее количество товаров в Price изменилось сильнее допустимого порога."

    for supplier in EXPECTED_SUPPLIERS:
        old_total = baseline.supplier_summary.get(supplier, SupplierSummary()).total
        new_total = metrics.supplier_summary.get(supplier, SupplierSummary()).total
        if pct_change(old_total, new_total) > WARN_SUPPLIER_DELTA_PCT:
            return "ТРЕБУЕТ ВНИМАНИЯ", f"У поставщика {supplier} количество товаров изменилось сильнее допустимого порога."

    if has_warn_abs_pct(baseline.ready_to_ship_no_price, metrics.ready_to_ship_no_price, WARN_READY_TO_SHIP_NO_PRICE_DELTA_ABS, WARN_READY_TO_SHIP_NO_PRICE_DELTA_PCT):
        return "ТРЕБУЕТ ВНИМАНИЯ", "Слишком сильно выросло количество товаров, готовых к отправке без цены."

    if has_warn_abs_pct(baseline.placeholder, metrics.placeholder, WARN_PLACEHOLDER_DELTA_ABS, WARN_PLACEHOLDER_DELTA_PCT):
        return "ТРЕБУЕТ ВНИМАНИЯ", "Слишком сильно выросло количество товаров с заглушкой фото."

    if baseline.available_false > 0 and metrics.available_false > baseline.available_false and pct_change(baseline.available_false, metrics.available_false) > WARN_FALSE_DELTA_PCT:
        return "ТРЕБУЕТ ВНИМАНИЯ", "Слишком сильно выросло количество товаров со статусом 'нет в наличии'."

    return "УСПЕШНО", "Критичных проблем не обнаружено."


def build_summary_report(status: str, reason: str, metrics: Metrics, baseline: Metrics | None, checked_at: datetime) -> str:
    icon = {"УСПЕШНО": "✅", "ТРЕБУЕТ ВНИМАНИЯ": "⚠️", "НЕУСПЕШНО": "❌"}.get(status, "ℹ️")
    top_ready_to_ship_no_price = build_top_suppliers({supplier: info.ready_to_ship_no_price for supplier, info in metrics.supplier_summary.items()})
    top_placeholder = build_top_suppliers({supplier: info.placeholder for supplier, info in metrics.supplier_summary.items()})
    top_excluded = build_top_suppliers(metrics.excluded_unmapped_by_supplier)
    satu_mapping_status = "УСПЕШНО" if metrics.unknown_satu == 0 else "НЕУСПЕШНО"

    lines = [
        f"{icon} Price — {status}",
        f"Время проверки: {fmt_dt(checked_at)}",
        f"Время сборки Price: {metrics.build_time or '-'}",
        "",
        f"Товаров в Price: {metrics.total} {fmt_delta(baseline.total if baseline else None, metrics.total)}",
        f"Есть в наличии: {metrics.available_true} {fmt_delta(baseline.available_true if baseline else None, metrics.available_true)}",
        f"Нет в наличии: {metrics.available_false} {fmt_delta(baseline.available_false if baseline else None, metrics.available_false)}",
        "",
        f"Товары без цены: {metrics.ready_to_ship_no_price} {fmt_delta(baseline.ready_to_ship_no_price if baseline else None, metrics.ready_to_ship_no_price)}",
        f"С заглушкой фото: {metrics.placeholder} {fmt_delta(baseline.placeholder if baseline else None, metrics.placeholder)}",
        f"Без categoryId: {metrics.empty_category}",
        f"С невалидным categoryId: {metrics.invalid_category}",
        f"Без категории Satu: {metrics.unknown_satu}",
        f"Не вошло в final без categoryId: {metrics.excluded_unmapped_total} {fmt_delta(baseline.excluded_unmapped_total if baseline else None, metrics.excluded_unmapped_total)}",
        "",
        "Проблемные хвосты по поставщикам",
        "",
        "Товары без цены:",
    ]
    if top_ready_to_ship_no_price == 'нет':
        lines.append('нет')
    else:
        lines.extend(top_ready_to_ship_no_price.split(', '))
    lines.extend([
        "",
        "Заглушка фото:",
    ])
    if top_placeholder == 'нет':
        lines.append('нет')
    else:
        lines.extend(top_placeholder.split(', '))
    lines.extend([
        "",
        f"Не вошло в final без categoryId: {top_excluded}",
        "",
        f"Привязка к категориям Satu: {satu_mapping_status}",
        f"Статус проверки Price: {status}",
        "",
        f"Причина: {reason}",
    ])
    return "\n".join(lines).strip() + "\n"


def build_telegram_summary_html(status: str, reason: str, metrics: Metrics, baseline: Metrics | None, checked_at: datetime) -> str:
    icon = {"УСПЕШНО": "✅", "ТРЕБУЕТ ВНИМАНИЯ": "⚠️", "НЕУСПЕШНО": "❌"}.get(status, "ℹ️")
    top_ready_to_ship_no_price = build_top_suppliers({supplier: info.ready_to_ship_no_price for supplier, info in metrics.supplier_summary.items()})
    top_placeholder = build_top_suppliers({supplier: info.placeholder for supplier, info in metrics.supplier_summary.items()})
    top_excluded = build_top_suppliers(metrics.excluded_unmapped_by_supplier)
    satu_mapping_status = "УСПЕШНО" if metrics.unknown_satu == 0 else "НЕУСПЕШНО"

    def esc(text: str) -> str:
        return html.escape(text)

    def field(label: str, value: str) -> str:
        return f"<b>{esc(label)}</b> {esc(value)}"

    lines = [
        f"{icon} <b>Price — {esc(status)}</b>",
        "",
        field("Время проверки:", fmt_dt(checked_at)),
        field("Время сборки Price:", metrics.build_time or '-'),
        "",
        field("Товаров в Price:", f"{metrics.total} {fmt_delta(baseline.total if baseline else None, metrics.total)}"),
        field("Есть в наличии:", f"{metrics.available_true} {fmt_delta(baseline.available_true if baseline else None, metrics.available_true)}"),
        field("Нет в наличии:", f"{metrics.available_false} {fmt_delta(baseline.available_false if baseline else None, metrics.available_false)}"),
        "",
        field("Товары без цены:", f"{metrics.ready_to_ship_no_price} {fmt_delta(baseline.ready_to_ship_no_price if baseline else None, metrics.ready_to_ship_no_price)}"),
        field("С заглушкой фото:", f"{metrics.placeholder} {fmt_delta(baseline.placeholder if baseline else None, metrics.placeholder)}"),
        field("Без categoryId:", str(metrics.empty_category)),
        field("С невалидным categoryId:", str(metrics.invalid_category)),
        field("Без категории Satu:", str(metrics.unknown_satu)),
        field("Не вошло в final без categoryId:", f"{metrics.excluded_unmapped_total} {fmt_delta(baseline.excluded_unmapped_total if baseline else None, metrics.excluded_unmapped_total)}"),
        "",
        "<b>Проблемные хвосты по поставщикам</b>",
        "",
        "<b>Товары без цены:</b>",
    ]
    if top_ready_to_ship_no_price == 'нет':
        lines.append('нет')
    else:
        lines.extend(esc(item) for item in top_ready_to_ship_no_price.split(', '))
    lines.extend([
        "",
        "<b>Заглушка фото:</b>",
    ])
    if top_placeholder == 'нет':
        lines.append('нет')
    else:
        lines.extend(esc(item) for item in top_placeholder.split(', '))
    lines.extend([
        "",
        field("Не вошло в final без categoryId:", top_excluded),
        "",
        field("Привязка к категориям Satu:", satu_mapping_status),
        field("Статус проверки Price:", status),
        "",
        field("Причина:", reason),
    ])
    return "\n".join(lines).strip() + "\n"


def build_supplier_summary_lines(metrics: Metrics) -> List[str]:
    lines: List[str] = []
    for supplier in EXPECTED_SUPPLIERS:
        info = metrics.supplier_summary.get(supplier, SupplierSummary())
        lines.append(
            f"{supplier} | всего={info.total} | в наличии={info.available} | нет в наличии={info.unavailable} "
            f"| без цены={info.ready_to_ship_no_price} | заглушка фото={info.placeholder} "
            f"| не вошло в final без categoryId={info.excluded_no_categoryid} | без категории Satu={info.no_satu_category}"
        )
    return lines or ["нет"]


def build_top_problem_lines(metrics: Metrics) -> List[str]:
    blocks = []
    blocks.append("Товары без цены:")
    for supplier, count in sorted(((s, i.ready_to_ship_no_price) for s, i in metrics.supplier_summary.items() if i.ready_to_ship_no_price > 0), key=lambda x: (-x[1], x[0]))[:5]:
        blocks.append(f"- {supplier}: {count}")
    if blocks[-1] == "Товары без цены:":
        blocks.append("нет")

    blocks.append("")
    blocks.append("Заглушка фото:")
    added = False
    for supplier, count in sorted(((s, i.placeholder) for s, i in metrics.supplier_summary.items() if i.placeholder > 0), key=lambda x: (-x[1], x[0]))[:5]:
        blocks.append(f"- {supplier}: {count}")
        added = True
    if not added:
        blocks.append("нет")

    blocks.append("")
    blocks.append("Не вошло в final без categoryId:")
    added = False
    for supplier, count in sorted(((s, c) for s, c in metrics.excluded_unmapped_by_supplier.items() if c > 0), key=lambda x: (-x[1], x[0]))[:5]:
        blocks.append(f"- {supplier}: {count}")
        added = True
    if not added:
        blocks.append("нет")
    return blocks


def build_details_report(status: str, reason: str, metrics: Metrics, checked_at: datetime) -> str:
    lines: List[str] = [
        "ПОДРОБНЫЙ ОТЧЁТ PRICE CHECKER",
        f"Сформирован: {fmt_dt(checked_at)}",
        f"Время сборки Price: {metrics.build_time or '-'}",
        f"Итоговый статус: {status}",
        f"Причина: {reason}",
        "",
        "======================================================================",
        "СВОДКА",
        "======================================================================",
        f"Всего товаров в Price: {metrics.total}",
        f"В наличии: {metrics.available_true}",
        f"Нет в наличии: {metrics.available_false}",
        "",
        f"Товары без цены: {metrics.ready_to_ship_no_price}",
        f"С заглушкой фото: {metrics.placeholder}",
        f"Без categoryId в Price: {metrics.empty_category}",
        f"С невалидным categoryId в Price: {metrics.invalid_category}",
        f"Без категории Satu: {metrics.unknown_satu}",
        f"Не вошло в final без categoryId: {metrics.excluded_unmapped_total}",
        "",
        f"Групп дублей offer id: {metrics.duplicate_offer_id_groups}",
        f"Групп дублей vendorCode: {metrics.duplicate_vendorcode_groups}",
        f"Привязка к категориям Satu: {'УСПЕШНО' if metrics.unknown_satu == 0 else 'НЕУСПЕШНО'}",
        f"Статус проверки Price: {status}",
        "",
        "======================================================================",
        "СВОДКА ПО ПОСТАВЩИКАМ",
        "======================================================================",
    ]
    lines.extend(build_supplier_summary_lines(metrics))

    sections: List[Tuple[str, List[str]]] = [
        (
            "НЕ ВОШЛИ В FINAL ИЗ-ЗА ОТСУТСТВИЯ CATEGORYID",
            metrics.excluded_details or ["нет"],
        ),
        (
            "ТОВАРЫ БЕЗ КАТЕГОРИИ SATU",
            limit_lines([format_entry_line(entry, "no_satu_category") for entry in metrics.no_satu_category_details], DETAIL_LIMIT_CRITICAL) or ["нет"],
        ),
        (
            "ТОВАРЫ БЕЗ ЦЕНЫ",
            limit_lines([format_entry_line(entry, "ready_to_ship_no_price") for entry in metrics.ready_to_ship_no_price_details], DETAIL_LIMIT_DEFAULT) or ["нет"],
        ),
        (
            "ТОВАРЫ С ЗАГЛУШКОЙ ФОТО",
            limit_lines([format_entry_line(entry, "placeholder_photo") for entry in metrics.placeholder_details], DETAIL_LIMIT_DEFAULT) or ["нет"],
        ),
        (
            "ДУБЛИ OFFER ID",
            format_duplicate_groups(metrics.duplicate_offer_id_details, "offer_id"),
        ),
        (
            "ДУБЛИ VENDORCODE",
            format_duplicate_groups(metrics.duplicate_vendorcode_details, "vendorCode"),
        ),
        (
            "ПОСТАВЩИКИ С НАИБОЛЬШИМ КОЛИЧЕСТВОМ ПРОБЛЕМ",
            build_top_problem_lines(metrics),
        ),
    ]

    for title, body_lines in sections:
        lines.extend([
            "",
            "======================================================================",
            title,
            "======================================================================",
        ])
        lines.extend(body_lines if body_lines else ["нет"])

    return "\n".join(lines).strip() + "\n"


def write_reports(summary_text: str, details_text: str) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_REPORT_FILE.write_text(summary_text, encoding="utf-8")
    DETAILS_REPORT_FILE.write_text(details_text, encoding="utf-8")


def send_telegram(text: str) -> None:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
        "parse_mode": "HTML",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def build_failure_summary(reason: str, checked_at: datetime) -> str:
    lines = [
        "❌ Price — НЕУСПЕШНО",
        "",
        f"Время проверки: {fmt_dt(checked_at)}",
        "",
        f"Причина: {reason}",
        "",
        "Привязка к категориям Satu: НЕИЗВЕСТНО",
        "Статус проверки Price: НЕУСПЕШНО",
    ]
    return "\n".join(lines).strip() + "\n"


def build_failure_details(reason: str, checked_at: datetime) -> str:
    return "\n".join([
        "ПОДРОБНЫЙ ОТЧЁТ PRICE CHECKER",
        f"Сформирован: {fmt_dt(checked_at)}",
        "Итоговый статус: НЕУСПЕШНО",
        f"Причина: {reason}",
        "",
        "Подробные товарные секции не сформированы, потому что checker завершился с ошибкой до полного формирования отчёта.",
    ]).strip() + "\n"


def main() -> int:
    checked_at = now_almaty()
    baseline = load_baseline()
    try:
        metrics = collect_metrics(PRICE_FILE)
        status, reason = evaluate(metrics, baseline)
        summary_text = build_summary_report(status, reason, metrics, baseline, checked_at)
        details_text = build_details_report(status, reason, metrics, checked_at)
        telegram_text = build_telegram_summary_html(status, reason, metrics, baseline, checked_at)
        write_reports(summary_text, details_text)
        try:
            send_telegram(telegram_text)
        except Exception:
            pass
        if status == "УСПЕШНО":
            save_baseline(metrics)
            return 0
        if status == "ТРЕБУЕТ ВНИМАНИЯ":
            return 0
        return 1
    except FileNotFoundError as exc:
        reason = str(exc)
    except ValueError as exc:
        reason = str(exc)
    except Exception as exc:
        reason = f"Неожиданная ошибка checker: {exc}"

    summary_text = build_failure_summary(reason, checked_at)
    details_text = build_failure_details(reason, checked_at)
    telegram_text = (
        f"❌ <b>Price — НЕУСПЕШНО</b>\n\n"
        f"<b>Время проверки:</b> {html.escape(fmt_dt(checked_at))}\n"
        f"<b>Причина:</b> {html.escape(reason)}\n"
        f"<b>Статус проверки Price:</b> НЕУСПЕШНО\n"
    )
    write_reports(summary_text, details_text)
    try:
        send_telegram(telegram_text)
    except Exception:
        pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
