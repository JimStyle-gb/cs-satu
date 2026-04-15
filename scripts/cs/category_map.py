# -*- coding: utf-8 -*-
"""
Path: scripts/cs/category_map.py

Единый shared-resolver categoryId.
Главный источник ID: scripts/cs/config/price_categories.yml
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

try:
    import yaml
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"Не установлен PyYAML: {exc}")

CONFIG_FILE = Path(__file__).resolve().parent / "config" / "price_categories.yml"
_RE_SPACES = re.compile(r"\s+")
_RE_PUNCT = re.compile(r"[^\w#+./%-]+", re.U)

_PRINTER_BRANDS = (
    "hp", "canon", "xerox", "epson", "brother", "kyocera", "ricoh", "pantum", "lexmark",
    "oki", "konica", "minolta", "samsung", "sharp", "panasonic", "toshiba", "develop",
    "gestetner", "riso", "fujifilm", "olivetti", "triumph-adler", "катюша", "katyusha",
)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Не найден файл: {path.as_posix()}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Ожидался YAML-словарь: {path.as_posix()}")
    return data


def _load_group_ids() -> dict[str, str]:
    data = _read_yaml(CONFIG_FILE)
    out: dict[str, str] = {}
    for item in data.get("leaf_groups", []) or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        cid = str(item.get("id", "")).strip()
        if key and cid:
            out[key] = cid
    return out


GROUP_IDS = _load_group_ids()


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
    return "\n".join(
        f"{(k or '').strip()}: {(v or '').strip()}"
        for k, v in params or []
        if (k or "").strip() and (v or "").strip()
    )


def _contains_any(text: str, fragments: Sequence[str]) -> bool:
    return any(_norm(str(f)).strip() and _norm(str(f)).strip() in text for f in fragments)


def _printer_brand_present(hay: str) -> bool:
    return any(f" {b} " in hay or b in hay for b in _PRINTER_BRANDS)


def _resolve_exact(*, name_n: str, vendor_n: str, type_n: str, tech_n: str, hay: str) -> str:
    # 1. Расходники
    if _contains_any(hay, ["тонер", "toner", "тонер-картридж"]) and not _contains_any(hay, ["чернила", "ink"]):
        return GROUP_IDS["laser_cartridges_toners"]
    if _contains_any(hay, ["картридж", "cartridge"]) and _contains_any(hay, ["струйн", "ink", "ecotank", "ultrachrome", "surecolor", "pixma", "deskjet", "officejet", "photosmart"]):
        return GROUP_IDS["ink_cartridges_tanks"]
    if _contains_any(hay, ["картридж", "cartridge"]) and not _contains_any(hay, ["ink", "струйн"]):
        return GROUP_IDS["laser_cartridges_toners"]
    if _contains_any(hay, ["чернила", "ink", "ink bottle", "чернильниц"]):
        return GROUP_IDS["inks"]
    if _contains_any(hay, ["девелопер", "developer", "тонер", "toner powder"]) and _contains_any(hay, ["банка", "туба", "bulk", "бутыль", "bottle", "bag"]):
        return GROUP_IDS["toners_developers"]
    if _contains_any(hay, [
        "драм",
        "drum",
        "фотобарабан",
        "барабан",
        "блок формирования изображения",
        "комплект блока формирования изображений",
        "image unit",
        "imaging unit",
    ]):
        return GROUP_IDS["drums_photodrums"]
    if _contains_any(hay, ["печатающ", "printhead", "print head", "головка"]):
        return GROUP_IDS["printheads"]
    if _contains_any(hay, ["отработанн", "waste", "maintenance box", "контейнер"]) and _contains_any(hay, ["чернил", "toner"]):
        return GROUP_IDS["waste_containers"]

    # 2. Запчасти печати
    if _contains_any(hay, ["термоблок", "печка", "fuser", "фьюзер"]):
        return GROUP_IDS["fusers"]
    if _contains_any(hay, ["блок проявки", "developer unit", "магнитный вал блока проявки"]):
        return GROUP_IDS["developer_units"]
    if _contains_any(hay, ["ремень переноса", "узел переноса", "вал переноса", "коротрон", "corona"]):
        return GROUP_IDS["transfer_units"]
    if _contains_any(hay, [
        "ролик подачи",
        "ролики подачи",
        "ремкомплект",
        "палец отделения",
        "пальцы отделения",
        "тормозная площадка",
        "площадка тормозная",
        "площадка отделения",
        "площадка отделени",
        "площадка отделени бумаги",
        "держатель площадки тормозной",
        "separation finger",
        "separation fingers",
        "pickup roller",
        "pickup rollers",
        "feed roller",
        "feed rollers",
        "separation roller",
        "separation rollers",
        "retard pad",
        "separation pad",
    ]):
        return GROUP_IDS["rollers_kits"]
    if _contains_any(hay, ["финишер", "лоток", "подставк", "степлер", "скрепк"]):
        return GROUP_IDS["finishers_trays_stands"]
    if _contains_any(hay, ["сервисный комплект", "service kit", "сервисный набор", "ремонтный комплект"]):
        return GROUP_IDS["service_kits"]

    # 3. Принтеры / МФУ / плоттеры / сканеры
    if _contains_any(hay, ["мфу", "multifunction", "all in one"]):
        return GROUP_IDS["mfp"]
    if _contains_any(hay, ["плоттер", "plotter", "wide format"]):
        return GROUP_IDS["plotters"]
    if _contains_any(hay, ["сканер", "scanner"]) and not _contains_any(hay, ["мфу", "multifunction"]):
        return GROUP_IDS["scanners"]
    if _contains_any(hay, ["принтер", "printer"]) and not _contains_any(hay, ["мфу", "multifunction", "plotter", "scanner"]):
        return GROUP_IDS["printers"]

    # 4. Проекторы / экраны / интерактивка
    if _contains_any(hay, ["проектор", "projector"]):
        return GROUP_IDS["projectors"]
    if _contains_any(hay, [
        "экран моторизированный",
        "моторизованный экран",
        "экран на треноге",
        "экран механический",
        "проекционный экран",
        "портативный экран",
        "моторизированный экран",
        "экран настенно потолочный",
        "настенно потолочный экран",
        "motorized screen",
        "tripod screen",
        "portable screen",
        "projection screen",
        "projector screen",
    ]):
        return GROUP_IDS["projector_screens"]
    if _contains_any(hay, ["экран", "screen"]) and _contains_any(hay, [
        "проекцион", "для проектора", "projector", "projection", "моторизирован", "моторизован",
        "на треноге", "tripod", "механическ", "настенно потолоч", "wall mounted"
    ]):
        return GROUP_IDS["projector_screens"]
    if _contains_any(hay, ["интерактивная панель", "interactive panel", "интерактивный киоск"]):
        return GROUP_IDS["interactive_panels"]
    if _contains_any(hay, ["интерактивная доска", "interactive board", "whiteboard"]):
        return GROUP_IDS["interactive_boards"]
    if _contains_any(hay, [
        "интерактивная трибуна",
        "interactive podium",
        "ops",
        "кронштейн для панели",
        "стойка для панели",
        "модуль для панели",
    ]):
        return GROUP_IDS["interactive_accessories"]

    # 5. Документы
    if _contains_any(hay, ["ламинатор", "laminator"]):
        return GROUP_IDS["laminators"]
    if _contains_any(hay, ["пленка для ламинирования", "laminating film", "ламин пленк"]):
        return GROUP_IDS["laminating_film"]
    if _contains_any(hay, ["переплетчик", "переплетная машина", "binder", "binding machine"]):
        return GROUP_IDS["binders"]
    if _contains_any(hay, ["шредер", "уничтожитель бумаги", "shredder"]):
        return GROUP_IDS["shredders"]

    # 6. Компы
    if _contains_any(hay, ["ноутбук", "laptop", "notebook"]):
        return GROUP_IDS["laptops"]
    if _contains_any(hay, ["монитор", "monitor"]) and not _contains_any(hay, [
        "интерактивная панель",
        "interactive panel",
        "интерактивный дисплей",
        "interactive display",
        "интерактивная доска",
        "interactive board",
    ]):
        return GROUP_IDS["monitors"]
    if _contains_any(hay, ["моноблок", "aio", "all in one pc"]):
        return GROUP_IDS["monoblocks"]
    if _contains_any(hay, [
        "barebone",
        "mini pc",
        "nettop",
        "nuc",
        "системный блок",
        "desktop",
        "настольный пк",
        "настольный компьютер",
        "компьютер",
        "small form factor",
        "sff",
    ]):
        return GROUP_IDS["desktops_barebone"]
    if _contains_any(hay, ["tower", "micro"]) and _contains_any(hay, [
        "dell",
        "hp",
        "lenovo",
        "asus",
        "acer",
        "desktop",
        "optiplex",
        "prodesk",
        "thinkcentre",
        "компьютер",
        "настольный пк",
    ]):
        return GROUP_IDS["desktops_barebone"]
    if _contains_any(hay, ["workstation", "рабочая станция"]):
        return GROUP_IDS["workstations"]
    if _contains_any(hay, ["материнская плата", "ssd", "hdd", "процессор", "оперативная память", "видеокарта", "корпус", "блок питания pc"]):
        return GROUP_IDS["pc_components"]

    # 7. Энергия
    if _contains_any(hay, ["источник бесперебойного питания", "ups", "ибп"]):
        return GROUP_IDS["ups"]
    if _contains_any(hay, ["стабилизатор", "stabilizer", "avr"]) and not _contains_any(hay, ["ups", "ибп"]):
        return GROUP_IDS["stabilizers"]
    if _contains_any(hay, ["аккумулятор", "battery", "батарейный блок", "battery pack", "battery module", "дополнительная батарея", "модуль батарей", "ebm"]):
        return GROUP_IDS["batteries"]
    if _contains_any(hay, ["power module", "upm module", "силовой модуль", "snmp", "карта мониторинга ибп", "модуль bypass"]):
        return GROUP_IDS["ups_accessories"]

    # 8. Кабели
    if _contains_any(hay, ["адаптер", "adapter", "переходник", "конвертер", "dock", "док станция"]):
        return GROUP_IDS["connection_accessories"]
    if _contains_any(hay, ["кабель", "cable", "шнур", "cord", "patch cord", "utp", "hdmi", "displayport", "usb c", "usb-c", "vga", "dvi", "rj45"]):
        return GROUP_IDS["cables"]

    return ""


def _resolve_soft_fallback(*, hay: str) -> str:
    if _printer_brand_present(hay):
        if _contains_any(hay, ["картридж", "тонер", "чернила", "девелопер", "фотобарабан", "драм", "печатающ", "maintenance box", "контейнер для отработки"]):
            return GROUP_IDS["other_consumables"]
        if _contains_any(hay, [
            "узел", "модуль", "kit", "ролик", "fuser", "термоблок", "ремень переноса", "лоток",
            "финишер", "кассета", "подставка", "шлейф", "муфта", "направляющая", "шарнир", "петля",
            "консоль", "шестерня", "зубчатая передача", "подшипник", "датчик", "активатор", "шкив",
            "привод", "панель управления", "автоподатчик", "dadf", "adf", "блок подачи чернил",
            "пылевой фильтр", "рычаг датчика", "газлифт", "жесткий диск", "нагревательная лампа",
            "транспортного модуля", "транспортный модуль"
        ]):
            return GROUP_IDS["other_parts"]
    return ""


def resolve_category_id(*, name: str, vendor: str = "", params: Sequence[tuple[str, str]] | None = None, native_desc: str = "", oid: str = "") -> str:
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

    return ""


__all__ = ["GROUP_IDS", "resolve_category_id"]
