# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/comportal/pictures.py

Только картинки ComPortal.

Роль:
- очистить urls;
- нормализовать comportal-ссылки к https;
- dedupe;
- вернуть placeholder если ничего не осталось.
"""

from __future__ import annotations

from cs.util import norm_ws


def _normalize_picture_url(url: str) -> str:
    u = norm_ws(url).replace(" ", "%20")
    if not u:
        return ""

    low = u.casefold()
    if low.startswith("http://www.comportal.kz/"):
        return "https://www.comportal.kz/" + u[len("http://www.comportal.kz/"):]
    if low.startswith("http://comportal.kz/"):
        return "https://comportal.kz/" + u[len("http://comportal.kz/"):]
    return u


def collect_picture_urls(urls: list[str], *, placeholder_picture: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    for raw in urls or []:
        u = _normalize_picture_url(raw)
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
