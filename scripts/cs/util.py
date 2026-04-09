# -*- coding: utf-8 -*-
"""
Path: scripts/cs/util.py

CS Util — общие мелкие утилиты shared core.

Роль файла:
- текстовые helper-ы без supplier-specific логики;
- единый источник norm_ws / safe_int / fix_mixed_cyr_lat / _truncate_text;
- без зависимостей от cs.core, чтобы не плодить циклические импорты.
"""

from __future__ import annotations

import re
from typing import Any


# -----------------------------
# Regex и mapping-константы
# -----------------------------

_RE_WS = re.compile(r"\s+")
_RE_INT = re.compile(r"-?\d+")
_RE_CYR = re.compile(r"[А-Яа-яЁё]")
_RE_LAT = re.compile(r"[A-Za-z]")
_RE_MIXED_TOKEN = re.compile(r"[A-Za-zА-Яа-яЁё]{2,}")

# Визуально похожие LAT -> CYR для смешанных токенов.
_LAT_TO_CYR = {
    "A": "А",
    "B": "В",
    "C": "С",
    "E": "Е",
    "H": "Н",
    "K": "К",
    "M": "М",
    "O": "О",
    "P": "Р",
    "T": "Т",
    "X": "Х",
    "Y": "У",
    "a": "а",
    "c": "с",
    "e": "е",
    "h": "н",
    "k": "к",
    "m": "м",
    "o": "о",
    "p": "р",
    "t": "т",
    "x": "х",
    "y": "у",
}

# Визуально похожие CYR -> LAT для смешанных токенов.
_CYR_TO_LAT = {
    "А": "A",
    "В": "B",
    "С": "C",
    "Е": "E",
    "Н": "H",
    "К": "K",
    "М": "M",
    "О": "O",
    "Р": "P",
    "Т": "T",
    "Х": "X",
    "У": "Y",
    "а": "a",
    "с": "c",
    "е": "e",
    "н": "h",
    "к": "k",
    "м": "m",
    "о": "o",
    "р": "p",
    "т": "t",
    "х": "x",
    "у": "y",
}


# -----------------------------
# Text helpers
# -----------------------------

def fix_mixed_cyr_lat(s: str) -> str:
    """Чинит смешение кириллицы/латиницы в одном токене."""
    if not s:
        return s

    def _fix_token(m: re.Match[str]) -> str:
        tok = m.group(0)
        has_cyr = bool(_RE_CYR.search(tok))
        has_lat = bool(_RE_LAT.search(tok))
        if not (has_cyr and has_lat):
            return tok

        lat_cnt = sum(("A" <= ch <= "Z") or ("a" <= ch <= "z") for ch in tok)
        cyr_cnt = sum(bool(_RE_CYR.match(ch)) for ch in tok)

        # LAT-перевес считаем техно-токеном/аббревиатурой.
        if lat_cnt >= cyr_cnt:
            return "".join(_CYR_TO_LAT.get(ch, ch) for ch in tok)
        return "".join(_LAT_TO_CYR.get(ch, ch) for ch in tok)

    return _RE_MIXED_TOKEN.sub(_fix_token, s)



def norm_ws(s: str) -> str:
    """Нормализует пробелы и правит смешанную кир/лат."""
    text = (s or "").replace("\u00a0", " ").strip()
    text = _RE_WS.sub(" ", text).strip()
    return fix_mixed_cyr_lat(text)



def safe_int(s: Any) -> int | None:
    """Безопасно парсит int из строки: берёт первое целое."""
    if s is None:
        return None
    text = str(s).strip().replace(" ", "")
    m = _RE_INT.search(text)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None



def _truncate_text(s: str, max_len: int, *, suffix: str = "") -> str:
    """Обрезает plain-text до max_len, опционально с suffix."""
    if not s:
        return ""

    limit = int(max_len or 0)
    if limit <= 0:
        return s
    if len(s) <= limit:
        return s

    tail = suffix or ""
    if not tail:
        return s[:limit].rstrip()
    if limit <= len(tail):
        return tail[:limit].rstrip()

    cut = limit - len(tail)
    return (s[:cut].rstrip() + tail).rstrip()
