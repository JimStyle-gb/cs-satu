#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

ALMATY_TZ = ZoneInfo("Asia/Almaty")
ROOT = Path(__file__).resolve().parents[1]
PRICE_PATH = ROOT / "docs" / "Price.yml"
REPORT_PATH = ROOT / "docs" / "raw" / "price_checker_report.txt"
LAST_SUCCESS_PATH = ROOT / "docs" / "raw" / "price_checker_last_success.json"
EXPECTED_SUPPLIERS = ["AkCent", "AlStyle", "ComPortal", "CopyLine", "VTT"]
WARN_TOTAL_DELTA_PCT = 5.0
WARN_SUPPLIER_DELTA_PCT = 10.0
WARN_PRICE100_DELTA_ABS = 50
WARN_PRICE100_DELTA_PCT = 10.0
WARN_PLACEHOLDER_DELTA_ABS = 50
WARN_PLACEHOLDER_DELTA_PCT = 10.0
WARN_FALSE_DELTA_PCT = 20.0
FAIL_TOTAL_DROP_PCT = 15.0
FAIL_SUPPLIER_DROP_PCT = 25.0
PLACEHOLDER_URL = "https://placehold.co/800x800/png?text=No+Photo"


@dataclass
class CheckResult:
    status: str
    reason: str
    metrics: Dict[str, object]
    has_other_issues: bool = False


def now_almaty() -> datetime:
    return datetime.now(ALMATY_TZ)


def format_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "-"
    return dt.astimezone(ALMATY_TZ).strftime("%Y-%m-%d %H:%M:%S")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_last_success() -> Optional[Dict[str, object]]:
    if not LAST_SUCCESS_PATH.exists():
        return None
    try:
        return json.loads(LAST_SUCCESS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_last_success(metrics: Dict[str, object]) -> None:
    LAST_SUCCESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_SUCCESS_PATH.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def send_telegram(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
        resp.read()


def parse_feed_meta(text: str) -> Dict[str, str]:
    start = text.find("<!--FEED_META")
    end = text.find("-->", start + 1)
    if start == -1 or end == -1:
        return {}
    block = text[start:end]
    rows: Dict[str, str] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if "|" not in line:
            continue
        left, right = line.split("|", 1)
        key = left.strip()
        value = right.strip()
        if key:
            rows[key] = value
    return rows


def safe_int(value: Optional[str]) -> int:
    if not value:
        return 0
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else 0


def safe_float(value: object) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def pct_change(old: int, new: int) -> float:
    if old <= 0:
        return 0.0 if new <= 0 else 100.0
    return abs((new - old) / old) * 100.0


def parse_price_xml() -> Tuple[Optional[ET.Element], str]:
    if not PRICE_PATH.exists():
        return None, "Файл Price.yml не найден."
    text = PRICE_PATH.read_text(encoding="utf-8")
    if not text.strip():
        return None, "Файл Price.yml пустой."
    try:
        root = ET.fromstring(text)
    except Exception:
        return None, "XML в Price повреждён."
    return root, text


def collect_metrics(root: ET.Element, text: str) -> Dict[str, object]:
    shop = root.find("shop")
    if shop is None:
        raise ValueError("В Price отсутствует блок shop.")
    categories_el = shop.find("categories")
    offers_el = shop.find("offers")
    if categories_el is None:
        raise ValueError("В Price отсутствует блок categories.")
    if offers_el is None:
        raise ValueError("В Price отсутствует блок offers.")

    category_ids = {c.attrib.get("id", "").strip() for c in categories_el.findall("category")}
    offers = offers_el.findall("offer")

    total = len(offers)
    available_true = 0
    available_false = 0
    price100 = 0
    placeholder = 0
    category_missing = 0
    satu_missing = 0
    invalid_category_refs = 0
    offer_ids_seen = set()
    vendor_codes_seen = set()
    offer_duplicates = 0
    vendor_duplicates = 0
    supplier_counts = {name: 0 for name in EXPECTED_SUPPLIERS}

    for offer in offers:
        offer_id = offer.attrib.get("id", "").strip()
        if offer_id in offer_ids_seen:
            offer_duplicates += 1
        elif offer_id:
            offer_ids_seen.add(offer_id)

        if offer.attrib.get("available", "").strip().lower() == "true":
            available_true += 1
        else:
            available_false += 1

        category_id = (offer.findtext("categoryId") or "").strip()
        if not category_id:
            category_missing += 1
        elif category_id not in category_ids:
            invalid_category_refs += 1

        portal_category_id = (offer.findtext("portal_category_id") or "").strip()
        if not portal_category_id:
            # считаем как отсутствие категории Satu только когда и у категории нет portal_id.
            category_node = next((c for c in categories_el.findall("category") if c.attrib.get("id", "").strip() == category_id), None)
            category_portal = category_node.attrib.get("portal_id", "").strip() if category_node is not None else ""
            if not category_portal:
                satu_missing += 1

        vendor_code = (offer.findtext("vendorCode") or "").strip()
        if vendor_code in vendor_codes_seen:
            vendor_duplicates += 1
        elif vendor_code:
            vendor_codes_seen.add(vendor_code)

        price = safe_int(offer.findtext("price"))
        if price == 100:
            price100 += 1

        pictures = [p.text.strip() for p in offer.findall("picture") if p.text and p.text.strip()]
        if any(p == PLACEHOLDER_URL for p in pictures):
            placeholder += 1

        if offer_id.startswith("AC"):
            supplier_counts["AkCent"] += 1
        elif offer_id.startswith("AS"):
            supplier_counts["AlStyle"] += 1
        elif offer_id.startswith("CP"):
            supplier_counts["ComPortal"] += 1
        elif offer_id.startswith("CL"):
            supplier_counts["CopyLine"] += 1
        elif offer_id.startswith("VT"):
            supplier_counts["VTT"] += 1

    meta = parse_feed_meta(text)
    build_time_text = meta.get("Время сборки (Алматы)", "")
    build_time = None
    try:
        if build_time_text:
            build_time = datetime.strptime(build_time_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ALMATY_TZ)
    except Exception:
        build_time = None

    return {
        "total": total,
        "available_true": available_true,
        "available_false": available_false,
        "price100": price100,
        "placeholder": placeholder,
        "category_missing": category_missing,
        "satu_missing": satu_missing,
        "invalid_category_refs": invalid_category_refs,
        "offer_duplicates": offer_duplicates,
        "vendor_duplicates": vendor_duplicates,
        "supplier_counts": supplier_counts,
        "build_time": format_dt(build_time),
        "checked_time": format_dt(now_almaty()),
        "status_price": meta.get("Статус проверки Price", ""),
        "status_satu": meta.get("Проверка привязки к категориям Satu", ""),
        "price_exists": True,
        "price_mtime": format_dt(datetime.fromtimestamp(PRICE_PATH.stat().st_mtime, tz=ALMATY_TZ)),
    }


def evaluate(metrics: Dict[str, object], previous_success: Optional[Dict[str, object]]) -> CheckResult:
    supplier_counts = metrics["supplier_counts"]
    assert isinstance(supplier_counts, dict)

    fail_reasons: List[str] = []
    warn_reasons: List[str] = []

    missing_suppliers = [name for name in EXPECTED_SUPPLIERS if safe_int(supplier_counts.get(name)) <= 0]
    if missing_suppliers:
        fail_reasons.append("В Price отсутствует один или несколько поставщиков.")
    if safe_int(metrics.get("category_missing")) > 0:
        fail_reasons.append("В Price есть товары без categoryId.")
    if safe_int(metrics.get("satu_missing")) > 0:
        fail_reasons.append("В Price есть товары без категории Satu.")
    if safe_int(metrics.get("offer_duplicates")) > 0:
        fail_reasons.append("В Price обнаружены дубли offer id.")
    if safe_int(metrics.get("vendor_duplicates")) > 0:
        fail_reasons.append("В Price обнаружены дубли vendorCode.")
    if safe_int(metrics.get("invalid_category_refs")) > 0:
        fail_reasons.append("В Price есть товары с categoryId, которых нет в блоке categories.")

    prev_total = safe_int(previous_success.get("total")) if previous_success else 0
    cur_total = safe_int(metrics.get("total"))
    if previous_success and prev_total > 0:
        total_drop = ((prev_total - cur_total) / prev_total) * 100.0
        if total_drop > FAIL_TOTAL_DROP_PCT:
            fail_reasons.append("Общее количество товаров просело больше допустимого порога.")
        elif pct_change(prev_total, cur_total) > WARN_TOTAL_DELTA_PCT:
            warn_reasons.append("Общее количество товаров изменилось больше допустимого порога.")

        prev_supplier_counts = previous_success.get("supplier_counts", {}) if isinstance(previous_success.get("supplier_counts"), dict) else {}
        for name in EXPECTED_SUPPLIERS:
            old_val = safe_int(prev_supplier_counts.get(name))
            new_val = safe_int(supplier_counts.get(name))
            if old_val > 0:
                supplier_drop = ((old_val - new_val) / old_val) * 100.0
                if supplier_drop > FAIL_SUPPLIER_DROP_PCT:
                    fail_reasons.append(f"Количество товаров у поставщика {name} просело больше допустимого порога.")
                    break
                if pct_change(old_val, new_val) > WARN_SUPPLIER_DELTA_PCT:
                    warn_reasons.append(f"Количество товаров у поставщика {name} изменилось больше допустимого порога.")

        old_price100 = safe_int(previous_success.get("price100"))
        new_price100 = safe_int(metrics.get("price100"))
        if (new_price100 - old_price100) >= WARN_PRICE100_DELTA_ABS or pct_change(old_price100, new_price100) > WARN_PRICE100_DELTA_PCT:
            if new_price100 > old_price100:
                warn_reasons.append("Количество товаров с ценой 100 выросло больше допустимого порога.")

        old_placeholder = safe_int(previous_success.get("placeholder"))
        new_placeholder = safe_int(metrics.get("placeholder"))
        if (new_placeholder - old_placeholder) >= WARN_PLACEHOLDER_DELTA_ABS or pct_change(old_placeholder, new_placeholder) > WARN_PLACEHOLDER_DELTA_PCT:
            if new_placeholder > old_placeholder:
                warn_reasons.append("Количество товаров с заглушкой фото выросло больше допустимого порога.")

        old_false = safe_int(previous_success.get("available_false"))
        new_false = safe_int(metrics.get("available_false"))
        if pct_change(old_false, new_false) > WARN_FALSE_DELTA_PCT and new_false > old_false:
            warn_reasons.append("Количество товаров Нет в наличии выросло больше допустимого порога.")

    if fail_reasons:
        return CheckResult(
            status="НЕУСПЕШНО",
            reason=fail_reasons[0],
            metrics=metrics,
            has_other_issues=len(fail_reasons) > 1,
        )
    if warn_reasons:
        return CheckResult(
            status="ТРЕБУЕТ ВНИМАНИЯ",
            reason=warn_reasons[0],
            metrics=metrics,
            has_other_issues=len(warn_reasons) > 1,
        )
    return CheckResult(
        status="УСПЕШНО",
        reason="",
        metrics=metrics,
        has_other_issues=False,
    )


def build_telegram_message(result: CheckResult) -> str:
    m = result.metrics
    header_icon = {
        "УСПЕШНО": "✅",
        "ТРЕБУЕТ ВНИМАНИЯ": "⚠️",
        "НЕУСПЕШНО": "❌",
    }[result.status]

    lines: List[str] = [f"{header_icon} Price — {result.status}", ""]
    lines.append(f"Проверка (Алматы): {m.get('checked_time', '-')}")
    build_time = m.get("build_time", "-")
    if build_time and build_time != "-":
        lines.append(f"Сборка Price (Алматы): {build_time}")
    lines.append("")

    if result.status == "НЕУСПЕШНО":
        lines.append(f"Причина: {result.reason}")
        if result.has_other_issues:
            lines.append("Дополнительно обнаружены другие ошибки.")
        lines.append("")
        lines.append("Статус проверки Price: НЕУСПЕШНО")
        return "\n".join(lines)

    lines.extend([
        f"• Товаров в Price: {m.get('total', 0)}",
        f"• Есть в наличии: {m.get('available_true', 0)}",
        f"• Нет в наличии: {m.get('available_false', 0)}",
        "",
        f"• С ценой 100: {m.get('price100', 0)}",
        f"• С заглушкой фото: {m.get('placeholder', 0)}",
        f"• Без categoryId: {m.get('category_missing', 0)}",
        f"• Без категории Satu: {m.get('satu_missing', 0)}",
        "",
    ])

    if result.status == "ТРЕБУЕТ ВНИМАНИЯ":
        lines.append(f"Причина: {result.reason}")
        if result.has_other_issues:
            lines.append("Есть и другие отклонения, которые стоит проверить.")
        lines.append("")

    lines.append(f"Привязка к категориям Satu: {m.get('status_satu', 'УСПЕШНО') or 'УСПЕШНО'}")
    lines.append(f"Статус проверки Price: {result.status}")
    return "\n".join(lines)


def build_report_text(result: CheckResult) -> str:
    m = result.metrics
    supplier_counts = m.get("supplier_counts", {})
    assert isinstance(supplier_counts, dict)
    lines = [
        f"Статус проверки Price                      | {result.status}",
        f"Время проверки (Алматы)                    | {m.get('checked_time', '-')}",
        f"Время сборки Price (Алматы)                | {m.get('build_time', '-')}",
        f"Товаров в Price                            | {m.get('total', 0)}",
        f"Есть в наличии                             | {m.get('available_true', 0)}",
        f"Нет в наличии                              | {m.get('available_false', 0)}",
        f"С ценой 100                                | {m.get('price100', 0)}",
        f"С заглушкой фото                           | {m.get('placeholder', 0)}",
        f"Без categoryId                             | {m.get('category_missing', 0)}",
        f"Без категории Satu                         | {m.get('satu_missing', 0)}",
        f"Дубли offer id                             | {m.get('offer_duplicates', 0)}",
        f"Дубли vendorCode                           | {m.get('vendor_duplicates', 0)}",
        f"Ошибочные ссылки на categoryId             | {m.get('invalid_category_refs', 0)}",
        f"AkCent                                     | {supplier_counts.get('AkCent', 0)}",
        f"AlStyle                                    | {supplier_counts.get('AlStyle', 0)}",
        f"ComPortal                                  | {supplier_counts.get('ComPortal', 0)}",
        f"CopyLine                                   | {supplier_counts.get('CopyLine', 0)}",
        f"VTT                                        | {supplier_counts.get('VTT', 0)}",
    ]
    if result.reason:
        lines.append(f"Причина                                     | {result.reason}")
    if result.has_other_issues:
        lines.append("Дополнительно                               | Есть и другие отклонения / ошибки")
    return "\n".join(lines) + "\n"


def main() -> int:
    root, text_or_reason = parse_price_xml()
    if root is None:
        metrics = {
            "checked_time": format_dt(now_almaty()),
            "build_time": "-",
            "total": 0,
            "available_true": 0,
            "available_false": 0,
            "price100": 0,
            "placeholder": 0,
            "category_missing": 0,
            "satu_missing": 0,
            "offer_duplicates": 0,
            "vendor_duplicates": 0,
            "invalid_category_refs": 0,
            "supplier_counts": {name: 0 for name in EXPECTED_SUPPLIERS},
            "status_satu": "НЕУСПЕШНО",
        }
        result = CheckResult("НЕУСПЕШНО", text_or_reason, metrics)
        write_text(REPORT_PATH, build_report_text(result))
        send_telegram(build_telegram_message(result))
        return 1

    try:
        metrics = collect_metrics(root, text_or_reason)
    except ValueError as exc:
        metrics = {
            "checked_time": format_dt(now_almaty()),
            "build_time": "-",
            "total": 0,
            "available_true": 0,
            "available_false": 0,
            "price100": 0,
            "placeholder": 0,
            "category_missing": 0,
            "satu_missing": 0,
            "offer_duplicates": 0,
            "vendor_duplicates": 0,
            "invalid_category_refs": 0,
            "supplier_counts": {name: 0 for name in EXPECTED_SUPPLIERS},
            "status_satu": "НЕУСПЕШНО",
        }
        result = CheckResult("НЕУСПЕШНО", str(exc), metrics)
        write_text(REPORT_PATH, build_report_text(result))
        send_telegram(build_telegram_message(result))
        return 1

    previous_success = read_last_success()
    result = evaluate(metrics, previous_success)
    write_text(REPORT_PATH, build_report_text(result))
    send_telegram(build_telegram_message(result))
    if result.status == "УСПЕШНО":
        save_last_success(metrics)
        return 0
    if result.status == "ТРЕБУЕТ ВНИМАНИЯ":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
