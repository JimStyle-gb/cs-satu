# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/comportal/builder.py

ComPortal Builder — supplier-layer сборка clean raw offers.

Что делает:
- собирает raw offer из normalized basics, params, compat и pictures;
- разводит text-for-data и text-for-display;
- готовит clean raw OfferOut для shared core.

Что не делает:
- не переносит supplier-specific repairs в shared core;
- не строит final shared HTML description;
- не заменяет source.py, params.py и compat.py.
"""

from __future__ import annotations

from html import unescape
from typing import Any
import re

from cs.core import OfferOut
from cs.util import norm_ws
from suppliers.comportal.compat import apply_compat_cleanup
from suppliers.comportal.desc_clean import sanitize_native_desc
from suppliers.comportal.desc_extract import extract_desc_fill_params
from suppliers.comportal.models import BuildStats, ParamItem, SourceOffer
from suppliers.comportal.normalize import (
    build_offer_oid,
    normalize_available,
    normalize_model,
    normalize_name,
    normalize_price_in,
    normalize_vendor,
)
from suppliers.comportal.params import build_params_from_xml
from suppliers.comportal.pictures import collect_picture_urls


_RECONCILE_KEYS = {
    "коды",
    "модель",
    "ресурс",
    "гарантия",
    "цвет",
}

_GLOBAL_DROP_PARAM_NAMES = {
    "серия",
}

_PRINT_DEVICE_DROP_PARAM_NAMES = {
    "объем памяти",
    "количество лотков",
    "емкость 1-го лотка",
    "емкость 2-го лотка",
    "емкость 3-го лотка",
}

_ORIGINALITY_PARAM_NAME = "Оригинальность"
_NAME_ORIGINALITY_SUFFIX_RE = re.compile(r"\s*\((?:оригинал|совместимый)\)\s*$", re.I)
_DESC_ORIGINALITY_HEAD_RE = re.compile(r"(?iu)^\s*(?:Оригинальн(?:ый|ая|ое|ые)|Совместим(?:ый|ая|ое|ые))")


def _param_map(params: list[ParamItem]) -> dict[str, str]:
    out: dict[str, str] = {}
    seen: set[str] = set()
    for p in params or []:
        name = norm_ws(p.name)
        value = norm_ws(p.value)
        if not name or not value:
            continue
        ncf = name.casefold()
        if ncf in seen:
            continue
        out[name] = value
        seen.add(ncf)
    return out


def _drop_param_casefold(params: list[ParamItem], name_to_drop: str) -> list[ParamItem]:
    target = norm_ws(name_to_drop).casefold()
    return [p for p in params if norm_ws(p.name).casefold() != target]


def _join_nonempty(parts: list[str], sep: str = ". ") -> str:
    vals = [norm_ws(x) for x in parts if norm_ws(x)]
    return sep.join(vals).strip()


def _append_param_line(bits: list[str], label: str, value: str) -> None:
    v = norm_ws(value)
    if v:
        bits.append(f"{label}: {v}")


def _finalize_desc(text: str) -> str:
    t = norm_ws(text)
    if t and not t.endswith("."):
        t += "."
    return t


def _param_value_score(name: str, value: str) -> int:
    ncf = norm_ws(name).casefold()
    v = norm_ws(value)
    if not v:
        return 0

    score = 0
    if len(v) >= 3:
        score += 1
    if any(ch.isdigit() for ch in v):
        score += 1
    if "#" in v or "/" in v:
        score += 1
    if len(v) >= 8:
        score += 1

    if ncf == "гарантия":
        if "мес" in v.casefold():
            score += 4
    elif ncf == "ресурс":
        if any(ch.isdigit() for ch in v):
            score += 3
        if "стр" in v.casefold():
            score += 1
    elif ncf in {"коды", "модель"}:
        if len(v) >= 5:
            score += 3
        if "#" in v or "/" in v or "-" in v:
            score += 2
    elif ncf == "цвет":
        if v.casefold() in {
            "чёрный", "черный", "жёлтый", "желтый", "голубой",
            "пурпурный", "серый", "белый", "синий", "красный", "зелёный", "зеленый",
        }:
            score += 3

    return score


def _model_reconcile_should_keep_old(old_value: str, new_value: str) -> bool:
    old_v = norm_ws(old_value)
    new_v = norm_ws(new_value)
    if not old_v or not new_v:
        return False

    old_cf = old_v.casefold()
    new_cf = new_v.casefold()

    if old_cf == new_cf:
        return True

    if new_cf.startswith(old_cf) and any(sep in new_v for sep in ("#", "/")):
        return True

    for sep in ("#", "/"):
        if sep in new_v:
            head = norm_ws(new_v.split(sep, 1)[0])
            if head.casefold() == old_cf:
                return True

    return False


_CODE_LIKE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_#./\-]{4,}$", re.IGNORECASE)
_TITLE_TAIL_CODE_RE = re.compile(r"\(([^()]{2,})\)\s*$")
_CM_INCH_RE = re.compile(r"\b\d{2,3}(?:[.,]\d+)?\s*cm\s*\((\d{1,2}(?:[.,]\d)?)\s*\"\)")
_INCH_RE = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*\"")
_MONITOR_DIGIT_RE = re.compile(r"(?iu)\b(\d{2})(?:\s|[-–—])")
_NOISY_MODEL_RE = re.compile(r"(?iu)^(?:монитор|ноутбук|моноблок|компьютер|плоттер|принтер)\b")
_PUBLIC_HEAD_RE = re.compile(
    r"(?iu)^(?:моноблок|ноутбук|принтер|мфу|сканер|проектор|монитор|плоттер|компьютер|настольный\s+пк|широкоформатный\s+принтер)\s+"
    r"(?:AIWA|Dell|HP|Canon|Epson|Xerox|Brother|Kyocera|Pantum|Ricoh|APC|Lenovo|ASUS|Acer|MSI|LG|Samsung|Huawei|iiyama|Gigabyte|Hikvision|ViewSonic|BenQ|AOC|TP\-?Link|D\-?Link|Cisco|Zyxel|Eaton|Poly)\s+"
)


def _decode_text(value: str) -> str:
    return norm_ws(unescape(value or "").replace("&quot;", '"').replace("quot;", '"'))


def _extract_title_tail_code(clean_name: str) -> str:
    s = _decode_text(clean_name)
    m = _TITLE_TAIL_CODE_RE.search(s)
    return _decode_text(m.group(1)) if m else ""


def _shorten_public_series(text: str) -> str:
    s = _decode_text(text)
    if not s:
        return ""

    m = re.search(r"(?iu)\bimagePROGRAF\s+[A-Z]{1,4}\-?\d{3,5}\b", s)
    if m:
        return _decode_text(m.group(0))

    m = re.search(
        r"(?iu)\b(?:"
        r"Pro\s+(?:Micro|Slim|Tower|Max|Plus)\s+[A-Z0-9-]+|"
        r"Pro\s+\d+\s+All\-in\-One(?:\s+Plus)?\s+[A-Z0-9-]+|"
        r"OptiPlex\s+[A-Z0-9-]+|"
        r"Latitude\s+[A-Z0-9-]+|"
        r"Precision\s+[A-Z0-9-]+|"
        r"AW\d{4,5}[A-Z]{0,4}|"
        r"S\d{4}[A-Z]{0,4}|"
        r"SE\d{4}[A-Z]{0,4}|"
        r"P\d{4}[A-Z]{0,4}"
        r")\b",
        s,
    )
    if m:
        return _decode_text(m.group(0))

    return s


def _is_code_like_value(value: str, *, codes: str = "") -> bool:
    v = _decode_text(value)
    c = _decode_text(codes)
    if not v:
        return True
    if c and v.casefold() == c.casefold():
        return True
    if "_" in v:
        return True
    if _CODE_LIKE_RE.fullmatch(v):
        return True
    if len(v) >= 18 and sum(ch.isdigit() for ch in v) >= 4 and any(sep in v for sep in ("-", "_", "#", "/")):
        return True
    return False


def _clean_public_series_text(text: str, *, vendor: str, ptype: str, code: str) -> str:
    s = _decode_text(text)
    if not s:
        return ""

    if code:
        s = re.sub(rf"\(\s*{re.escape(_decode_text(code))}\s*\)\s*$", "", s, flags=re.IGNORECASE).strip()

    s = re.sub(r"(?iu)^Плоттер\s+Canon\s+Плоттер\s+Canon\s*/\s*", "imagePROGRAF ", s)
    s = re.sub(r"(?iu)^Плоттер\s+Canon\s*/\s*", "", s)

    ptype_s = _decode_text(ptype)
    vendor_s = _decode_text(vendor)

    changed = True
    while changed:
        changed = False

        nxt = _PUBLIC_HEAD_RE.sub("", s).strip(" /-–—,;:")
        if nxt != s:
            s = nxt
            changed = True

        for pat in (
            rf"^{re.escape(ptype_s)}\s+{re.escape(vendor_s)}\s+" if ptype_s and vendor_s else "",
            rf"^{re.escape(vendor_s)}\s+{re.escape(ptype_s)}\s+" if ptype_s and vendor_s else "",
            rf"^{re.escape(ptype_s)}\s+" if ptype_s else "",
            rf"^{re.escape(vendor_s)}\s+" if vendor_s else "",
            rf"^Плоттер\s+{re.escape(vendor_s)}\s+" if vendor_s else "",
            rf"^{re.escape(vendor_s)}\s*/" if vendor_s else "",
        ):
            if not pat:
                continue
            nxt = re.sub(pat, "", s, flags=re.IGNORECASE).strip(" /-–—,;:")
            if nxt != s:
                s = nxt
                changed = True

    s = re.sub(r",\s*\d{2,3}(?:[.,]\d+)?\s*cm\s*\([^)]*\)\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s{2,}", " ", s).strip(" /-–—,;:")
    s = _shorten_public_series(s)
    return s


def _title_public_series(clean_name: str, *, vendor: str, ptype: str, codes: str) -> str:
    return _clean_public_series_text(clean_name, vendor=vendor, ptype=ptype, code=codes)


def _title_monitor_diagonal(clean_name: str) -> str:
    s = _decode_text(clean_name)
    m = _CM_INCH_RE.search(s)
    if m:
        return m.group(1).replace(",", ".")
    m = _INCH_RE.search(s)
    if m:
        return m.group(1).replace(",", ".")
    m = _MONITOR_DIGIT_RE.search(s)
    if m:
        return m.group(1)
    return ""


def _effective_public_model(pmap: dict[str, str], *, clean_name: str, vendor: str, ptype: str) -> str:
    model = _decode_text(pmap.get("Модель", ""))
    codes = _decode_text(pmap.get("Коды", ""))
    title_series = _title_public_series(clean_name, vendor=vendor, ptype=ptype, codes=codes)

    if title_series and (_is_code_like_value(model, codes=codes) or _NOISY_MODEL_RE.search(model)):
        return title_series
    return model or title_series


def _polish_model_param(params: list[ParamItem], *, clean_name: str, vendor: str) -> list[ParamItem]:
    pmap = _param_map(params)
    ptype = norm_ws(pmap.get("Тип", ""))
    better_model = _effective_public_model(pmap, clean_name=clean_name, vendor=vendor, ptype=ptype)
    current_model = _decode_text(pmap.get("Модель", ""))
    codes = _decode_text(pmap.get("Коды", ""))

    if not better_model:
        return params
    if current_model and current_model.casefold() == better_model.casefold():
        return params
    if not current_model or _is_code_like_value(current_model, codes=codes) or _NOISY_MODEL_RE.search(current_model) or (_extract_title_tail_code(clean_name) and _extract_title_tail_code(clean_name).casefold() in current_model.casefold()):
        return _upsert_param(params, name="Модель", value=better_model, source="title")
    return params


def _merge_desc_enrichment(xml_params: list[ParamItem], desc_params: list[ParamItem]) -> list[ParamItem]:
    out = list(xml_params)
    index: dict[str, int] = {}

    for i, p in enumerate(out):
        ncf = norm_ws(p.name).casefold()
        if ncf and ncf not in index:
            index[ncf] = i

    for p in desc_params or []:
        name = norm_ws(p.name)
        value = norm_ws(p.value)
        if not name or not value:
            continue

        ncf = name.casefold()

        if ncf not in _RECONCILE_KEYS:
            if ncf not in index:
                out.append(ParamItem(name=name, value=value, source=p.source))
                index[ncf] = len(out) - 1
            continue

        if ncf not in index:
            out.append(ParamItem(name=name, value=value, source=p.source))
            index[ncf] = len(out) - 1
            continue

        old_idx = index[ncf]
        old_param = out[old_idx]

        if ncf == "модель" and _model_reconcile_should_keep_old(old_param.value, value):
            continue

        old_score = _param_value_score(old_param.name, old_param.value)
        new_score = _param_value_score(name, value)

        if new_score > old_score:
            out[old_idx] = ParamItem(name=old_param.name, value=value, source=p.source)

    return out


def _enrich_sparse_device_desc(bits: list[str], pmap: dict[str, str]) -> list[str]:
    strong_payload_count = max(0, len(bits) - 1)
    if strong_payload_count >= 3:
        return bits

    for key in ("Для бренда", "Модель", "Коды", "Гарантия"):
        val = norm_ws(pmap.get(key, ""))
        if not val:
            continue
        probe = f"{key}: {val}"
        if probe not in bits:
            bits.append(probe)

    return bits


def _desc_for_printing_device(pmap: dict[str, str]) -> str:
    bits: list[str] = []
    ptype = norm_ws(pmap.get("Тип", ""))
    if ptype:
        bits.append(ptype)
    _append_param_line(bits, "Формат печати", pmap.get("Формат печати", ""))
    _append_param_line(bits, "Разрешение", pmap.get("Разрешение", ""))
    _append_param_line(bits, "Скорость печати ч/б", pmap.get("Скорость печати ч/б", ""))
    _append_param_line(bits, "Скорость печати цветной", pmap.get("Скорость печати цветной", ""))
    _append_param_line(bits, "Порты", pmap.get("Порты", ""))
    _append_param_line(bits, "Технология печати", pmap.get("Технология печати", ""))
    _append_param_line(bits, "Гарантия", pmap.get("Гарантия", ""))
    bits = _enrich_sparse_device_desc(bits, pmap)
    return _finalize_desc(_join_nonempty(bits))


def _desc_for_monitor(pmap: dict[str, str], *, clean_name: str, vendor: str) -> str:
    bits: list[str] = ["Монитор"]

    public_model = _effective_public_model(pmap, clean_name=clean_name, vendor=vendor, ptype="Монитор")
    diagonal = norm_ws(pmap.get("Диагональ", "") or pmap.get("Монитор", "") or _title_monitor_diagonal(clean_name))
    matrix = norm_ws(pmap.get("Тип матрицы", "") or pmap.get("Тип дисплея", ""))

    if public_model and not _is_code_like_value(public_model, codes=pmap.get("Коды", "")):
        _append_param_line(bits, "Модельная серия", public_model)

    _append_param_line(bits, "Диагональ", diagonal)
    _append_param_line(bits, "Максимальное разрешение", pmap.get("Максимальное разрешение", ""))
    _append_param_line(bits, "Тип матрицы", matrix)
    _append_param_line(bits, "Частота обновления", pmap.get("Частота обновления", ""))
    _append_param_line(bits, "Время отклика", pmap.get("Время отклика", ""))
    _append_param_line(bits, "Порты", pmap.get("Порты", ""))
    _append_param_line(bits, "Гарантия", pmap.get("Гарантия", ""))

    enrich_map = dict(pmap)
    if public_model and not _is_code_like_value(public_model, codes=pmap.get("Коды", "")):
        enrich_map["Модель"] = public_model
    if diagonal and not enrich_map.get("Диагональ"):
        enrich_map["Диагональ"] = diagonal

    bits = _enrich_sparse_device_desc(bits, enrich_map)
    return _finalize_desc(_join_nonempty(bits))


def _desc_for_computer(pmap: dict[str, str], *, clean_name: str, vendor: str) -> str:
    bits: list[str] = []
    ptype = norm_ws(pmap.get("Тип", ""))
    if ptype:
        bits.append(ptype)

    public_model = _effective_public_model(pmap, clean_name=clean_name, vendor=vendor, ptype=ptype)
    if public_model and not _is_code_like_value(public_model, codes=pmap.get("Коды", "")):
        _append_param_line(bits, "Модельная серия", public_model)

    cpu = _join_nonempty(
        [
            pmap.get("Производитель процессора", ""),
            pmap.get("Серия процессора", ""),
            pmap.get("Модель процессора", ""),
            pmap.get("Частота процессора", ""),
        ],
        sep=" ",
    )
    _append_param_line(bits, "Процессор", cpu)

    ram = _join_nonempty(
        [
            pmap.get("Оперативная память", ""),
            pmap.get("Тип оперативной памяти", ""),
            pmap.get("Частота оперативной памяти", ""),
        ],
        sep=" / ",
    )
    _append_param_line(bits, "Оперативная память", ram)

    storage = _join_nonempty(
        [
            pmap.get("Объем жесткого диска", ""),
            pmap.get("Тип жесткого диска", ""),
            f"{norm_ws(pmap.get('Количество дисков', ''))} шт" if norm_ws(pmap.get("Количество дисков", "")) else "",
        ],
        sep=" / ",
    )
    _append_param_line(bits, "Накопитель", storage)

    display_size = _join_nonempty([pmap.get("Размер дисплея", ""), pmap.get("Диагональ", "")], sep=" / ")
    _append_param_line(bits, "Диагональ", display_size)
    _append_param_line(bits, "Разрешение", pmap.get("Разрешение дисплея", "") or pmap.get("Максимальное разрешение", ""))

    os_name = _join_nonempty(
        [
            pmap.get("Производитель операционной системы", ""),
            pmap.get("Операционная система", ""),
            pmap.get("Версия операционной системы", ""),
            pmap.get("Битность операционной системы", ""),
        ],
        sep=" ",
    )
    _append_param_line(bits, "ОС", os_name)

    gpu = _join_nonempty(
        [
            pmap.get("Производитель чипсета видеокарты", "") or pmap.get("Марка чипсета видеокарты", ""),
            pmap.get("Модель чипсета видеокарты", ""),
            pmap.get("Объем видеопамяти", ""),
        ],
        sep=" ",
    )
    _append_param_line(bits, "Видеокарта", gpu)

    _append_param_line(bits, "Wi‑Fi", pmap.get("Wi-Fi", ""))
    _append_param_line(bits, "Bluetooth", pmap.get("Bluetooth", ""))
    _append_param_line(bits, "Камера", pmap.get("Камера", ""))
    _append_param_line(bits, "Гарантия", pmap.get("Гарантия", ""))

    enrich_map = dict(pmap)
    if public_model and not _is_code_like_value(public_model, codes=pmap.get("Коды", "")):
        enrich_map["Модель"] = public_model

    bits = _enrich_sparse_device_desc(bits, enrich_map)
    return _finalize_desc(_join_nonempty(bits))


def _desc_for_power(pmap: dict[str, str]) -> str:
    bits: list[str] = []
    ptype = norm_ws(pmap.get("Тип", ""))
    if ptype:
        bits.append(ptype)
    power_pair = _join_nonempty(
        [
            f"{norm_ws(pmap.get('Мощность (VA)', ''))} VA" if norm_ws(pmap.get("Мощность (VA)", "")) else "",
            f"{norm_ws(pmap.get('Мощность (W)', ''))} W" if norm_ws(pmap.get("Мощность (W)", "")) else "",
        ],
        sep=" / ",
    )
    _append_param_line(bits, "Мощность", power_pair)
    _append_param_line(bits, "Форм-фактор", pmap.get("Форм-фактор", ""))
    _append_param_line(bits, "Стабилизатор (AVR)", pmap.get("Стабилизатор (AVR)", ""))
    _append_param_line(bits, "Время работы при 100% нагрузке, мин", pmap.get("Типовая продолжительность работы при 100% нагрузке, мин", ""))
    _append_param_line(bits, "Выходные соединения", pmap.get("Выходные соединения", ""))
    _append_param_line(bits, "Гарантия", pmap.get("Гарантия", ""))
    bits = _enrich_sparse_device_desc(bits, pmap)
    return _finalize_desc(_join_nonempty(bits))


def _seo_head_word_from_originality(value: str) -> str:
    v = norm_ws(value).casefold().replace("ё", "е")
    if v == "оригинал":
        return "Оригинальный"
    if v == "совместимый":
        return "Совместимый"
    return ""


def _shorten_compat_list(text: str, *, max_items: int = 5, max_len: int = 160) -> str:
    s = norm_ws(text)
    if not s:
        return ""

    items = [norm_ws(x) for x in re.split(r"\s*[;,]\s*", s) if norm_ws(x)]
    if items:
        items = items[:max_items]
        out = ", ".join(items)
    else:
        out = s

    if len(out) <= max_len:
        return out

    cut = out[:max_len].rstrip(" ,;/")
    for sep in (",", " ", "/"):
        j = cut.rfind(sep)
        if j >= max_len - 35:
            cut = cut[:j].rstrip(" ,;/")
            break
    cut = cut.rstrip(" ,;/")
    return cut + "…"


def _desc_for_consumable(pmap: dict[str, str]) -> str:
    ptype = norm_ws(pmap.get("Тип", "")) or "Расходный материал"
    vendor = norm_ws(pmap.get("Для бренда", ""))
    model = norm_ws(pmap.get("Модель", ""))
    codes = norm_ws(pmap.get("Коды", ""))
    color = norm_ws(pmap.get("Цвет", ""))
    tech = norm_ws(pmap.get("Технология печати", ""))
    resource = norm_ws(pmap.get("Ресурс", ""))
    volume = norm_ws(pmap.get("Объём", ""))
    compat = _shorten_compat_list(pmap.get("Совместимость", ""))
    number = norm_ws(pmap.get("Номер", ""))
    use_case = norm_ws(pmap.get("Применение", ""))
    originality = _seo_head_word_from_originality(pmap.get("Оригинальность", ""))

    head_bits: list[str] = []
    if originality:
        head_bits.append(originality)
    head_bits.append(ptype.lower())
    if vendor:
        head_bits.append(vendor)
    if model and (not vendor or model.casefold() != vendor.casefold()):
        head_bits.append(model)
    intro = " ".join(x for x in head_bits if x).strip()

    tail_parts: list[str] = []
    if compat:
        tail_parts.append(f"для {compat}")
    elif use_case:
        tail_parts.append(f"для {use_case}")

    code_tokens = []
    for raw in (codes, number):
        token = norm_ws(raw)
        if token and token.casefold() not in {norm_ws(x).casefold() for x in code_tokens}:
            code_tokens.append(token)
    if code_tokens:
        label = "код" if len(code_tokens) == 1 else "коды"
        tail_parts.append(f"{label} — {' / '.join(code_tokens)}")

    if color:
        tail_parts.append(f"цвет — {color}")
    if resource:
        tail_parts.append(f"ресурс — {resource}")
    elif volume:
        tail_parts.append(f"объём — {volume}")
    if tech:
        tail_parts.append(f"технология печати — {tech}")

    if intro and tail_parts:
        return _finalize_desc(f"{intro} {'; '.join(tail_parts)}")
    if intro:
        return _finalize_desc(intro)

    bits: list[str] = []
    if ptype:
        bits.append(ptype)
    _append_param_line(bits, "Цвет", color)
    _append_param_line(bits, "Технология печати", tech)
    _append_param_line(bits, "Ресурс", resource)
    _append_param_line(bits, "Объём", volume)
    _append_param_line(bits, "Номер", number)
    _append_param_line(bits, "Применение", use_case)
    return _finalize_desc(_join_nonempty(bits))


def _canonical_param_name_for_prune(name: str) -> str:
    """
    Канонизация supplier-key только для prune-сравнения.
    """
    s = norm_ws(name).casefold()
    s = re.sub(r"\b(\d+)\s*-\s*го\b", r"\1-го", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _prune_low_value_params(params: list[ParamItem]) -> list[ParamItem]:
    """
    Точечная чистка слабополезных raw params.
    """
    pmap = _param_map(params)
    ptype = norm_ws(pmap.get("Тип", "")).casefold()

    drop_names = set(_GLOBAL_DROP_PARAM_NAMES)
    if ptype in {"мфу", "принтер", "сканер", "проектор", "широкоформатный принтер"}:
        drop_names |= set(_PRINT_DEVICE_DROP_PARAM_NAMES)

    out: list[ParamItem] = []
    for p in params or []:
        probe = _canonical_param_name_for_prune(p.name)
        if probe in drop_names:
            continue
        out.append(p)
    return out


def _build_native_desc(*, clean_name: str, source_offer: SourceOffer, params: list[ParamItem]) -> str:
    native = sanitize_native_desc(source_offer.description or "", title=clean_name)
    if native:
        return native
    pmap = _param_map(params)
    ptype = norm_ws(pmap.get("Тип", "")).casefold()

    if ptype in {"мфу", "принтер", "сканер", "проектор", "широкоформатный принтер"}:
        text = _desc_for_printing_device(pmap)
        if text:
            return text
    if ptype == "монитор":
        text = _desc_for_monitor(pmap, clean_name=clean_name, vendor=norm_ws(pmap.get("Для бренда", "")))
        if text:
            return text
    if ptype in {"ноутбук", "моноблок", "настольный пк", "рабочая станция"}:
        text = _desc_for_computer(pmap, clean_name=clean_name, vendor=norm_ws(pmap.get("Для бренда", "")))
        if text:
            return text
    if ptype in {"ибп", "стабилизатор", "батарея"}:
        text = _desc_for_power(pmap)
        if text:
            return text
    if ptype in {"картридж", "тонер", "расходный материал"}:
        text = _desc_for_consumable(pmap)
        if text:
            return text

    bits: list[str] = []
    if norm_ws(pmap.get("Тип", "")):
        bits.append(norm_ws(pmap.get("Тип", "")))
    for key in ("Для бренда", "Коды", "Модель", "Цвет", "Технология печати", "Ресурс", "Гарантия"):
        _append_param_line(bits, key, pmap.get(key, ""))
    body = _join_nonempty(bits)
    if body:
        return _finalize_desc(body)
    if source_offer.category_path:
        return f"Категория поставщика: {norm_ws(source_offer.category_path)}."
    return ""


_IDENTITY_GENERIC_VALUES = {
    "моноблок",
    "ноутбук",
    "принтер",
    "мфу",
    "сканер",
    "проектор",
    "монитор",
    "картридж",
    "тонер",
    "ибп",
    "сервер",
    "cs",
}


def _is_weak_identity_value(value: str, *, vendor: str) -> bool:
    v = norm_ws(value)
    if not v:
        return True

    vcf = v.casefold()
    if vcf in _IDENTITY_GENERIC_VALUES:
        return True
    if vendor and vcf == norm_ws(vendor).casefold():
        return True

    return False


def _upsert_param(params: list[ParamItem], *, name: str, value: str, source: str) -> list[ParamItem]:
    target = norm_ws(name).casefold()
    out: list[ParamItem] = []
    replaced = False

    for p in params or []:
        if norm_ws(p.name).casefold() == target and not replaced:
            out.append(ParamItem(name=norm_ws(name), value=norm_ws(value), source=source))
            replaced = True
        else:
            out.append(p)

    if not replaced:
        out.append(ParamItem(name=norm_ws(name), value=norm_ws(value), source=source))
    return out


def _ensure_base_params(*, source_offer: SourceOffer, params: list[ParamItem], vendor: str, model: str, clean_name: str) -> list[ParamItem]:
    out = list(params)
    pmap = _param_map(out)

    if vendor:
        cur_vendor_param = norm_ws(pmap.get("Для бренда", ""))
        if not cur_vendor_param or _is_weak_identity_value(cur_vendor_param, vendor=vendor):
            out = _upsert_param(out, name="Для бренда", value=vendor, source="normalize")

    pmap = _param_map(out)
    if model:
        cur_model = norm_ws(pmap.get("Модель", ""))
        if not cur_model or _is_weak_identity_value(cur_model, vendor=vendor):
            out = _upsert_param(out, name="Модель", value=model, source="normalize")

    pmap = _param_map(out)
    title_code = _extract_title_tail_code(clean_name)
    cur_codes = norm_ws(pmap.get("Коды", ""))
    if not cur_codes or _is_weak_identity_value(cur_codes, vendor=vendor):
        if title_code:
            out = _upsert_param(out, name="Коды", value=title_code, source="title")
        elif source_offer.vendor_code and re.search(r"(?i)[A-Z]", norm_ws(source_offer.vendor_code)):
            out = _upsert_param(out, name="Коды", value=norm_ws(source_offer.vendor_code), source="source")

    pmap = _param_map(out)
    if vendor and pmap.get("Для бренда") and pmap.get("Бренд"):
        out = _drop_param_casefold(out, "Бренд")

    return out


def _strip_originality_suffix(name: str) -> str:
    return norm_ws(_NAME_ORIGINALITY_SUFFIX_RE.sub("", norm_ws(name)))


def _upsert_param_tuple(params: list[tuple[str, str]], key: str, value: str) -> list[tuple[str, str]]:
    want = norm_ws(key).casefold()
    out: list[tuple[str, str]] = []
    replaced = False
    for k, v in params:
        if norm_ws(k).casefold() == want:
            if not replaced:
                out.append((key, value))
                replaced = True
            continue
        out.append((k, v))
    if not replaced:
        out.append((key, value))
    return out


_DESC_FIELD_START_RE = re.compile(
    r"(?iu)^(?:цвет|ресурс|технология(?:\s+печати)?|тип|партномер|модель|код(?:ы)?|совместимость|"
    r"для\s+бренда|гарантия|об[ъь]ем|объём|вес|номер|применение|количество)\s*:"
)


def _strip_leading_type_phrase(desc: str, type_label: str) -> str:
    d = norm_ws(desc)
    tl = norm_ws(type_label)
    if not d or not tl:
        return d
    pat = re.compile(rf"(?iu)^\s*{re.escape(tl)}(?=$|[\s.,:;()\-–—])")
    m = pat.match(d)
    if not m:
        return d
    rest = d[m.end():].lstrip(" .,:;()-–—")
    return norm_ws(rest)


def _merge_originality_sentence(sentence: str, desc: str, type_label: str) -> str:
    s = norm_ws(sentence)
    d = _strip_leading_type_phrase(desc, type_label)
    if not s:
        return d
    if not d:
        return s
    if _DESC_FIELD_START_RE.match(d):
        return f"{s} {d}" if s.endswith('.') else f"{s}. {d}"
    s_join = s[:-1] if s.endswith('.') else s
    return f"{s_join} {d}"


def _detect_consumable_type_label(name: str, params: list[tuple[str, str]]) -> str:
    type_from_param = ""
    for k, v in params:
        if norm_ws(k).casefold() == "тип":
            type_from_param = norm_ws(v)
            break
    if type_from_param:
        low = type_from_param.casefold().replace("ё", "е")
        mapping = (
            ("контейнер для отработанного тонера", "Контейнер для отработанного тонера"),
            ("бункер для отработанного тонера", "Бункер для отработанного тонера"),
            ("ролик переноса", "Ролик переноса"),
            ("лента переноса", "Лента переноса"),
            ("блок переноса", "Блок переноса"),
            ("блок проявки", "Блок проявки"),
            ("ремонтный комплект", "Ремонтный комплект"),
            ("печатающая головка", "Печатающая головка"),
            ("драм-юнит", "Драм-юнит"),
            ("драм-картридж", "Драм-картридж"),
            ("фотобарабан", "Фотобарабан"),
            ("копи-картридж", "Копи-картридж"),
            ("принт-картридж", "Принт-картридж"),
            ("тонер-картридж", "Тонер-картридж"),
            ("девелопер", "Девелопер"),
            ("чернила", "Чернила"),
            ("картридж", "Картридж"),
            ("тонер", "Тонер"),
        )
        for needle, label in mapping:
            if needle in low:
                return label
        return type_from_param

    title_low = norm_ws(name).casefold().replace("ё", "е")
    checks = (
        ("контейнер для отработанного тонера", "Контейнер для отработанного тонера"),
        ("бункер для отработанного тонера", "Бункер для отработанного тонера"),
        ("ролик переноса", "Ролик переноса"),
        ("лента переноса", "Лента переноса"),
        ("блок переноса", "Блок переноса"),
        ("блок проявки", "Блок проявки"),
        ("ремонтный комплект", "Ремонтный комплект"),
        ("печатающая головка", "Печатающая головка"),
        ("драм-юнит", "Драм-юнит"),
        ("драм-картридж", "Драм-картридж"),
        ("фотобарабан", "Фотобарабан"),
        ("копи-картридж", "Копи-картридж"),
        ("принт-картридж", "Принт-картридж"),
        ("тонер-картридж", "Тонер-картридж"),
        ("девелопер", "Девелопер"),
        ("чернила", "Чернила"),
        ("картридж", "Картридж"),
        ("тонер", "Тонер"),
    )
    for needle, label in checks:
        if needle in title_low:
            return label
    return "Расходный материал"


def _build_originality_sentence(status: str, type_label: str) -> str:
    tl = norm_ws(type_label) or "Расходный материал"
    tl_cf = tl.casefold().replace("ё", "е")
    original_map = {
        "лента переноса": "Оригинальная лента переноса.",
        "печатающая головка": "Оригинальная печатающая головка.",
        "чернила": "Оригинальные чернила.",
        "бункер для отработанного тонера": "Оригинальный бункер для отработанного тонера.",
        "контейнер для отработанного тонера": "Оригинальный контейнер для отработанного тонера.",
        "блок проявки": "Оригинальный блок проявки.",
        "ремонтный комплект": "Оригинальный ремонтный комплект.",
        "ролик переноса": "Оригинальный ролик переноса.",
        "блок переноса": "Оригинальный блок переноса.",
        "драм-юнит": "Оригинальный драм-юнит.",
        "драм-картридж": "Оригинальный драм-картридж.",
        "копи-картридж": "Оригинальный копи-картридж.",
        "принт-картридж": "Оригинальный принт-картридж.",
        "фотобарабан": "Оригинальный фотобарабан.",
        "тонер-картридж": "Оригинальный тонер-картридж.",
        "девелопер": "Оригинальный девелопер.",
        "картридж": "Оригинальный картридж.",
        "тонер": "Оригинальный тонер.",
        "расходный материал": "Оригинальный расходный материал.",
    }
    compatible_map = {
        "лента переноса": "Совместимая лента переноса.",
        "печатающая головка": "Совместимая печатающая головка.",
        "чернила": "Совместимые чернила.",
        "бункер для отработанного тонера": "Совместимый бункер для отработанного тонера.",
        "контейнер для отработанного тонера": "Совместимый контейнер для отработанного тонера.",
        "блок проявки": "Совместимый блок проявки.",
        "ремонтный комплект": "Совместимый ремонтный комплект.",
        "ролик переноса": "Совместимый ролик переноса.",
        "блок переноса": "Совместимый блок переноса.",
        "драм-юнит": "Совместимый драм-юнит.",
        "драм-картридж": "Совместимый драм-картридж.",
        "копи-картридж": "Совместимый копи-картридж.",
        "принт-картридж": "Совместимый принт-картридж.",
        "фотобарабан": "Совместимый фотобарабан.",
        "тонер-картридж": "Совместимый тонер-картридж.",
        "девелопер": "Совместимый девелопер.",
        "картридж": "Совместимый картридж.",
        "тонер": "Совместимый тонер.",
        "расходный материал": "Совместимый расходный материал.",
    }
    if status == "original":
        return original_map.get(tl_cf, f"Оригинальный {tl.lower()}.")
    if status == "compatible":
        return compatible_map.get(tl_cf, f"Совместимый {tl.lower()}.")
    return ""


def _is_consumable_for_originality(source_offer: SourceOffer, name: str, params: list[tuple[str, str]]) -> bool:
    title_low = norm_ws(name).casefold().replace("ё", "е")
    type_low = " ".join(norm_ws(v).casefold().replace("ё", "е") for k, v in params if norm_ws(k).casefold() == "тип")
    hay = f"{title_low} {type_low}".strip()
    needles = (
        "картридж",
        "тонер-картридж",
        "тонер",
        "драм",
        "фотобарабан",
        "девелопер",
        "чернила",
        "печатающая головка",
        "ролик переноса",
        "лента переноса",
        "блок переноса",
        "блок проявки",
        "контейнер для отработанного тонера",
        "бункер для отработанного тонера",
        "ремонтный комплект",
        "копи-картридж",
        "принт-картридж",
    )
    return any(x in hay for x in needles)


def _detect_consumable_originality(source_offer: SourceOffer, name: str, params: list[tuple[str, str]]) -> str:
    if not _is_consumable_for_originality(source_offer, name, params):
        return ""
    return "original"


def _apply_consumable_originality(name: str, params: list[tuple[str, str]], native_desc: str, status: str) -> tuple[str, list[tuple[str, str]], str]:
    if status not in {"original", "compatible"}:
        return name, params, native_desc
    base_name = _strip_originality_suffix(name)
    suffix = "(оригинал)" if status == "original" else "(совместимый)"
    value = "Оригинал" if status == "original" else "Совместимый"
    name_out = f"{base_name} {suffix}"
    params_out = _upsert_param_tuple(params, _ORIGINALITY_PARAM_NAME, value)
    desc_out = norm_ws(native_desc)
    type_label = _detect_consumable_type_label(base_name, params_out)
    sentence = _build_originality_sentence(status, type_label)
    if sentence:
        if not desc_out:
            desc_out = sentence
        elif not _DESC_ORIGINALITY_HEAD_RE.match(desc_out):
            desc_out = _merge_originality_sentence(sentence, desc_out, type_label)
    return name_out, params_out, desc_out


def build_offer_out(source_offer: SourceOffer, *, schema: dict[str, Any], policy: dict[str, Any]) -> OfferOut | None:
    prefix = norm_ws(schema.get("id_prefix") or schema.get("supplier_prefix") or "CP")
    placeholder_picture = norm_ws(schema.get("placeholder_picture") or "")
    vendor_blacklist = {str(x).casefold() for x in (schema.get("vendor_blacklist_casefold") or [])}
    fallback_vendor = norm_ws((((policy.get("vendor_policy") or {}).get("neutral_fallback_vendor")) or ""))

    clean_name = normalize_name(source_offer.name)
    clean_vendor = normalize_vendor(
        source_offer.vendor,
        name=clean_name,
        params=source_offer.params,
        description_text=source_offer.description,
        vendor_blacklist=vendor_blacklist,
        fallback_vendor=fallback_vendor,
    )
    clean_model = normalize_model(clean_name, source_offer.params)

    xml_params = build_params_from_xml(source_offer, schema)
    desc_hint_params = extract_desc_fill_params(
        title=clean_name,
        desc_text=source_offer.description,
        existing_params=[],
    )
    params = _merge_desc_enrichment(xml_params, desc_hint_params)
    params = _ensure_base_params(source_offer=source_offer, params=params, vendor=clean_vendor, model=clean_model, clean_name=clean_name)
    params = _polish_model_param(params, clean_name=clean_name, vendor=clean_vendor)
    params = apply_compat_cleanup(params)
    params = _prune_low_value_params(params)

    oid = build_offer_oid(source_offer.vendor_code, source_offer.raw_id, prefix=prefix)
    if not oid:
        return None

    pictures = collect_picture_urls(source_offer.picture_urls, placeholder_picture=placeholder_picture)
    available = normalize_available(source_offer.available_attr, source_offer.available_tag, source_offer.active)
    price_in = normalize_price_in(source_offer.price_text)
    native_desc = _build_native_desc(clean_name=clean_name, source_offer=source_offer, params=params)

    params_tuples = [(norm_ws(p.name), norm_ws(p.value)) for p in params if norm_ws(p.name) and norm_ws(p.value)]
    originality_status = _detect_consumable_originality(source_offer, clean_name, params_tuples)
    clean_name, params_tuples, native_desc = _apply_consumable_originality(
        clean_name,
        params_tuples,
        native_desc,
        originality_status,
    )

    return OfferOut(
        oid=oid,
        available=available,
        name=clean_name,
        price=price_in,
        pictures=pictures,
        vendor=clean_vendor,
        params=params_tuples,
        native_desc=native_desc,
    )


def build_offers(source_offers: list[SourceOffer], *, schema: dict[str, Any], policy: dict[str, Any]) -> tuple[list[OfferOut], BuildStats]:
    out: list[OfferOut] = []
    stats = BuildStats(before=len(source_offers), after=0)
    placeholder_picture = norm_ws(schema.get("placeholder_picture") or "")

    for src in source_offers:
        offer = build_offer_out(src, schema=schema, policy=policy)
        if offer is None:
            stats.filtered_out += 1
            continue
        if not src.picture_urls:
            stats.missing_picture_count += 1
        if offer.pictures and placeholder_picture and offer.pictures[0] == placeholder_picture:
            stats.placeholder_picture_count += 1
        if not norm_ws(offer.vendor):
            stats.empty_vendor_count += 1
        out.append(offer)

    stats.after = len(out)
    return out, stats
