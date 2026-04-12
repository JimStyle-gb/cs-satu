#!/usr/bin/env python3
from __future__ import annotations

import json
import html
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo
from urllib import parse, request

ROOT = Path(__file__).resolve().parents[1]
PRICE_PATH = ROOT / "docs" / "Price.yml"
RAW_DIR = ROOT / "docs" / "raw"
REPORT_PATH = RAW_DIR / "price_checker_report.txt"
BASELINE_PATH = RAW_DIR / "price_checker_last_success.json"
TZ = ZoneInfo("Asia/Almaty")
PLACEHOLDER = "https://placehold.co/800x800/png?text=No+Photo"
REQUIRED_SUPPLIERS = ["AkCent", "AlStyle", "ComPortal", "CopyLine", "VTT"]
SUPPLIER_PREFIXES = {
    "AkCent": ("AC",),
    "AlStyle": ("AS",),
    "ComPortal": ("CP",),
    "CopyLine": ("CL",),
    "VTT": ("VT",),
}
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


@dataclass
class CheckResult:
    status: str
    reason: str
    metrics: Dict[str, int]
    build_time: str
    check_time: str
    supplier_counts: Dict[str, int]
    extra_note: str = ""


class CheckError(Exception):
    pass


def now_almaty() -> datetime:
    return datetime.now(TZ)


def fmt_dt(dt: datetime) -> str:
    return f"{dt.day} {MONTHS_RU[dt.month]} {dt.year} г. {dt:%H:%M:%S}"


def fmt_dt_from_iso(value: str) -> str:
    value = value.strip()
    if not value:
        return "не найдено"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=TZ)
            return fmt_dt(dt)
        except ValueError:
            continue
    return value


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_price_xml() -> ET.Element:
    if not PRICE_PATH.exists():
        raise CheckError("Файл Price.yml не найден.")
    if PRICE_PATH.stat().st_size == 0:
        raise CheckError("Файл Price.yml пустой.")
    try:
        return ET.parse(PRICE_PATH).getroot()
    except ET.ParseError as exc:
        raise CheckError("XML в Price повреждён.") from exc


def extract_build_time() -> str:
    if not PRICE_PATH.exists():
        return "не найдено"
    text = PRICE_PATH.read_text(encoding="utf-8", errors="ignore")
    marker = "Price\nВремя сборки"
    idx = text.find(marker)
    if idx != -1:
        chunk = text[idx: idx + 1200]
        for line in chunk.splitlines():
            if line.startswith("Время сборки"):
                parts = line.split("|", 1)
                if len(parts) == 2:
                    return fmt_dt_from_iso(parts[1].strip())
    try:
        root = ET.fromstring(text)
        date_value = root.attrib.get("date", "")
        return fmt_dt_from_iso(date_value)
    except Exception:
        return "не найдено"


def get_text(elem: Optional[ET.Element]) -> str:
    return (elem.text or "").strip() if elem is not None else ""


def parse_metrics(root: ET.Element) -> tuple[Dict[str, int], Dict[str, str], Dict[str, int]]:
    shop = root.find("shop")
    if shop is None:
        raise CheckError("В Price отсутствует блок shop.")
    categories = shop.find("categories")
    if categories is None:
        raise CheckError("В Price отсутствует блок categories.")
    offers = shop.find("offers")
    if offers is None:
        raise CheckError("В Price отсутствует блок offers.")

    category_portal: Dict[str, str] = {}
    for cat in categories.findall("category"):
        cid = cat.attrib.get("id", "").strip()
        if cid:
            category_portal[cid] = cat.attrib.get("portal_id", "").strip()

    metrics = {
        "total": 0,
        "available_true": 0,
        "available_false": 0,
        "price_100": 0,
        "placeholder_picture": 0,
        "missing_category_id": 0,
        "missing_satu_category": 0,
        "duplicate_offer_id": 0,
        "duplicate_vendor_code": 0,
        "invalid_category_refs": 0,
    }

    offer_ids: Counter[str] = Counter()
    vendor_codes: Counter[str] = Counter()
    supplier_counts = {name: 0 for name in REQUIRED_SUPPLIERS}

    for offer in offers.findall("offer"):
        metrics["total"] += 1
        if offer.attrib.get("available", "").strip().lower() == "true":
            metrics["available_true"] += 1
        else:
            metrics["available_false"] += 1

        offer_id = offer.attrib.get("id", "").strip()
        if offer_id:
            offer_ids[offer_id] += 1
        vendor_code = get_text(offer.find("vendorCode"))
        if vendor_code:
            vendor_codes[vendor_code] += 1

        category_id = get_text(offer.find("categoryId"))
        if not category_id:
            metrics["missing_category_id"] += 1
        elif category_id not in category_portal:
            metrics["invalid_category_refs"] += 1

        price_text = get_text(offer.find("price"))
        if price_text == "100":
            metrics["price_100"] += 1

        pictures = [get_text(p) for p in offer.findall("picture") if get_text(p)]
        if any(p == PLACEHOLDER for p in pictures):
            metrics["placeholder_picture"] += 1

        portal_override = get_text(offer.find("portal_category_id"))
        category_portal_id = category_portal.get(category_id, "") if category_id else ""
        if not portal_override and not category_portal_id:
            metrics["missing_satu_category"] += 1

        pref = (vendor_code or offer_id).upper()
        for supplier, prefixes in SUPPLIER_PREFIXES.items():
            if pref.startswith(prefixes):
                supplier_counts[supplier] += 1
                break

    metrics["duplicate_offer_id"] = sum(1 for _, count in offer_ids.items() if count > 1)
    metrics["duplicate_vendor_code"] = sum(1 for _, count in vendor_codes.items() if count > 1)

    return metrics, category_portal, supplier_counts


def ensure_required_suppliers(supplier_counts: Dict[str, int]) -> None:
    missing = [name for name, count in supplier_counts.items() if count == 0]
    if missing:
        raise CheckError("В Price отсутствует один или несколько поставщиков.")


def load_baseline() -> Optional[dict]:
    if not BASELINE_PATH.exists():
        return None
    try:
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        required = {"metrics", "supplier_counts", "status", "build_time", "check_time"}
        if not required.issubset(data.keys()):
            return None
        return data
    except Exception:
        return None


def save_baseline(metrics: Dict[str, int], supplier_counts: Dict[str, int], build_time: str, check_time: str) -> None:
    data = {
        "status": "УСПЕШНО",
        "metrics": metrics,
        "supplier_counts": supplier_counts,
        "build_time": build_time,
        "check_time": check_time,
    }
    write_text(BASELINE_PATH, json.dumps(data, ensure_ascii=False, indent=2))


def pct_change(old: int, new: int) -> float:
    if old == 0:
        return 0.0 if new == 0 else 100.0
    return ((new - old) / old) * 100.0


def evaluate_warning(metrics: Dict[str, int], supplier_counts: Dict[str, int], baseline: Optional[dict]) -> tuple[str, str, str]:
    if not baseline:
        return "УСПЕШНО", "", ""

    base_metrics = baseline.get("metrics", {})
    base_suppliers = baseline.get("supplier_counts", {})

    total_old = int(base_metrics.get("total", 0))
    total_new = metrics["total"]
    total_delta = pct_change(total_old, total_new)
    if total_delta < -15:
        return "НЕУСПЕШНО", "Общее количество товаров просело больше допустимого порога.", ""
    if abs(total_delta) > 5:
        return "ТРЕБУЕТ ВНИМАНИЯ", "Общее количество товаров изменилось больше допустимого порога.", ""

    for supplier in REQUIRED_SUPPLIERS:
        old = int(base_suppliers.get(supplier, 0))
        new = int(supplier_counts.get(supplier, 0))
        delta = pct_change(old, new)
        if delta < -25:
            return "НЕУСПЕШНО", f"Количество товаров у поставщика {supplier} просело больше допустимого порога.", ""
        if abs(delta) > 10:
            return "ТРЕБУЕТ ВНИМАНИЯ", f"Количество товаров у поставщика {supplier} изменилось больше допустимого порога.", ""

    for key, reason in (
        ("price_100", "Количество товаров с ценой 100 выросло больше допустимого порога."),
        ("placeholder_picture", "Количество товаров с заглушкой фото выросло больше допустимого порога."),
    ):
        old = int(base_metrics.get(key, 0))
        new = metrics[key]
        if new > old and ((new - old) >= 50 or pct_change(old, new) > 10):
            return "ТРЕБУЕТ ВНИМАНИЯ", reason, ""

    old_false = int(base_metrics.get("available_false", 0))
    new_false = metrics["available_false"]
    if new_false > old_false and pct_change(old_false, new_false) > 20:
        return "ТРЕБУЕТ ВНИМАНИЯ", "Количество товаров Нет в наличии выросло больше допустимого порога.", ""

    return "УСПЕШНО", "", ""


def build_result() -> CheckResult:
    check_time = fmt_dt(now_almaty())
    build_time = extract_build_time()

    root = read_price_xml()
    metrics, _category_portal, supplier_counts = parse_metrics(root)
    ensure_required_suppliers(supplier_counts)

    if metrics["missing_category_id"] > 0:
        raise CheckError("В Price есть товары без categoryId.")
    if metrics["missing_satu_category"] > 0:
        raise CheckError("В Price есть товары без категории Satu.")
    if metrics["duplicate_offer_id"] > 0:
        raise CheckError("В Price обнаружены дубли offer id.")
    if metrics["duplicate_vendor_code"] > 0:
        raise CheckError("В Price обнаружены дубли vendorCode.")
    if metrics["invalid_category_refs"] > 0:
        raise CheckError("В Price есть товары с categoryId, которых нет в блоке categories.")

    baseline = load_baseline()
    status, reason, extra = evaluate_warning(metrics, supplier_counts, baseline)

    if status == "УСПЕШНО":
        save_baseline(metrics, supplier_counts, build_time, check_time)

    return CheckResult(
        status=status,
        reason=reason,
        metrics=metrics,
        build_time=build_time,
        check_time=check_time,
        supplier_counts=supplier_counts,
        extra_note=extra,
    )


def bold_line(label: str, value: str) -> str:
    return f"• <b>{html.escape(label)}:</b>&nbsp;&nbsp;&nbsp;{html.escape(value)}"


def plain_line(label: str, value: str) -> str:
    return f"{label:<42} | {value}"


def render_message(result: CheckResult) -> str:
    title_emoji = {
        "УСПЕШНО": "✅",
        "ТРЕБУЕТ ВНИМАНИЯ": "⚠️",
        "НЕУСПЕШНО": "❌",
    }[result.status]

    lines: List[str] = [f"{title_emoji} <b>Price — {html.escape(result.status)}</b>"]
    lines.append("")
    lines.append(f"<b>Время проверки:</b>&nbsp;&nbsp;&nbsp;{html.escape(result.check_time)}")
    if result.build_time and result.build_time != "не найдено":
        lines.append(f"<b>Время сборки Price:</b>&nbsp;&nbsp;&nbsp;{html.escape(result.build_time)}")
    lines.append("")

    if result.status != "НЕУСПЕШНО":
        lines.extend([
            bold_line("Товаров в Price", str(result.metrics["total"])),
            bold_line("Есть в наличии", str(result.metrics["available_true"])),
            bold_line("Нет в наличии", str(result.metrics["available_false"])),
            "",
            bold_line("С ценой 100", str(result.metrics["price_100"])),
            bold_line("С заглушкой фото", str(result.metrics["placeholder_picture"])),
            bold_line("Без categoryId", str(result.metrics["missing_category_id"])),
            bold_line("Без категории Satu", str(result.metrics["missing_satu_category"])),
            "",
        ])

    if result.reason:
        lines.append(f"<b>Причина:</b>&nbsp;&nbsp;&nbsp;{html.escape(result.reason)}")
        if result.extra_note:
            lines.append(html.escape(result.extra_note))
        lines.append("")

    satu_status = "НЕУСПЕШНО" if result.metrics.get("missing_satu_category", 0) > 0 else "УСПЕШНО"
    lines.append(f"<b>Привязка к категориям Satu:</b>&nbsp;&nbsp;&nbsp;{html.escape(satu_status)}")
    lines.append(f"<b>Статус проверки Price:</b>&nbsp;&nbsp;&nbsp;{html.escape(result.status)}")
    return "\n".join(lines)


def render_report(result: CheckResult) -> str:
    lines = [
        "Итог проверки Price",
        plain_line("Время проверки", result.check_time),
        plain_line("Время сборки Price", result.build_time),
        plain_line("Статус проверки Price", result.status),
        plain_line("Привязка к категориям Satu", "НЕУСПЕШНО" if result.metrics.get("missing_satu_category", 0) > 0 else "УСПЕШНО"),
        "",
        plain_line("Товаров в Price", str(result.metrics["total"])),
        plain_line("Есть в наличии", str(result.metrics["available_true"])),
        plain_line("Нет в наличии", str(result.metrics["available_false"])),
        plain_line("С ценой 100", str(result.metrics["price_100"])),
        plain_line("С заглушкой фото", str(result.metrics["placeholder_picture"])),
        plain_line("Без categoryId", str(result.metrics["missing_category_id"])),
        plain_line("Без категории Satu", str(result.metrics["missing_satu_category"])),
        plain_line("Дубли offer id", str(result.metrics["duplicate_offer_id"])),
        plain_line("Дубли vendorCode", str(result.metrics["duplicate_vendor_code"])),
        plain_line("Некорректные categoryId", str(result.metrics["invalid_category_refs"])),
        "",
        plain_line("AkCent", str(result.supplier_counts["AkCent"])),
        plain_line("AlStyle", str(result.supplier_counts["AlStyle"])),
        plain_line("ComPortal", str(result.supplier_counts["ComPortal"])),
        plain_line("CopyLine", str(result.supplier_counts["CopyLine"])),
        plain_line("VTT", str(result.supplier_counts["VTT"])),
    ]
    if result.reason:
        lines.extend(["", plain_line("Причина", result.reason)])
    return "\n".join(lines) + "\n"


def send_telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    data = parse.urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with request.urlopen(req, timeout=30) as resp:
        if resp.status >= 400:
            raise RuntimeError("Не удалось отправить сообщение в Telegram")


def main() -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    try:
        result = build_result()
    except CheckError as exc:
        failed = CheckResult(
            status="НЕУСПЕШНО",
            reason=str(exc),
            metrics={
                "total": 0,
                "available_true": 0,
                "available_false": 0,
                "price_100": 0,
                "placeholder_picture": 0,
                "missing_category_id": 0,
                "missing_satu_category": 0,
                "duplicate_offer_id": 0,
                "duplicate_vendor_code": 0,
                "invalid_category_refs": 0,
            },
            build_time="не найдено",
            check_time=fmt_dt(now_almaty()),
            supplier_counts={name: 0 for name in REQUIRED_SUPPLIERS},
        )
        write_text(REPORT_PATH, render_report(failed))
        send_telegram(render_message(failed))
        return 1
    except Exception as exc:
        failed = CheckResult(
            status="НЕУСПЕШНО",
            reason="Checker завершился с ошибкой на уровне скрипта.",
            metrics={
                "total": 0,
                "available_true": 0,
                "available_false": 0,
                "price_100": 0,
                "placeholder_picture": 0,
                "missing_category_id": 0,
                "missing_satu_category": 0,
                "duplicate_offer_id": 0,
                "duplicate_vendor_code": 0,
                "invalid_category_refs": 0,
            },
            build_time="не найдено",
            check_time=fmt_dt(now_almaty()),
            supplier_counts={name: 0 for name in REQUIRED_SUPPLIERS},
            extra_note=str(exc),
        )
        write_text(REPORT_PATH, render_report(failed))
        send_telegram(render_message(failed))
        return 1

    write_text(REPORT_PATH, render_report(result))
    send_telegram(render_message(result))
    return 1 if result.status == "НЕУСПЕШНО" else 0


if __name__ == "__main__":
    raise SystemExit(main())
