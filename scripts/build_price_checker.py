#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Checker для docs/Price.yml + Telegram уведомление."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PRICE_PATH = Path("docs/Price.yml")
REPORT_PATH = Path("docs/raw/price_checker_report.txt")
BASELINE_PATH = Path("docs/raw/price_checker_last_success.json")
EXPECTED_SUPPLIERS = ["AkCent", "AlStyle", "ComPortal", "CopyLine", "VTT"]
TIMEZONE_LABEL = "Алматы"

# Пороги предупреждений
WARN_TOTAL_DELTA_PCT = 5.0
WARN_SUPPLIER_DELTA_PCT = 10.0
WARN_PRICE100_ABS = 50
WARN_PRICE100_PCT = 10.0
WARN_PLACEHOLDER_ABS = 50
WARN_PLACEHOLDER_PCT = 10.0
WARN_FALSE_PCT = 20.0

# Пороги ошибок
FAIL_TOTAL_DROP_PCT = 15.0
FAIL_SUPPLIER_DROP_PCT = 25.0

PLACEHOLDER_URL = "https://placehold.co/800x800/png?text=No+Photo"


@dataclass
class CheckResult:
    status: str
    reason: str
    stats: dict[str, Any]
    report_text: str


def _safe_int(text: str | None, default: int = 0) -> int:
    try:
        return int(str(text).strip())
    except Exception:
        return default


def _safe_float(value: int, baseline: int) -> float:
    if baseline <= 0:
        return 0.0 if value == 0 else 100.0
    return abs(value - baseline) * 100.0 / baseline


def _extract_feed_meta(text: str) -> str:
    m = re.search(r"<!--FEED_META\n(.*?)-->", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_meta_value(meta: str, key: str) -> str:
    pattern = rf"^{re.escape(key)}\s*\|\s*(.+)$"
    for line in meta.splitlines():
        m = re.match(pattern, line)
        if m:
            return m.group(1).strip()
    return ""


def _parse_supplier_counts(meta: str) -> dict[str, int]:
    blocks = [b.strip() for b in meta.split("\n\n") if b.strip()]
    result: dict[str, int] = {}
    for block in blocks:
        supplier = _extract_meta_value(block, "Поставщик")
        if supplier in EXPECTED_SUPPLIERS:
            count = _safe_int(_extract_meta_value(block, "Сколько товаров у поставщика после фильтра"))
            result[supplier] = count
    return result


def _send_telegram(text: str) -> tuple[bool, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False, "Telegram секреты не заданы"

    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
    }).encode("utf-8")
    req = urllib.request.Request(endpoint, data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        return True, body[:200]
    except Exception as exc:
        return False, str(exc)


def _load_baseline() -> dict[str, Any]:
    if not BASELINE_PATH.exists():
        return {}
    try:
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_baseline(stats: dict[str, Any]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")


def _collect_stats(root: ET.Element, price_text: str) -> dict[str, Any]:
    offers_parent = root.find("./shop/offers")
    categories_parent = root.find("./shop/categories")
    if offers_parent is None:
        raise ValueError("В Price отсутствует блок offers.")
    if categories_parent is None:
        raise ValueError("В Price отсутствует блок categories.")

    category_ids = {str(cat.get("id", "")).strip() for cat in categories_parent.findall("category") if str(cat.get("id", "")).strip()}
    offers = offers_parent.findall("offer")

    total = len(offers)
    true_count = 0
    false_count = 0
    price100 = 0
    placeholder = 0
    no_category = 0
    no_satu_category = 0
    duplicate_offer_ids = 0
    duplicate_vendor_codes = 0
    bad_category_refs = 0

    seen_offer_ids: set[str] = set()
    seen_vendor_codes: set[str] = set()

    for offer in offers:
        offer_id = str(offer.get("id", "")).strip()
        if offer_id in seen_offer_ids:
            duplicate_offer_ids += 1
        elif offer_id:
            seen_offer_ids.add(offer_id)

        if str(offer.get("available", "")).strip().lower() == "true":
            true_count += 1
        else:
            false_count += 1

        category_id = (offer.findtext("categoryId") or "").strip()
        if not category_id:
            no_category += 1
        elif category_id not in category_ids:
            bad_category_refs += 1

        # Проверка наличия категории Satu: либо override в offer, либо portal_id у категории
        portal_category_id = (offer.findtext("portal_category_id") or "").strip()
        if not portal_category_id and category_id:
            cat_el = categories_parent.find(f"category[@id='{category_id}']")
            if cat_el is not None:
                portal_category_id = str(cat_el.get("portal_id", "")).strip()
        if not portal_category_id:
            no_satu_category += 1

        vendor_code = (offer.findtext("vendorCode") or "").strip()
        if vendor_code in seen_vendor_codes:
            duplicate_vendor_codes += 1
        elif vendor_code:
            seen_vendor_codes.add(vendor_code)

        price_val = (offer.findtext("price") or "").strip()
        if price_val == "100":
            price100 += 1

        for pic in offer.findall("picture"):
            if (pic.text or "").strip() == PLACEHOLDER_URL:
                placeholder += 1
                break

    meta = _extract_feed_meta(price_text)
    supplier_counts = _parse_supplier_counts(meta)
    missing_suppliers = [s for s in EXPECTED_SUPPLIERS if s not in supplier_counts]
    build_time = _extract_meta_value(meta, "Время сборки (Алматы)") or ""

    return {
        "build_time": build_time,
        "total": total,
        "true_count": true_count,
        "false_count": false_count,
        "price100": price100,
        "placeholder": placeholder,
        "no_category": no_category,
        "no_satu_category": no_satu_category,
        "duplicate_offer_ids": duplicate_offer_ids,
        "duplicate_vendor_codes": duplicate_vendor_codes,
        "bad_category_refs": bad_category_refs,
        "supplier_counts": supplier_counts,
        "missing_suppliers": missing_suppliers,
    }


def _main_failure_reason(price_text: str, stats: dict[str, Any], parse_error: str = "") -> str:
    if not price_text:
        return "Файл Price.yml пустой."
    if parse_error:
        return "XML в Price повреждён."
    if stats["missing_suppliers"]:
        return "В Price отсутствует один или несколько поставщиков."
    if stats["no_category"] > 0:
        return "В Price есть товары без categoryId."
    if stats["no_satu_category"] > 0:
        return "В Price есть товары без категории Satu."
    if stats["duplicate_offer_ids"] > 0:
        return "В Price обнаружены дубли offer id."
    if stats["duplicate_vendor_codes"] > 0:
        return "В Price обнаружены дубли vendorCode."
    if stats["bad_category_refs"] > 0:
        return "В Price есть товары с categoryId, которых нет в блоке categories."
    return "Сборка Price не завершилась успешно."


def _warning_reason(stats: dict[str, Any], baseline: dict[str, Any]) -> str:
    total = int(stats["total"])
    base_total = int(baseline.get("total", total))
    total_pct = _safe_float(total, base_total)
    if total_pct > WARN_TOTAL_DELTA_PCT:
        return "Общее количество товаров изменилось больше допустимого порога."

    for supplier in EXPECTED_SUPPLIERS:
        cur = int(stats["supplier_counts"].get(supplier, 0))
        base = int(baseline.get("supplier_counts", {}).get(supplier, cur))
        pct = _safe_float(cur, base)
        if pct > WARN_SUPPLIER_DELTA_PCT:
            return f"Количество товаров у поставщика {supplier} изменилось больше допустимого порога."

    cur_100 = int(stats["price100"])
    base_100 = int(baseline.get("price100", cur_100))
    if cur_100 - base_100 >= WARN_PRICE100_ABS or _safe_float(cur_100, base_100) > WARN_PRICE100_PCT:
        return "Количество товаров с ценой 100 выросло больше допустимого порога."

    cur_ph = int(stats["placeholder"])
    base_ph = int(baseline.get("placeholder", cur_ph))
    if cur_ph - base_ph >= WARN_PLACEHOLDER_ABS or _safe_float(cur_ph, base_ph) > WARN_PLACEHOLDER_PCT:
        return "Количество товаров с заглушкой фото выросло больше допустимого порога."

    cur_false = int(stats["false_count"])
    base_false = int(baseline.get("false_count", cur_false))
    if cur_false > base_false and _safe_float(cur_false, base_false) > WARN_FALSE_PCT:
        return "Количество товаров Нет в наличии выросло больше допустимого порога."

    return ""


def _hard_drop_failure(stats: dict[str, Any], baseline: dict[str, Any]) -> str:
    if not baseline:
        return ""
    total = int(stats["total"])
    base_total = int(baseline.get("total", total))
    if base_total > 0 and total < base_total:
        drop_pct = (base_total - total) * 100.0 / base_total
        if drop_pct > FAIL_TOTAL_DROP_PCT:
            return "Общее количество товаров просело больше допустимого порога."

    for supplier in EXPECTED_SUPPLIERS:
        cur = int(stats["supplier_counts"].get(supplier, 0))
        base = int(baseline.get("supplier_counts", {}).get(supplier, cur))
        if base > 0 and cur < base:
            drop_pct = (base - cur) * 100.0 / base
            if drop_pct > FAIL_SUPPLIER_DROP_PCT:
                return f"Количество товаров у поставщика {supplier} просело больше допустимого порога."
    return ""


def _format_message(result: CheckResult, checked_at: str) -> str:
    s = result.stats
    if result.status == "НЕУСПЕШНО":
        return (
            "❌ Price — НЕУСПЕШНО\n\n"
            f"Время проверки ({TIMEZONE_LABEL}): {checked_at}\n\n"
            f"Причина:\n{result.reason}\n\n"
            "Статус проверки Price: НЕУСПЕШНО"
        )

    body = [
        f"{'⚠️' if result.status == 'ТРЕБУЕТ ВНИМАНИЯ' else '✅'} Price — {result.status}",
        "",
        f"Время проверки ({TIMEZONE_LABEL}): {checked_at}",
        f"Время сборки Price ({TIMEZONE_LABEL}): {s.get('build_time', '')}",
        "",
        f"Товаров в Price: {s['total']}",
        f"Есть в наличии: {s['true_count']}",
        f"Нет в наличии: {s['false_count']}",
        "",
        f"С ценой 100: {s['price100']}",
        f"С заглушкой фото: {s['placeholder']}",
        f"Без categoryId: {s['no_category']}",
        f"Без категории Satu: {s['no_satu_category']}",
    ]
    if result.status == "ТРЕБУЕТ ВНИМАНИЯ":
        body.extend(["", f"Причина:\n{result.reason}"])
    body.extend([
        "",
        f"Привязка к категориям Satu: {'УСПЕШНО' if s['no_satu_category'] == 0 else 'НЕУСПЕШНО'}",
        f"Статус проверки Price: {result.status}",
    ])
    return "\n".join(body)


def _format_report(result: CheckResult, checked_at: str, tg_status: str) -> str:
    s = result.stats
    lines = [
        "Итог проверки Price",
        f"Время проверки ({TIMEZONE_LABEL})                      | {checked_at}",
        f"Статус проверки Price                      | {result.status}",
        f"Причина                                     | {result.reason or '-'}",
        f"Время сборки Price ({TIMEZONE_LABEL})        | {s.get('build_time', '')}",
        f"Товаров в Price                              | {s.get('total', 0)}",
        f"Есть в наличии                               | {s.get('true_count', 0)}",
        f"Нет в наличии                                | {s.get('false_count', 0)}",
        f"С ценой 100                                  | {s.get('price100', 0)}",
        f"С заглушкой фото                             | {s.get('placeholder', 0)}",
        f"Без categoryId                               | {s.get('no_category', 0)}",
        f"Без категории Satu                           | {s.get('no_satu_category', 0)}",
        f"Дубли offer id                               | {s.get('duplicate_offer_ids', 0)}",
        f"Дубли vendorCode                             | {s.get('duplicate_vendor_codes', 0)}",
        f"Некорректные categoryId                      | {s.get('bad_category_refs', 0)}",
        f"Пропавшие поставщики                         | {', '.join(s.get('missing_suppliers', [])) or '-'}",
        f"Статус Telegram                              | {tg_status}",
    ]
    return "\n".join(lines) + "\n"


def run() -> int:
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not PRICE_PATH.exists():
        result = CheckResult("НЕУСПЕШНО", "Файл Price.yml не найден.", {}, "")
        message = _format_message(result, checked_at)
        ok, tg = _send_telegram(message)
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(_format_report(result, checked_at, "ОТПРАВЛЕНО" if ok else f"НЕ ОТПРАВЛЕНО: {tg}"), encoding="utf-8")
        return 1

    price_text = PRICE_PATH.read_text(encoding="utf-8", errors="ignore")
    if not price_text.strip():
        result = CheckResult("НЕУСПЕШНО", "Файл Price.yml пустой.", {}, "")
        message = _format_message(result, checked_at)
        ok, tg = _send_telegram(message)
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(_format_report(result, checked_at, "ОТПРАВЛЕНО" if ok else f"НЕ ОТПРАВЛЕНО: {tg}"), encoding="utf-8")
        return 1

    parse_error = ""
    try:
        root = ET.fromstring(price_text)
        stats = _collect_stats(root, price_text)
    except Exception as exc:
        parse_error = str(exc)
        stats = {
            "build_time": "",
            "total": 0,
            "true_count": 0,
            "false_count": 0,
            "price100": 0,
            "placeholder": 0,
            "no_category": 0,
            "no_satu_category": 0,
            "duplicate_offer_ids": 0,
            "duplicate_vendor_codes": 0,
            "bad_category_refs": 0,
            "supplier_counts": {},
            "missing_suppliers": EXPECTED_SUPPLIERS,
        }

    baseline = _load_baseline()
    reason = ""
    status = "УСПЕШНО"

    if parse_error:
        status = "НЕУСПЕШНО"
        reason = _main_failure_reason(price_text, stats, parse_error)
    else:
        reason = _hard_drop_failure(stats, baseline)
        if reason:
            status = "НЕУСПЕШНО"
        else:
            reason = _main_failure_reason(price_text, stats)
            if reason != "Сборка Price не завершилась успешно.":
                status = "НЕУСПЕШНО"
            else:
                reason = _warning_reason(stats, baseline)
                if reason:
                    status = "ТРЕБУЕТ ВНИМАНИЯ"
                else:
                    reason = ""
                    status = "УСПЕШНО"

    result = CheckResult(status=status, reason=reason, stats=stats, report_text="")
    message = _format_message(result, checked_at)
    ok, tg = _send_telegram(message)
    tg_status = "ОТПРАВЛЕНО" if ok else f"НЕ ОТПРАВЛЕНО: {tg}"

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(_format_report(result, checked_at, tg_status), encoding="utf-8")

    if status == "УСПЕШНО":
        _save_baseline(stats)

    return 0 if status != "НЕУСПЕШНО" else 1


if __name__ == "__main__":
    raise SystemExit(run())
