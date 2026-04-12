# Replace scripts/build_price_checker.py with the version below.
# This version keeps the detailed Telegram message and formats key metric labels in bold
# with exactly three non-breaking spaces after the colon using Telegram HTML parse_mode.

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET
from urllib import parse, request

ALMATY_TZ = ZoneInfo("Asia/Almaty")
ROOT = Path(__file__).resolve().parents[1]
PRICE_PATH = ROOT / "docs" / "Price.yml"
RAW_DIR = ROOT / "docs" / "raw"
REPORT_PATH = RAW_DIR / "price_checker_report.txt"
LAST_SUCCESS_PATH = RAW_DIR / "price_checker_last_success.json"

EXPECTED_SUPPLIERS = ["AkCent", "AlStyle", "ComPortal", "CopyLine", "VTT"]
WARNING_TOTAL_DELTA_PCT = 5.0
WARNING_SUPPLIER_DELTA_PCT = 10.0
WARNING_PRICE100_ABS = 50
WARNING_PRICE100_PCT = 10.0
WARNING_PLACEHOLDER_ABS = 50
WARNING_PLACEHOLDER_PCT = 10.0
WARNING_FALSE_PCT = 20.0
FAIL_TOTAL_DROP_PCT = 15.0
FAIL_SUPPLIER_DROP_PCT = 25.0
PLACEHOLDER_URL = "https://placehold.co/800x800/png?text=No+Photo"


@dataclass
class CheckResult:
    status: str
    reason: str | None
    build_time_almaty: str | None
    total_offers: int
    available_true: int
    available_false: int
    price_100_count: int
    placeholder_count: int
    missing_category_id: int
    missing_satu_category: int
    duplicate_offer_ids: int
    duplicate_vendor_codes: int
    supplier_counts: dict[str, int]
    extra_issues: list[str]


def now_almaty() -> datetime:
    return datetime.now(ALMATY_TZ)


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def pct_change(old: int, new: int) -> float:
    if old == 0:
        return 0.0 if new == 0 else 100.0
    return ((new - old) / old) * 100.0


def safe_int(value: str | None) -> int:
    if not value:
        return 0
    digits = re.sub(r"[^0-9-]", "", value)
    return int(digits) if digits else 0


def extract_feed_meta(text: str) -> tuple[str, dict[str, int], str | None]:
    m = re.search(r"<!--FEED_META\n(.*?)-->", text, flags=re.S)
    block = m.group(1) if m else ""
    supplier_counts: dict[str, int] = {}
    build_time = None
    current_supplier = None
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or "|" not in line:
            continue
        key, val = [p.strip() for p in line.split("|", 1)]
        if key == "Поставщик":
            current_supplier = val
        elif key == "Сколько товаров у поставщика после фильтра" and current_supplier:
            supplier_counts[current_supplier] = safe_int(val)
        elif key == "Price":
            current_supplier = "Price"
        elif key == "Время сборки (Алматы)" and current_supplier == "Price":
            build_time = val
    return block, supplier_counts, build_time


def read_last_success() -> dict[str, Any] | None:
    if not LAST_SUCCESS_PATH.exists():
        return None
    try:
        return json.loads(LAST_SUCCESS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_last_success(result: CheckResult) -> None:
    payload = {
        "saved_at_almaty": fmt_dt(now_almaty()),
        "build_time_almaty": result.build_time_almaty,
        "total_offers": result.total_offers,
        "available_true": result.available_true,
        "available_false": result.available_false,
        "price_100_count": result.price_100_count,
        "placeholder_count": result.placeholder_count,
        "supplier_counts": result.supplier_counts,
    }
    LAST_SUCCESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_SUCCESS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_metrics(price_path: Path) -> CheckResult:
    if not price_path.exists():
        return CheckResult("НЕУСПЕШНО", "Файл Price.yml не найден.", None, 0, 0, 0, 0, 0, 0, 0, 0, 0, {}, [])
    text = price_path.read_text(encoding="utf-8")
    if not text.strip():
        return CheckResult("НЕУСПЕШНО", "Файл Price.yml пустой.", None, 0, 0, 0, 0, 0, 0, 0, 0, 0, {}, [])

    feed_meta_block, supplier_counts, build_time = extract_feed_meta(text)
    missing_suppliers = [s for s in EXPECTED_SUPPLIERS if s not in feed_meta_block]
    if missing_suppliers:
        return CheckResult(
            "НЕУСПЕШНО",
            "В Price отсутствует один или несколько поставщиков.",
            build_time,
            0, 0, 0, 0, 0, 0, 0, 0, 0,
            supplier_counts,
            [f"Отсутствуют поставщики: {', '.join(missing_suppliers)}"],
        )

    try:
        root = ET.fromstring(text)
    except Exception:
        return CheckResult("НЕУСПЕШНО", "XML в Price повреждён.", build_time, 0, 0, 0, 0, 0, 0, 0, 0, 0, supplier_counts, [])

    shop = root.find("shop")
    if shop is None:
        return CheckResult("НЕУСПЕШНО", "В Price отсутствует блок shop.", build_time, 0, 0, 0, 0, 0, 0, 0, 0, 0, supplier_counts, [])
    categories_el = shop.find("categories")
    if categories_el is None:
        return CheckResult("НЕУСПЕШНО", "В Price отсутствует блок categories.", build_time, 0, 0, 0, 0, 0, 0, 0, 0, 0, supplier_counts, [])
    offers_el = shop.find("offers")
    if offers_el is None:
        return CheckResult("НЕУСПЕШНО", "В Price отсутствует блок offers.", build_time, 0, 0, 0, 0, 0, 0, 0, 0, 0, supplier_counts, [])

    category_ids = {c.attrib.get("id", "") for c in categories_el.findall("category")}

    total = 0
    available_true = 0
    available_false = 0
    price100 = 0
    placeholder = 0
    missing_category = 0
    missing_satu = 0
    dup_offer_ids = 0
    dup_vendor_codes = 0
    seen_offer_ids: set[str] = set()
    seen_vendor_codes: set[str] = set()
    extra_issues: list[str] = []

    for offer in offers_el.findall("offer"):
        total += 1
        offer_id = offer.attrib.get("id", "")
        if offer_id in seen_offer_ids:
            dup_offer_ids += 1
        seen_offer_ids.add(offer_id)

        available = offer.attrib.get("available", "false").lower() == "true"
        if available:
            available_true += 1
        else:
            available_false += 1

        category_id = (offer.findtext("categoryId") or "").strip()
        if not category_id:
            missing_category += 1
        elif category_id not in category_ids:
            return CheckResult(
                "НЕУСПЕШНО",
                "В Price есть товары с categoryId, которых нет в блоке categories.",
                build_time,
                total,
                available_true,
                available_false,
                price100,
                placeholder,
                missing_category,
                missing_satu,
                dup_offer_ids,
                dup_vendor_codes,
                supplier_counts,
                extra_issues,
            )

        # Satu category exists if category has portal_id or offer has portal_category_id.
        portal_category_id = (offer.findtext("portal_category_id") or "").strip()
        has_satu = bool(portal_category_id)
        if not has_satu and category_id:
            for c in categories_el.findall("category"):
                if c.attrib.get("id") == category_id and c.attrib.get("portal_id"):
                    has_satu = True
                    break
        if not has_satu:
            missing_satu += 1

        vendor_code = (offer.findtext("vendorCode") or "").strip()
        if vendor_code in seen_vendor_codes:
            dup_vendor_codes += 1
        if vendor_code:
            seen_vendor_codes.add(vendor_code)

        price_text = (offer.findtext("price") or "").strip()
        if price_text == "100":
            price100 += 1

        pictures = [p.text.strip() for p in offer.findall("picture") if p.text]
        if any(pic == PLACEHOLDER_URL for pic in pictures):
            placeholder += 1

    if missing_category > 0:
        return CheckResult("НЕУСПЕШНО", "В Price есть товары без categoryId.", build_time, total, available_true, available_false, price100, placeholder, missing_category, missing_satu, dup_offer_ids, dup_vendor_codes, supplier_counts, extra_issues)
    if missing_satu > 0:
        return CheckResult("НЕУСПЕШНО", "В Price есть товары без категории Satu.", build_time, total, available_true, available_false, price100, placeholder, missing_category, missing_satu, dup_offer_ids, dup_vendor_codes, supplier_counts, extra_issues)
    if dup_offer_ids > 0:
        return CheckResult("НЕУСПЕШНО", "В Price обнаружены дубли offer id.", build_time, total, available_true, available_false, price100, placeholder, missing_category, missing_satu, dup_offer_ids, dup_vendor_codes, supplier_counts, extra_issues)
    if dup_vendor_codes > 0:
        return CheckResult("НЕУСПЕШНО", "В Price обнаружены дубли vendorCode.", build_time, total, available_true, available_false, price100, placeholder, missing_category, missing_satu, dup_offer_ids, dup_vendor_codes, supplier_counts, extra_issues)

    prev = read_last_success()
    if prev:
        total_delta = pct_change(int(prev.get("total_offers", 0)), total)
        if total_delta <= -FAIL_TOTAL_DROP_PCT:
            return CheckResult("НЕУСПЕШНО", "Общее количество товаров просело больше допустимого порога.", build_time, total, available_true, available_false, price100, placeholder, missing_category, missing_satu, dup_offer_ids, dup_vendor_codes, supplier_counts, extra_issues)
        for supplier, new_count in supplier_counts.items():
            old_count = int(prev.get("supplier_counts", {}).get(supplier, new_count))
            delta = pct_change(old_count, new_count)
            if delta <= -FAIL_SUPPLIER_DROP_PCT:
                return CheckResult("НЕУСПЕШНО", f"Количество товаров у поставщика {supplier} просело больше допустимого порога.", build_time, total, available_true, available_false, price100, placeholder, missing_category, missing_satu, dup_offer_ids, dup_vendor_codes, supplier_counts, extra_issues)

        if abs(total_delta) > WARNING_TOTAL_DELTA_PCT:
            return CheckResult("ТРЕБУЕТ ВНИМАНИЯ", "Общее количество товаров изменилось больше допустимого порога.", build_time, total, available_true, available_false, price100, placeholder, missing_category, missing_satu, dup_offer_ids, dup_vendor_codes, supplier_counts, extra_issues)
        for supplier, new_count in supplier_counts.items():
            old_count = int(prev.get("supplier_counts", {}).get(supplier, new_count))
            delta = abs(pct_change(old_count, new_count))
            if delta > WARNING_SUPPLIER_DELTA_PCT:
                return CheckResult("ТРЕБУЕТ ВНИМАНИЯ", f"Количество товаров у поставщика {supplier} изменилось больше допустимого порога.", build_time, total, available_true, available_false, price100, placeholder, missing_category, missing_satu, dup_offer_ids, dup_vendor_codes, supplier_counts, extra_issues)

        prev_price100 = int(prev.get("price_100_count", price100))
        if price100 - prev_price100 >= WARNING_PRICE100_ABS or pct_change(prev_price100, price100) > WARNING_PRICE100_PCT:
            return CheckResult("ТРЕБУЕТ ВНИМАНИЯ", "Количество товаров с ценой 100 выросло больше допустимого порога.", build_time, total, available_true, available_false, price100, placeholder, missing_category, missing_satu, dup_offer_ids, dup_vendor_codes, supplier_counts, extra_issues)

        prev_placeholder = int(prev.get("placeholder_count", placeholder))
        if placeholder - prev_placeholder >= WARNING_PLACEHOLDER_ABS or pct_change(prev_placeholder, placeholder) > WARNING_PLACEHOLDER_PCT:
            return CheckResult("ТРЕБУЕТ ВНИМАНИЯ", "Количество товаров с заглушкой фото выросло больше допустимого порога.", build_time, total, available_true, available_false, price100, placeholder, missing_category, missing_satu, dup_offer_ids, dup_vendor_codes, supplier_counts, extra_issues)

        prev_false = int(prev.get("available_false", available_false))
        if pct_change(prev_false, available_false) > WARNING_FALSE_PCT:
            return CheckResult("ТРЕБУЕТ ВНИМАНИЯ", "Количество товаров Нет в наличии выросло больше допустимого порога.", build_time, total, available_true, available_false, price100, placeholder, missing_category, missing_satu, dup_offer_ids, dup_vendor_codes, supplier_counts, extra_issues)

    return CheckResult("УСПЕШНО", None, build_time, total, available_true, available_false, price100, placeholder, missing_category, missing_satu, dup_offer_ids, dup_vendor_codes, supplier_counts, extra_issues)


def write_report(result: CheckResult) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "Итог проверки Price",
        f"Время проверки (Алматы)                      | {fmt_dt(now_almaty())}",
        f"Статус проверки Price                      | {result.status}",
        f"Причина                                    | {result.reason or ''}",
        f"Время сборки Price (Алматы)                | {result.build_time_almaty or ''}",
        f"Товаров в Price                            | {result.total_offers}",
        f"Есть в наличии                             | {result.available_true}",
        f"Нет в наличии                              | {result.available_false}",
        f"С ценой 100                                | {result.price_100_count}",
        f"С заглушкой фото                           | {result.placeholder_count}",
        f"Без categoryId                             | {result.missing_category_id}",
        f"Без категории Satu                         | {result.missing_satu_category}",
        f"Дубли offer id                             | {result.duplicate_offer_ids}",
        f"Дубли vendorCode                           | {result.duplicate_vendor_codes}",
    ]
    if result.extra_issues:
        lines.append("Дополнительно")
        lines.extend(result.extra_issues)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_metric_html(label: str, value: str | int) -> str:
    # Exactly three spaces after the colon using non-breaking spaces.
    return f"• <b>{label}:</b>&nbsp;&nbsp;&nbsp;{value}"


def build_telegram_message(result: CheckResult) -> str:
    now_text = fmt_dt(now_almaty())
    header = {
        "УСПЕШНО": "✅ <b>Price — УСПЕШНО</b>",
        "ТРЕБУЕТ ВНИМАНИЯ": "⚠️ <b>Price — ТРЕБУЕТ ВНИМАНИЯ</b>",
        "НЕУСПЕШНО": "❌ <b>Price — НЕУСПЕШНО</b>",
    }[result.status]

    lines: list[str] = [header, "", f"Проверка (Алматы): {now_text}"]
    if result.build_time_almaty:
        lines.append(f"Сборка Price (Алматы): {result.build_time_almaty}")
    lines.append("")

    if result.status == "НЕУСПЕШНО":
        if result.reason:
            lines.extend(["Причина:", result.reason, ""])
        lines.append("Статус проверки Price: НЕУСПЕШНО")
        return "\n".join(lines)

    lines.append(format_metric_html("Товаров в Price", result.total_offers))
    lines.append(format_metric_html("Есть в наличии", result.available_true))
    lines.append(format_metric_html("Нет в наличии", result.available_false))
    lines.append("")
    lines.append(format_metric_html("С ценой 100", result.price_100_count))
    lines.append(format_metric_html("С заглушкой фото", result.placeholder_count))
    lines.append(format_metric_html("Без categoryId", result.missing_category_id))
    lines.append(format_metric_html("Без категории Satu", result.missing_satu_category))
    lines.append("")

    if result.reason:
        lines.extend(["Причина:", result.reason, ""])

    lines.append(f"Привязка к категориям Satu: {'УСПЕШНО' if result.missing_satu_category == 0 else 'НЕУСПЕШНО'}")
    lines.append(f"Статус проверки Price: {result.status}")
    return "\n".join(lines)


def send_telegram(text: str) -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = request.Request(url, data=data)
    with request.urlopen(req, timeout=30) as resp:
        resp.read()


def main() -> int:
    result = collect_metrics(PRICE_PATH)
    write_report(result)

    if result.status == "УСПЕШНО":
        write_last_success(result)

    try:
        send_telegram(build_telegram_message(result))
    except Exception:
        # Do not hide the real checker status because of Telegram transport.
        pass

    return 0 if result.status in {"УСПЕШНО", "ТРЕБУЕТ ВНИМАНИЯ"} else 1


if __name__ == "__main__":
    sys.exit(main())
