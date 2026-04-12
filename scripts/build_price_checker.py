#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Price checker with Telegram HTML notifications and robust baseline handling."""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

ALMATY_TZ = ZoneInfo("Asia/Almaty")
PLACEHOLDER_URL = "https://placehold.co/800x800/png?text=No+Photo"
SCHEMA_VERSION = 2

PRICE_FILE = Path("docs/Price.yml")
REPORT_FILE = Path("docs/raw/price_checker_report.txt")
BASELINE_FILE = Path("docs/raw/price_checker_last_success.json")

SUPPLIERS = {
    "AkCent": ("AC",),
    "AlStyle": ("AS",),
    "ComPortal": ("CP",),
    "CopyLine": ("CL",),
    "VTT": ("VT",),
}
SUPPLIER_ORDER = ["AkCent", "AlStyle", "ComPortal", "CopyLine", "VTT"]

SUCCESS = "УСПЕШНО"
ATTENTION = "ТРЕБУЕТ ВНИМАНИЯ"
FAIL = "НЕУСПЕШНО"


@dataclass
class Metrics:
    price_build_time: str
    total_offers: int
    available_true: int
    available_false: int
    price_100_count: int
    placeholder_picture_count: int
    empty_category_id_count: int
    no_satu_category_count: int
    duplicate_offer_id_count: int
    duplicate_vendor_code_count: int
    invalid_category_ref_count: int
    categories_count: int
    supplier_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "price_build_time": self.price_build_time,
            "total_offers": self.total_offers,
            "available_true": self.available_true,
            "available_false": self.available_false,
            "price_100_count": self.price_100_count,
            "placeholder_picture_count": self.placeholder_picture_count,
            "empty_category_id_count": self.empty_category_id_count,
            "no_satu_category_count": self.no_satu_category_count,
            "duplicate_offer_id_count": self.duplicate_offer_id_count,
            "duplicate_vendor_code_count": self.duplicate_vendor_code_count,
            "invalid_category_ref_count": self.invalid_category_ref_count,
            "categories_count": self.categories_count,
            "supplier_counts": self.supplier_counts,
        }


def now_almaty_str() -> str:
    return datetime.now(ALMATY_TZ).strftime("%Y-%m-%d %H:%M:%S")


def send_telegram_html(token: str, chat_id: str, text: str) -> None:
    if not token or not chat_id:
        return
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30):
        pass


def parse_price_metrics(path: Path) -> Metrics:
    if not path.exists():
        raise FileNotFoundError("Файл Price.yml не найден.")
    if path.stat().st_size == 0:
        raise ValueError("Файл Price.yml пустой.")

    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ValueError("XML в Price повреждён.") from exc

    root = tree.getroot()
    price_build_time = root.attrib.get("date", "")
    shop = root.find("shop")
    if shop is None:
        raise ValueError("В Price отсутствует блок shop.")

    categories_el = shop.find("categories")
    if categories_el is None:
        raise ValueError("В Price отсутствует блок categories.")
    offers_el = shop.find("offers")
    if offers_el is None:
        raise ValueError("В Price отсутствует блок offers.")

    categories: set[str] = set()
    category_portal_map: dict[str, str] = {}
    for category in categories_el.findall("category"):
        cid = (category.attrib.get("id") or "").strip()
        if not cid:
            continue
        categories.add(cid)
        portal_id = (category.attrib.get("portal_id") or "").strip()
        if portal_id:
            category_portal_map[cid] = portal_id

    offers = offers_el.findall("offer")
    if not offers:
        raise ValueError("В Price отсутствуют товары в блоке offers.")

    offer_ids: list[str] = []
    vendor_codes: list[str] = []
    supplier_counts = {name: 0 for name in SUPPLIER_ORDER}

    available_true = 0
    available_false = 0
    price_100_count = 0
    placeholder_picture_count = 0
    empty_category_id_count = 0
    no_satu_category_count = 0
    invalid_category_ref_count = 0

    for offer in offers:
        oid = (offer.attrib.get("id") or "").strip()
        offer_ids.append(oid)
        available = (offer.attrib.get("available") or "").strip().lower()
        if available == "true":
            available_true += 1
        else:
            available_false += 1

        for supplier, prefixes in SUPPLIERS.items():
            if any(oid.startswith(prefix) for prefix in prefixes):
                supplier_counts[supplier] += 1
                break

        vendor_code = (offer.findtext("vendorCode") or "").strip()
        vendor_codes.append(vendor_code)

        category_id = (offer.findtext("categoryId") or "").strip()
        if not category_id:
            empty_category_id_count += 1
        elif category_id not in categories:
            invalid_category_ref_count += 1

        price_text = (offer.findtext("price") or "").strip()
        try:
            if int(float(price_text)) == 100:
                price_100_count += 1
        except Exception:
            pass

        pictures = [
            (p.text or "").strip()
            for p in offer.findall("picture")
            if (p.text or "").strip()
        ]
        if not pictures or all(p == PLACEHOLDER_URL for p in pictures):
            placeholder_picture_count += 1

        offer_portal = (offer.findtext("portal_category_id") or "").strip()
        category_portal = category_portal_map.get(category_id, "")
        if not offer_portal and not category_portal:
            no_satu_category_count += 1

    duplicate_offer_id_count = sum(v - 1 for v in Counter(offer_ids).values() if v > 1)
    duplicate_vendor_code_count = sum(v - 1 for v in Counter(vendor_codes).values() if v > 1 and v > 0)

    return Metrics(
        price_build_time=price_build_time,
        total_offers=len(offers),
        available_true=available_true,
        available_false=available_false,
        price_100_count=price_100_count,
        placeholder_picture_count=placeholder_picture_count,
        empty_category_id_count=empty_category_id_count,
        no_satu_category_count=no_satu_category_count,
        duplicate_offer_id_count=duplicate_offer_id_count,
        duplicate_vendor_code_count=duplicate_vendor_code_count,
        invalid_category_ref_count=invalid_category_ref_count,
        categories_count=len(categories),
        supplier_counts=supplier_counts,
    )


def load_baseline() -> Metrics | None:
    if not BASELINE_FILE.exists():
        return None
    try:
        data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("schema_version") != SCHEMA_VERSION:
        return None
    try:
        return Metrics(
            price_build_time=str(data.get("price_build_time", "")),
            total_offers=int(data.get("total_offers", 0)),
            available_true=int(data.get("available_true", 0)),
            available_false=int(data.get("available_false", 0)),
            price_100_count=int(data.get("price_100_count", 0)),
            placeholder_picture_count=int(data.get("placeholder_picture_count", 0)),
            empty_category_id_count=int(data.get("empty_category_id_count", 0)),
            no_satu_category_count=int(data.get("no_satu_category_count", 0)),
            duplicate_offer_id_count=int(data.get("duplicate_offer_id_count", 0)),
            duplicate_vendor_code_count=int(data.get("duplicate_vendor_code_count", 0)),
            invalid_category_ref_count=int(data.get("invalid_category_ref_count", 0)),
            categories_count=int(data.get("categories_count", 0)),
            supplier_counts={k: int(v) for k, v in dict(data.get("supplier_counts", {})).items()},
        )
    except Exception:
        return None


def save_baseline(metrics: Metrics) -> None:
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(
        json.dumps(metrics.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def pct_change(current: int, previous: int) -> float:
    if previous <= 0:
        return 0.0 if current <= 0 else 100.0
    return abs(current - previous) / previous * 100.0


def evaluate(current: Metrics, baseline: Metrics | None) -> tuple[str, str | None, list[str]]:
    critical_reasons: list[str] = []
    if current.total_offers <= 0:
        critical_reasons.append("В Price отсутствуют товары.")
    missing_suppliers = [name for name in SUPPLIER_ORDER if current.supplier_counts.get(name, 0) == 0]
    if missing_suppliers:
        critical_reasons.append("В Price отсутствует один или несколько поставщиков.")
    if current.empty_category_id_count > 0:
        critical_reasons.append("В Price есть товары без categoryId.")
    if current.no_satu_category_count > 0:
        critical_reasons.append("В Price есть товары без категории Satu.")
    if current.duplicate_offer_id_count > 0:
        critical_reasons.append("В Price обнаружены дубли offer id.")
    if current.duplicate_vendor_code_count > 0:
        critical_reasons.append("В Price обнаружены дубли vendorCode.")
    if current.invalid_category_ref_count > 0:
        critical_reasons.append("В Price есть товары с categoryId, которых нет в блоке categories.")

    if critical_reasons:
        return FAIL, critical_reasons[0], critical_reasons

    if baseline is None:
        return SUCCESS, None, []

    attention_reasons: list[str] = []

    if pct_change(current.total_offers, baseline.total_offers) > 5.0:
        attention_reasons.append("Общее количество товаров изменилось больше допустимого порога.")

    for supplier in SUPPLIER_ORDER:
        cur = current.supplier_counts.get(supplier, 0)
        prev = baseline.supplier_counts.get(supplier, 0)
        if pct_change(cur, prev) > 10.0:
            attention_reasons.append(
                f"Количество товаров у поставщика {supplier} изменилось больше допустимого порога."
            )
            break

    price100_growth_abs = current.price_100_count - baseline.price_100_count
    price100_growth_pct = pct_change(current.price_100_count, baseline.price_100_count)
    if price100_growth_abs >= 50 or price100_growth_pct > 10.0:
        attention_reasons.append("Количество товаров с ценой 100 выросло больше допустимого порога.")

    placeholder_growth_abs = current.placeholder_picture_count - baseline.placeholder_picture_count
    placeholder_growth_pct = pct_change(current.placeholder_picture_count, baseline.placeholder_picture_count)
    if placeholder_growth_abs >= 50 or placeholder_growth_pct > 10.0:
        attention_reasons.append("Количество товаров с заглушкой фото выросло больше допустимого порога.")

    if baseline.available_false > 0:
        if current.available_false > baseline.available_false and pct_change(current.available_false, baseline.available_false) > 20.0:
            attention_reasons.append("Количество товаров Нет в наличии выросло больше допустимого порога.")
    elif current.available_false > baseline.available_false + 0:
        # Do not warn on first appearance from zero unless it is substantial.
        if current.available_false >= 50:
            attention_reasons.append("Количество товаров Нет в наличии выросло больше допустимого порога.")

    if attention_reasons:
        return ATTENTION, attention_reasons[0], attention_reasons

    return SUCCESS, None, []


def build_report(check_time: str, status: str, current: Metrics | None, reason: str | None, extra: list[str]) -> str:
    lines = [f"Price checker", f"Время проверки (Алматы) | {check_time}", f"Статус | {status}"]
    if current is not None:
        lines.extend(
            [
                f"Время сборки Price (Алматы) | {current.price_build_time}",
                f"Товаров в Price | {current.total_offers}",
                f"Есть в наличии | {current.available_true}",
                f"Нет в наличии | {current.available_false}",
                f"С ценой 100 | {current.price_100_count}",
                f"С заглушкой фото | {current.placeholder_picture_count}",
                f"Без categoryId | {current.empty_category_id_count}",
                f"Без категории Satu | {current.no_satu_category_count}",
                f"Дубли offer id | {current.duplicate_offer_id_count}",
                f"Дубли vendorCode | {current.duplicate_vendor_code_count}",
            ]
        )
    if reason:
        lines.append(f"Причина | {reason}")
    if extra and len(extra) > 1:
        lines.append("Дополнительно:")
        lines.extend(f"- {item}" for item in extra[1:])
    return "\n".join(lines) + "\n"


def format_line(label: str, value: Any) -> str:
    nbsp = "\u00A0" * 3
    return f"• <b>{escape(label)}:</b>{nbsp}{escape(str(value))}"


def build_telegram_message(check_time: str, status: str, current: Metrics | None, reason: str | None, extra: list[str]) -> str:
    if status == SUCCESS and current is not None:
        lines = [
            "✅ <b>Price — УСПЕШНО</b>",
            "",
            f"Проверка (Алматы): {escape(check_time)}",
            f"Время сборки Price (Алматы): {escape(current.price_build_time)}",
            "",
            format_line("Товаров в Price", current.total_offers),
            format_line("Есть в наличии", current.available_true),
            format_line("Нет в наличии", current.available_false),
            "",
            format_line("С ценой 100", current.price_100_count),
            format_line("С заглушкой фото", current.placeholder_picture_count),
            format_line("Без categoryId", current.empty_category_id_count),
            format_line("Без категории Satu", current.no_satu_category_count),
            "",
            f"Привязка к категориям Satu: <b>{SUCCESS}</b>",
            f"Статус проверки Price: <b>{SUCCESS}</b>",
        ]
        return "\n".join(lines)

    if status == ATTENTION and current is not None:
        lines = [
            "⚠️ <b>Price — ТРЕБУЕТ ВНИМАНИЯ</b>",
            "",
            f"Проверка (Алматы): {escape(check_time)}",
            f"Время сборки Price (Алматы): {escape(current.price_build_time)}",
            "",
            format_line("Товаров в Price", current.total_offers),
            format_line("Есть в наличии", current.available_true),
            format_line("Нет в наличии", current.available_false),
            "",
            format_line("С ценой 100", current.price_100_count),
            format_line("С заглушкой фото", current.placeholder_picture_count),
            format_line("Без categoryId", current.empty_category_id_count),
            format_line("Без категории Satu", current.no_satu_category_count),
            "",
            "Причина:",
            escape(reason or "Есть показатели, которые стоит проверить."),
        ]
        if extra and len(extra) > 1:
            lines.extend(["", "Есть и другие отклонения, которые стоит проверить."])
        lines.extend(
            [
                "",
                f"Привязка к категориям Satu: <b>{SUCCESS}</b>",
                f"Статус проверки Price: <b>{ATTENTION}</b>",
            ]
        )
        return "\n".join(lines)

    lines = [
        "❌ <b>Price — НЕУСПЕШНО</b>",
        "",
        f"Проверка (Алматы): {escape(check_time)}",
        "",
        "Причина:",
        escape(reason or "Checker завершился с ошибкой на уровне workflow."),
    ]
    if extra and len(extra) > 1:
        lines.extend(["", "Дополнительно обнаружены другие ошибки."])
    lines.extend(["", f"Статус проверки Price: <b>{FAIL}</b>"])
    return "\n".join(lines)


def main() -> int:
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    check_time = now_almaty_str()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    current: Metrics | None = None
    baseline = load_baseline()

    try:
        current = parse_price_metrics(PRICE_FILE)
        status, reason, extra = evaluate(current, baseline)
    except Exception as exc:
        status = FAIL
        reason = str(exc)
        extra = [reason]

    if status == SUCCESS and current is not None:
        save_baseline(current)

    report = build_report(check_time, status, current, reason, extra)
    REPORT_FILE.write_text(report, encoding="utf-8")

    try:
        send_telegram_html(token, chat_id, build_telegram_message(check_time, status, current, reason, extra))
    except Exception:
        pass

    return 0 if status in {SUCCESS, ATTENTION} else 1


if __name__ == "__main__":
    raise SystemExit(main())
