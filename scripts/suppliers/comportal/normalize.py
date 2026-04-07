# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/comportal/normalize.py

Базовая supplier-нормализация полей ComPortal.

Что улучшено:
- v43: чище public model/title для Dell corporate и Canon plotter кейсов;
- public name чище:
  - HP Europe -> HP
  - Hewlett Packard / Hewlett-Packard -> HP
  - HP Enterprise -> HPE
  - МФП -> МФУ
- vendor inference больше не узкий:
  - ищем бренд в vendor
  - в нескольких param-ключах
  - в name
  - в description
- это добивает кейсы вроде AIWA, где бренд есть в name/params,
  но раньше падал в fallback "CS".
"""

from __future__ import annotations

import re

from cs.util import norm_ws, safe_int
from suppliers.comportal.models import ParamItem


_VENDOR_CANON_MAP = {
    "HP EUROPE": "HP",
    "HP INC.": "HP",
    "HEWLETT PACKARD": "HP",
    "HEWLETT-PACKARD": "HP",
    "HP": "HP",
    "HPE": "HPE",
    "HP ENTERPRISE": "HPE",
    "HEWLETT PACKARD ENTERPRISE": "HPE",
    "CANON": "Canon",
    "EPSON": "Epson",
    "XEROX": "Xerox",
    "BROTHER": "Brother",
    "KYOCERA": "Kyocera",
    "PANTUM": "Pantum",
    "RICOH": "Ricoh",
    "APC": "APC",
    "DELL": "Dell",
    "LENOVO": "Lenovo",
    "ASUS": "ASUS",
    "ACER": "Acer",
    "MSI": "MSI",
    "LG": "LG",
    "SAMSUNG": "Samsung",
    "IIYAMA": "iiyama",
    "GIGABYTE": "Gigabyte",
    "HIKVISION": "Hikvision",
    "MICROSOFT": "Microsoft",
    "KASPERSKY": "Kaspersky",
    "DR.WEB": "Dr.Web",
    "DR. WEB": "Dr.Web",
    "VIEWSONIC": "ViewSonic",
    "BENQ": "BenQ",
    "AOC": "AOC",
    "HUAWEI": "Huawei",
    "TP-LINK": "TP-Link",
    "TPLINK": "TP-Link",
    "D-LINK": "D-Link",
    "DLINK": "D-Link",
    "CISCO": "Cisco",
    "ZYXEL": "Zyxel",
    "EATON": "Eaton",
    "AIWA": "AIWA",
    "POLY": "Poly",
}

_NAME_VENDOR_PATTERNS: list[tuple[str, str]] = [
    (r"\bHP\s+Europe\b", "HP"),
    (r"\bHP\s+Inc\.\b", "HP"),
    (r"\bHPE\b", "HPE"),
    (r"\bHP\b", "HP"),
    (r"\bCanon\b", "Canon"),
    (r"\bEpson\b", "Epson"),
    (r"\bXerox\b", "Xerox"),
    (r"\bBrother\b", "Brother"),
    (r"\bKyocera\b", "Kyocera"),
    (r"\bPantum\b", "Pantum"),
    (r"\bRicoh\b", "Ricoh"),
    (r"\bAPC\b", "APC"),
    (r"\bDell\b", "Dell"),
    (r"\bLenovo\b", "Lenovo"),
    (r"\bASUS\b", "ASUS"),
    (r"\bAcer\b", "Acer"),
    (r"\bMSI\b", "MSI"),
    (r"\bLG\b", "LG"),
    (r"\bSamsung\b", "Samsung"),
    (r"\biiyama\b", "iiyama"),
    (r"\bGigabyte\b", "Gigabyte"),
    (r"\bHikvision\b", "Hikvision"),
    (r"\bViewSonic\b", "ViewSonic"),
    (r"\bBenQ\b", "BenQ"),
    (r"\bAOC\b", "AOC"),
    (r"\bHuawei\b", "Huawei"),
    (r"\bTP-?Link\b", "TP-Link"),
    (r"\bD-?Link\b", "D-Link"),
    (r"\bCisco\b", "Cisco"),
    (r"\bZyxel\b", "Zyxel"),
    (r"\bMicrosoft\b", "Microsoft"),
    (r"\bEaton\b", "Eaton"),
    (r"\bAIWA\b", "AIWA"),
    (r"\bPoly\b", "Poly"),
]

_DEVICE_WORDS_RE = re.compile(
    r"(?iu)^(?:моноблок|ноутбук|принтер|мфу|сканер|проектор|монитор|плоттер|компьютер|настольный\s+пк|широкоформатный\s+принтер)\b\s*"
)
_VENDOR_HEAD_RE = re.compile(
    r"(?iu)^(?:AIWA|Dell|HP|Canon|Epson|Xerox|Brother|Kyocera|Pantum|Ricoh|APC|Lenovo|ASUS|Acer|MSI|LG|Samsung|Huawei|iiyama|Gigabyte|Hikvision|ViewSonic|BenQ|AOC|TP\-?Link|D\-?Link|Cisco|Zyxel|Eaton|Poly)\b\s*"
)
_DUPLICATE_HEAD_RE = re.compile(
    r"(?iu)^(Плоттер|Монитор|Ноутбук|Моноблок|Компьютер|Принтер|МФУ)\s+"
    r"(Canon|Dell|HP|Epson|Xerox|Brother|Kyocera|Pantum|Ricoh|APC|Lenovo|ASUS|Acer|MSI|LG|Samsung|Huawei|iiyama|Gigabyte|Hikvision|ViewSonic|BenQ|AOC|TP\-?Link|D\-?Link|Cisco|Zyxel|Eaton|Poly)"
    r"\s+\1\s+\2(?:\s*/\s*|\s+)"
)
_CODE_PAREN_RE = re.compile(r"\(([^()]{2,})\)\s*$")
_CODE_LIKE_MODEL_RE = re.compile(r"(?iu)^[A-Z0-9][A-Z0-9_#./\-]{4,}$")
_IMAGEPROGRAF_RE = re.compile(r"(?iu)\bimagePROGRAF\s+[A-Z]{1,4}\-?\d{3,5}\b")
_DELL_SERIES_RE = re.compile(
    r"(?iu)\b(?:Pro\s+(?:Micro|Slim|Tower|Max|Plus)\s+[A-Z0-9-]+|"
    r"Pro\s+\d+\s+All\-in\-One(?:\s+Plus)?\s+[A-Z0-9-]+|"
    r"OptiPlex\s+[A-Z0-9-]+|"
    r"Latitude\s+[A-Z0-9-]+|"
    r"Precision\s+[A-Z0-9-]+|"
    r"AW\d{4,5}[A-Z]{0,4}|"
    r"S\d{4}[A-Z]{0,4}|"
    r"SE\d{4}[A-Z]{0,4}|"
    r"P\d{4}[A-Z]{0,4})\b"
)
_CANON_SERIES_RE = re.compile(r"(?iu)\b(?:imagePROGRAF\s+[A-Z]{1,4}\-?\d{3,5}|i\-SENSYS\s+[A-Z0-9-]+)\b")

_GENERIC_VENDOR_WORDS = {
    "МФП", "МФУ", "ПРИНТЕР", "НОУТБУК", "МОНИТОР", "ИБП", "СКАНЕР", "ПРОЕКТОР",
    "КАРТРИДЖ", "ТОНЕР", "БАТАРЕЯ", "АККУМУЛЯТОР", "СТАБИЛИЗАТОР", "МОНОБЛОК",
    "СЕРВЕР", "КОММУТАТОР", "МАРШРУТИЗАТОР", "ДИСПЛЕЙ", "ПЛОТТЕР", "РАБОЧАЯ",
}


def _param_map(params: list[ParamItem]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in params or []:
        name = norm_ws(p.name)
        value = norm_ws(p.value)
        if name and value:
            out[name] = value
    return out


def _canonical_vendor_token(vendor: str) -> str:
    s = norm_ws(vendor)
    if not s:
        return ""

    up = s.upper()
    if up in _GENERIC_VENDOR_WORDS:
        return ""

    if up in _VENDOR_CANON_MAP:
        return _VENDOR_CANON_MAP[up]

    if up.startswith("HP EUROPE"):
        return "HP"
    if up.startswith("HEWLETT PACKARD ENTERPRISE"):
        return "HPE"
    if up.startswith("HEWLETT PACKARD"):
        return "HP"
    if up.startswith("HP ENTERPRISE"):
        return "HPE"

    return s


def _infer_vendor_from_text(text: str) -> str:
    s = norm_ws(text)
    if not s:
        return ""
    for pattern, vendor in _NAME_VENDOR_PATTERNS:
        if re.search(pattern, s, flags=re.IGNORECASE):
            return vendor
    return ""


def _canonicalize_brand_tokens_in_name(name: str) -> str:
    s = norm_ws(name)
    if not s:
        return ""

    replacements = [
        (r"\bHP\s+Europe\b", "HP"),
        (r"\bHP\s+Inc\.\b", "HP"),
        (r"\bHewlett[\- ]Packard\s+Enterprise\b", "HPE"),
        (r"\bHewlett[\- ]Packard\b", "HP"),
        (r"\bHP\s+Enterprise\b", "HPE"),
        (r"\bМФП\b", "МФУ"),
        (r"\bAsus\b", "ASUS"),
    ]
    for pattern, repl in replacements:
        s = re.sub(pattern, repl, s, flags=re.IGNORECASE)

    s = re.sub(r"\s+", " ", s).strip()
    return s


def _clean_title_tail(name: str) -> str:
    s = normalize_name(name)
    s = _DUPLICATE_HEAD_RE.sub(lambda m: f"{m.group(1)} {m.group(2)} ", s)
    m = _CODE_PAREN_RE.search(s)
    if m:
        s = s[:m.start()].strip()
    s = re.sub(r"\s+", " ", s).strip(" /-–—,;:.")
    return s


def _strip_public_head(title_tail: str) -> str:
    s = norm_ws(title_tail)
    if not s:
        return ""
    changed = True
    while changed:
        changed = False
        nxt = _DEVICE_WORDS_RE.sub("", s).strip(" /-–—,;:.")
        if nxt != s:
            s = nxt
            changed = True
        nxt = _VENDOR_HEAD_RE.sub("", s).strip(" /-–—,;:.")
        if nxt != s:
            s = nxt
            changed = True
    s = s.replace(" / ", " / ")
    s = re.sub(r"\s{2,}", " ", s).strip(" /-–—,;:.")
    return s


def _extract_public_model_from_name(name: str) -> str:
    s = _clean_title_tail(name)
    if not s:
        return ""

    for rx in (_DELL_SERIES_RE, _CANON_SERIES_RE, _IMAGEPROGRAF_RE):
        m = rx.search(s)
        if m:
            return norm_ws(m.group(0)).strip(" /-–—,;:.")

    m = re.search(
        r"(?iu)\b(?:моноблок|ноутбук|принтер|мфу|сканер|проектор|монитор|плоттер|компьютер|настольный\s+пк|широкоформатный\s+принтер)\s+"
        r"(?:AIWA|Dell|HP|Canon|Epson|Xerox|Brother|Kyocera|Pantum|Ricoh|APC|Lenovo|ASUS|Acer|MSI|LG|Samsung|Huawei|iiyama|Gigabyte|Hikvision|ViewSonic|BenQ|AOC|TP\-?Link|D\-?Link|Cisco|Zyxel|Eaton|Poly)\s+"
        r"([^()]{3,})",
        s,
    )
    if m:
        tail = _strip_public_head(m.group(1))
        if tail:
            return tail

    tail = _strip_public_head(s)
    return tail


def _is_weak_model_value(value: str, *, name: str) -> bool:
    v = norm_ws(value)
    if not v:
        return True

    up = v.upper()
    if up in _GENERIC_VENDOR_WORDS:
        return True
    if up in _VENDOR_CANON_MAP:
        return True

    inferred_vendor = _infer_vendor_from_text(name)
    if inferred_vendor and v.casefold() == inferred_vendor.casefold():
        return True

    if _CODE_LIKE_MODEL_RE.fullmatch(v) or "_" in v:
        return True

    return False


def normalize_name(name: str) -> str:
    s = norm_ws(name)
    s = _canonicalize_brand_tokens_in_name(s)
    s = _DUPLICATE_HEAD_RE.sub(lambda m: f"{m.group(1)} {m.group(2)} ", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r"\s*/\s*", " / ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def build_offer_oid(raw_vendor_code: str, raw_id: str, *, prefix: str) -> str:
    base = norm_ws(raw_vendor_code) or norm_ws(raw_id)
    if not base:
        return ""
    base = re.sub(r"[^A-Za-z0-9]+", "", base)
    if not base:
        return ""
    if base.upper().startswith(prefix.upper()):
        return base
    return f"{prefix}{base}"


def normalize_available(available_attr: str, available_tag: str, active: str) -> bool:
    av_attr = (available_attr or "").strip().lower()
    if av_attr in ("true", "1", "yes"):
        return True
    if av_attr in ("false", "0", "no"):
        return False

    av_tag = (available_tag or "").strip().lower()
    if av_tag in ("true", "1", "yes"):
        return True
    if av_tag in ("false", "0", "no"):
        return False

    act = (active or "").strip().upper()
    if act == "Y":
        return True
    if act == "N":
        return False

    return False


def normalize_vendor(
    vendor: str,
    *,
    name: str,
    params: list[ParamItem],
    description_text: str = "",
    vendor_blacklist: set[str],
    fallback_vendor: str = "",
) -> str:
    # 1) source vendor
    s = _canonical_vendor_token(vendor)
    if s and s.casefold() in vendor_blacklist:
        s = ""
    if s:
        return s

    # 2) params by priority
    pmap = _param_map(params)
    for key in (
        "Бренд",
        "Для бренда",
        "Производитель",
        "Производитель операционной системы",
        "Производитель чипсета видеокарты",
        "Марка чипсета видеокарты",
        "Модель",
        "Коды",
    ):
        s = _canonical_vendor_token(pmap.get(key, ""))
        if s and s.casefold() in vendor_blacklist:
            s = ""
        if s:
            return s

    # 3) name
    s = _infer_vendor_from_text(name)
    if s and s.casefold() in vendor_blacklist:
        s = ""
    if s:
        return s

    # 4) description
    s = _infer_vendor_from_text(description_text)
    if s and s.casefold() in vendor_blacklist:
        s = ""
    if s:
        return s

    return norm_ws(fallback_vendor)


def normalize_model(name: str, params: list[ParamItem]) -> str:
    pmap = _param_map(params)

    title_public_model = _extract_public_model_from_name(name)

    for key in ("Модель", "Партномер", "Артикул", "Номер"):
        val = norm_ws(pmap.get(key, ""))
        if val and not _is_weak_model_value(val, name=name):
            return val

    if title_public_model and not _is_weak_model_value(title_public_model, name=name):
        return title_public_model

    s = normalize_name(name)

    m = _CODE_PAREN_RE.search(s)
    if m:
        val = norm_ws(m.group(1))
        if val and not _is_weak_model_value(val, name=name):
            return val

    m = re.search(r"\b([A-Z]{1,6}[A-Z0-9/\-]{2,})\b", s)
    if m:
        val = norm_ws(m.group(1))
        if val and not _is_weak_model_value(val, name=name):
            return val

    return ""


def normalize_price_in(price_text: str) -> int | None:
    return safe_int(price_text)
