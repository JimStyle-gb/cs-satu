#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка итогового файла Price.yml."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PRICE_PATH = ROOT / "docs" / "Price.yml"
RAW_DIR = ROOT / "docs" / "raw"
REPORT_PATH = RAW_DIR / "price_checker_report.txt"
BASELINE_PATH = RAW_DIR / "price_checker_last_success.json"

SUPPLIERS = {
    "AkCent": ("ACC", "AK", "AC"),
    "AlStyle": ("AS",),
    "ComPortal": ("CP",),
    "CopyLine": ("CL",),
    "VTT": ("VT",),
}

PLACEHOLDER = "https://placehold.co/800x800/png?text=No+Photo"


@dataclass
class CheckResult:
    status: str
    reason: str
    report_text: str
    metrics: dict[str, Any]
    extra_issue_count: int = 0


def _now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def _safe_int(value: str | None, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_float(value: str | None, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def _text(elem: ET.Element | None) -> str:
    return (elem.text or "").strip() if elem is not None else ""


def _prefix_supplier(offer_id: str) -> str | None:
    offer_id = offer_id.strip().upper()
    for supplier, prefixes in SUPPLIERS.items():
        for prefix in prefixes:
            if offer_id.startswith(prefix):
                return supplier
    return None


def _load_xml(path: Path) -> ET.ElementTree:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ET.parse(path, parser=parser)


def _collect_metrics(price_path: Path) -> dict[str, Any]:
    if not price_path.exists():
        raise FileNotFoundError("Файл Price.yml не найден.")
    if price_path.stat().st_size == 0:
        raise ValueError("Файл Price.yml пустой.")

    tree = _load_xml(price_path)
    root = tree.getroot()

    if root.tag != "yml_catalog":
        raise ValueError("Корневой тег yml_catalog отсутствует.")

    build_time = root.attrib.get("date", "").strip()
    shop = root.find("shop")
    if shop is None:
        raise ValueError("Тег shop отсутствует.")

    categories_node = shop.find("categories")
    if categories_node is None:
        raise ValueError("В Price отсутствует блок categories.")

    offers_node = shop.find("offers")
    if offers_node is None:
        raise ValueError("В Price отсутствует блок offers.")

    categories = categories_node.findall("category")
    offers = offers_node.findall("offer")
    if not offers:
        raise ValueError("В Price отсутствует блок offers.")

    category_ids: set[str] = set()
    category_portal_map: dict[str, str] = {}
    for cat in categories:
        cid = cat.attrib.get("id", "").strip()
        if cid:
            category_ids.add(cid)
            portal_id = cat.attrib.get("portal_id", "").strip()
            if portal_id:
                category_portal_map[cid] = portal_id

    offer_ids: list[str] = []
    vendor_codes: list[str] = []
    supplier_counts: Counter[str] = Counter()
    missing_category_id = 0
    missing_satu_category = 0
    missing_category_ref = 0
    price_100 = 0
    placeholder_photo = 0
    available_true = 0
    available_false = 0

    for offer in offers:
        offer_id = offer.attrib.get("id", "").strip()
        offer_ids.append(offer_id)
        supplier = _prefix_supplier(offer_id)
        if supplier:
            supplier_counts[supplier] += 1

        available = offer.attrib.get("available", "").strip().lower()
        if available == "true":
            available_true += 1
        else:
            available_false += 1

        category_id = _text(offer.find("categoryId"))
        if not category_id:
            missing_category_id += 1
        elif category_id not in category_ids:
            missing_category_ref += 1

        portal_category_id = _text(offer.find("portal_category_id"))
        if not portal_category_id:
            portal_category_id = category_portal_map.get(category_id, "")
        if not portal_category_id:
            missing_satu_category += 1

        vendor_code = _text(offer.find("vendorCode"))
        vendor_codes.append(vendor_code)

        price = _safe_float(_text(offer.find("price")), default=-1)
        if price == 100:
            price_100 += 1

        for pic in offer.findall("picture"):
            if _text(pic) == PLACEHOLDER:
                placeholder_photo += 1
                break

    duplicate_offer_ids = sum(count - 1 for count in Counter(offer_ids).values() if count > 1)
    duplicate_vendor_codes = sum(count - 1 for count in Counter(vendor_codes).values() if count > 1)

    missing_suppliers = [name for name in SUPPLIERS if supplier_counts.get(name, 0) == 0]

    return {
        "checked_at": _now_str(),
        "build_time": build_time,
        "price_path": str(price_path),
        "total_offers": len(offers),
        "available_true": available_true,
        "available_false": available_false,
        "price_100": price_100,
        "placeholder_photo": placeholder_photo,
        "missing_category_id": missing_category_id,
        "missing_satu_category": missing_satu_category,
        "duplicate_offer_ids": duplicate_offer_ids,
        "duplicate_vendor_codes": duplicate_vendor_codes,
        "missing_category_ref": missing_category_ref,
        "categories_total": len(categories),
        "supplier_counts": {name: supplier_counts.get(name, 0) for name in SUPPLIERS},
        "missing_suppliers": missing_suppliers,
        "status_check_price": "УСПЕШНО",
        "status_check_satu": "УСПЕШНО" if missing_satu_category == 0 else "НЕУСПЕШНО",
    }


def _load_baseline(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _critical_reason(metrics: dict[str, Any]) -> tuple[str | None, list[str]]:
    errors: list[str] = []

    price_path = Path(metrics["price_path"])
    if not price_path.exists():
        return "Файл Price.yml не найден.", errors
    if price_path.stat().st_size == 0:
        return "Файл Price.yml пустой.", errors

    if metrics["missing_suppliers"]:
        errors.append("В Price отсутствует один или несколько поставщиков.")
    if metrics["missing_category_id"] > 0:
        errors.append("В Price есть товары без categoryId.")
    if metrics["missing_satu_category"] > 0:
        errors.append("В Price есть товары без категории Satu.")
    if metrics["duplicate_offer_ids"] > 0:
        errors.append("В Price обнаружены дубли offer id.")
    if metrics["duplicate_vendor_codes"] > 0:
        errors.append("В Price обнаружены дубли vendorCode.")
    if metrics["missing_category_ref"] > 0:
        errors.append("В Price есть товары с categoryId, которых нет в блоке categories.")

    if errors:
        return errors[0], errors[1:]
    return None, []


def _pct_change(old: int, new: int) -> float:
    if old <= 0:
        return 0.0 if new <= 0 else 100.0
    return ((new - old) / old) * 100.0


def _warning_reason(metrics: dict[str, Any], baseline: dict[str, Any] | None) -> tuple[str | None, list[str]]:
    if not baseline:
        return None, []

    warnings: list[str] = []
    total_change = abs(_pct_change(_safe_int(baseline.get("total_offers")), metrics["total_offers"]))
    if total_change > 5:
        warnings.append("Общее количество товаров изменилось больше допустимого порога.")

    for supplier in SUPPLIERS:
        old_count = _safe_int((baseline.get("supplier_counts") or {}).get(supplier))
        new_count = _safe_int(metrics["supplier_counts"].get(supplier))
        if abs(_pct_change(old_count, new_count)) > 10:
            warnings.append(f"Количество товаров у поставщика {supplier} изменилось больше допустимого порога.")
            break

    old_price_100 = _safe_int(baseline.get("price_100"))
    price_100_delta = metrics["price_100"] - old_price_100
    if price_100_delta > 50 or (old_price_100 > 0 and _pct_change(old_price_100, metrics["price_100"]) > 10):
        warnings.append("Количество товаров с ценой 100 выросло больше допустимого порога.")

    old_placeholder = _safe_int(baseline.get("placeholder_photo"))
    placeholder_delta = metrics["placeholder_photo"] - old_placeholder
    if placeholder_delta > 50 or (old_placeholder > 0 and _pct_change(old_placeholder, metrics["placeholder_photo"]) > 10):
        warnings.append("Количество товаров с заглушкой фото выросло больше допустимого порога.")

    old_unavailable = _safe_int(baseline.get("available_false"))
    if _pct_change(old_unavailable, metrics["available_false"]) > 20:
        warnings.append("Количество товаров Нет в наличии выросло больше допустимого порога.")

    if warnings:
        return warnings[0], warnings[1:]
    return None, []


def _catastrophic_drop_reason(metrics: dict[str, Any], baseline: dict[str, Any] | None) -> tuple[str | None, list[str]]:
    if not baseline:
        return None, []

    errors: list[str] = []
    total_change = _pct_change(_safe_int(baseline.get("total_offers")), metrics["total_offers"])
    if total_change < -15:
        errors.append("Общее количество товаров просело больше допустимого порога.")

    for supplier in SUPPLIERS:
        old_count = _safe_int((baseline.get("supplier_counts") or {}).get(supplier))
        new_count = _safe_int(metrics["supplier_counts"].get(supplier))
        if _pct_change(old_count, new_count) < -25:
            errors.append(f"Количество товаров у поставщика {supplier} просело больше допустимого порога.")
            break

    if errors:
        return errors[0], errors[1:]
    return None, []


def _render_report(status: str, reason: str, metrics: dict[str, Any], extra: list[str]) -> str:
    lines = [
        f"Price — {status}",
        "",
        f"Время проверки                              | {metrics['checked_at']}",
        f"Время сборки Price                           | {metrics['build_time'] or 'не определено'}",
        f"Файл Price                                   | {metrics['price_path']}",
        "",
        f"Причина                                      | {reason}",
        "",
        f"Сколько товаров в Price                      | {metrics['total_offers']}",
        f"Сколько товаров есть в наличии               | {metrics['available_true']}",
        f"Сколько товаров нет в наличии                | {metrics['available_false']}",
        f"Сколько товаров с ценой 100                  | {metrics['price_100']}",
        f"Сколько товаров с заглушкой фото             | {metrics['placeholder_photo']}",
        f"Сколько товаров без categoryId               | {metrics['missing_category_id']}",
        f"Сколько товаров без категории Satu           | {metrics['missing_satu_category']}",
        f"Сколько дублей offer id                      | {metrics['duplicate_offer_ids']}",
        f"Сколько дублей vendorCode                    | {metrics['duplicate_vendor_codes']}",
        f"Сколько неверных ссылок на categoryId        | {metrics['missing_category_ref']}",
        "",
        "Поставщики",
    ]
    for supplier in SUPPLIERS:
        lines.append(f"{supplier:<45} | {metrics['supplier_counts'].get(supplier, 0)}")

    if metrics["missing_suppliers"]:
        lines.extend([
            "",
            "Отсутствующие поставщики",
            ", ".join(metrics["missing_suppliers"]),
        ])

    if extra:
        lines.extend([
            "",
            "Дополнительно",
            *extra,
        ])

    return "\n".join(lines).rstrip() + "\n"


def _write_report(text: str) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(text, encoding="utf-8")


def _save_baseline(metrics: dict[str, Any]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")


def run() -> CheckResult:
    try:
        metrics = _collect_metrics(PRICE_PATH)
    except FileNotFoundError as exc:
        metrics = {
            "checked_at": _now_str(),
            "build_time": "",
            "price_path": str(PRICE_PATH),
            "total_offers": 0,
            "available_true": 0,
            "available_false": 0,
            "price_100": 0,
            "placeholder_photo": 0,
            "missing_category_id": 0,
            "missing_satu_category": 0,
            "duplicate_offer_ids": 0,
            "duplicate_vendor_codes": 0,
            "missing_category_ref": 0,
            "categories_total": 0,
            "supplier_counts": {name: 0 for name in SUPPLIERS},
            "missing_suppliers": list(SUPPLIERS.keys()),
        }
        text = _render_report("НЕУСПЕШНО", str(exc), metrics, [])
        _write_report(text)
        return CheckResult("НЕУСПЕШНО", str(exc), text, metrics)
    except Exception as exc:
        metrics = {
            "checked_at": _now_str(),
            "build_time": "",
            "price_path": str(PRICE_PATH),
            "total_offers": 0,
            "available_true": 0,
            "available_false": 0,
            "price_100": 0,
            "placeholder_photo": 0,
            "missing_category_id": 0,
            "missing_satu_category": 0,
            "duplicate_offer_ids": 0,
            "duplicate_vendor_codes": 0,
            "missing_category_ref": 0,
            "categories_total": 0,
            "supplier_counts": {name: 0 for name in SUPPLIERS},
            "missing_suppliers": list(SUPPLIERS.keys()),
        }
        text = _render_report("НЕУСПЕШНО", f"XML в Price повреждён или не удалось прочитать файл. {exc}", metrics, [])
        _write_report(text)
        return CheckResult("НЕУСПЕШНО", f"XML в Price повреждён или не удалось прочитать файл. {exc}", text, metrics)

    baseline = _load_baseline(BASELINE_PATH)

    critical_reason, critical_extra = _critical_reason(metrics)
    catastrophic_reason, catastrophic_extra = _catastrophic_drop_reason(metrics, baseline)
    if critical_reason:
        text = _render_report("НЕУСПЕШНО", critical_reason, metrics, critical_extra)
        _write_report(text)
        return CheckResult("НЕУСПЕШНО", critical_reason, text, metrics, len(critical_extra))
    if catastrophic_reason:
        text = _render_report("НЕУСПЕШНО", catastrophic_reason, metrics, catastrophic_extra)
        _write_report(text)
        return CheckResult("НЕУСПЕШНО", catastrophic_reason, text, metrics, len(catastrophic_extra))

    warning_reason, warning_extra = _warning_reason(metrics, baseline)
    if warning_reason:
        text = _render_report("ТРЕБУЕТ ВНИМАНИЯ", warning_reason, metrics, warning_extra)
        _write_report(text)
        return CheckResult("ТРЕБУЕТ ВНИМАНИЯ", warning_reason, text, metrics, len(warning_extra))

    text = _render_report("УСПЕШНО", "Критичных ошибок и заметных отклонений не обнаружено.", metrics, [])
    _write_report(text)
    _save_baseline(metrics)
    return CheckResult("УСПЕШНО", "Критичных ошибок и заметных отклонений не обнаружено.", text, metrics)


def main() -> int:
    result = run()
    print(result.report_text)
    if result.status == "НЕУСПЕШНО":
        return 2
    if result.status == "ТРЕБУЕТ ВНИМАНИЯ":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
