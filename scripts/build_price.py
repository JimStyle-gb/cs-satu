# -*- coding: utf-8 -*-
"""
Path: scripts/build_price.py

Собирает итоговый docs/Price.yml из final-файлов поставщиков:
AkCent, AlStyle, ComPortal, CopyLine, VTT.

Логика:
- берет только docs/*.yml final-файлы;
- raw-файлы не использует;
- копирует FEED_META поставщиков как есть;
- вставляет общий блок Price в конец FEED_META;
- строит только новое CS-дерево категорий;
- добавляет portal_id на уровне leaf-категорий;
- при необходимости добавляет portal_category_id в offer по override-правилам;
- пишет отчеты:
  docs/raw/price_satu_unmapped_offers.txt
  docs/raw/price_satu_portal_audit.txt
- валидирует структуру и падает при критичных ошибках.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import yaml
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"Не установлен PyYAML: {exc}")


# --- Пути ---------------------------------------------------------------------
BASE_DIR = Path(".")
DOCS_DIR = BASE_DIR / "docs"
RAW_DOCS_DIR = DOCS_DIR / "raw"
CONFIG_DIR = BASE_DIR / "scripts" / "cs" / "config"

OUTPUT_FILE = DOCS_DIR / "Price.yml"
UNMAPPED_FILE = RAW_DOCS_DIR / "price_satu_unmapped_offers.txt"
AUDIT_FILE = RAW_DOCS_DIR / "price_satu_portal_audit.txt"

SATU_PORTAL_CATEGORIES_FILE = CONFIG_DIR / "satu_portal_categories.yml"
PRICE_PORTAL_MAP_FILE = CONFIG_DIR / "price_portal_map.yml"
PRICE_PORTAL_OVERRIDES_FILE = CONFIG_DIR / "price_portal_overrides.yml"

PLACEHOLDER_PICTURE = "https://placehold.co/800x800/png?text=No+Photo"
TZ = ZoneInfo("Asia/Almaty")


# --- Источники final ----------------------------------------------------------
FINAL_SOURCES = [
    ("AkCent", DOCS_DIR / "akcent.yml"),
    ("AlStyle", DOCS_DIR / "alstyle.yml"),
    ("ComPortal", DOCS_DIR / "comportal.yml"),
    ("CopyLine", DOCS_DIR / "copyline.yml"),
    ("VTT", DOCS_DIR / "vtt.yml"),
]
EXPECTED_SUPPLIERS = [name for name, _ in FINAL_SOURCES]


# --- Каноническое CS-дерево ---------------------------------------------------
TOP_GROUPS: list[tuple[str, str]] = [
    ("100", "Расходные материалы для принтеров и МФУ"),
    ("200", "Запчасти и комплектующие для принтеров и МФУ"),
    ("300", "Принтеры, МФУ, плоттеры и сканеры"),
    ("400", "Проекторы, экраны и интерактивное оборудование"),
    ("500", "Оборудование для документов"),
    ("600", "Компьютерная техника и мониторы"),
    ("700", "ИБП, стабилизаторы и аккумуляторы"),
    ("800", "Кабели и аксессуары подключения"),
]

LEAF_GROUPS: list[tuple[str, str, str]] = [
    ("101", "100", "Картриджи лазерные и тонер-картриджи"),
    ("102", "100", "Картриджи струйные и чернильные ёмкости"),
    ("103", "100", "Чернила"),
    ("104", "100", "Тонеры и девелоперы"),
    ("105", "100", "Драм-юниты и фотобарабаны"),
    ("106", "100", "Печатающие головки"),
    ("107", "100", "Контейнеры для отработки"),
    ("108", "100", "Прочие расходные материалы"),
    ("201", "200", "Термоблоки и фьюзеры"),
    ("202", "200", "Блоки проявки"),
    ("203", "200", "Узлы и ремни переноса"),
    ("204", "200", "Ролики подачи и ремкомплекты"),
    ("205", "200", "Финишеры, лотки и подставки"),
    ("206", "200", "Сервисные комплекты и наборы"),
    ("207", "200", "Прочие запчасти и комплектующие"),
    ("301", "300", "МФУ"),
    ("302", "300", "Принтеры"),
    ("303", "300", "Плоттеры и широкоформатные принтеры"),
    ("304", "300", "Сканеры"),
    ("401", "400", "Проекторы"),
    ("402", "400", "Экраны для проекторов"),
    ("403", "400", "Интерактивные панели"),
    ("404", "400", "Интерактивные доски"),
    ("405", "400", "Аксессуары для интерактивного оборудования"),
    ("501", "500", "Ламинаторы"),
    ("502", "500", "Плёнка для ламинирования"),
    ("503", "500", "Переплетчики"),
    ("504", "500", "Шредеры"),
    ("601", "600", "Ноутбуки"),
    ("602", "600", "Мониторы"),
    ("603", "600", "Моноблоки"),
    ("604", "600", "Настольные ПК и barebone"),
    ("605", "600", "Рабочие станции"),
    ("606", "600", "Комплектующие ПК"),
    ("701", "700", "Источники бесперебойного питания"),
    ("702", "700", "Стабилизаторы напряжения"),
    ("703", "700", "Аккумуляторы и батарейные блоки"),
    ("704", "700", "Аксессуары и модули для ИБП"),
    ("801", "800", "Кабели"),
    ("802", "800", "Аксессуары подключения"),
]
VALID_CATEGORY_IDS = {cid for cid, _ in TOP_GROUPS} | {cid for cid, _, _ in LEAF_GROUPS}
LEAF_CATEGORY_IDS = {cid for cid, _, _ in LEAF_GROUPS}


@dataclass
class OfferInfo:
    supplier: str
    offer_id: str
    vendor_code: str
    category_id: str
    name: str
    available: bool
    price: str
    pictures: list[str]
    block: str
    portal_category_id: str | None


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Не найден файл: {path.as_posix()}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Ожидался YAML-словарь: {path.as_posix()}")
    return data


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _find_current_satu_file() -> str:
    raw_dir = BASE_DIR / "data" / "portal" / "satu" / "raw"
    if not raw_dir.exists():
        return "актуальный файл из data/portal/satu/raw/"

    files = sorted(
        [p for p in raw_dir.iterdir() if p.is_file() and p.suffix.lower() in {".xls", ".xlsx"}],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return "актуальный файл из data/portal/satu/raw/"
    return files[0].name


def _offer_blocks(text: str) -> list[str]:
    return re.findall(r"(<offer\b.*?</offer>)", text, flags=re.S)


def _extract_tag(block: str, tag: str) -> str:
    m = re.search(fr"<{tag}>(.*?)</{tag}>", block, flags=re.S)
    return m.group(1) if m else ""


def _extract_all_tags(block: str, tag: str) -> list[str]:
    return re.findall(fr"<{tag}>(.*?)</{tag}>", block, flags=re.S)


def _extract_offer_id(block: str) -> str:
    m = re.search(r'<offer\b[^>]*id="([^"]+)"', block)
    return m.group(1) if m else ""


def _extract_available(block: str) -> bool:
    m = re.search(r'<offer\b[^>]*available="([^"]+)"', block)
    return (m.group(1).strip().lower() == "true") if m else False


def _extract_feed_meta_body(text: str, source_name: str) -> str:
    m = re.search(r"<!--FEED_META\n(.*?)\n-->", text, flags=re.S)
    if not m:
        raise ValueError(f"[{source_name}] Не найден FEED_META")
    return m.group(1).rstrip()


def _extract_supplier_summary(meta_body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in meta_body.splitlines():
        if "|" not in line:
            continue
        key, value = line.split("|", 1)
        out[key.strip()] = value.strip()
    return out


def _build_portal_registry() -> dict[str, dict[str, Any]]:
    data = _read_yaml(SATU_PORTAL_CATEGORIES_FILE)
    categories = data.get("categories", [])
    registry: dict[str, dict[str, Any]] = {}
    for item in categories:
        if not isinstance(item, dict):
            continue
        portal_id = str(item.get("portal_id", "")).strip()
        if portal_id:
            registry[portal_id] = item
    return registry


def _build_default_map(portal_registry: dict[str, dict[str, Any]]) -> dict[str, str]:
    data = _read_yaml(PRICE_PORTAL_MAP_FILE)
    mappings = data.get("mappings", [])
    out: dict[str, str] = {}
    for item in mappings:
        if not isinstance(item, dict):
            continue
        cs_category_id = str(item.get("cs_category_id", "")).strip()
        portal_id = str(item.get("default_portal_id", "")).strip()
        if not cs_category_id or not portal_id:
            continue
        if portal_id not in portal_registry:
            raise ValueError(
                f"В price_portal_map.yml указан portal_id={portal_id}, которого нет в satu_portal_categories.yml"
            )
        out[cs_category_id] = portal_id
    return out


def _build_overrides(portal_registry: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    data = _read_yaml(PRICE_PORTAL_OVERRIDES_FILE)
    overrides = data.get("overrides", [])
    result: list[dict[str, Any]] = []
    for item in overrides:
        if not isinstance(item, dict):
            continue
        portal_id = str(item.get("portal_category_id", "")).strip()
        if portal_id and portal_id not in portal_registry:
            raise ValueError(
                f"В price_portal_overrides.yml указан portal_category_id={portal_id}, которого нет в satu_portal_categories.yml"
            )
        result.append(item)
    result.sort(key=lambda x: int(x.get("priority", 999999)))
    return result


def _parse_params(block: str) -> list[tuple[str, str]]:
    return [(name.strip(), value.strip()) for name, value in re.findall(r'<param name="([^"]+)">(.*?)</param>', block, flags=re.S)]


def _override_for_offer(
    *,
    supplier: str,
    category_id: str,
    name: str,
    vendor: str,
    params: list[tuple[str, str]],
    overrides: list[dict[str, Any]],
) -> str | None:
    name_n = _norm(name)
    vendor_n = _norm(vendor)
    params_norm = [(_norm(pname), _norm(pvalue)) for pname, pvalue in params]

    for rule in overrides:
        rule_cat = str(rule.get("cs_category_id", "")).strip()
        if rule_cat and rule_cat != category_id:
            continue

        rule_supplier = str(rule.get("supplier", "")).strip()
        if rule_supplier and rule_supplier != supplier:
            continue

        matched = False

        for needle in rule.get("name_contains", []) or []:
            if _norm(str(needle)) and _norm(str(needle)) in name_n:
                matched = True
                break

        if not matched:
            for needle in rule.get("vendor_contains", []) or []:
                if _norm(str(needle)) and _norm(str(needle)) in vendor_n:
                    matched = True
                    break

        if not matched:
            for cond in rule.get("params", []) or []:
                if not isinstance(cond, dict):
                    continue
                target_name = _norm(str(cond.get("name", "")))
                contains = _norm(str(cond.get("contains", "")))
                if not target_name or not contains:
                    continue
                for pname, pvalue in params_norm:
                    if pname == target_name and contains in pvalue:
                        matched = True
                        break
                if matched:
                    break

        if matched:
            portal_id = str(rule.get("portal_category_id", "")).strip()
            return portal_id or None

    return None


def _inject_portal_category_id(block: str, portal_category_id: str) -> str:
    if not portal_category_id:
        return block
    if "<portal_category_id>" in block:
        return re.sub(
            r"<portal_category_id>.*?</portal_category_id>",
            f"<portal_category_id>{portal_category_id}</portal_category_id>",
            block,
            count=1,
            flags=re.S,
        )
    return re.sub(
        r"(\s*<categoryId>.*?</categoryId>)",
        rf"\1\n<portal_category_id>{portal_category_id}</portal_category_id>",
        block,
        count=1,
        flags=re.S,
    )


def _build_categories_block(default_map: dict[str, str]) -> str:
    lines: list[str] = ["<categories>"]
    for cid, name in TOP_GROUPS:
        lines.append(f'<category id="{cid}">{name}</category>')
    for cid, parent_id, name in LEAF_GROUPS:
        portal_id = default_map.get(cid, "")
        portal_attr = f' portal_id="{portal_id}"' if portal_id else ""
        lines.append(f'<category id="{cid}" parentId="{parent_id}"{portal_attr}>{name}</category>')
    lines.append("</categories>")
    return "\n".join(lines)


def _format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _next_price_run(now: datetime) -> str:
    next_run = now.replace(hour=7, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return _format_dt(next_run)


def _count_true_false(offers: list[OfferInfo]) -> tuple[int, int]:
    true_count = sum(1 for item in offers if item.available)
    false_count = len(offers) - true_count
    return true_count, false_count


def _price_summary_block(*, now: datetime, offers: list[OfferInfo], default_map: dict[str, str], mapped_subcategories_count: int, current_satu_source: str) -> str:
    total = len(offers)
    available_true, available_false = _count_true_false(offers)
    price100 = sum(1 for item in offers if item.price.strip() == "100")
    placeholder = sum(1 for item in offers if any(pic.strip() == PLACEHOLDER_PICTURE for pic in item.pictures))
    missing_category = sum(1 for item in offers if not item.category_id.strip())
    missing_satu = sum(1 for item in offers if not item.portal_category_id)

    offer_ids = [item.offer_id for item in offers]
    vendor_codes = [item.vendor_code for item in offers]
    dup_offer = sum(count - 1 for count in Counter(offer_ids).values() if count > 1)
    dup_vendor = sum(count - 1 for count in Counter(vendor_codes).values() if count > 1)

    lines = [
        ("Price", ""),
        ("Время сборки (Алматы)", _format_dt(now)),
        ("Ближайшая сборка (Алматы)", _next_price_run(now)),
        ("Расписание (Алматы)", "ежедневно в 07:00"),
        ("Сколько поставщиков в Price", str(len(EXPECTED_SUPPLIERS))),
        ("Порядок поставщиков", ", ".join(EXPECTED_SUPPLIERS)),
        ("Сколько товаров в Price всего", str(total)),
        ("Сколько товаров есть в наличии (true)", str(available_true)),
        ("Сколько товаров нет в наличии (false)", str(available_false)),
        ("Сколько товаров с ценой 100", str(price100)),
        ("Сколько товаров с заглушкой фото", str(placeholder)),
        ("Сколько товаров без categoryId", str(missing_category)),
        ("Сколько дублей offer id", str(dup_offer)),
        ("Сколько дублей vendorCode", str(dup_vendor)),
        ("Источник категорий Satu", current_satu_source),
        ("Сколько категорий всего", str(len(TOP_GROUPS) + len(LEAF_GROUPS))),
        ("Сколько рабочих подкатегорий", str(len(LEAF_GROUPS))),
        ("Сколько подкатегорий привязано к Satu", str(mapped_subcategories_count)),
        ("Сколько товаров без категории Satu", str(missing_satu)),
        ("Проверка привязки к категориям Satu", "УСПЕШНО" if missing_satu == 0 else "НЕУСПЕШНО"),
        ("Статус проверки Price", "УСПЕШНО" if all([missing_category == 0, dup_offer == 0, dup_vendor == 0, missing_satu == 0]) else "НЕУСПЕШНО"),
    ]

    width = max(len(key) for key, _ in lines)
    rendered: list[str] = []
    for key, value in lines:
        if value:
            rendered.append(f"{key.ljust(width)} | {value}")
        else:
            rendered.append(key)
    return "\n".join(rendered)


def _write_unmapped_report(unmapped_rows: list[dict[str, str]]) -> None:
    RAW_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    if not unmapped_rows:
        UNMAPPED_FILE.write_text(
            "Товары без категории Satu\n\nНет товаров без категории Satu.\n",
            encoding="utf-8",
        )
        return

    lines = ["Товары без категории Satu", ""]
    for row in unmapped_rows:
        lines.append(
            f"{row['offer_id']} | {row['supplier']} | {row['category_id']} | {row['category_name']} | {row['name']} | {row['reason']}"
        )
    UNMAPPED_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_audit_report(*, now: datetime, offers: list[OfferInfo], default_count: int, override_count: int, unmapped_count: int, mapped_subcategories_count: int, current_satu_source: str) -> None:
    RAW_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    status = "УСПЕШНО" if unmapped_count == 0 else "НЕУСПЕШНО"
    lines = [
        "Итог привязки Price к категориям Satu",
        f"Время сборки (Алматы)                      | {_format_dt(now)}",
        f"Источник категорий Satu                    | {current_satu_source}",
        f"Сколько категорий Satu в реестре           | {len(_build_portal_registry())}",
        f"Сколько категорий всего                    | {len(TOP_GROUPS) + len(LEAF_GROUPS)}",
        f"Сколько рабочих подкатегорий               | {len(LEAF_GROUPS)}",
        f"Сколько подкатегорий привязано к Satu      | {mapped_subcategories_count}",
        f"Сколько товаров получили категорию Satu по умолчанию | {default_count}",
        f"Сколько товаров получили отдельную категорию Satu   | {override_count}",
        f"Сколько товаров без категории Satu         | {unmapped_count}",
        f"Статус привязки к категориям Satu          | {status}",
    ]
    AUDIT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _category_name(category_id: str) -> str:
    for cid, _, name in LEAF_GROUPS:
        if cid == category_id:
            return name
    for cid, name in TOP_GROUPS:
        if cid == category_id:
            return name
    return "Неизвестная категория"


def build_price() -> int:
    portal_registry = _build_portal_registry()
    default_map = _build_default_map(portal_registry)
    overrides = _build_overrides(portal_registry)

    missing_leaf_mappings = sorted(LEAF_CATEGORY_IDS - set(default_map.keys()))
    if missing_leaf_mappings:
        missing_names = ", ".join(f"{cid}:{_category_name(cid)}" for cid in missing_leaf_mappings)
        raise ValueError(f"Не всем рабочим подкатегориям назначен portal_id: {missing_names}")

    supplier_meta_blocks: list[str] = []
    supplier_meta_stats: dict[str, dict[str, str]] = {}
    all_offers: list[OfferInfo] = []
    missing_files: list[str] = []

    for supplier_name, source_path in FINAL_SOURCES:
        if not source_path.exists():
            missing_files.append(source_path.as_posix())
            continue

        text = source_path.read_text(encoding="utf-8")
        supplier_meta_body = _extract_feed_meta_body(text, supplier_name)
        supplier_meta_blocks.append(supplier_meta_body)
        supplier_meta_stats[supplier_name] = _extract_supplier_summary(supplier_meta_body)

        blocks = _offer_blocks(text)
        if not blocks:
            raise ValueError(f"[{supplier_name}] В final-файле нет ни одного offer")

        for block in blocks:
            offer_id = _extract_offer_id(block).strip()
            vendor_code = _extract_tag(block, "vendorCode").strip()
            category_id = _extract_tag(block, "categoryId").strip()
            name = _extract_tag(block, "name").strip()
            vendor = _extract_tag(block, "vendor").strip()
            price = _extract_tag(block, "price").strip()
            pictures = [pic.strip() for pic in _extract_all_tags(block, "picture")]
            params = _parse_params(block)

            if not offer_id:
                raise ValueError(f"[{supplier_name}] Найден offer без id")
            if not vendor_code:
                raise ValueError(f"[{supplier_name}] [{offer_id}] Пустой vendorCode")
            if not category_id:
                raise ValueError(f"[{supplier_name}] [{offer_id}] Пустой categoryId")
            if category_id not in VALID_CATEGORY_IDS:
                raise ValueError(f"[{supplier_name}] [{offer_id}] Неизвестный categoryId={category_id}")

            default_portal_id = default_map.get(category_id)
            override_portal_id = _override_for_offer(
                supplier=supplier_name,
                category_id=category_id,
                name=name,
                vendor=vendor,
                params=params,
                overrides=overrides,
            )
            effective_portal_id = override_portal_id or default_portal_id
            final_block = block
            explicit_portal_category_id: str | None = None

            if override_portal_id and override_portal_id != default_portal_id:
                final_block = _inject_portal_category_id(block, override_portal_id)
                explicit_portal_category_id = override_portal_id
            elif "<portal_category_id>" in block:
                # На всякий случай чистим чужие старые portal_category_id.
                final_block = re.sub(r"\n?<portal_category_id>.*?</portal_category_id>", "", block, count=1, flags=re.S)

            all_offers.append(
                OfferInfo(
                    supplier=supplier_name,
                    offer_id=offer_id,
                    vendor_code=vendor_code,
                    category_id=category_id,
                    name=name,
                    available=_extract_available(block),
                    price=price,
                    pictures=pictures,
                    block=final_block,
                    portal_category_id=effective_portal_id if effective_portal_id else None,
                )
            )

    if missing_files:
        raise FileNotFoundError("Не найдены final-файлы: " + ", ".join(missing_files))

    if sorted(supplier_meta_stats.keys()) != sorted(EXPECTED_SUPPLIERS):
        raise ValueError("В Price отсутствует один или несколько поставщиков")

    offer_ids = [item.offer_id for item in all_offers]
    vendor_codes = [item.vendor_code for item in all_offers]
    dup_offer = sum(count - 1 for count in Counter(offer_ids).values() if count > 1)
    dup_vendor = sum(count - 1 for count in Counter(vendor_codes).values() if count > 1)
    if dup_offer:
        raise ValueError(f"Обнаружены дубли offer id: {dup_offer}")
    if dup_vendor:
        raise ValueError(f"Обнаружены дубли vendorCode: {dup_vendor}")

    unmapped_rows: list[dict[str, str]] = []
    default_count = 0
    override_count = 0
    for item in all_offers:
        if not item.portal_category_id:
            unmapped_rows.append(
                {
                    "offer_id": item.offer_id,
                    "supplier": item.supplier,
                    "category_id": item.category_id,
                    "category_name": _category_name(item.category_id),
                    "name": item.name,
                    "reason": "не найдено соответствие категории Satu",
                }
            )
        elif "<portal_category_id>" in item.block:
            override_count += 1
        else:
            default_count += 1

    now = datetime.now(TZ)
    current_satu_source = _find_current_satu_file()
    mapped_subcategories_count = len(default_map)

    _write_unmapped_report(unmapped_rows)
    _write_audit_report(
        now=now,
        offers=all_offers,
        default_count=default_count,
        override_count=override_count,
        unmapped_count=len(unmapped_rows),
        mapped_subcategories_count=mapped_subcategories_count,
        current_satu_source=current_satu_source,
    )

    if unmapped_rows:
        raise ValueError(f"Есть товары без категории Satu: {len(unmapped_rows)}")

    price_meta = _price_summary_block(
        now=now,
        offers=all_offers,
        default_map=default_map,
        mapped_subcategories_count=mapped_subcategories_count,
        current_satu_source=current_satu_source,
    )
    full_meta = "<!--FEED_META\n" + "\n\n\n".join(supplier_meta_blocks + [price_meta]) + "\n-->"

    categories_block = _build_categories_block(default_map)
    offers_block = "\n\n".join(item.block for item in all_offers)

    output = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE yml_catalog SYSTEM "shops.dtd">\n'
        f'<yml_catalog date="{now.strftime("%Y-%m-%d %H:%M")}">\n'
        '  <shop>\n'
        '    <name>Complex Solutions Ltd</name>\n'
        '    <company>Complex Solutions Ltd</company>\n'
        '    <url>https://complex-solutions.kz/</url>\n'
        '    <currencies>\n'
        '      <currency id="KZT" rate="1"/>\n'
        '    </currencies>\n\n'
        f'    {full_meta}\n\n'
        + "\n".join(f"    {line}" for line in categories_block.splitlines())
        + "\n\n"
        + '    <offers>\n'
        + "\n\n".join("      " + block.replace("\n", "\n      ") for block in offers_block.split("\n\n"))
        + '\n    </offers>\n'
        '  </shop>\n'
        '</yml_catalog>\n'
    )

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(output, encoding="utf-8")

    print("=" * 72)
    print("[PRICE] build summary")
    print("=" * 72)
    print(f"out_file: {OUTPUT_FILE.as_posix()}")
    print(f"offers_total: {len(all_offers)}")
    print(f"suppliers_total: {len(EXPECTED_SUPPLIERS)}")
    print(f"mapped_default: {default_count}")
    print(f"mapped_override: {override_count}")
    print(f"unmapped_total: {len(unmapped_rows)}")
    print(f"report_unmapped: {UNMAPPED_FILE.as_posix()}")
    print(f"report_audit: {AUDIT_FILE.as_posix()}")
    print("=" * 72)
    return 0


def main() -> int:
    try:
        return build_price()
    except Exception as exc:
        print(f"[PRICE] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
