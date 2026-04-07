# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/alstyle/normalize.py

Базовая supplier-нормализация полей AlStyle.

Что делает:
- мягко чистит name;
- собирает стабильный oid;
- нормализует available;
- берёт входную цену purchase_price -> price;
- канонизирует vendor и умеет добрать его из name/description, если source vendor пуст.

Важно:
- без supplier-specific compat/params cleanup;
- без final-description логики;
- только базовые поля supplier-layer до builder.py.
"""

from __future__ import annotations

import re

from cs.util import norm_ws, safe_int


_VENDOR_CANON_MAP = {
    "asus": "ASUS",
    "asustek": "ASUS",
    "eaton": "Eaton",
    "iiyama": "iiyama",
    "cyberpower": "CyberPower",
    "apc": "APC",
    "d-link": "D-Link",
    "dlink": "D-Link",
    "tp-link": "TP-Link",
    "tplink": "TP-Link",
    "hewlett packard": "HP",
    "hewlett-packard": "HP",
    "hp inc.": "HP",
    "hp europe": "HP",
}

_NAME_VENDOR_PATTERNS: list[tuple[str, str]] = [
    (r"\bCyberPower\b", "CyberPower"),
    (r"\bAPC\b", "APC"),
    (r"\bEATON\b", "Eaton"),
    (r"\bEaton\b", "Eaton"),
    (r"\bASUS\b", "ASUS"),
    (r"\bAsus\b", "ASUS"),
    (r"\biiyama\b", "iiyama"),
    (r"\bIiyama\b", "iiyama"),
    (r"\bD-?Link\b", "D-Link"),
    (r"\bTP-?Link\b", "TP-Link"),
    (r"\bHewlett[\- ]Packard\b", "HP"),
    (r"\bHP\b", "HP"),
]


def _clean_spaces(text: str) -> str:
    s = norm_ws(text)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _canon_vendor_token(vendor: str) -> str:
    s = _clean_spaces(vendor)
    if not s:
        return ""
    key = s.casefold()
    return _VENDOR_CANON_MAP.get(key, s)


def _infer_vendor_from_text(*parts: str) -> str:
    text = " ".join(_clean_spaces(x) for x in parts if _clean_spaces(x))
    if not text:
        return ""
    for pattern, vendor in _NAME_VENDOR_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return vendor
    return ""


def normalize_name(name: str) -> str:
    s = _clean_spaces(name)
    if not s:
        return ""
    replacements = [
        (r"\bAsus\b", "ASUS"),
        (r"\bEATON\b", "Eaton"),
        (r"\bIiyama\b", "iiyama"),
        (r"\bCyber\s+Power\b", "CyberPower"),
    ]
    for pattern, repl in replacements:
        s = re.sub(pattern, repl, s, flags=re.IGNORECASE)
    return s


def build_offer_oid(raw_id: str, *, prefix: str) -> str:
    rid = _clean_spaces(raw_id)
    if not rid:
        return ""
    if rid.upper().startswith(prefix.upper()):
        return rid
    return f"{prefix}{rid}"


def normalize_available(available_attr: str, available_tag: str) -> bool:
    av_attr = (available_attr or "").strip().lower()
    if av_attr in ("true", "1", "yes"):
        return True
    if av_attr in ("false", "0", "no"):
        return False
    return (available_tag or "").strip().lower() in ("true", "1", "yes")


def normalize_vendor(
    vendor: str,
    *,
    name: str = "",
    description_text: str = "",
    vendor_blacklist: set[str] | None = None,
) -> str:
    """Нормализует vendor и, при необходимости, добирает его из name/description.

    Back-compat:
    - работает и со старым вызовом normalize_vendor(vendor, vendor_blacklist=...)
    - работает и с новым вызовом normalize_vendor(vendor, name=..., description_text=..., vendor_blacklist=...)
    """
    blacklist = {x.casefold() for x in (vendor_blacklist or set()) if norm_ws(x)}

    s = _canon_vendor_token(vendor)
    if s and s.casefold() not in blacklist:
        return s

    guessed = _infer_vendor_from_text(name, description_text)
    if guessed and guessed.casefold() not in blacklist:
        return guessed

    return ""


def normalize_price_in(purchase_price_text: str, price_text: str) -> int | None:
    price_in = safe_int(purchase_price_text)
    if price_in is None:
        price_in = safe_int(price_text)
    return price_in
