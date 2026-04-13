# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
import urllib.parse
import urllib.request

ALMATY_TZ = ZoneInfo("Asia/Almaty")
ROOT = Path(__file__).resolve().parents[1]
PRICE_FILE = ROOT / "docs" / "Price.yml"
RAW_DIR = ROOT / "docs" / "raw"
REPORT_FILE = RAW_DIR / "price_checker_report.txt"
LAST_SUCCESS_FILE = RAW_DIR / "price_checker_last_success.json"

UNMAPPED_FILES = {
    "AkCent": RAW_DIR / "akcent_unmapped_category_ids.txt",
    "AlStyle": RAW_DIR / "alstyle_unmapped_category_ids.txt",
    "ComPortal": RAW_DIR / "comportal_unmapped_category_ids.txt",
    "CopyLine": RAW_DIR / "copyline_unmapped_category_ids.txt",
    "VTT": RAW_DIR / "vtt_unmapped_category_ids.txt",
}

EXPECTED_SUPPLIERS = ("AkCent", "AlStyle", "ComPortal", "CopyLine", "VTT")

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

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


@dataclass
class Metrics:
    build_time: str = ""
    total: int = 0
    available_true: int = 0
    available_false: int = 0
    price100: int = 0
    placeholder: int = 0
    empty_category: int = 0
    unknown_satu: int = 0
    duplicate_offer_ids: int = 0
    duplicate_vendor_codes: int = 0
    supplier_counts: Dict[str, int] | None = None
    excluded_unmapped_total: int = 0
    excluded_unmapped_by_supplier: Dict[str, int] | None = None

    def to_json(self) -> dict:
        return {
            "build_time": self.build_time,
            "total": self.total,
            "available_true": self.available_true,
            "available_false": self.available_false,
            "price100": self.price100,
            "placeholder": self.placeholder,
            "empty_category": self.empty_category,
            "unknown_satu": self.unknown_satu,
            "duplicate_offer_ids": self.duplicate_offer_ids,
            "duplicate_vendor_codes": self.duplicate_vendor_codes,
            "supplier_counts": self.supplier_counts or {},
            "excluded_unmapped_total": self.excluded_unmapped_total,
            "excluded_unmapped_by_supplier": self.excluded_unmapped_by_supplier or {},
        }

    @classmethod
    def from_json(cls, data: dict) -> "Metrics":
        return cls(
            build_time=str(data.get("build_time", "")),
            total=int(data.get("total", 0)),
            available_true=int(data.get("available_true", 0)),
            available_false=int(data.get("available_false", 0)),
            price100=int(data.get("price100", 0)),
            placeholder=int(data.get("placeholder", 0)),
            empty_category=int(data.get("empty_category", 0)),
            unknown_satu=int(data.get("unknown_satu", 0)),
            duplicate_offer_ids=int(data.get("duplicate_offer_ids", 0)),
            duplicate_vendor_codes=int(data.get("duplicate_vendor_codes", 0)),
            supplier_counts={k: int(v) for k, v in (data.get("supplier_counts") or {}).items()},
            excluded_unmapped_total=int(data.get("excluded_unmapped_total", 0)),
            excluded_unmapped_by_supplier={k: int(v) for k, v in (data.get("excluded_unmapped_by_supplier") or {}).items()},
        )


def now_almaty() -> datetime:
    return datetime.now(ALMATY_TZ)


def fmt_dt(dt: datetime) -> str:
    dt = dt.astimezone(ALMATY_TZ)
    return f"{dt.day} {MONTHS_RU[dt.month]} {dt.year} г. {dt:%H:%M:%S}"


def parse_build_time_from_feed_meta(text: str) -> str:
    m = re.search(r"Price\s*\n.*?Время сборки \(Алматы\)\s*\|\s*([^\n\r]+)", text, re.S)
    if not m:
        return ""
    raw = m.group(1).strip()
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ALMATY_TZ)
        return fmt_dt(dt)
    except Exception:
        return raw


def load_xml_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def count_unmapped_lines(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        count += 1
    return count


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

    category_ids = {str(cat.get("id", "")).strip() for cat in categories.findall("category") if str(cat.get("id", "")).strip()}

    supplier_counts = {name: 0 for name in EXPECTED_SUPPLIERS}
    excluded_by_supplier = {name: count_unmapped_lines(path) for name, path in UNMAPPED_FILES.items()}

    offer_ids_seen: set[str] = set()
    vendor_codes_seen: set[str] = set()
    dup_offer = 0
    dup_vendor = 0
    price100 = 0
    placeholder = 0
    empty_category = 0
    unknown_satu = 0
    total = 0
    avail_true = 0
    avail_false = 0

    for offer in offers.findall("offer"):
        total += 1
        oid = str(offer.get("id", "")).strip()
        if oid in offer_ids_seen:
            dup_offer += 1
        elif oid:
            offer_ids_seen.add(oid)

        supplier = extract_offer_supplier(oid)
        if supplier:
            supplier_counts[supplier] += 1

        avail = str(offer.get("available", "")).strip().lower()
        if avail == "true":
            avail_true += 1
        elif avail == "false":
            avail_false += 1

        vc = (offer.findtext("vendorCode") or "").strip()
        if vc in vendor_codes_seen:
            dup_vendor += 1
        elif vc:
            vendor_codes_seen.add(vc)

        cid = (offer.findtext("categoryId") or "").strip()
        if not cid:
            empty_category += 1
        elif cid not in category_ids:
            empty_category += 1

        pcid = (offer.findtext("portal_category_id") or "").strip()
        if not pcid:
            cat = categories.find(f"category[@id='{cid}']") if cid else None
            if cat is None or not str(cat.get("portal_id", "")).strip():
                unknown_satu += 1

        price_text = (offer.findtext("price") or "").strip()
        try:
            if int(float(price_text)) == 100:
                price100 += 1
        except Exception:
            pass

        for pic in offer.findall("picture"):
            if (pic.text or "").strip() == PLACEHOLDER_URL:
                placeholder += 1
                break

    missing_suppliers = [name for name, count in supplier_counts.items() if count <= 0]
    if missing_suppliers:
        raise ValueError("В Price отсутствует один или несколько поставщиков.")

    return Metrics(
        build_time=parse_build_time_from_feed_meta(text),
        total=total,
        available_true=avail_true,
        available_false=avail_false,
        price100=price100,
        placeholder=placeholder,
        empty_category=empty_category,
        unknown_satu=unknown_satu,
        duplicate_offer_ids=dup_offer,
        duplicate_vendor_codes=dup_vendor,
        supplier_counts=supplier_counts,
        excluded_unmapped_total=sum(excluded_by_supplier.values()),
        excluded_unmapped_by_supplier=excluded_by_supplier,
    )


def pct_change(old: int, new: int) -> float:
    if old <= 0:
        return 0.0 if new <= 0 else 100.0
    return abs(new - old) / old * 100.0


def has_warn_abs_pct(old: int, new: int, abs_threshold: int, pct_threshold: float) -> bool:
    return (new - old) >= abs_threshold or pct_change(old, new) >= pct_threshold


def evaluate(metrics: Metrics, baseline: Metrics | None) -> Tuple[str, str]:
    if metrics.empty_category > 0:
        return "НЕУСПЕШНО", "В Price есть товары без categoryId."
    if metrics.unknown_satu > 0:
        return "НЕУСПЕШНО", "В Price есть товары без категории Satu."
    if metrics.duplicate_offer_ids > 0:
        return "НЕУСПЕШНО", "В Price обнаружены дубли offer id."
    if metrics.duplicate_vendor_codes > 0:
        return "НЕУСПЕШНО", "В Price обнаружены дубли vendorCode."
    if baseline is None:
        if metrics.excluded_unmapped_total > 0:
            return "ТРЕБУЕТ ВНИМАНИЯ", "Есть новые товары, исключённые из final из-за отсутствия categoryId."
        return "УСПЕШНО", ""

    if baseline.total > 0 and metrics.total < baseline.total and pct_change(baseline.total, metrics.total) > FAIL_TOTAL_DROP_PCT:
        return "НЕУСПЕШНО", "Общее количество товаров просело больше допустимого порога."

    for supplier in EXPECTED_SUPPLIERS:
        old = (baseline.supplier_counts or {}).get(supplier, 0)
        new = (metrics.supplier_counts or {}).get(supplier, 0)
        if old > 0 and new < old and pct_change(old, new) > FAIL_SUPPLIER_DROP_PCT:
            return "НЕУСПЕШНО", f"Количество товаров у поставщика {supplier} просело больше допустимого порога."

    if metrics.excluded_unmapped_total > 0:
        return "ТРЕБУЕТ ВНИМАНИЯ", "Есть новые товары, исключённые из final из-за отсутствия categoryId."

    if pct_change(baseline.total, metrics.total) > WARN_TOTAL_DELTA_PCT:
        return "ТРЕБУЕТ ВНИМАНИЯ", "Общее количество товаров изменилось больше допустимого порога."

    for supplier in EXPECTED_SUPPLIERS:
        old = (baseline.supplier_counts or {}).get(supplier, 0)
        new = (metrics.supplier_counts or {}).get(supplier, 0)
        if pct_change(old, new) > WARN_SUPPLIER_DELTA_PCT:
            return "ТРЕБУЕТ ВНИМАНИЯ", f"Количество товаров у поставщика {supplier} изменилось больше допустимого порога."

    if has_warn_abs_pct(baseline.price100, metrics.price100, WARN_PRICE100_DELTA_ABS, WARN_PRICE100_DELTA_PCT):
        return "ТРЕБУЕТ ВНИМАНИЯ", "Количество товаров с ценой 100 выросло больше допустимого порога."

    if has_warn_abs_pct(baseline.placeholder, metrics.placeholder, WARN_PLACEHOLDER_DELTA_ABS, WARN_PLACEHOLDER_DELTA_PCT):
        return "ТРЕБУЕТ ВНИМАНИЯ", "Количество товаров с заглушкой фото выросло больше допустимого порога."

    if baseline.available_false > 0 and metrics.available_false > baseline.available_false and pct_change(baseline.available_false, metrics.available_false) > WARN_FALSE_DELTA_PCT:
        return "ТРЕБУЕТ ВНИМАНИЯ", "Количество товаров Нет в наличии выросло больше допустимого порога."

    return "УСПЕШНО", ""


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
    LAST_SUCCESS_FILE.write_text(json.dumps(metrics.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")


def esc_html(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def bold_line(label: str, value: str | int) -> str:
    nbsp = "\u00A0\u00A0\u00A0"
    return f"• <b>{esc_html(label)}:</b>{nbsp}{esc_html(value)}"


def render_message(status: str, reason: str, metrics: Metrics, checked_at: datetime) -> str:
    icon = {"УСПЕШНО": "✅", "ТРЕБУЕТ ВНИМАНИЯ": "⚠️", "НЕУСПЕШНО": "❌"}.get(status, "ℹ️")
    lines = [f"{icon} Price — {status}", ""]
    lines.append(f"<b>Время проверки:</b>\u00A0\u00A0\u00A0{esc_html(fmt_dt(checked_at))}")
    if metrics.build_time:
        lines.append(f"<b>Время сборки Price:</b>\u00A0\u00A0\u00A0{esc_html(metrics.build_time)}")
    lines.append("")
    lines.append(bold_line("Товаров в Price", metrics.total))
    lines.append(bold_line("Есть в наличии", metrics.available_true))
    lines.append(bold_line("Нет в наличии", metrics.available_false))
    lines.append("")
    lines.append(bold_line("С ценой 100", metrics.price100))
    lines.append(bold_line("С заглушкой фото", metrics.placeholder))
    lines.append(bold_line("Без categoryId", metrics.empty_category))
    lines.append(bold_line("Без категории Satu", metrics.unknown_satu))
    lines.append(bold_line("Исключено из final без categoryId", metrics.excluded_unmapped_total))

    by_supplier = metrics.excluded_unmapped_by_supplier or {}
    if any(by_supplier.values()):
        lines.append("")
        lines.append("<b>Исключено по поставщикам:</b>")
        for supplier in EXPECTED_SUPPLIERS:
            count = by_supplier.get(supplier, 0)
            if count > 0:
                lines.append(f"• {esc_html(supplier)} — {count}")

    if reason:
        lines.append("")
        lines.append(f"<b>Причина:</b>\u00A0\u00A0\u00A0{esc_html(reason)}")

    lines.append("")
    lines.append(f"<b>Привязка к категориям Satu:</b>\u00A0\u00A0\u00A0{'УСПЕШНО' if metrics.unknown_satu == 0 else 'НЕУСПЕШНО'}")
    lines.append(f"<b>Статус проверки Price:</b>\u00A0\u00A0\u00A0{esc_html(status)}")
    return "\n".join(lines)


def write_report(status: str, reason: str, metrics: Metrics, checked_at: datetime) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    by_supplier = metrics.excluded_unmapped_by_supplier or {}
    lines = [
        "Проверка Price",
        f"Время проверки | {fmt_dt(checked_at)}",
        f"Время сборки Price | {metrics.build_time}",
        f"Статус | {status}",
    ]
    if reason:
        lines.append(f"Причина | {reason}")
    lines.extend([
        f"Товаров в Price | {metrics.total}",
        f"Есть в наличии | {metrics.available_true}",
        f"Нет в наличии | {metrics.available_false}",
        f"С ценой 100 | {metrics.price100}",
        f"С заглушкой фото | {metrics.placeholder}",
        f"Без categoryId | {metrics.empty_category}",
        f"Без категории Satu | {metrics.unknown_satu}",
        f"Исключено из final без categoryId | {metrics.excluded_unmapped_total}",
    ])
    for supplier in EXPECTED_SUPPLIERS:
        lines.append(f"Исключено из final без categoryId ({supplier}) | {by_supplier.get(supplier, 0)}")
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def send_telegram(message_html: str) -> None:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message_html,
        "parse_mode": "HTML",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def main() -> int:
    checked_at = now_almaty()
    status = "НЕУСПЕШНО"
    reason = ""
    metrics = Metrics(supplier_counts={name: 0 for name in EXPECTED_SUPPLIERS}, excluded_unmapped_by_supplier={name: 0 for name in EXPECTED_SUPPLIERS})

    try:
        metrics = collect_metrics(PRICE_FILE)
        baseline = load_baseline()
        status, reason = evaluate(metrics, baseline)
    except FileNotFoundError as exc:
        reason = str(exc)
    except ValueError as exc:
        reason = str(exc)
    except Exception as exc:
        reason = f"Неожиданная ошибка checker: {exc}"

    write_report(status, reason, metrics, checked_at)
    try:
        send_telegram(render_message(status, reason, metrics, checked_at))
    except Exception:
        pass

    if status == "УСПЕШНО":
        save_baseline(metrics)
        return 0
    if status == "ТРЕБУЕТ ВНИМАНИЯ":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
