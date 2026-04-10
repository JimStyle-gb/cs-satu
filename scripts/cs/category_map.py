# -*- coding: utf-8 -*-
"""
Path: scripts/cs/category_map.py

CS Category Map — shared назначение Satu group id в <categoryId>.

Что делает:
- держит единую межпоставщицкую Satu-таксономию проекта;
- по name/vendor/params/native_desc назначает номер группы в <categoryId>;
- использует точные правила, мягкие fallback'и и логирование неразобранных товаров.

Что не делает:
- не подменяет supplier-layer и не чинит raw offers;
- не хранит supplier-specific regex/fixes внутри core.py;
- не размазывает одну и ту же таксономию по адаптерам.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Sequence

# -----------------------------
# Номера новых групп Satu
# -----------------------------

GROUP_IDS: dict[str, str] = {
    # 1. Расходные материалы для принтеров и МФУ
    "laser_cartridges_toners": "9631597",
    "ink_cartridges_tanks": "9631598",
    "inks": "9631599",
    "toners_developers": "9631600",
    "drums_photodrums": "9631601",
    "printheads": "9631602",
    "waste_containers": "9631603",
    "other_consumables": "9631604",

    # 2. Запчасти и комплектующие для принтеров и МФУ
    "fusers": "9631606",
    "developer_units": "9631607",
    "transfer_units": "9631608",
    "rollers_kits": "9631609",
    "finishers_trays_stands": "9631610",
    "service_kits": "9631611",
    "other_parts": "9631613",

    # 3. Принтеры, МФУ, плоттеры и сканеры
    "mfp": "9631615",
    "printers": "9631616",
    "plotters": "9631617",
    "scanners": "9631618",

    # 4. Проекторы, экраны и интерактивное оборудование
    "projectors": "9631620",
    "projector_screens": "9631621",
    "interactive_panels": "9631622",
    "interactive_boards": "9631623",
    "interactive_accessories": "9631624",

    # 5. Оборудование для документов
    "laminators": "9631626",
    "laminating_film": "9631630",
    "binders": "9631631",
    "shredders": "9631632",

    # 6. Компьютерная техника и мониторы
    "laptops": "9631634",
    "monitors": "9631635",
    "monoblocks": "9631637",
    "desktops_barebone": "9631638",
    "workstations": "9631639",
    "pc_components": "9631640",

    # 7. ИБП, стабилизаторы и аккумуляторы
    "ups": "9631642",
    "stabilizers": "9631643",
    "batteries": "9631644",
    "ups_accessories": "9631645",

    # 8. Кабели и аксессуары подключения
    "cables": "9631647",
    "connection_accessories": "9631648",
}

UNRESOLVED_REPORT_DEFAULT = "docs/raw/category_map_unresolved.txt"
_ENABLE_UNRESOLVED_REPORT = (os.getenv("CS_CATEGORY_MAP_REPORT_UNRESOLVED", "1") or "1").strip() == "1"
_UNRESOLVED_SEEN: set[str] = set()

_RE_SPACES = re.compile(r"\s+")
_RE_PUNCT = re.compile(r"[^\w#+./%-]+", re.U)

_PRINTER_BRANDS = (
    "hp", "canon", "xerox", "epson", "brother", "kyocera", "ricoh", "pantum", "lexmark",
    "oki", "konica", "minolta", "samsung", "sharp", "panasonic", "toshiba", "develop",
    "gestetner", "riso", "fujifilm", "olivetti", "triumph-adler", "katyusha", "катюша",
)

def _norm(text: str) -> str:
    s = (text or "").replace("\u00a0", " ").replace("ё", "е").replace("Ё", "Е")
    s = s.strip().casefold()
    s = _RE_PUNCT.sub(" ", s)
    s = _RE_SPACES.sub(" ", s)
    return f" {s.strip()} " if s.strip() else " "

def _param_map(params: Sequence[tuple[str, str]] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in params or []:
        k = _norm(key).strip()
        v = (value or "").strip()
        if k and v and k not in out:
            out[k] = v
    return out

def _join_params(params: Sequence[tuple[str, str]] | None) -> str:
    parts: list[str] = []
    for key, value in params or []:
        key_s = (key or "").strip()
        val_s = (value or "").strip()
        if key_s and val_s:
            parts.append(f"{key_s}: {val_s}")
    return "\n".join(parts)

def _contains_any(text: str, fragments: Sequence[str]) -> bool:
    return any(f" {frag.casefold()} " in text or frag.casefold() in text for frag in fragments if frag)

def _title_or_type(name_n: str, type_n: str, *terms: str) -> bool:
    return any(f" {t.casefold()} " in name_n or f" {t.casefold()} " in type_n for t in terms)

def _printer_brand_present(hay: str) -> bool:
    return any(f" {b} " in hay or b in hay for b in _PRINTER_BRANDS)

def _append_unresolved_report(*, oid: str, name: str, vendor: str, params: Sequence[tuple[str, str]] | None, native_desc: str) -> None:
    if not _ENABLE_UNRESOLVED_REPORT:
        return

    key = f"{oid.strip()}|{name.strip()}|{vendor.strip()}".casefold()
    if key in _UNRESOLVED_SEEN:
        return
    _UNRESOLVED_SEEN.add(key)

    path = Path(os.getenv("CS_CATEGORY_MAP_UNRESOLVED_REPORT", UNRESOLVED_REPORT_DEFAULT))
    path.parent.mkdir(parents=True, exist_ok=True)

    params_preview = "; ".join(
        f"{(k or '').strip()}={(v or '').strip()}"
        for k, v in (params or [])[:8]
        if (k or "").strip() and (v or "").strip()
    )
    desc_preview = " ".join((native_desc or "").replace("\r", " ").replace("\n", " ").split())
    if len(desc_preview) > 280:
        desc_preview = desc_preview[:280] + "..."

    lines = [
        f"oid: {oid.strip()}",
        f"name: {name.strip()}",
        f"vendor: {vendor.strip()}",
        f"params: {params_preview}",
        f"desc: {desc_preview}",
        "-" * 80,
    ]
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

def _resolve_exact(*, name_n: str, vendor_n: str, type_n: str, tech_n: str, hay: str) -> str:
    # 4. Проекторы / интерактивка
    if _contains_any(hay, ["ops", "крепление", "стойка", "стенд", "пульт", "стилус", "маркер", "держатель"]) and _contains_any(hay, ["интерактив", "проектор", "экран"]):
        return GROUP_IDS["interactive_accessories"]
    if _title_or_type(name_n, type_n, "интерактивная панель", "interactive panel", "интерактивный дисплей"):
        return GROUP_IDS["interactive_panels"]
    if _title_or_type(name_n, type_n, "интерактивная доска", "interactive board"):
        return GROUP_IDS["interactive_boards"]
    if _title_or_type(name_n, type_n, "экран", "экран для проектора", "projector screen"):
        return GROUP_IDS["projector_screens"]
    if _title_or_type(name_n, type_n, "проектор", "projector"):
        return GROUP_IDS["projectors"]

    # 5. Оборудование для документов
    if _title_or_type(name_n, type_n, "пленка для ламинирования", "laminating film"):
        return GROUP_IDS["laminating_film"]
    if _title_or_type(name_n, type_n, "ламинатор", "laminator"):
        return GROUP_IDS["laminators"]
    if _title_or_type(name_n, type_n, "переплетчик", "binder", "binding machine"):
        return GROUP_IDS["binders"]
    if _title_or_type(name_n, type_n, "шредер", "shredder"):
        return GROUP_IDS["shredders"]

    # 6. Компьютерная техника и мониторы
    if _title_or_type(name_n, type_n, "рабочая станция", "workstation"):
        return GROUP_IDS["workstations"]
    if _title_or_type(name_n, type_n, "моноблок", "all in one", "all-in-one", "aio"):
        return GROUP_IDS["monoblocks"]
    if _title_or_type(name_n, type_n, "ноутбук", "laptop", "notebook"):
        return GROUP_IDS["laptops"]
    if _title_or_type(name_n, type_n, "монитор", "monitor"):
        return GROUP_IDS["monitors"]
    if _title_or_type(name_n, type_n, "barebone", "mini pc", "desktop", "настольный пк", "системный блок", "неттоп"):
        return GROUP_IDS["desktops_barebone"]
    if _contains_any(hay, [
        "процессор", "cpu", "материнская плата", "motherboard", "видеокарта", "gpu",
        "оперативная память", "ddr4", "ddr5", "ssd", "hdd", "nvme", "корпус", "power supply", "блок питания"
    ]):
        return GROUP_IDS["pc_components"]

    # 7. ИБП / стабилизаторы / батареи
    if _contains_any(hay, ["snmp", "адаптер для ибп", "рельсы для ибп", "карта управления", "коммуникационная карта"]) and _contains_any(hay, ["ибп", "ups"]):
        return GROUP_IDS["ups_accessories"]
    if _contains_any(hay, ["аккумулятор", "аккумуляторная батарея", "батарейный блок", "battery pack", "battery module", "акб"]):
        return GROUP_IDS["batteries"]
    if _title_or_type(name_n, type_n, "стабилизатор", "stabilizer", "avr"):
        return GROUP_IDS["stabilizers"]
    if _title_or_type(name_n, type_n, "ибп", "ups", "источник бесперебойного питания"):
        return GROUP_IDS["ups"]

    # 8. Кабели и аксессуары подключения
    if _title_or_type(name_n, type_n, "кабель", "cable", "patch cord", "витая пара", "utp", "ftp", "hdmi", "displayport", "usb", "vga", "dvi"):
        return GROUP_IDS["cables"]
    if _contains_any(hay, ["коннектор", "разъем", "адаптер", "переходник", "splitter", "сплиттер", "док-станция"]) and not _contains_any(hay, ["ибп", "ups"]):
        return GROUP_IDS["connection_accessories"]

    # 3. Печатающая техника
    if _title_or_type(name_n, type_n, "плоттер", "широкоформатный принтер", "wide format"):
        return GROUP_IDS["plotters"]
    if _title_or_type(name_n, type_n, "мфу", "многофункциональное устройство", "multifunction"):
        return GROUP_IDS["mfp"]
    if _title_or_type(name_n, type_n, "сканер", "scanner"):
        return GROUP_IDS["scanners"]
    if _title_or_type(name_n, type_n, "принтер", "printer"):
        return GROUP_IDS["printers"]

    # 2. Запчасти и комплектующие для принтеров и МФУ
    if _contains_any(hay, ["финишер", "лоток", "кассета", "подающий модуль", "дополнительный лоток", "подставка", "pedestal", "tray"]):
        return GROUP_IDS["finishers_trays_stands"]
    if _contains_any(hay, ["комплект обслуживания", "maintenance kit", "сервисный комплект", "service kit", "комплект инициализации", "init kit"]):
        return GROUP_IDS["service_kits"]
    if _contains_any(hay, ["термоблок", "fuser", "печка", "узел закрепления", "fixing unit"]):
        return GROUP_IDS["fusers"]
    if _contains_any(hay, ["блок проявки", "developer unit", "dev unit"]):
        return GROUP_IDS["developer_units"]
    if _contains_any(hay, ["ремень переноса", "узел переноса", "transfer belt", "transfer unit", "лента переноса"]):
        return GROUP_IDS["transfer_units"]
    if _contains_any(hay, ["ролик подачи", "ролик захвата", "ролик отделения", "pickup roller", "feed roller", "separation roller", "ремкомплект"]):
        return GROUP_IDS["rollers_kits"]

    # 1. Расходные материалы для принтеров и МФУ
    if _contains_any(hay, ["печатающая головка", "printhead", "print head"]):
        return GROUP_IDS["printheads"]
    if _contains_any(hay, ["контейнер для отработки", "контейнер отработанного", "waste toner", "waste ink", "maintenance box", "емкость для отработанных", "бункер отработки"]):
        return GROUP_IDS["waste_containers"]
    if _contains_any(hay, ["девелопер", "developer"]) or (_contains_any(hay, ["тонер"]) and not _contains_any(hay, ["тонер-картридж", "toner cartridge"])):
        return GROUP_IDS["toners_developers"]
    if _contains_any(hay, ["драм-юнит", "drum unit", "фотобарабан", "барабан", "imaging drum", "image drum", "drum cartridge"]):
        return GROUP_IDS["drums_photodrums"]
    if _contains_any(hay, ["чернила", "ink bottle", "ultrachrome"]) and not _contains_any(hay, ["картридж", "cartridge", "емкость", "tank"]):
        return GROUP_IDS["inks"]

    is_ink_tech = _contains_any(tech_n, ["струйная", "ink"]) or _contains_any(hay, [
        "designjet", "deskjet", "officejet", "photosmart", "stylus", "ecotank", "ultrachrome", "surecolor", "pixma"
    ])
    if _contains_any(hay, ["картридж", "cartridge", "чернильный картридж", "емкость с чернилами", "чернильная емкость", "tank"]) and is_ink_tech:
        return GROUP_IDS["ink_cartridges_tanks"]
    if _contains_any(hay, ["тонер-картридж", "toner cartridge"]):
        return GROUP_IDS["laser_cartridges_toners"]
    if _contains_any(hay, ["картридж", "cartridge"]) and not is_ink_tech:
        return GROUP_IDS["laser_cartridges_toners"]

    return ""

def _resolve_soft_fallback(*, hay: str) -> str:
    # Мягкий fallback только если широкий класс понятен.
    if _printer_brand_present(hay):
        if _contains_any(hay, [
            "картридж", "тонер", "чернила", "девелопер", "фотобарабан", "драм",
            "печатающая головка", "контейнер для отработки", "waste toner", "maintenance box"
        ]):
            return GROUP_IDS["other_consumables"]

        if _contains_any(hay, [
            "узел", "модуль", "kit", "ролик", "fuser", "печка", "термоблок",
            "ремень переноса", "лоток", "финишер", "кассета", "подставка"
        ]):
            return GROUP_IDS["other_parts"]

    return ""

def resolve_category_id(
    *,
    name: str,
    vendor: str = "",
    params: Sequence[tuple[str, str]] | None = None,
    native_desc: str = "",
    oid: str = "",
) -> str:
    """
    Возвращает номер группы Satu для <categoryId>.

    Логика:
    - сначала точные правила;
    - потом мягкий fallback в "Прочие ..." только если широкий класс понятен;
    - если класс неясен, оставляет categoryId пустым и логирует товар для разбора.
    """
    pmap = _param_map(params)
    type_raw = pmap.get("тип", "")
    tech_raw = pmap.get("технология печати", "")
    model_raw = pmap.get("модель", "")
    name_n = _norm(name)
    vendor_n = _norm(vendor)
    type_n = _norm(type_raw)
    tech_n = _norm(tech_raw)
    model_n = _norm(model_raw)
    desc_n = _norm(native_desc)
    params_n = _norm(_join_params(params))
    hay = f"{name_n}{vendor_n}{type_n}{tech_n}{model_n}{desc_n}{params_n}"

    exact = _resolve_exact(name_n=name_n, vendor_n=vendor_n, type_n=type_n, tech_n=tech_n, hay=hay)
    if exact:
        return exact

    fallback = _resolve_soft_fallback(hay=hay)
    if fallback:
        return fallback

    _append_unresolved_report(
        oid=oid,
        name=name,
        vendor=vendor,
        params=params,
        native_desc=native_desc,
    )
    return ""

__all__ = [
    "GROUP_IDS",
    "UNRESOLVED_REPORT_DEFAULT",
    "resolve_category_id",
]
