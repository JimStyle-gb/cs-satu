# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/vtt/builder.py

VTT builder layer.

Что делает:
- собирает clean raw offers supplier-layer;
- не подменяет shared core final rendering;

Что не делает:
- не хранит shared final rules;
- не подменяет shared description/keywords/writer.
"""
from __future__ import annotations

import re

from cs.core import OfferOut, compute_price

from .compat import (
    ALT_PART_TAIL_RE,
    CODE_SOURCE_KEYS,
    collect_codes,
    derive_display_part_number,
    derive_hiblack_color,
    extract_compat,
    extract_part_number,
)
from .desc_extract import build_native_description, extract_resource
from .normalize import (
    build_offer_oid,
    clean_title,
    guess_vendor,
    infer_color_from_title,
    infer_tech,
    infer_type,
    make_oid,
    norm_color,
    norm_ws,
    safe_str,
)
from .pictures import PLACEHOLDER, collect_picture_urls

SKIP_PARAM_KEYS = {
    "Артикул",
    "Штрих-код",
    "Вендор",
    "Категория",
    "Подкатегория",
    "В упаковке, штук",
    "Местный склад, штук",
    "Местный, до новой поставки, дней",
    "Склад Москва, штук",
    "Москва, до новой поставки, дней",
    "Категория VTT",
}

ID_SOURCE_PARAM_KEYS = {
    "артикул",
    "sku",
    "код товара",
    "товарный код",
    "article",
    "product code",
    "part number",
    "партномер",
    "каталожный номер",
    "oem-номер",
}

_TITLE_COLOR_TAIL_RE = re.compile(
    r"(?:,?\s*(?:black|photo\s*black|photoblack|matte\s*black|matt\s*black|cyan|yellow|magenta|grey|gray|red|blue|color|colour|"
    r"bk|c|m|y|cl|ml|lc|lm|"
    r"черн(?:ый|ая|ое)?|чёрн(?:ый|ая|ое)?|голуб(?:ой|ая|ое)?|син(?:ий|яя|ее)?|цветн(?:ой|ая|ое)?|желт(?:ый|ая|ое)?|жёлт(?:ый|ая|ое)?|"
    r"пурпурн(?:ый|ая|ое)?|малинов(?:ый|ая|ое)?|сер(?:ый|ая|ое)?|красн(?:ый|ая|ое)?))\s*$",
    re.I,
)

_VENDOR_ALIASES = (
    ("HP", "HP"),
    ("HPE", "HPE"),
    ("Canon", "Canon"),
    ("Xerox", "Xerox"),
    ("Kyocera", "Kyocera"),
    ("Brother", "Brother"),
    ("Epson", "Epson"),
    ("Ricoh", "Ricoh"),
    ("Samsung", "Samsung"),
    ("Lexmark", "Lexmark"),
    ("Pantum", "Pantum"),
    ("Sharp", "Sharp"),
    ("Panasonic", "Panasonic"),
    ("Toshiba", "Toshiba"),
    ("Develop", "Develop"),
    ("Gestetner", "Gestetner"),
    ("RISO", "RISO"),
    ("Avision", "Avision"),
    ("DELI", "Deli"),
    ("Deli", "Deli"),
    ("OKI", "OKI"),
    ("Oki", "OKI"),
    ("Olivetti", "Olivetti"),
    ("Triumph-Adler", "Triumph-Adler"),
    ("FUJIFILM", "FUJIFILM"),
    ("FujiFilm", "FUJIFILM"),
    ("Fujifilm", "FUJIFILM"),
    ("Huawei", "Huawei"),
    ("Катюша", "Катюша"),
    ("F+ imaging", "F+ imaging"),
    ("F+", "F+ imaging"),
    ("Konica Minolta", "Konica Minolta"),
    ("Minolta", "Konica Minolta"),
)

_DEVICE_VENDOR_HINTS = (
    (re.compile(r"(?iu)\b(?:LaserJet|DeskJet|DesignJet|OfficeJet|OJ\s+Pro|Color\s+LaserJet)\b"), "HP"),
    (re.compile(r"(?iu)\b(?:PIXMA|imageRUNNER|imagePRESS|i-SENSYS|LBP|MF\d|TM-\d|PRO-\d)\b"), "Canon"),
    (re.compile(r"(?iu)\b(?:VersaLink|AltaLink|WorkCentre|Phaser|ColorQube|DocuColor|Versant)\b"), "Xerox"),
    (re.compile(r"(?iu)\b(?:ECOSYS|TASKalfa|FS-\d|M\d{4}dn|P\d{4}dn)\b"), "Kyocera"),
    (re.compile(r"(?iu)\b(?:DCP|MFC|HL-\d|TN-\d|DR-\d)\b"), "Brother"),
    (re.compile(r"(?iu)\b(?:SCX|CLP|CLX|ML-\d|SL-[A-Z0-9-]+)\b"), "Samsung"),
    (re.compile(r"(?iu)\b(?:L\d{3,4}|XP-\d|WF-\d|Expression|Stylus)\b"), "Epson"),
    (re.compile(r"(?iu)\b(?:ApeosPort|Apeos)\b"), "FUJIFILM"),
    (re.compile(r"(?iu)\bAvision\b"), "Avision"),
    (re.compile(r"(?iu)\bDELI\b"), "Deli"),
    (re.compile(r"(?iu)\bDeli\b"), "Deli"),
    (re.compile(r"(?iu)\bOlivetti\b"), "Olivetti"),
    (re.compile(r"(?iu)\bTriumph-?Adler\b"), "Triumph-Adler"),
    (re.compile(r"(?iu)\bHuawei\b"), "Huawei"),
    (re.compile(r"(?iu)\bКатюша\b"), "Катюша"),
    (re.compile(r"(?iu)\bF\+\s*imaging\b"), "F+ imaging"),
    (re.compile(r"(?iu)\bPixLab\b"), "Huawei"),
    (re.compile(r"(?iu)\bBizhub\b"), "Konica Minolta"),
)

_FOR_BRAND_PATTERNS = (
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+HP\b"), "HP"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+HPE\b"), "HPE"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Canon\b"), "Canon"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Xerox\b"), "Xerox"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Kyocera\b"), "Kyocera"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Brother\b"), "Brother"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Epson\b"), "Epson"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Ricoh\b"), "Ricoh"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Samsung\b"), "Samsung"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Lexmark\b"), "Lexmark"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Pantum\b"), "Pantum"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Sharp\b"), "Sharp"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Panasonic\b"), "Panasonic"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Toshiba\b"), "Toshiba"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Develop\b"), "Develop"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Gestetner\b"), "Gestetner"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+RISO\b"), "RISO"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Avision\b"), "Avision"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+DELI\b"), "Deli"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Deli\b"), "Deli"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Olivetti\b"), "Olivetti"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Triumph-?Adler\b"), "Triumph-Adler"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+FUJIFILM\b"), "FUJIFILM"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+FujiFilm\b"), "FUJIFILM"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Fujifilm\b"), "FUJIFILM"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Huawei\b"), "Huawei"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Катюша\b"), "Катюша"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+F\+\s*imaging\b"), "F+ imaging"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Konica\s+Minolta\b"), "Konica Minolta"),
    (re.compile(r"(?iu)(?:^|\b)(?:для|for)\s+Minolta\b"), "Konica Minolta"),
)

def _canonical_vendor(value: str) -> str:
    s = norm_ws(value)
    if not s:
        return ""
    for raw, canon in _VENDOR_ALIASES:
        if s.casefold() == raw.casefold():
            return canon
    return s

def _vendor_from_texts(*texts: str) -> str:
    hay = "\n".join([norm_ws(x) for x in texts if norm_ws(x)])
    if not hay:
        return ""

    for rx, vendor in _FOR_BRAND_PATTERNS:
        if rx.search(hay):
            return vendor

    low = hay.casefold()
    for raw, canon in _VENDOR_ALIASES:
        if raw.casefold() in low:
            return canon

    for rx, vendor in _DEVICE_VENDOR_HINTS:
        if rx.search(hay):
            return vendor

    return ""

def _resolve_vendor(
    *,
    raw_vendor: str,
    title: str,
    params: list[tuple[str, str]],
    compat: str,
    description_text: str,
    codes: list[str],
    part_number: str,
    display_part_number: str,
) -> str:
    vendor = _canonical_vendor(guess_vendor(raw_vendor, title, params))
    if vendor:
        return vendor

    for key, value in params or []:
        key_n = norm_ws(key).casefold()
        val_n = _canonical_vendor(value)
        if not val_n:
            continue
        if key_n in {"для бренда", "бренд", "марка", "vendor", "brand", "производитель"}:
            return val_n

    vendor = _vendor_from_texts(
        compat,
        description_text,
        title,
        display_part_number,
        part_number,
        ", ".join(codes),
        *[f"{k}: {v}" for k, v in params],
    )
    if vendor:
        return vendor

    return ""

def _resolve_for_brand(*, vendor: str, title: str, compat: str) -> str:
    """Определить target printer brand для param 'Для бренда'.

    Важно:
    - vendor у совместимок может быть брендом расходника (например Hi-Black);
    - param 'Для бренда' должен отражать бренд устройства, а не бренд совместимого картриджа.
    """
    vendor_n = _canonical_vendor(vendor)
    if not vendor_n:
        return ""
    if vendor_n.casefold() != "hi-black":
        return vendor_n

    target = _vendor_from_texts(compat, title)
    if target and target.casefold() != "hi-black":
        return target

    m = re.search(r"\bдля\s+(.+)$", norm_ws(title), re.I)
    if m:
        target = _vendor_from_texts(m.group(1))
        if target and target.casefold() != "hi-black":
            return target
    return ""

def _prefer_title_type(current_type: str, title: str) -> str:
    t = norm_ws(title).lower()
    explicit = (
        ("тонер-картридж", "Тонер-картридж"),
        ("драм-картридж", "Драм-картридж"),
        ("драм-юнит", "Драм-юнит"),
        ("драм-юниты", "Драм-юнит"),
        ("драм юнит", "Драм-юнит"),
        ("копи-картридж", "Копи-картридж"),
        ("принт-картридж", "Принт-картридж"),
        ("печатающая головка", "Печатающая головка"),
        ("картридж", "Картридж"),
        ("девелопер", "Девелопер"),
        ("термоблок", "Термоблок"),
        ("контейнер", "Контейнер"),
        ("тонер", "Тонер"),
        ("чернила", "Чернила"),
        ("кабель сетевой", "Кабель сетевой"),
        ("блок проявки", "Блок проявки"),
        ("сервисный набор", "Блок проявки"),
        ("блок", "Блок"),
    )
    for prefix, canonical in explicit:
        if t.startswith(prefix):
            return canonical
    return current_type

def _normalize_code_token(value: str) -> str:
    s = norm_ws(value).upper()
    s = s.replace(" ", "")
    return s

def _primary_id_from_params(params: list[tuple[str, str]]) -> str:
    for key, value in params or []:
        key_n = norm_ws(key).casefold()
        if key_n not in ID_SOURCE_PARAM_KEYS:
            continue
        token = _normalize_code_token(value)
        if not token:
            continue
        if "/" in token:
            token = token.split("/", 1)[0].strip()
        if token:
            return token
    return ""

def _select_stable_offer_code(*, raw_params: list[tuple[str, str]], raw_sku: str, part_number: str, display_part_number: str) -> str:
    from_params = _primary_id_from_params(raw_params)
    if from_params:
        return from_params

    part = _normalize_code_token(part_number)
    if part:
        return part

    display = _normalize_code_token(display_part_number)
    if display:
        return display

    sku = _normalize_code_token(raw_sku)
    if sku:
        if "/" in sku:
            return sku.split("/", 1)[0].strip() or sku
        return sku

    return ""

def _merge_params(
    raw: dict,
    vendor: str,
    type_name: str,
    tech: str,
    part_number: str,
    display_part_number: str,
    codes: list[str],
    title: str,
    compat: str,
    resource: str,
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    color_found = ""

    def add(k: str, v: str) -> None:
        key = norm_ws(k)
        val = norm_ws(v)
        if not key or not val:
            return
        sig = (key.casefold(), val.casefold())
        if sig in seen:
            return
        seen.add(sig)
        out.append((key, val))

    raw_params = [(safe_str(k), safe_str(v)) for (k, v) in (raw.get("params") or [])]

    if type_name:
        add("Тип", type_name)
    if tech:
        add("Технология печати", tech)
    target_brand = _resolve_for_brand(vendor=vendor, title=title, compat=compat)
    if target_brand and type_name and any(
        x in type_name.casefold()
        for x in ("картридж", "драм", "девелопер", "чернила", "тонер", "головка", "блок", "барабан", "контейнер", "носитель")
    ):
        add("Для бренда", target_brand)

    for key, value in raw_params:
        if key in SKIP_PARAM_KEYS or key in CODE_SOURCE_KEYS:
            continue
        if key == "Цвет":
            value = norm_color(value)
            color_found = value or color_found
        if key.casefold() == "ресурс":
            resource = resource or norm_ws(value)
            continue
        if key in {"Модель", "Партномер"}:
            continue
        add(key, value)

    if display_part_number:
        add("Партномер", display_part_number)
    if compat:
        add("Совместимость", compat)
    if resource:
        add("Ресурс", resource)
    if codes:
        add("Коды расходников", ", ".join(codes))

    inferred_color = infer_color_from_title(title)
    hiblack_color = derive_hiblack_color(title=title, raw_part_number=part_number)
    final_color = hiblack_color or inferred_color

    if final_color:
        replaced = False
        if color_found and final_color != color_found and (
            "Hi-Black" in title or any(x in title for x in ("Cyan", "Magenta", "Yellow", "Grey", "Gray", "Photoblack", "Mattblack"))
        ):
            out2: list[tuple[str, str]] = []
            for k, v in out:
                if k == "Цвет" and not replaced:
                    out2.append(("Цвет", final_color))
                    replaced = True
                else:
                    out2.append((k, v))
            out = out2
        elif not color_found:
            add("Цвет", final_color)

    return out

def _strip_tail_noise(title_no_suffix: str) -> str:
    changed = True
    t = title_no_suffix
    while changed and t:
        before = t
        t = re.sub(r"\(\s*уцен[^)]*\)\s*$", "", t, flags=re.I).strip(" ,")
        t = re.sub(r"(?:,?\s*\d+(?:[.,]\s*\d+)?\s*[KКkк])\s*$", "", t, flags=re.I).strip(" ,")
        t = re.sub(r"(?:,?\s*\d+(?:[.,]\s*\d+)?\s*(?:мл|ml|л|l))\s*$", "", t, flags=re.I).strip(" ,")
        t = _TITLE_COLOR_TAIL_RE.sub("", t).strip(" ,")
        t = ALT_PART_TAIL_RE.sub("", t).strip(" ,/")
        t = re.sub(r"(?:,\s*|\s+)(?:0|1|2|3|4|5|6|7|8|9)\s*$", "", t).strip(" ,")
        t = re.sub(r"(?:,?\s*0[.,]\s*[36]\s*[KКkк])\s*$", "", t, flags=re.I).strip(" ,")
        t = re.sub(r"(?:,\s*|\s+)(?:bk|c|m|y|cl|ml|lc|lm)\s*$", "", t, flags=re.I).strip(" ,")
        changed = t != before
    return t

def _repair_known_titles(title_no_suffix: str, compat: str) -> str:
    t = norm_ws(title_no_suffix)
    comp = norm_ws(compat)

    if t.startswith("Тонер-картридж Xerox для WC ") and comp.startswith("Xerox WC "):
        row = comp[len("Xerox "):]
        if "/7835" in row:
            return "Тонер-картридж Xerox для WC 7525/7530/7535/7545/7556/7830/7835"
        return f"Тонер-картридж Xerox для {row}"

    if t.startswith("Тонер-картридж Xerox Color C60/"):
        return "Тонер-картридж Xerox Color C60/C70"

    if t.startswith("Тонер-картридж Xerox DC S"):
        return "Тонер-картридж Xerox DC SC2020"

    if t.startswith("Картридж 052H для Canon MF421dw/MF426dw/MF428x/MF429x"):
        return "Картридж 052H для Canon MF421dw/MF426dw/MF428x/MF429x"

    if t.startswith("Картридж 651 для HP DJ 5645"):
        return "Картридж 651 для HP DJ 5645"

    if "Hi-Black" in t and "HP DJ T920/T1500" in t:
        m = re.search(r"Hi-Black\s*\(([^)]+)\)", t)
        oem = m.group(1).strip() if m else ""
        if oem:
            return f"Картридж Hi-Black 727 для HP DJ T920/T1500 {oem}"
        return "Картридж Hi-Black 727 для HP DJ T920/T1500"

    if "Hi-Black 46" in t and "HP DJ 2020/2520" in t:
        m = re.search(r"\b(CZ63[78]AE)\b", t, re.I)
        oem = m.group(1).upper() if m else ""
        if oem:
            return f"Картридж Hi-Black 46 для HP DJ 2020/2520 {oem}"
        return "Картридж Hi-Black 46 для HP DJ 2020/2520"

    if "Hi-Black" in t and "HP OJ Pro 6230/6830" in t:
        m = re.search(r"\b(C2P\d{2}AE)\b", t, re.I)
        oem = m.group(1).upper() if m else ""
        if oem:
            return f"Картридж Hi-Black для HP OJ Pro 6230/6830 {oem}"
        return "Картридж Hi-Black для HP OJ Pro 6230/6830"

    return t

_ORIGINALITY_PARAM_NAME = "Оригинальность"
_ORIGINALITY_MARK_RE = re.compile(r"(?iu)\(\s*[OО]\s*\)")
_DESC_ORIGINALITY_HEAD_RE = re.compile(r"(?iu)^\s*(?:Оригинальн(?:ый|ая|ое)|Совместим(?:ый|ая|ое))\b")
_DESC_FIELD_START_RE = re.compile(
    r"(?iu)^(?:тип|партномер|совместимость|ресурс|цвет|оригинальность|"
    r"технология(?:\s+печати)?|коды\s+расходников|для\s+бренда)\s*:"
)
_DESC_FIELD_LABEL_RE = re.compile(
    r"(?iu)\b(?:тип|партномер|совместимость|ресурс|цвет|оригинальность|"
    r"технология(?:\s+печати)?|коды\s+расходников|для\s+бренда)\s*:"
)

def _strip_originality_suffix(name: str) -> str:
    return re.sub(r"\s*\((?:оригинал|совместимый)\)\s*$", "", norm_ws(name), flags=re.I).strip()

def _apply_originality_suffix(name: str, status: str) -> str:
    base_name = _strip_originality_suffix(name)
    if status == "original":
        return f"{base_name} (оригинал)"
    if status == "compatible":
        return f"{base_name} (совместимый)"
    return base_name

def _strip_leading_type_phrase(text: str, type_label: str) -> str:
    src = norm_ws(text)
    tl = norm_ws(type_label)
    if not src or not tl:
        return src
    pat = re.compile(rf"(?iu)^\s*{re.escape(tl)}(?=$|[\s.,:;()\-–—])")
    m = pat.match(src)
    if not m:
        return src
    return norm_ws(src[m.end():].lstrip(" .,:;()-–—"))

def _contains_token(hay: str, needle: str) -> bool:
    h = norm_ws(hay).casefold()
    n = norm_ws(needle).casefold()
    return bool(h and n and n in h)

def _append_intro_field(parts: list[str], label: str, value: str, *, seen_text: str) -> None:
    val = norm_ws(value)
    if not val:
        return
    if _contains_token(seen_text, val):
        return
    parts.append(f"{label} — {val}")

def _desc_needs_seo_intro(desc: str) -> bool:
    d = norm_ws(desc)
    if not d:
        return True
    if _DESC_FIELD_START_RE.match(d):
        return True
    field_hits = len(_DESC_FIELD_LABEL_RE.findall(d))
    if field_hits >= 2 and d.count(";") >= 2 and len(d) <= 280:
        return True
    return False

def _build_consumable_seo_intro(
    *,
    title_core: str,
    status: str,
    type_name: str,
    params: list[tuple[str, str]],
    part_number: str,
    compat: str,
    resource: str,
    color: str,
    tech: str,
) -> str:
    if status not in {"original", "compatible"}:
        return ""

    title_clean = _strip_originality_suffix(title_core)
    type_label = _detect_consumable_type_label(title_clean, type_name, params)
    sentence = _build_originality_sentence(status, type_label).rstrip(".")
    title_rest = _strip_leading_type_phrase(title_clean, type_label)

    if title_rest and title_rest.casefold() != title_clean.casefold():
        first = f"{sentence} {title_rest}".strip()
    else:
        first = sentence

    parts: list[str] = [first]
    seen_text = f"{title_clean} {first}"

    _append_intro_field(parts, "партномер", part_number, seen_text=seen_text)
    _append_intro_field(parts, "совместимость", compat, seen_text=seen_text)
    _append_intro_field(parts, "цвет", color, seen_text=seen_text)
    _append_intro_field(parts, "ресурс", resource, seen_text=seen_text)
    _append_intro_field(parts, "технология печати", tech, seen_text=seen_text)

    intro = "; ".join([x for x in parts if norm_ws(x)]).strip()
    if intro and not intro.endswith("."):
        intro += "."
    return intro

def _upsert_param(params: list[tuple[str, str]], key: str, value: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    inserted = False
    key_cf = norm_ws(key).casefold()
    for k, v in params:
        if norm_ws(k).casefold() == key_cf:
            if not inserted and norm_ws(value):
                out.append((key, value))
                inserted = True
            continue
        out.append((k, v))
    if not inserted and norm_ws(value):
        out.append((key, value))
    return out

def _detect_consumable_type_label(name: str, type_name: str, params: list[tuple[str, str]]) -> str:
    title_cf = norm_ws(name).casefold()
    type_cf = norm_ws(type_name).casefold()
    param_type_cf = " ".join(
        norm_ws(v).casefold() for k, v in params if norm_ws(k).casefold() == "тип" and norm_ws(v)
    )
    hay = " ".join(x for x in (title_cf, type_cf, param_type_cf) if x)

    checks = (
        ("картридж скрепок для буклетирования", "Картридж скрепок для буклетирования"),
        ("картридж скрепок", "Картридж скрепок"),
        ("бункер для отработанного тонера", "Бункер для отработанного тонера"),
        ("бункер отработанного тонера", "Бункер для отработанного тонера"),
        ("контейнер для отработанного тонера", "Контейнер для отработанного тонера"),
        ("контейнер отработанного тонера", "Контейнер для отработанного тонера"),
        ("поглотитель чернил", "Поглотитель чернил"),
        ("абсорбер", "Абсорбер"),
        ("копи-картридж", "Копи-картридж"),
        ("принт-картридж", "Принт-картридж"),
        ("картридж для плоттера", "Картридж для плоттера"),
        ("ролик переноса", "Ролик переноса"),
        ("лента переноса", "Лента переноса"),
        ("блок переноса", "Блок переноса"),
        ("блок проявки", "Блок проявки"),
        ("узел проявки", "Узел проявки"),
        ("ремонтный комплект", "Ремонтный комплект"),
        ("драм-юнит", "Драм-юнит"),
        ("драм-картридж", "Драм-картридж"),
        ("драм", "Драм-картридж"),
        ("фотобарабан", "Фотобарабан"),
        ("печатающая головка", "Печатающая головка"),
        ("комплект печатающих головок", "Печатающая головка"),
        ("головка", "Печатающая головка"),
        ("тонер-картридж", "Тонер-картридж"),
        ("девелопер", "Девелопер"),
        ("чернила", "Чернила"),
        ("картридж", "Картридж"),
        ("тонер", "Тонер"),
    )
    for needle, label in checks:
        if needle in hay:
            return label

    if type_cf == "контейнер":
        return "Контейнер для отработанного тонера"
    if type_cf == "бункер":
        return "Бункер для отработанного тонера"

    return norm_ws(type_name) or "Расходный материал"

def _build_originality_sentence(status: str, type_label: str) -> str:
    tl = norm_ws(type_label)
    tl_cf = tl.casefold()
    original_map = {
        "лента переноса": "Оригинальная лента переноса.",
        "печатающая головка": "Оригинальная печатающая головка.",
        "чернила": "Оригинальные чернила.",
        "картридж скрепок": "Оригинальный картридж скрепок.",
        "картридж скрепок для буклетирования": "Оригинальный картридж скрепок для буклетирования.",
        "бункер для отработанного тонера": "Оригинальный бункер для отработанного тонера.",
        "контейнер для отработанного тонера": "Оригинальный контейнер для отработанного тонера.",
        "поглотитель чернил": "Оригинальный поглотитель чернил.",
        "абсорбер": "Оригинальный абсорбер.",
        "блок проявки": "Оригинальный блок проявки.",
        "узел проявки": "Оригинальный узел проявки.",
        "ремонтный комплект": "Оригинальный ремонтный комплект.",
        "ролик переноса": "Оригинальный ролик переноса.",
        "блок переноса": "Оригинальный блок переноса.",
        "драм-юнит": "Оригинальный драм-юнит.",
        "драм-картридж": "Оригинальный драм-картридж.",
        "копи-картридж": "Оригинальный копи-картридж.",
        "принт-картридж": "Оригинальный принт-картридж.",
        "картридж для плоттера": "Оригинальный картридж для плоттера.",
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
        "картридж скрепок": "Совместимый картридж скрепок.",
        "картридж скрепок для буклетирования": "Совместимый картридж скрепок для буклетирования.",
        "бункер для отработанного тонера": "Совместимый бункер для отработанного тонера.",
        "контейнер для отработанного тонера": "Совместимый контейнер для отработанного тонера.",
        "поглотитель чернил": "Совместимый поглотитель чернил.",
        "абсорбер": "Совместимый абсорбер.",
        "блок проявки": "Совместимый блок проявки.",
        "узел проявки": "Совместимый узел проявки.",
        "ремонтный комплект": "Совместимый ремонтный комплект.",
        "ролик переноса": "Совместимый ролик переноса.",
        "блок переноса": "Совместимый блок переноса.",
        "драм-юнит": "Совместимый драм-юнит.",
        "драм-картридж": "Совместимый драм-картридж.",
        "копи-картридж": "Совместимый копи-картридж.",
        "принт-картридж": "Совместимый принт-картридж.",
        "картридж для плоттера": "Совместимый картридж для плоттера.",
        "фотобарабан": "Совместимый фотобарабан.",
        "тонер-картридж": "Совместимый тонер-картридж.",
        "девелопер": "Совместимый девелопер.",
        "картридж": "Совместимый картридж.",
        "тонер": "Совместимый тонер.",
        "расходный материал": "Совместимый расходный материал.",
    }
    if status == "original":
        return original_map.get(tl_cf, f"Оригинальный {tl.lower()}." if tl else "Оригинальный расходный материал.")
    if status == "compatible":
        return compatible_map.get(tl_cf, f"Совместимый {tl.lower()}." if tl else "Совместимый расходный материал.")
    return ""

def _is_consumable_for_originality(raw: dict, name: str, type_name: str, params: list[tuple[str, str]]) -> bool:
    hay = " ".join(
        [
            norm_ws(name),
            norm_ws(type_name),
            norm_ws(safe_str(raw.get("description_body"))),
            norm_ws(safe_str(raw.get("description_meta"))),
        ]
        + [norm_ws(k) for k, _ in params]
        + [norm_ws(v) for _, v in params]
    ).casefold()
    needles = (
        "картридж",
        "тонер-картридж",
        "копи-картридж",
        "принт-картридж",
        "картридж для плоттера",
        "тонер",
        "драм",
        "драм-юнит",
        "фотобарабан",
        "девелопер",
        "чернила",
        "печатающая головка",
        "ролик переноса",
        "лента переноса",
        "блок переноса",
        "блок проявки",
        "контейнер для отработанного тонера",
        "контейнер отработанного тонера",
        "бункер для отработанного тонера",
        "бункер отработанного тонера",
        "поглотитель чернил",
        "абсорбер",
        "картридж скрепок",
        "ремонтный комплект",
    )
    return any(x in hay for x in needles)

def _detect_consumable_originality(raw: dict, name: str, type_name: str, params: list[tuple[str, str]]) -> str:
    if not _is_consumable_for_originality(raw, name, type_name, params):
        return ""
    source_text = "\n".join(
        [
            safe_str(raw.get("name")),
            safe_str(raw.get("description_body")),
            safe_str(raw.get("description_meta")),
            safe_str(raw.get("sku")),
        ]
    )
    if _ORIGINALITY_MARK_RE.search(source_text):
        return "original"
    return "compatible"

def _apply_consumable_originality(name: str, params: list[tuple[str, str]], native_desc: str, status: str, type_name: str) -> tuple[str, list[tuple[str, str]], str]:
    if status not in {"original", "compatible"}:
        return name, params, native_desc

    value = "Оригинал" if status == "original" else "Совместимый"
    name_out = _apply_originality_suffix(name, status)
    params_out = _upsert_param(params, _ORIGINALITY_PARAM_NAME, value)

    desc_out = norm_ws(native_desc)
    sentence = _build_originality_sentence(status, _detect_consumable_type_label(name_out, type_name, params_out))
    if sentence:
        if not desc_out:
            desc_out = sentence
        elif not _DESC_ORIGINALITY_HEAD_RE.match(desc_out):
            desc_out = f"{sentence} {desc_out}"

    return name_out, params_out, desc_out

def build_offer_from_raw(raw: dict, *, id_prefix: str = "VT", placeholder_picture: str | None = None) -> OfferOut | None:
    clean_title_value = clean_title(norm_ws(raw.get("name")))
    title = norm_ws(clean_title_value)
    if not title:
        return None

    sku = safe_str(raw.get("sku"))
    raw_params = raw.get("params") or []
    source_categories = list(
        raw.get("source_categories") or ([] if not safe_str(raw.get("category_code")) else [safe_str(raw.get("category_code"))])
    )

    vendor_pre = _canonical_vendor(guess_vendor(safe_str(raw.get("vendor")), clean_title_value, raw_params))
    type_name = infer_type(source_categories, clean_title_value)
    type_name = _prefer_title_type(type_name, clean_title_value)
    tech = infer_tech(source_categories, type_name, clean_title_value)
    part_number = extract_part_number(raw, raw_params, clean_title_value)

    title_no_suffix = _strip_originality_suffix(title).strip(" ,")
    if part_number:
        title_no_suffix = re.sub(rf"(?:,?\s*{re.escape(part_number)})+$", "", title_no_suffix, flags=re.I).strip(" ,")
    if sku:
        title_no_suffix = re.sub(rf"(?:,?\s*{re.escape(sku)})+$", "", title_no_suffix, flags=re.I).strip(" ,")
    title_no_suffix = _strip_tail_noise(title_no_suffix)

    compat = extract_compat(
        clean_title_value,
        vendor_pre,
        raw_params,
        safe_str(raw.get("description_body")),
        part_number,
        sku,
    )
    title_no_suffix = _repair_known_titles(title_no_suffix, compat)
    originality_status = _detect_consumable_originality(raw, clean_title_value, type_name, raw_params)
    title = _apply_originality_suffix(norm_ws(title_no_suffix), originality_status)

    resource = extract_resource(
        clean_title_value,
        raw_params,
        safe_str(raw.get("description_body")),
    )
    codes = collect_codes(raw, raw_params, resource, part_number, compat)
    display_part_number = derive_display_part_number(
        title=title,
        raw_part_number=part_number,
        codes=codes,
    )

    vendor = _resolve_vendor(
        raw_vendor=safe_str(raw.get("vendor")),
        title=title,
        params=raw_params,
        compat=compat,
        description_text=safe_str(raw.get("description_body") or raw.get("description_meta")),
        codes=codes,
        part_number=part_number,
        display_part_number=display_part_number,
    )

    params = _merge_params(
        raw,
        vendor,
        type_name,
        tech,
        part_number,
        display_part_number,
        codes,
        clean_title_value,
        compat,
        resource,
    )

    raw_price = int(raw.get("price_rub_raw") or 0)
    price = compute_price(raw_price)

    pictures = collect_picture_urls(
        [safe_str(x) for x in (raw.get("pictures") or []) if safe_str(x)],
        placeholder_picture=(placeholder_picture or PLACEHOLDER),
    )

    color = ""
    for k, v in params:
        if k == "Цвет" and not color:
            color = norm_color(v)

    desc = build_native_description(
        title=title,
        type_name=type_name,
        part_number=(display_part_number or part_number),
        compat=compat,
        resource=resource,
        color=color,
        is_original=(originality_status == "original"),
        desc_body=safe_str(raw.get("description_body") or raw.get("description_meta")),
    )
    if _is_consumable_for_originality(raw, clean_title_value, type_name, raw_params) and _desc_needs_seo_intro(desc):
        seo_intro = _build_consumable_seo_intro(
            title_core=norm_ws(title_no_suffix),
            status=originality_status,
            type_name=type_name,
            params=params,
            part_number=(display_part_number or part_number),
            compat=compat,
            resource=resource,
            color=color,
            tech=tech,
        )
        if seo_intro:
            desc = seo_intro
    params = _upsert_param(params, _ORIGINALITY_PARAM_NAME, "Оригинал" if originality_status == "original" else ("Совместимый" if originality_status == "compatible" else ""))
    title, params, desc = _apply_consumable_originality(title, params, desc, originality_status, type_name)

    stable_offer_code = _select_stable_offer_code(
        raw_params=raw_params,
        raw_sku=sku,
        part_number=part_number,
        display_part_number=display_part_number,
    )
    oid = build_offer_oid(
        raw_vendor_code=stable_offer_code,
        raw_id=make_oid(stable_offer_code or sku, clean_title_value),
        prefix=id_prefix,
    )
    if not oid:
        return None

    return OfferOut(
        oid=oid,
        available=True,
        name=title,
        price=price,
        pictures=pictures,
        vendor=vendor,
        params=params,
        native_desc=desc,
    )

__all__ = [
    "SKIP_PARAM_KEYS",
    "build_offer_from_raw",
]
