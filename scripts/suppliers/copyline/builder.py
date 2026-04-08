# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/copyline/builder.py
CopyLine builder layer.

Что изменено в этой версии:
- builder больше не кормит extractor narrative-cleaned текстом;
- для extraction используется отдельный very-light `extract_desc`;
- для показа используется отдельный `display_desc`;
- поддержаны новые source-каналы:
  - raw_desc
  - raw_desc_pairs
  - raw_table_params
- сохранена backward-safe совместимость со старым payload:
  - desc
  - params

Главная идея:
- text-for-data и text-for-display больше не смешиваются;
- главный extractor работает по `extract_desc`, а не по `cleaned_desc`.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence, Tuple

from cs.core import OfferOut
from suppliers.copyline.compat import reconcile_copyline_params
from suppliers.copyline.desc_clean import clean_description
from suppliers.copyline.desc_extract import extract_desc_params
from suppliers.copyline.normalize import normalize_source_basics
from suppliers.copyline.params_page import extract_page_params
from suppliers.copyline.pictures import full_only_if_present, prefer_full_product_pictures


BRAND_HINTS: tuple[tuple[str, str], ...] = (
    (r"\bKonica[- ]?Minolta\b", "Konica-Minolta"),
    (r"\bToshiba\b", "Toshiba"),
    (r"\bRicoh\b", "Ricoh"),
    (r"\bRICOH\b", "Ricoh"),
    (r"\bPanasonic\b", "Panasonic"),
    (r"\bКАТЮША\b", "КАТЮША"),
    (r"\bKATYUSHA\b", "КАТЮША"),
    (r"\bXerox\b", "Xerox"),
    (r"\bCanon\b", "Canon"),
    (r"\bSamsung\b", "Samsung"),
    (r"\bKyocera\b", "Kyocera"),
    (r"\bBrother\b", "Brother"),
    (r"\bEpson\b", "Epson"),
    (r"\bLexmark\b", "Lexmark"),
    (r"\bRISO\b", "RISO"),
    (r"\bHP\b", "HP"),
)

CODE_SCORE_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^(?:CF|CE|CB|CC|Q|W)\d", re.I), 100),
    (re.compile(r"^(?:106R|006R|108R|113R|013R)\d", re.I), 100),
    (re.compile(r"^016\d{6}$", re.I), 95),
    (re.compile(r"^(?:MLT-|CLT-|TK-|KX-FA|KX-FAT|C-?EXV|DR-|TN-|C13T|C12C|C33S|T-)", re.I), 95),
    (re.compile(r"^ML-D\d", re.I), 90),
    (re.compile(r"^ML-\d{4,5}[A-Z]\d?$", re.I), 85),
)

_ORIGINALITY_PARAM_NAME = "Оригинальность"
_NAME_ORIGINALITY_SUFFIX_RE = re.compile(r"\s*\((?:оригинал|совместимый)\)\s*$", re.I)
_DESC_ORIGINALITY_HEAD_RE = re.compile(r"(?iu)^\s*(?:Оригинальн(?:ый|ая|ое|ые)|Совместим(?:ый|ая|ое|ые))")
_RAW_ORIGINAL_RE = re.compile(
    r"(?iu)(?<![A-Za-zА-Яа-яЁё])(?:оригинал|original)(?![A-Za-zА-Яа-яЁё])"
)
_STRIP_ORIGINAL_TOKEN_RE = re.compile(
    r"(?iu)(?:(?<=^)|(?<=[\s(\[\{,.;:!?\-–—/]))(?:оригинал|original)(?=$|(?=[\s)\]\},.;:!?\-–—/]))"
)

_COPYLINE_SEO_MODEL_CODE_PARAM_NAMES = ("Модель", "Коды расходников")
_COPYLINE_SEO_COMPAT_PARAM_NAMES = ("Совместимость",)
_COPYLINE_SEO_RESOURCE_PARAM_NAMES = ("Ресурс", "Количество страниц (5% заполнение)")
_COPYLINE_SEO_TECH_PARAM_NAMES = ("Технология печати",)
_COPYLINE_BAD_CONSUMABLE_DESC_RE = re.compile(
    r"(?iu)(?:"
    r"картридж\s+фотобарабана|"
    r"drum\s*unit\s*-\s*блок\s+барабана|"
    r"применяется\s+в\s+мфу|"
    r"ресурс\s+печати\s*[-:]|"
    r"об[ъь]ем\s*[-:]|"
    r"оригинальн(?:ые|ый|ая|ое)\s+чернила\s+оригинальн"
    r")"
)
_TITLE_AFTERMARKET_BRAND_RE = re.compile(r"(?iu)\b(Europrint(?:\s+Business)?|Hi-Black)\b")
_TITLE_RESOURCE_RE = re.compile(r"(?iu)\b(\d+(?:[.,]\d+)?)\s*(мл|ml|k)\b")


# ----------------------------- basic helpers -----------------------------

def safe_str(x: object) -> str:
    """Безопасно привести значение к строке."""
    return str(x).strip() if x is not None else ""


def _norm_spaces(text: str) -> str:
    """Лёгкая нормализация текста без narrative-cleaning."""
    s = safe_str(text).replace("\xa0", " ")
    s = re.sub(r"\r\n?", "\n", s)
    s = re.sub(r"[ \t\f\v]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _mk_oid(sku: str) -> str:
    """Стабильный OID по supplier SKU."""
    sku = safe_str(sku)
    sku = re.sub(r"[^A-Za-z0-9\-\._/]", "", sku)
    return "CL" + sku


def _merge_params(*blocks: Sequence[Tuple[str, str]]) -> list[Tuple[str, str]]:
    """Мягко склеить param-блоки без дублей."""
    out: list[Tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        for key, value in block or []:
            k = safe_str(key)
            v = safe_str(value)
            if not k or not v:
                continue
            sig = (k.casefold(), v.casefold())
            if sig in seen:
                continue
            seen.add(sig)
            out.append((k, v))
    return out


def _coerce_pairs(items: Iterable[object]) -> list[Tuple[str, str]]:
    """Нормализовать список сырых pair-элементов к (key, value)."""
    out: list[Tuple[str, str]] = []
    for item in items or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            key = safe_str(item[0])
            value = safe_str(item[1])
        elif isinstance(item, dict):
            key = safe_str(item.get("key") or item.get("name"))
            value = safe_str(item.get("value") or item.get("val"))
        else:
            continue
        if key and value:
            out.append((key, value))
    return out


def _build_extract_desc(raw_desc: str) -> str:
    """
    Подготовить text-for-data.

    ВАЖНО:
    - это НЕ narrative-cleaning;
    - здесь нельзя рано резать теххвост и секции;
    - задача только сделать body пригодным для extraction.
    """
    s = _norm_spaces(raw_desc)
    if not s:
        return ""

    # Убираем только совсем шумные повторяющиеся строки-заполнители.
    lines: list[str] = []
    prev = ""
    for raw in s.splitlines():
        line = _norm_spaces(raw)
        if not line:
            lines.append("")
            prev = ""
            continue
        if line.casefold() == prev.casefold():
            continue
        lines.append(line)
        prev = line

    s = "\n".join(lines)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _is_numeric_model(value: str) -> bool:
    return bool(re.fullmatch(r"\d+", safe_str(value)))


def _is_allowed_numeric_code(value: str) -> bool:
    return bool(re.fullmatch(r"016\d{6}", safe_str(value)))


def _code_score(code: str) -> int:
    token = safe_str(code)
    for rx, score in CODE_SCORE_PATTERNS:
        if rx.search(token):
            return score
    if _is_allowed_numeric_code(token):
        return 95
    return 10


def _first_code_from_params(params: Sequence[Tuple[str, str]]) -> str:
    """Взять лучший код из уже собранных params."""
    best_code = ""
    best_score = -1
    for key, value in params:
        if safe_str(key) != "Коды расходников":
            continue
        parts = [x.strip() for x in re.split(r"\s*,\s*", safe_str(value)) if x.strip()]
        for part in parts:
            score = _code_score(part)
            if score > best_score:
                best_score = score
                best_code = part
    return best_code


def _infer_vendor_from_text(text: str) -> str:
    """Грубый vendor-hint из текста."""
    hay = safe_str(text)
    if not hay:
        return ""
    for pattern, vendor in BRAND_HINTS:
        if re.search(pattern, hay, flags=re.I):
            return vendor
    return ""


def _infer_vendor_from_compat(params: Sequence[Tuple[str, str]]) -> str:
    """Попытаться понять vendor по полю совместимости."""
    compat = ""
    for key, value in params:
        if safe_str(key) == "Совместимость":
            compat = safe_str(value)
            break
    return _infer_vendor_from_text(compat)


def _drop_weak_params(params: Sequence[Tuple[str, str]]) -> list[Tuple[str, str]]:
    """Отфильтровать совсем слабые значения."""
    bad_values = {"-", "—", "нет", "n/a", "null"}
    out: list[Tuple[str, str]] = []
    for key, value in params:
        k = safe_str(key)
        v = safe_str(value)
        if not k or not v:
            continue
        if v.casefold() in bad_values:
            continue
        out.append((k, v))
    return out


def _has_consumable_type(params: Sequence[Tuple[str, str]]) -> bool:
    """Понять, является ли товар расходником."""
    consumable_types = {
        "Картридж",
        "Тонер-картридж",
        "Тонер",
        "Драм-картридж",
        "Драм-юнит",
        "Фотобарабан",
        "Девелопер",
        "Чернила",
        "Копи-картридж",
        "Принт-картридж",
        "Ремонтный комплект",
        "Печатающая головка",
        "Контейнер для отработанного тонера",
        "Бункер для отработанного тонера",
    }
    return any(safe_str(key) == "Тип" and safe_str(value) in consumable_types for key, value in params)


def _strip_originality_suffix(name: str) -> str:
    return safe_str(_NAME_ORIGINALITY_SUFFIX_RE.sub("", safe_str(name)))


def _strip_originality_markers(text: str) -> str:
    s = safe_str(text)
    if not s:
        return ""
    s = _STRIP_ORIGINAL_TOKEN_RE.sub("", s)
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\[\s*\]", "", s)
    s = re.sub(r"\{\s*\}", "", s)
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\s+([,.;:])", r"\1", s)
    s = re.sub(r"([,.;:]){2,}", r"\1", s)
    return s.strip(" ,-–—")


def _source_originality_haystack(page: dict, name: str, params: Sequence[Tuple[str, str]]) -> str:
    chunks: list[str] = [safe_str(name)]
    for key in (
        "title",
        "raw_desc",
        "desc",
        "vendor",
        "model",
        "sku",
    ):
        chunks.append(safe_str(page.get(key)))
    for key, value in params:
        chunks.append(f"{safe_str(key)}: {safe_str(value)}")
    for seq_key in ("raw_desc_pairs", "raw_table_params", "params"):
        for k, v in _coerce_pairs(page.get(seq_key) or []):
            chunks.append(f"{k}: {v}")
    return "\n".join(x for x in chunks if x)


def _upsert_param(params: Sequence[Tuple[str, str]], key: str, value: str) -> list[Tuple[str, str]]:
    want = safe_str(key).casefold()
    out: list[Tuple[str, str]] = []
    replaced = False
    for k, v in params:
        if safe_str(k).casefold() == want:
            if not replaced:
                out.append((key, value))
                replaced = True
            continue
        out.append((safe_str(k), safe_str(v)))
    if not replaced:
        out.append((key, value))
    return out


_DESC_FIELD_START_RE = re.compile(
    r"(?iu)^(?:цвет|ресурс|технология(?:\s+печати)?|тип|партномер|модель|код(?:ы)?|совместимость|"
    r"для\s+бренда|гарантия|об[ъь]ем|объём|вес|номер|применение|количество)\s*:"
)


def _strip_leading_type_phrase(desc: str, type_label: str) -> str:
    d = safe_str(desc)
    tl = safe_str(type_label)
    if not d or not tl:
        return d
    pat = re.compile(rf"(?iu)^\s*{re.escape(tl)}(?=$|[\s.,:;()\-–—])")
    m = pat.match(d)
    if not m:
        return d
    rest = d[m.end():].lstrip(" .,:;()-–—")
    return safe_str(rest)


def _merge_originality_sentence(sentence: str, desc: str, type_label: str) -> str:
    s = safe_str(sentence)
    d = _strip_leading_type_phrase(desc, type_label)
    if not s:
        return d
    if not d:
        return s
    if _DESC_FIELD_START_RE.match(d):
        return f"{s} {d}" if s.endswith('.') else f"{s}. {d}"
    s_join = s[:-1] if s.endswith('.') else s
    return f"{s_join} {d}"


def _detect_consumable_type_label(name: str, params: Sequence[Tuple[str, str]]) -> str:
    type_from_param = ""
    for k, v in params:
        if safe_str(k) == "Тип":
            type_from_param = safe_str(v)
            break
    if type_from_param:
        low = type_from_param.casefold().replace("ё", "е")
        mapping = (
            ("контейнер для отработанного тонера", "Контейнер для отработанного тонера"),
            ("бункер для отработанного тонера", "Бункер для отработанного тонера"),
            ("печатающая головка", "Печатающая головка"),
            ("ремонтный комплект", "Ремонтный комплект"),
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

    title_low = safe_str(name).casefold().replace("ё", "е")
    checks = (
        ("контейнер для отработанного тонера", "Контейнер для отработанного тонера"),
        ("бункер для отработанного тонера", "Бункер для отработанного тонера"),
        ("печатающая головка", "Печатающая головка"),
        ("ремонтный комплект", "Ремонтный комплект"),
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
    tl = safe_str(type_label) or "Расходный материал"
    tl_cf = tl.casefold().replace("ё", "е")
    original_map = {
        "печатающая головка": "Оригинальная печатающая головка.",
        "чернила": "Оригинальные чернила.",
        "бункер для отработанного тонера": "Оригинальный бункер для отработанного тонера.",
        "контейнер для отработанного тонера": "Оригинальный контейнер для отработанного тонера.",
        "ремонтный комплект": "Оригинальный ремонтный комплект.",
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
        "печатающая головка": "Совместимая печатающая головка.",
        "чернила": "Совместимые чернила.",
        "бункер для отработанного тонера": "Совместимый бункер для отработанного тонера.",
        "контейнер для отработанного тонера": "Совместимый контейнер для отработанного тонера.",
        "ремонтный комплект": "Совместимый ремонтный комплект.",
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


def _is_consumable_for_originality(page: dict, name: str, params: Sequence[Tuple[str, str]]) -> bool:
    _ = page
    if _has_consumable_type(params):
        return True
    title_low = safe_str(name).casefold().replace("ё", "е")
    needles = (
        "картридж",
        "тонер-картридж",
        "тонер",
        "драм",
        "фотобарабан",
        "девелопер",
        "чернила",
        "печатающая головка",
        "контейнер для отработанного тонера",
        "бункер для отработанного тонера",
        "ремонтный комплект",
    )
    return any(x in title_low for x in needles)


def _detect_consumable_originality(page: dict, name: str, params: Sequence[Tuple[str, str]]) -> str:
    if not _is_consumable_for_originality(page, name, params):
        return ""

    hay = _source_originality_haystack(page, name, params)
    if _RAW_ORIGINAL_RE.search(hay):
        return "original"
    return "compatible"


def _apply_consumable_originality(name: str, params: Sequence[Tuple[str, str]], native_desc: str, status: str) -> tuple[str, list[Tuple[str, str]], str]:
    if status not in {"original", "compatible"}:
        return safe_str(name), list(params), safe_str(native_desc)
    base_name = _strip_originality_markers(_strip_originality_suffix(name))
    suffix = "(оригинал)" if status == "original" else "(совместимый)"
    value = "Оригинал" if status == "original" else "Совместимый"
    name_out = f"{base_name} {suffix}"
    params_out = _upsert_param(params, _ORIGINALITY_PARAM_NAME, value)
    desc_out = _strip_originality_markers(native_desc)
    type_label = _detect_consumable_type_label(base_name, params_out)
    sentence = _build_originality_sentence(status, type_label)
    if sentence:
        if not desc_out:
            desc_out = sentence
        elif not _DESC_ORIGINALITY_HEAD_RE.match(desc_out):
            desc_out = _merge_originality_sentence(sentence, desc_out, type_label)
    return name_out, params_out, desc_out


def _get_param_ci(params: Sequence[Tuple[str, str]], *keys: str) -> str:
    wants = {safe_str(x).casefold() for x in keys if safe_str(x)}
    for k, v in params:
        if safe_str(k).casefold() in wants and safe_str(v):
            return safe_str(v)
    return ""


def _title_aftermarket_brand(title: str) -> str:
    m = _TITLE_AFTERMARKET_BRAND_RE.search(safe_str(title))
    return safe_str(m.group(1)) if m else ""


def _resource_from_title(title: str) -> str:
    m = _TITLE_RESOURCE_RE.search(safe_str(title))
    if not m:
        return ""
    num = safe_str(m.group(1)).replace(',', '.')
    unit = safe_str(m.group(2))
    if unit.lower() == 'ml':
        unit = 'мл'
    elif unit.lower() == 'k':
        unit = 'K'
    return f"{num} {unit}".strip()


def _is_consumable_seo_target(name: str, params: Sequence[Tuple[str, str]]) -> bool:
    return _detect_consumable_type_label(name, params) != "Расходный материал"


def _looks_generic_consumable_desc(desc: str) -> bool:
    body = safe_str(desc)
    if not body:
        return True
    if _DESC_ORIGINALITY_HEAD_RE.fullmatch(body.rstrip('.')):
        return True
    if _COPYLINE_BAD_CONSUMABLE_DESC_RE.search(body):
        return True
    return False


def _build_consumable_seo_intro(name: str, vendor: str, params: Sequence[Tuple[str, str]], status: str) -> str:
    if status not in {"original", "compatible"}:
        return ""

    type_label = _detect_consumable_type_label(name, params)
    if type_label == "Расходный материал":
        return ""

    device_brand = safe_str(vendor) or _get_param_ci(params, "Для бренда", "Бренд")
    aftermarket_brand = _title_aftermarket_brand(name)
    model = _get_param_ci(params, *_COPYLINE_SEO_MODEL_CODE_PARAM_NAMES)
    compat = _get_param_ci(params, *_COPYLINE_SEO_COMPAT_PARAM_NAMES)
    color = _get_param_ci(params, "Цвет")
    resource = _get_param_ci(params, *_COPYLINE_SEO_RESOURCE_PARAM_NAMES) or _resource_from_title(name)
    tech = _get_param_ci(params, *_COPYLINE_SEO_TECH_PARAM_NAMES)

    opener = _build_originality_sentence(status, type_label).rstrip('.')
    head_bits: list[str] = []
    if aftermarket_brand:
        head_bits.append(aftermarket_brand)
    if device_brand and device_brand.casefold() not in ' '.join(head_bits).casefold():
        head_bits.append(device_brand)
    if model and model.casefold() not in ' '.join(head_bits).casefold():
        head_bits.append(model)

    first = opener
    if head_bits:
        first = f"{first} {' '.join(x for x in head_bits if x).strip()}".strip()

    extras: list[str] = []
    if compat:
        extras.append(f"для {compat}")
    if color:
        extras.append(f"цвет — {color}")
    if resource:
        extras.append(f"ресурс — {resource}")
    if tech:
        extras.append(f"технология печати — {tech}")

    if not first:
        return ""
    if not extras:
        return f"{first}."
    return f"{first} {'; '.join(extras)}."


def _apply_consumable_seo_intro(name: str, vendor: str, params: Sequence[Tuple[str, str]], native_desc: str, status: str) -> str:
    body = safe_str(native_desc)
    if not _is_consumable_seo_target(name, params):
        return body
    if body and not _looks_generic_consumable_desc(body):
        return body
    intro = _build_consumable_seo_intro(name, vendor, params, status)
    return intro or body


# ----------------------------- resolve helpers -----------------------------

def _resolve_source_channels(page: dict) -> tuple[str, list[Tuple[str, str]], list[Tuple[str, str]], list[Tuple[str, str]]]:
    """
    Собрать source-каналы с backward-safe совместимостью.

    Возвращает:
    - raw_desc
    - raw_desc_pairs
    - raw_table_params
    - legacy_params
    """
    raw_desc = safe_str(page.get("raw_desc") or page.get("desc"))
    raw_desc_pairs = _coerce_pairs(page.get("raw_desc_pairs") or [])
    raw_table_params = _coerce_pairs(page.get("raw_table_params") or [])
    legacy_params = _coerce_pairs(page.get("params") or [])
    return raw_desc, raw_desc_pairs, raw_table_params, legacy_params


def _resolve_page_basics(page: dict, *, fallback_title: str) -> tuple[str, str, str, str, str, str, list[Tuple[str, str]]]:
    """
    Подготовить basics и развести text-for-data / text-for-display.

    Возвращает:
    - sku
    - title
    - vendor
    - model
    - extract_desc
    - display_desc
    - page_params_input
    """
    sku = safe_str(page.get("sku"))
    source_title = safe_str(page.get("title") or fallback_title)

    raw_desc, raw_desc_pairs, raw_table_params, legacy_params = _resolve_source_channels(page)

    # Главный extractor должен видеть более полный текст, а не уже narrative-cleaned body.
    extract_desc = _build_extract_desc(raw_desc)

    # normalize.py пока ещё backward-safe и сам умеет clean_description внутри.
    # Мы используем из него только basics, а не его `description`.
    basics = normalize_source_basics(
        title=source_title,
        sku=sku,
        description_text=extract_desc or raw_desc,
        params=raw_table_params or raw_desc_pairs or legacy_params,
    )
    title = safe_str(basics.get("title") or source_title)
    vendor = safe_str(basics.get("vendor"))
    model = safe_str(basics.get("model"))

    # display_desc — отдельный слой только для показа.
    display_desc = clean_description(raw_desc)

    # Для текущего контракта params_page ещё нельзя передать provenance отдельно,
    # поэтому аккуратно собираем input здесь, а не в source.py.
    page_params_input = _merge_params(raw_table_params, raw_desc_pairs, legacy_params)

    return sku, title, vendor, model, extract_desc, display_desc, page_params_input


def _repair_model_param(params: Sequence[Tuple[str, str]], model: str) -> list[Tuple[str, str]]:
    """Подстраховать `Модель` лучшим кодом, если там слабое значение."""
    merged = _merge_params(params, [("Модель", model)]) if model else list(params)

    current_model = ""
    for key, value in merged:
        if safe_str(key) == "Модель":
            current_model = safe_str(value)
            break

    if _is_numeric_model(current_model) and not _is_allowed_numeric_code(current_model):
        first_code = _first_code_from_params(merged)
        out: list[Tuple[str, str]] = []
        for key, value in merged:
            if safe_str(key) != "Модель":
                out.append((key, value))
        if first_code:
            out.append(("Модель", first_code))
        return out

    if not current_model:
        first_code = _first_code_from_params(merged)
        if first_code:
            return _merge_params(merged, [("Модель", first_code)])

    return merged


def _resolve_vendor(title: str, vendor: str, params: Sequence[Tuple[str, str]]) -> str:
    """Финально определить vendor по basics → compat → title."""
    resolved = safe_str(vendor)
    if not resolved:
        resolved = _infer_vendor_from_compat(params)
    if not resolved:
        resolved = _infer_vendor_from_text(title)
    return resolved


def _finalize_params(params: Sequence[Tuple[str, str]], vendor: str) -> list[Tuple[str, str]]:
    """Финальный supplier-side cleanup params."""
    merged = list(params)
    if vendor and _has_consumable_type(merged):
        merged = _merge_params(merged, [("Для бренда", vendor)])
    merged = _drop_weak_params(merged)
    merged = reconcile_copyline_params(merged)
    return merged


def _build_pictures(page: dict) -> list[str]:
    """Подготовить supplier pictures."""
    pictures = prefer_full_product_pictures(page.get("pics") or [])
    return full_only_if_present(pictures)


def _resolve_available(_: dict) -> bool:
    """По текущему правилу проекта CopyLine всегда available=true."""
    return True


# ----------------------------- main builder -----------------------------

def build_offer_from_page(page: dict, *, fallback_title: str = "") -> OfferOut | None:
    """Собрать raw OfferOut из page-payload."""
    sku, title, vendor, model, extract_desc, display_desc, page_params_input = _resolve_page_basics(
        page,
        fallback_title=fallback_title,
    )
    if not sku or not title:
        return None

    # Главный extractor должен работать по text-for-data.
    page_params = extract_page_params(
        title=title,
        description=extract_desc,
        page_params=page_params_input,
    )

    # Fill-missing слой тоже работает по text-for-data, а не по display narrative.
    desc_params = extract_desc_params(
        title=title,
        description=extract_desc,
        existing_params=page_params,
    )

    params = _merge_params(page_params, desc_params)
    params = _repair_model_param(params, model)
    vendor = _resolve_vendor(title, vendor, params)
    params = _finalize_params(params, vendor)

    originality_status = _detect_consumable_originality(page, title, params)
    title, params, native_desc = _apply_consumable_originality(
        title,
        params,
        display_desc or title,
        originality_status,
    )
    native_desc = _apply_consumable_seo_intro(title, vendor, params, native_desc, originality_status)

    pictures = _build_pictures(page)
    raw_price = int(page.get("price_raw") or 0)
    available = _resolve_available(page)

    return OfferOut(
        oid=_mk_oid(sku),
        available=available,
        name=title,
        price=raw_price,
        pictures=pictures,
        vendor=vendor,
        params=params,
        native_desc=native_desc,
    )
