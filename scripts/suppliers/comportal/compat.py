# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/comportal/compat.py

ComPortal Compat — supplier-layer нормализация совместимости и кодов.

Что делает:
- нормализует OEM, партномера и compat-токены;
- очищает supplier-specific совместимость;
- готовит compat-данные для params и builder слоя.

Что не делает:
- не переносит compat-логику в shared core;
- не строит финальный shared description;
- не заменяет params.py и builder.py.
"""

from __future__ import annotations

import re
from typing import Iterable

from cs.util import norm_ws
from suppliers.comportal.models import ParamItem


_MULTI_WS_RE = re.compile(r"\s{2,}")
_COMMA_WS_RE = re.compile(r"\s*,\s*")
_SLASH_WS_RE = re.compile(r"\s*/\s*")
_SEMICOLON_WS_RE = re.compile(r"\s*;\s*")


def _normalize_codes_value(value: str) -> str:
    s = norm_ws(value)
    if not s:
        return ""
    s = _SLASH_WS_RE.sub(" / ", s)
    s = _COMMA_WS_RE.sub(", ", s)
    s = _SEMICOLON_WS_RE.sub("; ", s)
    s = _MULTI_WS_RE.sub(" ", s)
    return s.strip(" ,;/")


def _normalize_compat_value(value: str) -> str:
    s = norm_ws(value)
    if not s:
        return ""
    s = _SLASH_WS_RE.sub(" / ", s)
    s = _COMMA_WS_RE.sub(", ", s)
    s = _SEMICOLON_WS_RE.sub("; ", s)
    s = _MULTI_WS_RE.sub(" ", s)
    return s.strip(" ,;/")


def apply_compat_cleanup(params: Iterable[ParamItem]) -> list[ParamItem]:
    """
    Применить безопасную cleanup-логику к supplier params.
    Совместимость не генерируем — только чистим уже найденное.
    """
    out: list[ParamItem] = []

    for p in params or []:
        name = norm_ws(p.name)
        value = norm_ws(p.value)
        if not name or not value:
            continue

        ncf = name.casefold()
        if ncf in {"коды", "модель", "партномер", "номер"}:
            value = _normalize_codes_value(value)
        elif ncf == "совместимость":
            value = _normalize_compat_value(value)

        if not value:
            continue

        out.append(ParamItem(name=name, value=value, source=p.source))

    return out


__all__ = [
    "apply_compat_cleanup",
]
