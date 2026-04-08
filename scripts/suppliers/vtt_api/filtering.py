# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/vtt_api/filtering.py
Только ассортиментная политика.
"""
from __future__ import annotations

from typing import Any


def _norm(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def filter_items(items: list[dict[str, Any]], cfg: dict) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prefixes = [_norm(x) for x in cfg.get("allowed_title_prefixes", []) if _norm(x)]
    mode = _norm(cfg.get("mode") or "passthrough")
    if not prefixes:
        return items, {"mode": mode, "before": len(items), "after": len(items), "rejected_total": 0}

    out: list[dict[str, Any]] = []
    rejected = 0
    for item in items:
        title = _norm(item.get("name"))
        ok = any(title.startswith(p) for p in prefixes)
        if ok:
            out.append(item)
        else:
            rejected += 1
    return out, {
        "mode": mode,
        "before": len(items),
        "after": len(out),
        "rejected_total": rejected,
        "allowed_prefix_count": len(prefixes),
    }
