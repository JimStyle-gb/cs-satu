# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/alstyle/pictures.py

AlStyle Pictures — supplier-layer для картинок.

Что делает:
- собирает и нормализует picture URLs из supplier-source;
- чистит пробелы и убирает дубли;
- возвращает placeholder, если реальных картинок не осталось.

Что не делает:
- не решает вопросы description, params и vendor;
- не меняет business-логику offers;
- не подменяет builder и quality gate.
"""

from __future__ import annotations

from cs.util import norm_ws

def collect_picture_urls(urls: list[str], *, placeholder_picture: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls or []:
        u = norm_ws(raw).replace(" ", "%20")
        if not u:
            continue
        sig = u.casefold()
        if sig in seen:
            continue
        seen.add(sig)
        out.append(u)
    if not out:
        out = [placeholder_picture]
    return out
