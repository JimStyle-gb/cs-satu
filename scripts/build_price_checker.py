#!/usr/bin/env python3
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
import xml.etree.ElementTree as ET
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Пути
PRICE_PATH = Path("docs/Price.yml")
REPORT_PATH = Path("docs/raw/price_checker_report.txt")
BASELINE_PATH = Path("docs/raw/price_checker_last_success.json")

# Константы
TZ = ZoneInfo("Asia/Almaty")
REQUIRED_SUPPLIERS = ["AkCent", "AlStyle", "ComPortal", "CopyLine", "VTT"]
LABEL_GAP = "\u00A0\u00A0\u00A0"  # 3 неразрывных пробела

MONTHS_RU = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

WARNING_GLOBAL_DELTA_PCT = 5.0
WARNING_SUPPLIER_DELTA_PCT = 10.0
WARNING_PRICE100_DELTA_ABS = 50
WARNING_PRICE100_DELTA_PCT = 10.0
WARNING_PLACEHOLDER_DELTA_ABS = 50
WARNING_PLACEHOLDER_DELTA_PCT = 10.0
WARNING_FALSE_DELTA_PCT = 20.0

FAIL_GLOBAL_DROP_PCT = 15.0
FAIL_SUPPLIER_DROP_PCT = 25.0

PLACEHOLDER_URL = "https://placehold.co/800x800/png?text=No+Photo"


@dataclass
class CheckResult:
    status: str
    reason: str
    details: dict[str, Any]
    extra_errors: list[str]


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def now_almaty() -> datetime:
    return datetime.now(TZ)


def fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    dt = dt.astimezone(TZ)
    return f"{dt.day} {MONTHS_RU[dt.month]} {dt.year} г. {dt:%H:%M:%S}"


def html_escape(text: Any) -> str:
    s = str(text)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def html_label(label: str, value: Any, bullet: bool = False) -> str:
    prefix = "• " if bullet else ""
    return f"{prefix}<b>{html_escape(label)}:</b>{LABEL_GAP}{html_escape(value)}"


def parse_yml_catalog_date(root: ET.Element) -> datetime | None:
    raw = root.attrib.get("date", "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=TZ)
        except ValueError:
            pass
    return None


def load_price_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError("Файл Price.yml не найден.")
    if path.stat().st_size == 0:
        raise ValueError("Файл Price.yml пустой.")

    raw = path.read_text(encoding="utf-8")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise ValueError("XML в Price повреждён.") from e

    shop = root.find("shop")
    if shop is None:
        raise ValueError("В Price отсутствует блок shop.")

    categories = shop.find("categories")
    if categories is None:
        raise ValueError("В Price отсутствует блок categories.")

    offers = shop.find("offers")
    if offers is None:
        raise ValueError("В Price отсутствует блок offers.")

    category_ids = set()
    category_portal_map: dict[str, str] = {}
    for cat in categories.findall("category"):
        cid = (cat.attrib.get("id") or "").strip()
        if cid:
            category_ids.add(cid)
            portal = (cat.attrib.get("portal_id") or "").strip()
            if portal:
                category_portal_map[cid] = portal

    offers_list = offers.findall("offer")
    offer_ids: set[str] = set()
    vendor_codes: set[str] = set()
    dup_offer_ids = 0
    dup_vendor_codes = 0
    missing_category_id = 0
    bad_category_ref = 0
    missing_satu = 0
    price100 = 0
    placeholder = 0
    true_count = 0
    false_count = 0

    for offer in offers_list:
        oid = (offer.attrib.get("id") or "").strip()
        if oid:
            if oid in offer_ids:
                dup_offer_ids += 1
            offer_ids.add(oid)

        available = (offer.attrib.get("available") or "").strip().lower()
        if available == "true":
            true_count += 1
        elif available == "false":
            false_count += 1

        vc = (offer.findtext("vendorCode") or "").strip()
        if vc:
            if vc in vendor_codes:
                dup_vendor_codes += 1
            vendor_codes.add(vc)

        cid = (offer.findtext("categoryId") or "").strip()
        if not cid:
            missing_category_id += 1
        elif cid not in category_ids:
            bad_category_ref += 1

        # Категория Satu: либо override у оффера, либо portal_id у категории
        offer_portal = (offer.findtext("portal_category_id") or "").strip()
        if not offer_portal:
            if cid and cid in category_portal_map:
                offer_portal = category_portal_map[cid]
        if not offer_portal:
            missing_satu += 1

        price = (offer.findtext("price") or "").strip()
        if price == "100":
            price100 += 1

        pics = [((p.text or "").strip()) for p in offer.findall("picture")]
        if any(p == PLACEHOLDER_URL for p in pics):
            placeholder += 1

    feed_meta_match = re.search(r"<!--FEED_META(.*?)-->", raw, flags=re.S)
    feed_meta = feed_meta_match.group(1) if feed_meta_match else ""

    supplier_counts: dict[str, int] = {}
    for name in REQUIRED_SUPPLIERS:
        m = re.search(
            rf"Поставщик\s*\|\s*{re.escape(name)}.*?Сколько товаров у поставщика после фильтра\s*\|\s*(\d+)",
            feed_meta,
            flags=re.S,
        )
        if m:
            supplier_counts[name] = int(m.group(1))

    return {
        "checked_at": now_almaty().isoformat(),
        "build_time": parse_yml_catalog_date(root).isoformat() if parse_yml_catalog_date(root) else None,
        "total": len(offers_list),
        "true_count": true_count,
        "false_count": false_count,
        "price100": price100,
        "placeholder": placeholder,
        "missing_category_id": missing_category_id,
        "missing_satu": missing_satu,
        "dup_offer_ids": dup_offer_ids,
        "dup_vendor_codes": dup_vendor_codes,
        "bad_category_ref": bad_category_ref,
        "supplier_counts": supplier_counts,
        "supplier_meta_present": [name for name in REQUIRED_SUPPLIERS if name in feed_meta],
        "has_categories": True,
        "has_offers": True,
    }


def load_baseline(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    # Минимальная валидность базы
    if not isinstance(data.get("total"), int):
        return None
    if not isinstance(data.get("supplier_counts"), dict):
        return None
    return data


def pct_change(old: int, new: int) -> float:
    if old <= 0:
        return 0.0 if new <= 0 else 100.0
    return abs(new - old) / old * 100.0


def pct_drop(old: int, new: int) -> float:
    if old <= 0:
        return 0.0
    if new >= old:
        return 0.0
    return (old - new) / old * 100.0


def evaluate(stats: dict[str, Any], baseline: dict[str, Any] | None) -> CheckResult:
    errors: list[str] = []
    warnings: list[str] = []

    # Жёсткие проверки
    missing_suppliers = [s for s in REQUIRED_SUPPLIERS if s not in stats["supplier_meta_present"]]
    if missing_suppliers:
        errors.append("В Price отсутствует один или несколько поставщиков.")
    if stats["missing_category_id"] > 0:
        errors.append("В Price есть товары без categoryId.")
    if stats["missing_satu"] > 0:
        errors.append("В Price есть товары без категории Satu.")
    if stats["dup_offer_ids"] > 0:
        errors.append("В Price обнаружены дубли offer id.")
    if stats["dup_vendor_codes"] > 0:
        errors.append("В Price обнаружены дубли vendorCode.")
    if stats["bad_category_ref"] > 0:
        errors.append("В Price есть товары с categoryId, которых нет в блоке categories.")

    # Сравнение только с последней успешной базой
    if baseline:
        old_total = int(baseline.get("total", 0))
        new_total = int(stats["total"])

        if pct_drop(old_total, new_total) > FAIL_GLOBAL_DROP_PCT:
            errors.append("Общее количество товаров просело больше допустимого порога.")
        elif pct_change(old_total, new_total) > WARNING_GLOBAL_DELTA_PCT:
            warnings.append("Общее количество товаров изменилось больше допустимого порога.")

        old_suppliers = baseline.get("supplier_counts", {}) or {}
        for supplier in REQUIRED_SUPPLIERS:
            old_count = int(old_suppliers.get(supplier, 0))
            new_count = int(stats["supplier_counts"].get(supplier, 0))
            if old_count <= 0 and new_count <= 0:
                continue
            if pct_drop(old_count, new_count) > FAIL_SUPPLIER_DROP_PCT:
                errors.append(f"Количество товаров у поставщика {supplier} просело больше допустимого порога.")
                break
            if pct_change(old_count, new_count) > WARNING_SUPPLIER_DELTA_PCT:
                warnings.append(f"Количество товаров у поставщика {supplier} изменилось больше допустимого порога.")
                break

        old_price100 = int(baseline.get("price100", 0))
        new_price100 = int(stats["price100"])
        if (new_price100 - old_price100) >= WARNING_PRICE100_DELTA_ABS or pct_change(old_price100, new_price100) > WARNING_PRICE100_DELTA_PCT:
            if new_price100 > old_price100:
                warnings.append("Количество товаров с ценой 100 выросло больше допустимого порога.")

        old_placeholder = int(baseline.get("placeholder", 0))
        new_placeholder = int(stats["placeholder"])
        if (new_placeholder - old_placeholder) >= WARNING_PLACEHOLDER_DELTA_ABS or pct_change(old_placeholder, new_placeholder) > WARNING_PLACEHOLDER_DELTA_PCT:
            if new_placeholder > old_placeholder:
                warnings.append("Количество товаров с заглушкой фото выросло больше допустимого порога.")

        old_false = int(baseline.get("false_count", 0))
        new_false = int(stats["false_count"])
        if new_false > old_false and pct_change(old_false, new_false) > WARNING_FALSE_DELTA_PCT:
            warnings.append("Количество товаров Нет в наличии выросло больше допустимого порога.")

    if errors:
        return CheckResult("НЕУСПЕШНО", errors[0], stats, errors[1:])
    if warnings:
        return CheckResult("ТРЕБУЕТ ВНИМАНИЯ", warnings[0], stats, warnings[1:])
    return CheckResult("УСПЕШНО", "", stats, [])


def build_html_message(result: CheckResult) -> str:
    details = result.details
    checked_at = datetime.fromisoformat(details["checked_at"]) if details.get("checked_at") else None
    build_at = datetime.fromisoformat(details["build_time"]) if details.get("build_time") else None

    lines: list[str] = []
    if result.status == "УСПЕШНО":
        lines.append("✅ <b>Price — УСПЕШНО</b>")
    elif result.status == "ТРЕБУЕТ ВНИМАНИЯ":
        lines.append("⚠️ <b>Price — ТРЕБУЕТ ВНИМАНИЯ</b>")
    else:
        lines.append("❌ <b>Price — НЕУСПЕШНО</b>")

    lines.append("")
    lines.append(html_label("Время проверки", fmt_dt(checked_at)))
    if build_at is not None:
        lines.append(html_label("Время сборки Price", fmt_dt(build_at)))

    if result.status != "НЕУСПЕШНО":
        lines.append("")
        lines.append(html_label("Товаров в Price", details["total"], bullet=True))
        lines.append(html_label("Есть в наличии", details["true_count"], bullet=True))
        lines.append(html_label("Нет в наличии", details["false_count"], bullet=True))
        lines.append("")
        lines.append(html_label("С ценой 100", details["price100"], bullet=True))
        lines.append(html_label("С заглушкой фото", details["placeholder"], bullet=True))
        lines.append(html_label("Без categoryId", details["missing_category_id"], bullet=True))
        lines.append(html_label("Без категории Satu", details["missing_satu"], bullet=True))

    if result.reason:
        lines.append("")
        lines.append("<b>Причина:</b>")
        lines.append(html_escape(result.reason))
        if result.extra_errors:
            tail = "Дополнительно обнаружены другие ошибки." if result.status == "НЕУСПЕШНО" else "Есть и другие отклонения, которые стоит проверить."
            lines.append("")
            lines.append(html_escape(tail))

    if result.status != "НЕУСПЕШНО":
        lines.append("")
        lines.append(html_label("Привязка к категориям Satu", "УСПЕШНО" if details["missing_satu"] == 0 else "НЕУСПЕШНО"))
    lines.append(html_label("Статус проверки Price", result.status))

    return "\n".join(lines)


def build_text_report(result: CheckResult) -> str:
    details = result.details
    checked_at = datetime.fromisoformat(details["checked_at"]) if details.get("checked_at") else None
    build_at = datetime.fromisoformat(details["build_time"]) if details.get("build_time") else None
    lines = [f"Price — {result.status}", ""]
    lines.append(f"Время проверки: {fmt_dt(checked_at)}")
    if build_at is not None:
        lines.append(f"Время сборки Price: {fmt_dt(build_at)}")
    lines.append("")
    lines.append(f"Товаров в Price: {details['total']}")
    lines.append(f"Есть в наличии: {details['true_count']}")
    lines.append(f"Нет в наличии: {details['false_count']}")
    lines.append(f"С ценой 100: {details['price100']}")
    lines.append(f"С заглушкой фото: {details['placeholder']}")
    lines.append(f"Без categoryId: {details['missing_category_id']}")
    lines.append(f"Без категории Satu: {details['missing_satu']}")
    lines.append(f"Дубли offer id: {details['dup_offer_ids']}")
    lines.append(f"Дубли vendorCode: {details['dup_vendor_codes']}")
    if result.reason:
        lines.append("")
        lines.append("Причина:")
        lines.append(result.reason)
    return "\n".join(lines) + "\n"


def send_telegram(html_text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return

    data = urlencode({
        "chat_id": chat_id,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = Request(
        url=f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(req, timeout=30) as resp:
        resp.read()


def main() -> int:
    ensure_parent(REPORT_PATH)
    ensure_parent(BASELINE_PATH)

    try:
        stats = load_price_stats(PRICE_PATH)
    except FileNotFoundError as e:
        result = CheckResult("НЕУСПЕШНО", str(e), {
            "checked_at": now_almaty().isoformat(),
            "total": 0,
            "true_count": 0,
            "false_count": 0,
            "price100": 0,
            "placeholder": 0,
            "missing_category_id": 0,
            "missing_satu": 0,
            "dup_offer_ids": 0,
            "dup_vendor_codes": 0,
            "build_time": None,
        }, [])
        REPORT_PATH.write_text(build_text_report(result), encoding="utf-8")
        send_telegram(build_html_message(result))
        return 1
    except ValueError as e:
        result = CheckResult("НЕУСПЕШНО", str(e), {
            "checked_at": now_almaty().isoformat(),
            "total": 0,
            "true_count": 0,
            "false_count": 0,
            "price100": 0,
            "placeholder": 0,
            "missing_category_id": 0,
            "missing_satu": 0,
            "dup_offer_ids": 0,
            "dup_vendor_codes": 0,
            "build_time": None,
        }, [])
        REPORT_PATH.write_text(build_text_report(result), encoding="utf-8")
        send_telegram(build_html_message(result))
        return 1

    baseline = load_baseline(BASELINE_PATH)
    result = evaluate(stats, baseline)
    REPORT_PATH.write_text(build_text_report(result), encoding="utf-8")
    send_telegram(build_html_message(result))

    # Сохраняем базу только для успешной проверки.
    if result.status == "УСПЕШНО":
        BASELINE_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    if result.status == "ТРЕБУЕТ ВНИМАНИЯ":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
