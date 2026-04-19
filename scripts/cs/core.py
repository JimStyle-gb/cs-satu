# -*- coding: utf-8 -*-
"""
Path: scripts/cs/core.py

CS Core — shared final/raw orchestration layer.

Что делает:
- держит общий OfferOut и final/raw orchestration;
- применяет только shared post-rules, одинаковые для всех поставщиков;

Что не делает:
- не должен хранить supplier-specific repairs;
- не должен чинить raw вместо supplier-layer.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo
import os
import hashlib
import re

# Числа для парсинга float/int (вес/объём/габариты и т.п.)
_RE_NUM = re.compile(r"(\d+(?:[\.,]\d+)?)")
_RE_DIM_SEP = re.compile(r"(?:[xх×*/]|\bto\b)")  # 10x20, 10×20, 10х20, 10/20, 10 to 20
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .keywords import build_keywords, CS_KEYWORDS_MAX_LEN
from .description import build_description
from .pricing import compute_price, CS_PRICE_TIERS
from .category_map import resolve_category_id
from .meta import now_almaty, next_run_at_hour
from .validators import validate_cs_yml
from .util import norm_ws, safe_int, _truncate_text
from .writer import (
    xml_escape_text,
    xml_escape_attr,
    bool_to_xml,
    xml_escape,
    make_header,
    make_footer,
    ensure_footer_spacing,
    make_feed_meta,
    build_cs_feed_xml,
    build_cs_feed_xml_raw,
    write_if_changed,
)

# Back-compat guard: адаптеры импортируют OfferOut из cs.core
# Если вы случайно удалили OfferOut — верните полный core.py.

# Fallback: если кто-то случайно удалит импорт, всё равно будет лимит
CS_KEYWORDS_MAX_LEN_FALLBACK = 380
def _dedup_keep_order(items: list[str]) -> list[str]:
    """CS: дедупликация со стабильным порядком (без сортировки)."""
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if not x:
            continue
        k = x.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out

def _cs_norm_url(u: str) -> str:
    # CS: нормализуем URL картинок (пробелы ломают загрузку)
    return (u or "").replace(" ", "%20").replace("\t", "%20")

# Регексы для fix_text (компилируем один раз)
_RE_SHUKO = re.compile(r"\bShuko\b", flags=re.IGNORECASE)
_RE_MULTI_NL = re.compile(r"\n{3,}")
_RE_MULTI_SP = re.compile(r"[ \u00a0]{2,}")
# Регексы: мусорные имена параметров (цифры/числа/Normal) — включается через env
_RE_TRASH_PARAM_NAME_NUM = re.compile(r"^[0-9][0-9\s\.,]*$")

# Флаги поведения shared-слоя
CS_DROP_TRASH_PARAM_NAMES = (os.getenv("CS_DROP_TRASH_PARAM_NAMES", "0") or "0").strip() == "1"

# Дефолты (используются адаптерами)
OUTPUT_ENCODING_DEFAULT = "utf-8"
CURRENCY_ID_DEFAULT = "KZT"
ALMATY_TZ = "Asia/Almaty"
CORE_FILE = Path(__file__).resolve()
PROJECT_ROOT = CORE_FILE.parents[2]
DOCS_RAW_DIR = PROJECT_ROOT / "docs" / "raw"

# --- CS: Совместимость (только безопасно и только где нужно) ---
_COMPAT_PARAM_NAME = "Совместимость"
_COMPAT_ALIAS_NAMES = {
    "совместимость с моделями",
    "совместимость с принтерами",
    "совместимые модели",
    "для принтеров",
    "для принтера",
    "принтер",
    "принтеры",
    "применение",
    "подходит для",
    "совместимость",
}

_PARTNUMBER_PARAM_NAMES = {
    "партномер",
    "partnumber",
    "part number",
    "part no",
    "pn",
    "код производителя",
    # VTT/другие: часто приходят так
    "oem-номер",
    "oem номер",
    "каталожный номер",
    "кат. номер",
    "каталожный №",
    "кат. №",
}

# Типы, где совместимость реально уместна (расходники)
_COMPAT_TYPE_HINTS = (
    "картридж",
    "тонер",
    "тонер-картридж",
    "драм",
    "драм-юнит",
    "драм-картридж",
    "фотобарабан",
    "барабан",
    "чернила",
    "печатающая головка",
    "девелопер",
    "термопленка",
)

def _cs_is_consumable(name_full: str, params: list[tuple[str, str]]) -> bool:
    """
    Core больше не определяет "это расходник или нет" по name/params.

    По правилу CS:
    - любые supplier-specific heuristics по расходникам живут в adapter/compat.py;
    - shared core не должен чинить raw и не должен ветвиться по логике "если это расходник".

    Функция оставлена только для backward-safe совместимости со старым кодом.
    """
    _ = name_full
    _ = params
    return False

def _cs_is_consumable_code_token(tok: str) -> bool:
    t = (tok or "").strip().strip(" ,;./()[]{}").upper()
    if not t:
        return False
    # чистые числа 6–9 знаков
    if re.fullmatch(r"\d{6,9}", t):
        return True
    # Xerox: 106R02773 / 113R00780 / 008R13041
    if re.fullmatch(r"\d{3}R\d{5}", t):
        return True
    # Epson: C13T00R140 / C13T66414A и т.п.
    if re.fullmatch(r"C\d{2}T[0-9A-Z]{5,8}", t):
        return True

    # Canon: C-EXV34 / NPG-59 / GPR-53
    if re.fullmatch(r"C-?EXV\d{1,3}", t.replace(" ", "").replace("-", "")):
        return True
    if re.fullmatch(r"(?:NPG|GPR)-?\d{1,3}", t.replace(" ", "")):
        return True

    # HP ink: 3ED77A / 1VK08A
    if re.fullmatch(r"\d[A-Z]{2}\d{2}[A-Z]{1,2}", t):
        return True
    # Canon OEM: 0287C001 / 0491C001AA и т.п.
    if re.fullmatch(r"\d{4}[A-Z]\d{3}[A-Z]{0,2}", t):
        return True
    # HP: CF283A / CE285A / W1106A и т.п.
    if re.fullmatch(r"(?:CF|CE|CB|CC|Q|W)\d{3,5}[A-Z]{0,3}", t):
        return True
    # Canon T-коды (T06/T07/...) — код расходника (важно: не путать с T3000/T5200 и т.п.)
    if re.fullmatch(r"T0\d", t):
        return True

    # ML-коды: ML-1710D3 / ML-1210D3 / ML-D1630A
    if re.fullmatch(r"ML-?\d{3,5}D\d{1,2}", t):
        return True
    if re.fullmatch(r"ML-?D\d{3,5}[A-Z]{0,2}", t):
        return True

    # HP/Canon: C7115A / C9730A / C8543X (но не C11... — это SKU техники)
    if re.fullmatch(r"C\d{4}[A-Z]{0,3}", t) and (not t.startswith("C11")):
        if re.fullmatch(r"C\d{4}", t):
            return False
        if re.fullmatch(r"C\d{4}(?:DN|DW|DWF|FDN|FDW|MFP)$", t):
            return False
        return True
    # Kyocera: TK-1150 / TK1150
    if re.fullmatch(r"TK-?\d{3,5}[A-Z]{0,3}", t):
        return True
    # Brother: TN-2375 / TN2375 / DR-2335 / DR2335
    if re.fullmatch(r"(?:TN|DR)-?\d{3,5}[A-Z]{0,3}", t):
        return True
    # Samsung: MLT-D111S / CLT-K404S
    if re.fullmatch(r"(?:MLT|CLT)-[A-Z]?\d{3,5}[A-Z]{0,3}", t):
        return True
    # Canon/HP short codes: 710H / 051H / 056H / 126A / 435A
    if re.fullmatch(r"\d{3,4}[AHX]", t):
        return True
    return False

def _cs_looks_like_consumable_code_list(s: str) -> bool:
    """
    Core не должен угадывать списки кодов расходников в тексте.
    Это обязанность supplier-layer.

    Оставлено как backward-safe no-op.
    """
    _ = s
    return False

def _cs_expand_consumable_code_ranges(s: str) -> str:
    """Раскрывает диапазоны кодов вида T0481–T0486 → T0481 T0482 ... T0486.
    Делает это только для безопасных коротких диапазонов (<=20).
    """
    if not s:
        return ""
    # унифицируем тире
    t = s.replace("–", "-").replace("—", "-")
    # Пример: T0481- T0486
    def _repl(m: re.Match) -> str:
        a = (m.group("a") or "").upper()
        b = (m.group("b") or "").upper()
        # отделяем буквенную и цифровую часть
        ma = re.match(r"^([A-Z]{1,3})(\d{3,6})$", a)
        mb = re.match(r"^([A-Z]{1,3})(\d{3,6})$", b)
        if not ma or not mb:
            return m.group(0)
        p1, n1 = ma.group(1), ma.group(2)
        p2, n2 = mb.group(1), mb.group(2)
        if p1 != p2:
            return m.group(0)
        if len(n1) != len(n2):
            return m.group(0)
        i1 = int(n1)
        i2 = int(n2)
        if i2 < i1:
            return m.group(0)
        if (i2 - i1) > 20:
            return m.group(0)
        width = len(n1)
        out = [f"{p1}{str(i).zfill(width)}" for i in range(i1, i2 + 1)]
        return " " + " ".join(out) + " "
    t = re.sub(r"(?i)\b(?P<a>[A-Z]{1,3}\d{3,6})\s*-\s*(?P<b>[A-Z]{1,3}\d{3,6})\b", _repl, t)
    return t

# CS: извлекаем коды расходников (в исходном порядке) из текста. Никаких моделей техники сюда не пускаем.

def _cs_strip_consumable_codes_from_text(text: str, allow_short_3dig: bool = True) -> str:
    """
    Core не чистит текст от кодов расходников.
    Если supplier-layer отдал raw с такими хвостами — это ошибка адаптера.

    Оставлено как backward-safe pass-through.
    """
    _ = allow_short_3dig
    return norm_ws(text)

def _cs_clean_compat_value(v: str) -> str:
    s = (v or "").strip()
    if not s:
        return ""
    # HTML → текст
    s = _COMPAT_HTML_TAG_RE.sub(" ", s)
    s = norm_ws(s)

    # убираем маркетинг/служебку
    s = re.sub(r"(?i)\bбез\s+чипа\b", " ", s)

    s = re.sub(r"(?i)\b(?:новинка|распродажа|акция|хит|sale|new)\b", " ", s)

    # убираем префиксы/служебные слова, которые часто прилетают от поставщиков
    s = re.sub(r"(?i)\bприменени[ея]\s*[:\-]\s*", " ", s)
    s = re.sub(r"(?i)\bдля\s+принтер[а-я]*\b\s*[:\-]?\s*", " ", s)

    # если в строке есть явный хинт бренда/линейки — оставляем хвост с первой модели (режем "Применение: ...")
    m = _RE_COMPAT_DEVICE_HINT.search(s)
    if m and m.start() > 0:
        s = s[m.start():]

    # из совместимости вырезаем цвет/страницы/единицы — это НЕ модели устройств
    s = re.sub(r"(?i)\b(?:ч[её]рн\w*|голуб\w*|ж[её]лт\w*|желт\w*|магент\w*|пурпур\w*|сер\w*|цветн\w*|пигмент\w*)\b", " ", s)
    s = re.sub(r"(?i)\b(?:black|cyan|magenta|yellow|grey|gray)\b", " ", s)
    s = re.sub(r"(?i)\b(?:LC|LM|LK|MBK|PBK|Bk|C|M|Y|K)\b", " ", s)
    s = re.sub(r"(?i)\b\d+\s*(?:стр\.?|страниц\w*|pages?)\b", " ", s)

    s = norm_ws(s)

    # режем типовые "Ресурс: 1600 стр" / "yield 7.3K" и т.п.
    s = re.sub(r"(?i)\bресурс\b\s*[:\-]?\s*\d+(?:[.,]\d+)?\s*(?:k|к|стр\.?|страниц\w*|pages?)\b", " ", s)
    s = re.sub(r"(?i)\byield\b\s*[:\-]?\s*\d+(?:[.,]\d+)?\s*k\b", " ", s)

    # отдельный кейс вида 29млХ3шт (убираем полностью)
    s = re.sub(r"(?i)\b\d+\s*(?:мл|ml)\s*[xхXХ]\s*\d+\s*(?:шт|pcs|pieces)\b", " ", s)

    # чистим по фрагментам (важно: запятая-разделитель, но НЕ десятичная 2,4K)
    parts = re.split(r"[\n;]+|\s*(?:(?<!\d),|,(?!\d))\s*", s)
    cleaned: list[str] = []
    for p in parts:
        p = _clean_compat_fragment(p)
        p = norm_ws(p).strip(" ,;/:-")
        if not p:
            continue
        if not _is_valid_compat_fragment(p):
            continue
        cleaned.append(p)

    cleaned = _dedup_keep_order(cleaned)
    out = ", ".join(cleaned).strip()
    out = norm_ws(out)
    if not out:
        return ""
    # безопасность по длине (дальше всё равно тримится до 260 в clean_params)
    if len(out) > 600:
        return ""
    return out

def _cs_trim_compat_to_max(v: str, max_len: int = 260) -> str:
    """Обрезает совместимость безопасно, не разрезая модель на середине.
    Стараемся обрезать по последней запятой/точке с запятой/пробелу в пределах max_len,
    затем удаляем возможные обрывки вида '/P1' на конце.
    """
    s = (v or "").strip()
    if not s:
        return ""
    if len(s) <= max_len:
        return s

    cut = s[:max_len]
    # Предпочитаем резать по разделителю списка моделей
    pos = cut.rfind(", ")
    if pos >= 40:
        cut = cut[:pos]
    else:
        pos = cut.rfind("; ")
        if pos >= 40:
            cut = cut[:pos]
        else:
            pos = cut.rfind(" ")
            if pos >= 40:
                cut = cut[:pos]

    cut = cut.rstrip(" ,;/.-")
    # Удаляем короткий обрывок после '/', если он начинается с буквы и слишком короткий (например '/P1')
    cut = re.sub(r"/(?=[A-Za-zА-Яа-я])[A-Za-zА-Яа-я0-9]{1,2}$", "", cut).rstrip(" ,;/.-")
    # И короткий обрывок после запятой/пробела (например ', M1')
    cut = re.sub(r"(?:,|\s)+(?=[A-Za-zА-Яа-я])[A-Za-zА-Яа-я0-9]{1,2}$", "", cut).rstrip(" ,;/.-")
    return cut


def _cs_trim_compat_for_satu_param(v: str, max_len: int = 255) -> str:
    """Короткая версия только для экспортируемого <param name=\"Совместимость\">.

    ВАЖНО:
    - не меняет исходные params товара;
    - полная совместимость остаётся доступной для description/SEO;
    - ограничение применяется только в final XML под лимит Satu.
    """
    s = norm_ws(v)
    if not s:
        return ""
    if len(s) <= max_len:
        return s

    suffix = " и др."
    body_limit = max(40, max_len - len(suffix))
    cut = _cs_trim_compat_to_max(s, body_limit).rstrip(" ,;/.-")
    if not cut:
        cut = s[:body_limit].rstrip(" ,;/.-")
    elif len(cut) > body_limit:
        cut = cut[:body_limit].rstrip(" ,;/.-")

    if cut:
        cut = f"{cut}{suffix}"
    return cut[:max_len].rstrip(" ,;/.-")


def _cs_merge_compat_values(vals: list[str]) -> str:
    parts: list[str] = []
    for v in vals:
        v = _cs_clean_compat_value(v)
        if not v:
            continue
        # дробим по запятым/точкам с запятой/переводам строк
        for p in re.split(r"[\n;]+|\s*(?:(?<!\d),|,(?!\d))\s*", v):  # запятая-разделитель, но НЕ десятичная 2,4K
            p = _cs_clean_compat_value(p)
            if not p:
                continue
            parts.append(p)
    parts = _dedup_keep_order(parts)
    out = ", ".join(parts).strip()
    if len(out) > 600:
        return ""
    return out


def normalize_offer_name(name: str) -> str:
    # CS: лёгкая типографика имени (без изменения смысла)
    s = norm_ws(name)
    # CS: орфография (частая опечатка)
    s = re.sub(r"(?i)\bmaintance\b", "Maintenance", s)
    if not s:
        return ""
    # "дляPantum" -> "для Pantum"
    s = re.sub(r"\bдля(?=[A-ZА-Я])", "для ", s)
    # "(аналогDL-5120)" -> "(аналог DL-5120)" (только если далее заглавная/цифра)
    s = re.sub(r"(?i)\bаналог(?=[A-ZА-Я0-9])", "аналог ", s)
    # двойной слэш в моделях
    s = s.replace("//", "/")
    # ",Color" -> ", Color"
    s = re.sub(r"(?i),\s*color\b", ", Color", s)
    # убрать пробелы перед знаками
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    # CS: добавить пробел после запятой/точки с запятой (если дальше буква)
    s = re.sub(r",(?=[A-Za-zА-Яа-яЁё])", ", ", s)
    s = re.sub(r";(?=[A-Za-zА-Яа-яЁё])", "; ", s)
    # убрать лишние пробелы внутри скобок
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    # хвостовая запятая
    s = re.sub(r",\s*$", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    # CS: чинить смешение кириллицы/латиницы в имени
    s = fix_mixed_cyr_lat(s)
    return s

_RE_COLOR_TOKENS = [
    ("Голубой", re.compile(r"\bcyan\b", re.IGNORECASE)),
    ("Пурпурный", re.compile(r"\bmagenta\b", re.IGNORECASE)),
    ("Желтый", re.compile(r"\byellow\b", re.IGNORECASE)),
    ("Черный", re.compile(r"\bblack\b", re.IGNORECASE)),
    ("Серый", re.compile(r"\bgr(?:a|e)y\b", re.IGNORECASE)),
    # RU
    ("Голубой", re.compile(r"\bголуб(?:ой|ая|ое|ые|ого|ому|ым|ых)\b", re.IGNORECASE)),
    ("Пурпурный", re.compile(r"\bпурпурн(?:ый|ая|ое|ые|ого|ому|ым|ых)\b", re.IGNORECASE)),
    ("Пурпурный", re.compile(r"\bмаджент(?:а|овый|овая|овое|овые)\b", re.IGNORECASE)),
    ("Пурпурный", re.compile(r"\bмалин(?:овый|овая|овое|овые|ого|ому|ым|ых)\b", re.IGNORECASE)),
    ("Желтый", re.compile(r"\bжелт(?:ый|ая|ое|ые|ого|ому|ым|ых)\b", re.IGNORECASE)),
    ("Черный", re.compile(r"\bчерн(?:ый|ая|ое|ые|ого|ому|ым|ых)\b", re.IGNORECASE)),
    ("Серый", re.compile(r"\bсер(?:ый|ая|ое|ые|ого|ому|ым|ых)\b", re.IGNORECASE)),
]

_RE_HI_BLACK = re.compile(r"\bhi[-\s]?black\b", re.IGNORECASE)

def _compat_fragments(s: str) -> list[str]:
    # CS: разбиваем строку совместимости на фрагменты (стабильно)
    s = norm_ws(s)
    if not s:
        return []
    # унифицируем разделители
    s = s.replace(";", ",").replace("|", ",")
    parts = [norm_ws(p) for p in s.split(",")]
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if not p:
            continue
        # нормализуем пробелы вокруг слэшей, чтобы одинаковые списки схлопывались
        p = _COMPAT_SLASH_SPACES_RE.sub("/", p)
        p = _COMPAT_MULTI_SLASH_RE.sub("/", p)
        p = norm_ws(p).strip(" ,;/:-")
        if not p:
            continue
        key = p.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out

def _get_param_value(params: list[tuple[str, str]], key_name: str) -> str:
    kn = key_name.casefold()
    for k, v in params:
        if norm_ws(k).casefold() == kn:
            return norm_ws(v)
    return ""

# CS: ключи, где может жить совместимость (в разных поставщиках)
_COMPAT_KEYS = ("Совместимость", "Совместимые модели", "Для", "Применение")

# CS: фильтрация мусора в совместимости (цвет/объём/служебные слова)
_COMPAT_UNIT_RE = re.compile(r"^\s*(?:\d+\s*(?:[*xх]\s*)\d+|\d+(?:[.,]\d+)?)\s*(?:мл|ml)\b", re.I)
_COMPAT_PARENS_UNIT_RE = re.compile(r"\(\s*(?:\d+\s*(?:[*xх]\s*)\d+|\d+(?:[.,]\d+)?)\s*(?:мл|ml)\s*\)", re.I)
# CS: вычищаем единицы/объём и служебные слова внутри фрагмента (если они встречаются вместе с моделью)
_COMPAT_UNIT_ANY_RE = re.compile(r"(?i)\b(?:\d+\s*(?:[*xх]\s*)\d+|\d+(?:[.,]\d+)?)\s*(?:мл|ml)\b")
_COMPAT_SKIP_ANY_RE = re.compile(r"(?i)\b(?:совместим\w*|compatible|original|оригинал)\b")
# CS: слова/форматы/ОС, которые не должны попадать в "Совместимость" (часто прилетают из ТТХ)
_COMPAT_PAPER_OS_WORD_RE = re.compile(
    r"(?i)\b(?:letter|legal|a[4-6]|b5|c6|dl|no\.\s*10|windows(?:\s*\d+)?|mac\s*os|linux|android|ios|"
    r"конверт\w*|дуплекс|формат|выбрать\s*формат|paper\s*tray\s*capacity)\b"
)
# CS: размеры/соотношения сторон — тоже мусор для совместимости
_COMPAT_DIM_TOKEN_RE = re.compile(r"(?i)\b\d+\s*[xх]\s*\d+\b|\b\d+\s*см\b|\b16:9\b")
# CS: шумовые слова, которые иногда попадают в совместимость (цвета/маркетинг/описания)
_COMPAT_NOISE_IN_COMPAT_RE = re.compile(
    r"(?i)\b(?:euro\s*print|отработанн\w*|чернил\w*|ink|pigment|dye|"
    r"cyan|magenta|yellow|black|grey|gray|matt\s*black|photo\s*black|photoblack|light\s*cyan|light\s*magenta)\b"
)

# CS: мусор в скобках (ресурс/комплект/обрезанные хвосты)
_COMPAT_PARENS_YIELD_PACK_RE = re.compile(r"(?i)\([^)]*(?:\b\d+\s*[kк]\b|\b\d+\s*шт\b|pcs|pieces|yield|страниц|стр\.?|ресурс|увелич)[^)]*\)")
_COMPAT_YIELD_ANY_RE = re.compile(r"(?i)\b\d+\s*[kк]\b")
_COMPAT_PACK_ANY_RE = re.compile(r"(?i)\b\d+\s*шт\b|\b\d+\s*pcs\b|\b\d+\s*pieces\b")
_COMPAT_SLASH_SPACES_RE = re.compile(r"\s*/\s*")
_COMPAT_MULTI_SLASH_RE = re.compile(r"/{2,}")
_COMPAT_HYPHEN_MODEL_RE = re.compile(r"(?<=\D)-(?=\d)")

_COMPAT_COLOR_ONLY_RE = re.compile(
    r"^\s*(?:cyan|magenta|yellow|black|grey|gray|matt\s*black|photo\s*black|photoblack|light\s*cyan|light\s*magenta|"
    r"ч[её]рн(?:ый|ая|ое|ые)?|син(?:ий|яя|ее|ие)?|голуб(?:ой|ая|ое|ые)?|желт(?:ый|ая|ое|ые)?|"
    r"пурпур(?:ный|ная|ное|ные)?|магент(?:а|ы)?|сер(?:ый|ая|ое|ые)?)\s*$",
    re.I,
)
_COMPAT_SKIP_WORD_RE = re.compile(r"^\s*(?:совместим\w*|compatible|original|оригинал)\s*$", re.I)
_COMPAT_NUM_ONLY_RE = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*$")
_COMPAT_NO_CODE_RE = re.compile(r"^\s*(?:№|#)\s*\d{2,}\s*$")

def _clean_compat_fragment(f: str) -> str:
    # CS: чистим один фрагмент совместимости (безопасно)
    f = norm_ws(f)
    if not f:
        return ""

    # нормализуем слэши + модельные дефисы (KM-1620 -> KM 1620)
    f = _COMPAT_SLASH_SPACES_RE.sub("/", f)
    f = _COMPAT_MULTI_SLASH_RE.sub("/", f)
    f = _COMPAT_HYPHEN_MODEL_RE.sub(" ", f)

    # выкидываем цвет/объём/служебные слова
    f = _COMPAT_PARENS_UNIT_RE.sub("", f)
    f = _COMPAT_UNIT_ANY_RE.sub("", f)
    f = _COMPAT_SKIP_ANY_RE.sub("", f)

    # дополнительно: ресурс/комплект в совместимости — мусор
    if CS_COMPAT_CLEAN_YIELD_PACK:
        f = _COMPAT_PARENS_YIELD_PACK_RE.sub("", f)
        f = _COMPAT_YIELD_ANY_RE.sub("", f)
        f = _COMPAT_PACK_ANY_RE.sub("", f)

    # CS: форматы бумаги / ОС / размеры — не должны жить в "Совместимость"
    if CS_COMPAT_CLEAN_PAPER_OS_DIM:
        f = _COMPAT_PAPER_OS_WORD_RE.sub("", f)
        f = _COMPAT_DIM_TOKEN_RE.sub("", f)

    # CS: шумовые слова (цвета/маркетинг/описания) — тоже режем
    if CS_COMPAT_CLEAN_NOISE_WORDS:
        f = _COMPAT_NOISE_IN_COMPAT_RE.sub("", f)

    # если скобки сломаны (обрезан хвост) — режем с последней '('
    if f.count("(") != f.count(")"):
        last = f.rfind("(")
        if last != -1:
            f = f[:last]
        f = f.replace(")", "")

    f = norm_ws(f).strip(" ,;/:-")

    # в совместимости скобки не нужны — убираем остатки, чтобы не было "битых" хвостов
    if "(" in f or ")" in f:
        f = f.replace("(", " ").replace(")", " ")
        f = norm_ws(f).strip(" ,;/:-")

    # CS: иногда поставщик повторяет целый список дважды — режем повтор (часто у NVPrint)
    if CS_COMPAT_CLEAN_REPEAT_BLOCKS and len(f) >= 80:
        f_low = f.casefold()
        pfx = norm_ws(f[:60]).casefold()
        if len(pfx) >= 24:
            pos = f_low.find(pfx, len(pfx))
            if pos != -1:
                f = f[:pos]
                f = norm_ws(f).strip(" ,;/:-")

    # убираем дубли внутри "A/B/C" (частая грязь у поставщиков)
    if "/" in f:
        parts = [norm_ws(x) for x in f.split("/") if norm_ws(x)]
        out: list[str] = []
        seen: set[str] = set()
        for x in parts:
            if CS_COMPAT_CLEAN_PAPER_OS_DIM:
                x = _COMPAT_PAPER_OS_WORD_RE.sub("", x)
                x = _COMPAT_DIM_TOKEN_RE.sub("", x)
            if CS_COMPAT_CLEAN_NOISE_WORDS:
                x = _COMPAT_NOISE_IN_COMPAT_RE.sub("", x)
            x = norm_ws(x).strip(" ,;/:-")
            if not x:
                continue
            k = x.casefold()
            if k in seen:
                continue
            seen.add(k)
            out.append(x)
        f = "/".join(out)

    return f

def _is_valid_compat_fragment(f: str) -> bool:
    """CS: проверка, что фрагмент похож на совместимость (модель/список моделей), а не мусор."""
    f = norm_ws(f)
    if not f:
        return False

    # чисто цвет/служебные слова —
    if _COMPAT_COLOR_ONLY_RE.match(f) or _COMPAT_SKIP_WORD_RE.match(f):
        return False

    # чисто единицы/объём —
    if _COMPAT_UNIT_RE.match(f):
        return False

    # голые числа/номера —
    if _COMPAT_NUM_ONLY_RE.match(f) or _COMPAT_NO_CODE_RE.match(f):
        return False

    # форматы бумаги / ОС / размеры — не совместимость принтеров
    if CS_COMPAT_CLEAN_PAPER_OS_DIM:
        if _COMPAT_PAPER_OS_WORD_RE.search(f) or _COMPAT_DIM_TOKEN_RE.search(f):
            return False

    # должна быть цифра (модели почти всегда с цифрами)
    if not re.search(r"\d", f):
        return False

    # и буква (чтобы не ловить голые числа)
    if not re.search(r"[A-Za-zА-Яа-я]", f):
        return False

    # слишком коротко — почти наверняка мусор
    if len(f) < 4:
        return False

    return True

_COMPAT_MODEL_TOKEN_RE = re.compile(r"(?i)\b[A-ZА-Я]{1,6}\s*\d{2,5}[A-ZА-Я]?\b")
_COMPAT_TEXT_SPLIT_RE = re.compile(r"[\n\r\.\!\?]+")
_COMPAT_HTML_TAG_RE = re.compile(r"<[^>]+>")

def _shorten_smart_name(name: str, params: list[tuple[str, str]], max_len: int) -> str:
    # CS: Универсально — делаем короткое имя без потери кода/смысла.
    # Полная совместимость остаётся в param "Совместимость" (обогащённая из name/desc/params).
    name = norm_ws(name)
    if len(name) <= max_len:
        return name

    compat_full = _get_param_value(params, "Совместимость")
    frags = _compat_fragments(compat_full)

    # Пытаемся выделить "префикс" до "для ..."
    low = name.casefold()
    pfx = name
    tail_sep = ""
    if " для " in low:
        i = low.find(" для ")
        pfx = norm_ws(name[:i])
        tail_sep = " для "
    elif "для " in low:
        # на случай если без пробелов
        i = low.find("для ")
        pfx = norm_ws(name[:i].rstrip())
        tail_sep = " для "

    # Если нет compat в params — берём хвост из name после "для"
    if not frags and tail_sep:
        tail = norm_ws(name[len(pfx) + len(tail_sep):])
        frags = _compat_fragments(tail)

    # Собираем короткую совместимость: уменьшаем число фрагментов, пока не влезет
    # Начинаем с 6, дальше 5..1
    max_items = 6
    while max_items >= 1:
        short = ", ".join(frags[:max_items]) if frags else ""
        if short:
            cand = f"{pfx}{tail_sep}{short} и др."
        else:
            cand = f"{pfx}"
        if len(cand) <= max_len:
            return cand
        max_items -= 1

    # Фоллбэк: просто режем по границе и добавляем "…"
    return _truncate_text(name, max_len, suffix=" и др.")

# Лимиты (по умолчанию):
# - <name> держим коротким и читаемым (150 по решению пользователя)
CS_NAME_MAX_LEN = int((os.getenv("CS_NAME_MAX_LEN", "110") or "110").strip() or "110")

CS_COMPAT_CLEAN_YIELD_PACK = (os.getenv("CS_COMPAT_CLEAN_YIELD_PACK", "1") or "1").strip().lower() not in ("0", "false", "no")
CS_COMPAT_CLEAN_PAPER_OS_DIM = (os.getenv("CS_COMPAT_CLEAN_PAPER_OS_DIM", "1") or "1").strip().lower() not in ("0", "false", "no")
CS_COMPAT_CLEAN_NOISE_WORDS = (os.getenv("CS_COMPAT_CLEAN_NOISE_WORDS", "1") or "1").strip().lower() not in ("0", "false", "no")
CS_COMPAT_CLEAN_REPEAT_BLOCKS = (os.getenv("CS_COMPAT_CLEAN_REPEAT_BLOCKS", "1") or "1").strip().lower() not in ("0", "false", "no")

# Заглушка картинки, если у оффера нет фото (можно переопределить env CS_PICTURE_PLACEHOLDER_URL)
CS_PICTURE_PLACEHOLDER_URL = (os.getenv("CS_PICTURE_PLACEHOLDER_URL") or "https://placehold.co/800x800/png?text=No+Photo").strip()

PARAM_DROP_DEFAULT = {
    "Штрихкод",
    "Штрих-код",
    "Штрих код",
    "EAN",
    "EAN-13",
    "EAN13",
    "Barcode",
    "GTIN",
    "UPC",
    "Артикул",
    "Новинка",
    "Снижена цена",
    "Благотворительность",
    "Код товара Kaspi",
    "Код ТН ВЭД",
    "Назначение",
}
PARAM_DROP_DEFAULT_CF = {str(x).strip().casefold() for x in PARAM_DROP_DEFAULT}

def enforce_name_policy(oid: str, name: str, params: list[tuple[str, str]]) -> str:
    # CS: глобальная политика имени — одинаково для всех поставщиков
    name = norm_ws(name)
    if not name:
        return ""
    if len(name) <= CS_NAME_MAX_LEN:
        return name

    # Универсальное "умное" укорочение
    return _shorten_smart_name(name, params, CS_NAME_MAX_LEN)

def extract_color_from_name(name: str) -> str:
    # CS: цвет берём строго из имени (без ложных совпадений на бренд Hi-Black)
    s = normalize_offer_name(name)
    if not s:
        return ""
    s = _RE_HI_BLACK.sub(" ", s)
    # CS: нормализуем 'ё' → 'е', чтобы ловить 'чёрный/жёлтый'
    s = s.replace("ё", "е").replace("Ё", "Е")
    # CS: явные маркеры "цветной"
    if re.search(r"(?i)\b(cmyk|cmy)\b", s) or re.search(r"(?i)\bцветн\w*\b", s):
        return "Цветной"
    # CS: если "Color" стоит В КОНЦЕ (вариант картриджа), а не в середине (Color LaserJet)
    if re.search(r"(?i)\bcolor\b\s*(?:\)|\]|\}|$)", s):
        return "Цветной"
    # CS: если "Color" идёт перед "для/for" (вариант картриджа), считаем цветной
    if re.search(r"(?i)\bcolor\b\s*(?:для|for)\b", s):
        return "Цветной"
    # CS: если явно указан составной цвет (черный+цвет / black+color) → Цветной
    if re.search(r"\b(черн\w*|black)\b\s*\+\s*\b(цвет(?:н\w*)?|colou?r)\b", s, re.IGNORECASE) or \
       re.search(r"\b(цвет(?:н\w*)?|colou?r)\b\s*\+\s*\b(черн\w*|black)\b", s, re.IGNORECASE):
        return "Цветной"
    found: list[str] = []
    for color, rx in _RE_COLOR_TOKENS:
        if rx.search(s):
            if color not in found:
                found.append(color)
    if not found:
        return ""
    if len(found) > 1:
        return "Цветной"
    return found[0]


_RE_SERVICE_KV = re.compile(
    r"^(?:артикул|каталожный номер|oem\s*-?номер|oem\s*номер|ш?трих\s*-?код|штрихкод|код товара|код производителя|аналоги|аналог)\s*[:\-].*$",
    re.IGNORECASE,
)

def strip_service_kv_lines(text: str) -> str:
    # CS: удаляем служебные строки "Ключ: значение" из текста описания
    raw = fix_text(text or "")
    if not raw:
        return ""
    lines = [ln.strip() for ln in raw.split("\n")]
    keep: list[str] = []
    for ln in lines:
        if not ln:
            continue
        if _RE_SERVICE_KV.match(ln):
            continue
        keep.append(ln)
    return "\n".join(keep).strip()

# Shared core не чинит смешанные кир/лат токены внутри кодов и названий.
# Любая агрессивная правка таких токенов должна жить в supplier-layer.
def fix_mixed_cyr_lat(s: str) -> str:
    return s or ""

# Безопасное int из любого значения
# Нормализация смешанных LAT-CYR токенов с дефисом: "LED-индикаторы" -> "LED индикаторы"
_RE_MIXED_HYPHEN_LAT_CYR = re.compile(r"\b([A-Za-z]{2,}[A-Za-z0-9]*)[\-–—]([А-Яа-яЁё]{2,})\b")
_RE_MIXED_HYPHEN_CYR_LAT = re.compile(r"\b([А-Яа-яЁё]{2,})[\-–—]([A-Za-z]{2,}[A-Za-z0-9]*)\b")
_RE_MIXED_HYPHEN_A1_CYR = re.compile(r"\b([A-Za-z]\d{1,3})[\-–—]([А-Яа-яЁё]{2,})\b")

_RE_MIXED_SLASH_LAT_CYR = re.compile(r"([A-Za-z]{1,}[A-Za-z0-9]*)/([Ѐ-ӿ]{2,})")
_RE_MIXED_SLASH_CYR_LAT = re.compile(r"([Ѐ-ӿ]{2,})/([A-Za-z]{1,}[A-Za-z0-9]*)")

_RE_MIXED_SLASH_LAT_CYR_RE_MIXED_SLASH_LAT_CYR = re.compile(r"([A-Za-z]{1,}[A-Za-z0-9]*)/([Ѐ-ӿ]{2,})")
_RE_MIXED_SLASH_CYR_LAT = re.compile(r"([Ѐ-ӿ]{2,})/([A-Za-z]{1,}[A-Za-z0-9]*)")

_RE_KEEP_LAT_CYR_SLASH = re.compile(r"[A-Z]{1,5}(?:\d{0,3}[A-Z]{0,3})?")

def _mixed_slash_repl_lat_cyr(m: re.Match[str]) -> str:
    left = m.group(1)
    right = m.group(2)
    return f"{left}/{right}"

def _mixed_slash_repl_cyr_lat(m: re.Match[str]) -> str:
    left = m.group(1)
    right = m.group(2)
    return f"{left}/{right}"

def normalize_mixed_slash(s: str) -> str:
    t = s or ""
    if not t:
        return t
    # Только кир/лат переходы: колодка/IEC, CD/банк, ЖК/USB, контактілер/EPO.
    # Лат/лат (RJ11/RJ45) и цифры/лат (4/IEC) не трогаем.
    for _ in range(3):  # на случай нескольких вхождений
        t2 = _RE_MIXED_SLASH_LAT_CYR.sub(_mixed_slash_repl_lat_cyr, t)
        t2 = _RE_MIXED_SLASH_CYR_LAT.sub(_mixed_slash_repl_cyr_lat, t2)
        if t2 == t:
            break
        t = t2
    return t

# Нормализация слэша между разными алфавитами (LAT <-> CYR), включая казахские буквы.
_CYR_CHAR_RE = re.compile(r"[\u0400-\u04FF]")
_LAT_CHAR_RE = re.compile(r"[A-Za-z]")

def _char_script(ch: str) -> str | None:
    if not ch:
        return None
    if _LAT_CHAR_RE.match(ch):
        return "LAT"
    if _CYR_CHAR_RE.match(ch):
        return "CYR"
    return None

def sanitize_mixed_text(s: str) -> str:
    t = fix_mixed_cyr_lat(s)
    # Каз/рус тексты: исправляем короткие смешанные токены (ЖK -> ЖК)
    t = t.replace("ЖK", "ЖК").replace("Жk", "ЖК")
    # Не ломаем техно-токены вида LCD-дисплей, SNMP-карты, Android-приставка.
    # Слэш тоже сохраняем: LX/Улучшенный, Xerox/Карты и т.п.
    return normalize_mixed_slash(t)

def parse_id_set(env_value: str | None, fallback: Iterable[int] | None = None) -> set[str]:
    out: set[str] = set()
    if env_value:
        for part in env_value.split(","):
            p = part.strip()
            if p:
                out.add(p)
    if not out and fallback:
        out = {str(int(x)) for x in fallback}
    return out

# Генератор стабильного id (если у поставщика нет id)

_WGT_WORDS = ("вес", "масса", "weight")
_VOL_WORDS = ("объем", "объём", "volume")
_DIM_WORDS = ("габарит", "размер", "длина", "ширина", "высота")

def _looks_like_weight(name: str) -> bool:
    nl = (name or "").casefold()
    return any(w in nl for w in _WGT_WORDS)

def _looks_like_volume(name: str) -> bool:
    nl = (name or "").casefold()
    return any(w in nl for w in _VOL_WORDS)

def _looks_like_dims(name: str) -> bool:
    nl = (name or "").casefold()
    return any(w in nl for w in _DIM_WORDS)

def _to_float(v: str) -> float | None:
    m = _RE_NUM.search(v or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except Exception:
        return None

def _is_sane_weight(v: str) -> bool:
    x = _to_float(v)
    if x is None:
        return False
    vv = (v or "").casefold()
    # если явно граммы — переводим в кг
    if ("кг" not in vv) and (re.search(r"\bг\b|гр", vv) is not None):
        x = x / 1000.0
    return 0.001 <= x <= 2000.0

def _is_sane_volume(v: str) -> bool:
    x = _to_float(v)
    if x is None:
        return False
    return 0.001 <= x <= 5000.0

def _is_sane_dims(v: str) -> bool:
    vv = (v or "").casefold()
    nums = _RE_NUM.findall(vv)
    # минимум 2 числа + разделитель или единицы измерения
    if len(nums) >= 2 and (_RE_DIM_SEP.search(vv) or any(u in vv for u in ("мм", "см", "м", "cm", "mm"))):
        return True
    return False

# Эвристика: похоже ли значение "Совместимость" на список моделей/серий (а не на общее назначение "для дома")
def _looks_like_model_compat(v: str) -> bool:
    s = norm_ws(v)
    if not s:
        return False
    scf = s.casefold()

    # CS: "увеличения/использования ..." — это описание назначения, а не список моделей
    if scf.startswith(("увеличения ", "использования ")):
        return False

    # CS: ссылки/маркетинг в "Совместимость" — мусор
    if "http://" in scf or "https://" in scf or "www." in scf:
        return False
    if ("™" in s or "®" in s) and len(s) > 40:
        return False

    has_sep = bool(re.search(r"[,;/\\|]", s))
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9\-]+", s)
    word_count = len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", s))

    # ! ? … — всегда предложение; точка — только если после буквы (не "1.0" и не "1010.")
    has_sentence = bool(re.search(r"[!?…]", s)) or bool(re.search(r"(?<=[A-Za-zА-Яа-яЁё])\.(?:\s|$)", s))

    # бренды/линейки (часто встречающиеся в совместимости)
    brands = r"(xerox|hp|canon|epson|brother|samsung|kyocera|ricoh|konica|minolta|lexmark|oki|pantum|dell|sharp|olivetti|toshiba|triumph|adler|panasonic)"

    # токены моделей (буквы+цифры)
    model_tokens = 0
    for t in tokens:
        if re.search(r"\d", t) and re.search(r"[A-Za-zА-Яа-яЁё]", t):
            model_tokens += 1
        elif re.match(r"^[A-Z]{1,4}\d{2,6}[A-Z]{0,3}$", t):
            model_tokens += 1

    cyr_words = [t for t in tokens if re.search(r"[а-яё]", t.casefold())]
    series_hits = sum(
        1 for t in tokens
        if t.casefold() in {
            "laserjet", "deskjet", "officejet", "pixma", "ecotank", "workforce",
            "workcentre", "versalink", "taskalfa", "ecosys", "bizhub", "i-sensys", "lbp", "mfp", "phaser"
        }
    )

    # 1) Списки (коммы/слэши/точки с запятой) и цифры — почти всегда модели
    if has_sep and re.search(r"\d", s):
        return True

    # 2) Типовая форма "для принтеров ..."
    if scf.startswith("для ") and re.search(r"\d", s):
        return True

    # 3) Короткие коды/серии (без предложений) — ok
    if (not has_sentence) and len(s) <= 40 and re.search(r"\d", s) and word_count <= 10:
        return True

    # 4) Много моделей — ok
    if model_tokens >= 2:
        return True

    # 5) Коротко + бренд — ok (Sharp C-CUBE, Olivetti PR II, Xerox ...)
    if len(tokens) <= 6 and re.search(rf"\b{brands}\b", scf):
        return True

    # 6) Маркетинг/предложения: длинный русскоязычный текст без списков и с точкой после слова
    if has_sentence and (not has_sep) and word_count >= 7 and len(cyr_words) >= 3 and model_tokens <= 2:
        stop = {
            "и","в","во","на","с","со","к","по","при","для","от","это","даже","если","чтобы",
            "как","но","или","то","же","также","еще","уже","благодаря","обеспечивает","используя",
            "работы","дома","офиса","победы","плавного","максимальной","четкости","детализации"
        }
        stop_hits = sum(1 for t in cyr_words if t.casefold() in stop)
        ratio = stop_hits / max(1, len(cyr_words))
        if len(s) > 45 or ratio >= 0.2:
            return False

    # 7) 1 модель + бренд/линейка — ok
    if model_tokens >= 1 and (series_hits >= 1 or re.search(rf"\b{brands}\b", scf)):
        return True

    return False

def _cs_trim_float(v: str, max_decimals: int = 4) -> str:
    # CS: аккуратно укорачиваем длинные дроби (объём/вес/габариты) для читаемости
    s = (v or "").strip()
    if not s:
        return s
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return s
    if "." not in s:
        return s
    intp, frac = s.split(".", 1)
    if len(frac) <= max_decimals:
        return s
    try:
        d = Decimal(s)
        q = Decimal("1." + ("0" * max_decimals))
        d2 = d.quantize(q, rounding=ROUND_HALF_UP)
        out = format(d2, "f")
        # убираем хвостовые нули и точку
        out = out.rstrip("0").rstrip(".")
        return out
    except (InvalidOperation, ValueError):
        return s


# --- Backward-safe shims: supplier-specific param rules больше не живут в shared core. ---

def sort_params(params: Sequence[tuple[str, str]], priority: Sequence[str] | None = None) -> list[tuple[str, str]]:
    pr = [norm_ws(x) for x in (priority or []) if norm_ws(x)]
    pr_map = {p.casefold(): i for i, p in enumerate(pr)}

    def key(kv):
        k = norm_ws(kv[0])
        idx = pr_map.get(k.casefold(), 10_000)
        return (idx, k.casefold())

    return sorted(list(params), key=key)

# Пробует извлечь пары "Характеристика: значение" из HTML описания (если поставщик кладёт это в description)

# Лёгкое обогащение характеристик из name/description (когда у поставщика params бедные)


# Делает текст описания "без странностей" (убираем лишние пробелы)
def fix_text(s: str) -> str:
    # Нормализует переносы строк и убирает мусорные пробелы/табуляции на пустых строках
    t = (s or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    # Убираем служебные/паспортные строки (CRC/Barcode/внутренние коды), чтобы не портить описание
    def _is_service_line(ln: str) -> bool:
        s2 = (ln or "").strip()
        if not s2:
            return False
        # типичные ключи паспорта/склада
        if re.search(r"(?i)^(CRC|Retail\s*Bar\s*Code|Retail\s*Barcode|Bar\s*Code|Barcode|EAN|GTIN|SKU)\b", s2):
            return (":" in s2) or ("\t" in s2)
        # русские служебные строки (VTT часто так пишет)
        if re.search(r"(?i)^(Артикул|Каталожн\w*\s*номер|Кат\.\s*номер|OEM(?:-номер)?|ОЕМ(?:-номер)?|Код\s*производител\w*|Код\s*товара|Штрих[-\s]?код)\b", s2):
            return (":" in s2) or ("\t" in s2)
        if re.search(r"(?i)^Дата\s*(ввода|вывода|введения|обновления)\b", s2):
            return (":" in s2) or ("\t" in s2)
        # строки вида "1.01 ...:" или "2.14 ...\t..."
        if re.match(r"^\d+\.\d+\b", s2) and ((":" in s2[:60]) or ("\t" in s2)):
            return True
        return False

    if t:
        t = "\n".join([ln for ln in t.split("\n") if not _is_service_line(ln)])

    # строки, которые состоят только из пробелов/табов, считаем пустыми
    if t:
        t = "\n".join("" if (ln.strip() == "") else ln for ln in t.split("\n"))

    # убираем тройные пустые строки
    t = _RE_MULTI_NL.sub("\n\n", t)

    # Нормализация частой опечатки (Shuko -> Schuko)
    t = _RE_SHUKO.sub("Schuko", t)
    t = fix_mixed_cyr_lat(t)
    return t


def _looks_like_section_header(line: str) -> bool:
    # Заголовок секции внутри характеристик (без табов, не слишком длинный)
    if not line:
        return False
    if "\t" in line:
        return False
    s = line.strip()
    if len(s) < 3 or len(s) > 64:
        return False
    # часто секции — 1-3 слова, без точки в конце
    if s.endswith("."):
        return False
    return True


_RE_SPECS_HDR_LINE = re.compile(r"^[^A-Za-zА-Яа-яЁё]*\s*(?:Технические характеристики|Основные характеристики|Характеристики)\b", re.IGNORECASE)
_RE_SPECS_HDR_ANY = re.compile(r"\b(Технические характеристики|Основные характеристики|Характеристики)\b", re.IGNORECASE)

def _htmlish_to_text(s: str) -> str:
    """Превращает HTML-подобный текст (с <br>, <p>, списками) в текст с \n.
    Нужно, чтобы корректно вытащить тех/осн характеристики из CopyLine и похожих источников.
    """
    raw = s or ""
    if not raw:
        return ""
    # основные разрывы строк
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</?(?:p|div|li|ul|ol|tr|td|th|table|h[1-6])[^>]*>", "\n", raw)
    # вычищаем остальные теги
    raw = re.sub(r"<[^>]+>", " ", raw)
    # html entities
    try:
        import html as _html
        raw = _html.unescape(raw)
    except Exception:
        pass
    raw = raw.replace("\xa0", " ")
    return raw

# Разбивает инлайновые списки тех/осн характеристик (AlStyle/часть AkCent).
# Пример: "Основные характеристики: - Диапазон ...- Скорость ..." -> строки "- ...".
def _split_inline_specs_bullets(rest: str) -> str:
    t = rest or ""
    if not t:
        return ""
    # "...характеристики:" -> заголовок на отдельной строке
    t = re.sub(
        r"(?i)\b(Технические характеристики|Основные характеристики|Характеристики)\s*:\s*",
        lambda m: m.group(1) + "\n",
        t,
    )
    # ".- Скорость" / "мкм.-Скорость" -> новая строка с буллетом
    t = re.sub(r"\.-\s*(?=[A-Za-zА-Яа-яЁё])", ".\n- ", t)
    # " ... - Время" -> новая строка с буллетом (не трогаем диапазоны 3-5)
    t = re.sub(r"\s+-\s+(?=[A-Za-zА-Яа-яЁё])", "\n- ", t)
    # нормализуем пустые строки
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t


def _cmp_name_like_text(s: str) -> str:
    # Для сравнения "похоже ли это на название" (используем только в дедупе описаний).
    t = (s or "")
    # срезаем простые HTML-теги и HTML-энтити (иногда поставщик кладёт <p>Название</p>)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&[a-zA-Z]+;|&#\d+;|&#x[0-9a-fA-F]+;", " ", t)
    t = norm_ws(t)
    t = t.strip(" \t\r\n\"'«»„“”‘’`")
    t = re.sub(r"[\s\-–—:|·•,\.]+$", "", t).strip()
    t = re.sub(r"^[\s\-–—:|·•,\.]+", "", t).strip()
    return t.casefold()


def normalize_cdata_inner(inner: str) -> str:
    # Убираем мусорные пробелы/пустые строки внутри CDATA, без лишних ведущих/хвостовых переводов строк
    inner = (inner or "").strip()
    inner = _RE_MULTI_NL.sub("\n\n", inner)
    return inner

def normalize_pictures(pictures: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for p in pictures or []:
        u = norm_ws(p)
        # CS: NVPrint отдаёт заглушку nophoto.jpg — приводим к общему placeholder
        if u.lower() in {"https://nvprint.ru/promo/photo/nophoto.jpg", "http://nvprint.ru/promo/photo/nophoto.jpg"}:
            u = CS_PICTURE_PLACEHOLDER_URL
        if not u:
            continue
        # если это просто домен без пути — это не картинка
        try:
            from urllib.parse import urlparse
            pr = urlparse(u)
            if pr.scheme in {"http", "https"} and pr.netloc and pr.path in {"", "/"}:
                continue
        except Exception:
            pass
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _cs_limit_pictures_for_satu_xml(pictures: Sequence[str], max_count: int = 10) -> list[str]:
    """Ограничивает только экспортируемые <picture> под лимит Satu.

    ВАЖНО:
    - не меняет исходные supplier/raw данные;
    - применяется только в final XML;
    - сначала нормализует и дедуплицирует фото, затем оставляет первые max_count.
    """
    pics = normalize_pictures(pictures or [])
    return pics[:max_count]

# Собирает keywords: бренд + полное имя + разбор имени на слова + города (в конце)

# Собирает keywords: бренд + имя + ключи по доставке + города (компактно, без "простыни")
# Важно: никаких "CS_CITY_TAIL" больше нет — keywords строятся только здесь.
# Похоже на "предложение" (инструкция/маркетинг) в имени параметра — переносим в notes, а не в характеристики.
# Дублирует часть эвристик выше, но даёт дополнительную страховку.
_RE_PARAM_SENTENCEY = re.compile(r"[.!?]|\b(?:вы|вам|вас|можете|пожалуйста|важно|внимание|доставка|оплата)\b", re.IGNORECASE)

def _is_sentence_like_param_name(k: str) -> bool:
    kk = norm_ws(k)
    if not kk:
        return False

    cf = kk.casefold()
    # исключение: это нормальные характеристики (оставляем в блоке "Характеристики")
    if (("рекомендуемая" in cf) or ("рекомендуемое" in cf)) and (("нагрузк" in cf) or ("количеств" in cf)):
        return False

    # исключение: гарантия — это характеристика (а не маркетинг/фраза)
    if cf.startswith("гаранти") and (len(kk) <= 25) and (len(kk.split()) <= 3):
        return False

    # 1) Явные фразы/инструкции/маркетинг — не характеристики
    if any(x in cf for x in (
        "вы можете купить",
        "в городах",
        "доставка",
        "оплата",
        "рекомендуем",
        "важно",
        "внимание",
        "обратите",
        "пожалуйста",
        "не обновля",
        "не обновлять",
        "маркир",
        "подлинност",
        "original",
        "оригинал",
        "упаковк",
        "предупрежден",
        "гаранти",
        "качества используемой бумаги",
        "заполняемость выводимых",
    )):
        return True

    # 2) Ключ со строчной буквы (обрывок/продолжение) — не характеристика
    first = kk[0]
    if first.isalpha() and first.islower():
        return True

    # 3) Слишком длинный "ключ-фраза"
    if len(kk) >= 65:
        return True
    words = kk.split()
    if len(words) >= 8:
        return True

    # 4) Похоже на предложение / обрывок
    if kk.endswith((".", "!", "?", ";")):
        return True
    if kk.endswith((",", ":")):
        return True
    if (kk.count(",") >= 1) and len(kk) >= 45:
        return True
    if _RE_PARAM_SENTENCEY.search(kk):
        return True

    return False

def get_public_vendor(supplier: str | None = None) -> str:
    """
    Публичный fallback-вендор для финального YML.

    Shared core не хранит список конкретных поставщиков.
    Защита здесь только от очевидных служебных значений и от имени текущего supplier,
    если оно было передано вызывающей стороной.
    """
    raw = (os.getenv("CS_PUBLIC_VENDOR", "") or os.getenv("PUBLIC_VENDOR", "") or "CS").strip()
    raw = norm_ws(raw) or "CS"

    reserved = {
        "cs",
        "supplier",
        "vendor",
        "поставщик",
        "unknown",
        "n/a",
    }
    if supplier and supplier.strip():
        reserved.add(supplier.strip().casefold())

    raw_cf = raw.casefold()
    if raw_cf in reserved:
        return "CS"
    if any(token and token in raw_cf for token in reserved if token not in {"cs"}):
        return "CS"
    return raw

def _category_unresolved_report_path(supplier: str) -> Path:
    _ = supplier
    return DOCS_RAW_DIR / "category_id_unresolved.txt"

def _write_category_unresolved_report(path: Path, supplier: str, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    start_marker = f"## START {supplier}"
    end_marker = f"## END {supplier}"

    block_lines = [
        start_marker,
        f"Поставщик: {supplier}",
        f"Товаров без categoryId: {len(lines)}",
        "",
    ]
    if lines:
        block_lines.extend(list(lines))
    else:
        block_lines.append("# Все товары получили categoryId")
    block_lines.append(end_marker)

    new_block = "\n".join(block_lines).rstrip() + "\n"

    current = path.read_text(encoding="utf-8") if path.exists() else ""
    block_re = re.compile(
        rf"(?ms)^## START {re.escape(supplier)}\n.*?^## END {re.escape(supplier)}\n?"
    )
    current = block_re.sub("", current).strip()

    header = "# Сводный отчёт по товарам без categoryId\n\n"
    pieces: list[str] = []
    if current:
        current = current.strip()
        if current.startswith("# Сводный отчёт по товарам без categoryId"):
            current = re.sub(r"^# Сводный отчёт по товарам без categoryId\n*", "", current).strip()
        if current:
            pieces.append(current)
    pieces.append(new_block.strip())

    data = header + "\n\n".join(pieces).rstrip() + "\n"
    path.write_text(data, encoding="utf-8")

def _resolve_offer_category_id(offer: "OfferOut", *, public_vendor: str) -> str:
    name_full = normalize_offer_name(offer.name)
    name_full = sanitize_mixed_text(name_full)
    native_desc = fix_text(offer.native_desc)
    native_desc = strip_service_kv_lines(native_desc)
    vendor = pick_vendor(offer.vendor, name_full, offer.params, native_desc, public_vendor=public_vendor)
    category_id = norm_ws(offer.category_id) or resolve_category_id(
        oid=offer.oid,
        name=name_full,
        vendor=vendor,
        params=offer.params,
        native_desc=native_desc,
    )
    return norm_ws(category_id)

def _split_offers_for_final(
    offers: Sequence["OfferOut"],
    *,
    supplier: str,
    public_vendor: str,
) -> tuple[list["OfferOut"], list[str]]:
    resolved: list[OfferOut] = []
    unresolved_lines: list[str] = []

    for offer in offers:
        category_id = _resolve_offer_category_id(offer, public_vendor=public_vendor)
        if category_id:
            resolved.append(replace(offer, category_id=category_id))
            continue

        unresolved_lines.append(
            f"{supplier} | {offer.oid} | {norm_ws(offer.name)} | categoryId не определён"
        )

    return resolved, unresolved_lines

# CS: собирает полный XML фида (header + FEED_META + offers + footer)
def write_cs_feed_raw(
    offers: Sequence["OfferOut"],
    *,
    supplier: str,
    supplier_url: str,
    out_file: str,
    build_time: datetime,
    next_run: datetime,
    before: int,
    encoding: str = OUTPUT_ENCODING_DEFAULT,
    currency_id: str = CURRENCY_ID_DEFAULT,
) -> bool:
    full = build_cs_feed_xml_raw(
        offers,
        supplier=supplier,
        supplier_url=supplier_url,
        build_time=build_time,
        next_run=next_run,
        before=before,
        encoding=encoding,
        currency_id=currency_id,
    )
    return write_if_changed(out_file, full, encoding=encoding)

# CS: пишет фид в файл (validate + write_if_changed)
def write_cs_feed(
    offers: Sequence["OfferOut"],
    *,
    supplier: str,
    supplier_url: str,
    out_file: str,
    build_time: datetime,
    next_run: datetime,
    before: int,
    encoding: str = OUTPUT_ENCODING_DEFAULT,
    public_vendor: str = "CS",
    currency_id: str = CURRENCY_ID_DEFAULT,
    param_priority: Sequence[str] | None = None,
) -> bool:
    resolved_offers, unresolved_lines = _split_offers_for_final(
        offers,
        supplier=supplier,
        public_vendor=public_vendor,
    )

    _write_category_unresolved_report(
        _category_unresolved_report_path(supplier),
        supplier,
        unresolved_lines,
    )

    full = build_cs_feed_xml(
        resolved_offers,
        supplier=supplier,
        supplier_url=supplier_url,
        build_time=build_time,
        next_run=next_run,
        before=before,
        encoding=encoding,
        public_vendor=public_vendor,
        currency_id=currency_id,
        param_priority=param_priority,
    )
    validate_cs_yml(full, param_drop_default_cf=PARAM_DROP_DEFAULT_CF)
    return write_if_changed(out_file, full, encoding=encoding)

# Пишет файл только если изменился (атомарно)
def normalize_vendor(v: str) -> str:
    # CS: нормализация vendor (убираем дубль 'Hewlett-Packard' -> 'HP' и т.п.)
    if not v:
        return ""
    v = str(v).strip()
    if not v:
        return ""
    v_cf = v.casefold().replace("ё", "е")
    # унификация SMART
    if v_cf == "smart":
        v = "SMART"
        v_cf = "smart"
    # частые алиасы/опечатки
    if v_cf.startswith("epson proj"):
        v = "Epson"
    elif v_cf.startswith("viewsonic proj"):
        v = "ViewSonic"
    elif v_cf.startswith("brothe"):
        v = "Brother"
    elif v_cf.startswith("europrint"):
        v = "Europrint"
    elif v_cf in {"xg", "x-game"}:
        v = "X-Game"
    # унификация Konica Minolta
    if "konica" in v_cf and "minolta" in v_cf:
        v = "Konica Minolta"
    # унификация Kyocera-Mita
    if "kyocera" in v_cf and "mita" in v_cf:
        v = "Kyocera"
        v_cf = "kyocera"
    # нормализуем слэш-списки (HP/Canon)
    parts = [p.strip() for p in re.split(r"\s*/\s*", v) if p.strip()]
    norm_parts: list[str] = []
    for p in parts:
        low = p.lower().replace("‑", "-").replace("–", "-")
        if re.search(r"hewlett\s*-?\s*packard", low):
            norm_parts.append("HP")
        else:
            norm_parts.append(p)
    # склеиваем обратно
    out = "/".join(norm_parts)
    out = re.sub(r"\s{2,}", " ", out).strip()
    # CS: не смешиваем бренды через '/' (HP/Canon -> HP) — для vendor нужен один бренд
    if "/" in out:
        parts2 = [p.strip() for p in out.split("/") if p.strip()]
        if len(parts2) >= 2:
            # берём первый бренд, если список состоит из известных брендов
            canon_set = set(CS_BRANDS_MAP.values())
            if all(p in canon_set for p in parts2):
                out = parts2[0]
    return out

# Satu import compatibility: часть brand/vendor токенов портал может не принимать как
# справочник производителей. Для таких токенов в final-XML бренд не публикуем как
# vendor/brand-param, чтобы не ломать импорт, при этом name/description сохраняются.
_SATU_UNSUPPORTED_VENDOR_CASEFOLD = {"astergo"}
_SATU_BRAND_PARAM_NAMES = {"для бренда", "бренд", "производитель"}

def _normalize_vendor_for_satu_xml(v: str) -> str:
    s = normalize_vendor(v)
    if not s:
        return ""
    if s.casefold() in _SATU_UNSUPPORTED_VENDOR_CASEFOLD:
        return ""
    return s

# Пытается определить бренд (vendor) по vendor_src / name / params / description (если пусто — public_vendor)

# CS: защита от ошибочного vendor (тип товара/префикс/код вместо бренда)
_BAD_VENDOR_WORDS = {
    "мфу", "принтер", "сканер", "плоттер", "шредер", "ламинатор", "переплетчик",
    "монитор", "экран", "проектор",
    "интерактивная", "интерактивный", "интерактивная панель", "интерактивный дисплей", "интерактивная доска",
    "экономичный", "экономичный набор",
    "картридж", "чернила", "тонер", "барабан", "чип",
    "пленка для ламинирования", "емкость для отработанных чернил",
}
_RE_VENDOR_CODELIKE = re.compile(r"^[A-ZА-ЯЁ]{1,3}\d")

def _is_bad_vendor_token(v: str) -> bool:
    vv = norm_ws(v)
    if not vv:
        return False
    cf = vv.casefold().replace("ё", "е")
    if cf in _BAD_VENDOR_WORDS:
        return True
    # коды/артикулы вида C13T55KD00, W1335A, V12H... не являются брендом
    if (" " not in vv) and (len(vv) <= 24) and _RE_VENDOR_CODELIKE.match(vv):
        return True
    return False

# Словарь брендов для pick_vendor (упорядочен, расширяем при необходимости)
CS_BRANDS_MAP = {
    "hp": "HP",
    "hewlett": "HP",
    "canon": "Canon",
    "epson": "Epson",
    "brother": "Brother",
    "samsung": "Samsung",
    "sv": "SVC",
    "svc": "SVC",
    "apc": "APC",
    "schneider": "Schneider Electric",
    "cyberpower": "CyberPower",
    "cyber-power": "CyberPower",
    "cyber power": "CyberPower",
    "smart": "SMART",
    "idprt": "IDPRT",
    "id-prt": "IDPRT",
    "id prt": "IDPRT",
    "asus": "ASUS",
    "lenovo": "Lenovo",
    "acer": "Acer",
    "dell": "Dell",
    "logitech": "Logitech",
    "xiaomi": "Xiaomi",

    "ripo": "RIPO",
    "xerox": "Xerox",
    "kyocera": "Kyocera",
    "ricoh": "Ricoh",
    "toshiba": "Toshiba",
    "integral": "INTEGRAL",
    "pantum": "Pantum",
    "oki": "OKI",
    "lexmark": "Lexmark",
    "konica": "Konica Minolta",
    "minolta": "Konica Minolta",
    "fujifilm": "FUJIFILM",
    "huawei": "Huawei",
    "deli": "Deli",
    "olivetti": "Olivetti",
    "panasonic": "Panasonic",
    "riso": "Riso",
    "avision": "Avision",
    "fellowes": "Fellowes",
    "viewsonic": "ViewSonic",
    "philips": "Philips",
    "zebra": "Zebra",
    "euro print": "Europrint",
    "designjet": "HP",
    "mr.pixel": "Mr.Pixel",
    "hyperx": "HyperX",
    "aoc": "AOC",
    "benq": "BenQ",
    "lg": "LG",
    "msi": "MSI",
    "gigabyte": "GIGABYTE",
    "tp-link": "TP-Link",
    "tplink": "TP-Link",
    "mikrotik": "MikroTik",
    "ubiquiti": "Ubiquiti",
    "d-link": "D-Link",
    "europrint": "Europrint",
    "brothe": "Brother",
}

def pick_vendor(
    vendor_src: str,
    name: str,
    params: Sequence[tuple[str, str]],
    desc_html: str,
    *,
    public_vendor: str = "CS",
) -> str:
    """
    Общий vendor-guard без supplier-rescue.

    Правило CS:
    - core НЕ ищет vendor в name / description / params;
    - core принимает vendor, который уже отдал адаптер;
    - если vendor пустой/мусорный — применяет только общий fallback public_vendor.

    Аргументы name/params/desc_html оставлены для back-compat сигнатуры,
    чтобы не ломать существующие вызовы и адаптеры.
    """
    _ = name
    _ = params
    _ = desc_html

    v = norm_ws(vendor_src)
    if v:
        v2 = normalize_vendor(v)
        if v2 and (not _is_bad_vendor_token(v2)):
            return v2

    return norm_ws(public_vendor)

@dataclass
class OfferOut:
    oid: str
    available: bool
    name: str
    price: int | None
    pictures: list[str]
    vendor: str
    params: list[tuple[str, str]]
    native_desc: str
    category_id: str = ""

    # Собирает XML offer (фиксированный порядок)
    def to_xml(
        self,
        *,
        currency_id: str = CURRENCY_ID_DEFAULT,
        public_vendor: str = "CS",
        param_priority: Sequence[str] | None = None,
    ) -> str:
        name_full = normalize_offer_name(self.name)
        name_full = sanitize_mixed_text(name_full)
        native_desc = fix_text(self.native_desc)
        # RAW должен уже отдавать идеальные params и чистое supplier-description.
        # Core НЕ переносит характеристики из description в params и не enrich'ит их из desc/name.
        native_desc = strip_service_kv_lines(native_desc)
        vendor = pick_vendor(self.vendor, name_full, self.params, native_desc, public_vendor=public_vendor)
        vendor_xml = _normalize_vendor_for_satu_xml(vendor)
        price_final = compute_price(self.price)

        # categoryId — это общая Satu-таксономия проекта, поэтому резолвится в shared-слое.
        # Если адаптер когда-нибудь задаст category_id сам, core его уважает.
        category_id = norm_ws(self.category_id) or resolve_category_id(
            oid=self.oid,
            name=name_full,
            vendor=vendor,
            params=self.params,
            native_desc=native_desc,
        )

        # RAW обязан отдавать уже чистые и финальные supplier params.
        # Core не чистит, не нормализует и не перестраивает параметры под поставщика.
        params = [(sanitize_mixed_text(k), sanitize_mixed_text(v)) for (k, v) in (self.params or [])]
        params_sorted = sort_params(params, priority=list(param_priority or []))
        notes: list[str] = []

        # CS: лимитируем <name> (умно для NVPrint)
        name_short = enforce_name_policy(self.oid, name_full, params_sorted)
        name_short = sanitize_mixed_text(name_short)

        # CS: В описании сохраняем полное наименование (если оно было укорочено).
        # Если <name> был укорочен — в описании сохраняем полное наименование.
        name_for_desc = name_full if (name_short != name_full) else name_short
        name_for_desc = sanitize_mixed_text(name_for_desc)

        desc_cdata = build_description(name_for_desc, native_desc, params_sorted, notes=notes)
        desc_cdata = sanitize_mixed_text(desc_cdata)
        keywords = build_keywords(vendor, name_short)
        keywords = _truncate_text(keywords, int(CS_KEYWORDS_MAX_LEN or CS_KEYWORDS_MAX_LEN_FALLBACK))
        keywords = sanitize_mixed_text(keywords)

        # Core не знает поставщиков и не применяет supplier-specific санитайзеры.
        # Любая такая очистка должна происходить только в RAW / supplier-layer.

        pics_xml = ""
        pics = _cs_limit_pictures_for_satu_xml(self.pictures or [], 10)
        if not pics and CS_PICTURE_PLACEHOLDER_URL:
            pics = [CS_PICTURE_PLACEHOLDER_URL]
        for pp in pics:
            pics_xml += f"\n<picture>{xml_escape_text(_cs_norm_url(pp))}</picture>"

        params_xml = ""
        for k, v in params_sorted:
            k_src = norm_ws(k)
            v_src = norm_ws(v)
            if not k_src or not v_src:
                continue
            key_cf = k_src.casefold()
            if key_cf in _SATU_BRAND_PARAM_NAMES:
                v_src = _normalize_vendor_for_satu_xml(v_src)
                if not v_src:
                    continue
            elif key_cf == "совместимость":
                v_src = _cs_trim_compat_for_satu_param(v_src, 255)
            else:
                v_src = _truncate_text(v_src, 255)
            kk = xml_escape_attr(k_src)
            vv = xml_escape_text(v_src)
            if not kk or not vv:
                continue
            params_xml += f'\n<param name="{kk}">{vv}</param>'

        # Core не знает поставщиков и не меняет supplier-specific availability.
        # Исключение только общее CS-правило: если финальной цены нет, не ставим <price>100</price>,
        # а публикуем offer как готовый к отправке через in_stock="true".
        avail_effective = bool(self.available)
        vendor_xml_line = f"<vendor>{xml_escape_text(vendor_xml)}</vendor>\n" if vendor_xml else ""

        offer_attrs = [f'id="{xml_escape_attr(self.oid)}"']
        price_xml = ""
        if price_final is None:
            offer_attrs.append('available="true"')
            offer_attrs.append('in_stock="true"')
        else:
            offer_attrs.append(f'available="{bool_to_xml(bool(avail_effective))}"')
            price_xml = f"<price>{int(price_final)}</price>"

        out = (
            f"<offer {' '.join(offer_attrs)}>\n"
            f"<categoryId>{xml_escape_text(category_id)}</categoryId>\n"
            f"<vendorCode>{xml_escape_text(self.oid)}</vendorCode>\n"
            f"<name>{xml_escape_text(name_short)}</name>\n"
            f"{price_xml}"
            f"{pics_xml}\n"
            f"{vendor_xml_line}"
            f"<currencyId>{xml_escape_text(currency_id)}</currencyId>\n"
            f"<description><![CDATA[\n{desc_cdata}]]></description>"
            f"{params_xml}\n"
            f"<keywords>{xml_escape_text(keywords)}</keywords>\n"
            f"</offer>"
        )
        return out

# Собирает XML offer (СЫРОЙ: без enrich/clean/compat/keywords/описания-шаблона)
# Нужен только для диагностики: "что адаптер отдал в core".

    # Собирает XML offer в "сыром" виде (до core: без enrich/clean/compat/keywords/шаблона description).
    # Нужно только для диагностики: сравнить docs/raw/*.yml (вход core) и docs/*.yml (выход core).
    def to_xml_raw(
        self,
        *,
        currency_id: str = CURRENCY_ID_DEFAULT,
    ) -> str:
        oid = xml_escape_attr(self.oid)
        avail = bool_to_xml(bool(self.available))

        name = xml_escape_text(fix_text(norm_ws(self.name)))
        vendor = xml_escape_text(fix_text(norm_ws(self.vendor)))
        category_id = norm_ws(self.category_id)
        pi = safe_int(self.price)
        price = int(pi) if pi is not None else 0

        # native_desc сохраняем максимально как есть (только делаем безопасным для XML)
        native_desc = fix_text(self.native_desc or "").replace("]]>", "]]&gt;")

        pics_xml = ""
        for pp in (self.pictures or []):
            pp2 = (pp or "").strip()
            if not pp2:
                continue
            pics_xml += f"\n<picture>{xml_escape_text(_cs_norm_url(pp2))}</picture>"

        params_xml = ""
        for k, v in (self.params or []):
            kk = xml_escape_attr(norm_ws(k))
            # сырое: не выводим служебные/отладочные параметры
            if re.fullmatch(r"(?i)товаров:\s*\d{1,7}", kk or ""):
                continue
            vv = xml_escape_text(fix_text(norm_ws(v)))
            if not kk or not vv:
                continue
            params_xml += f"\n<param name=\"{kk}\">{vv}</param>"

        out = (
            f"<offer id=\"{oid}\" available=\"{avail}\">\n"
            f"<categoryId>{xml_escape_text(category_id)}</categoryId>\n"
            f"<vendorCode>{xml_escape_text(self.oid)}</vendorCode>\n"
            f"<name>{name}</name>\n"
            f"<price>{price}</price>"
            f"{pics_xml}\n"
            f"<vendor>{vendor}</vendor>\n"
            f"<currencyId>{xml_escape_text(currency_id)}</currencyId>\n"
            f"<description><![CDATA[\n{native_desc}]]></description>"
            f"{params_xml}\n"
            f"</offer>"
        )
        return out
