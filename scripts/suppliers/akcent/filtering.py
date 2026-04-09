# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/akcent/filtering.py

AkCent supplier layer — жёсткая фильтрация входного потока.

Логика:
- пропускаем только товары, чьё name начинается с разрешённого префикса;
- article-исключения режем сразу;
- water-filter cartridge кейсы (Philips/AWP) режем отдельным правилом;
- всё лишнее отсекаем на входе;
- возвращаем filtered list и подробный report для diagnostics/orchestrator.
"""

from __future__ import annotations

from collections import Counter
import re
from typing import Any, Iterable


_SPACE_RE = re.compile(r"\s+")


# -----------------------------
# Базовые helper'ы
# -----------------------------

def _norm_text(value: Any) -> str:
    """Короткая нормализация текста для сравнений."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("Ё", "Е").replace("ё", "е")
    text = _SPACE_RE.sub(" ", text)
    return text.casefold()


def _get_field(obj: Any, *names: str) -> Any:
    """Безопасно достаёт поле у объекта или dict."""
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj.get(name)
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _offer_text_blob(src: Any) -> str:
    """Собирает текст товара для drop-rules."""
    parts = [
        _get_field(src, "name"),
        _get_field(src, "article"),
        _get_field(src, "model"),
        _get_field(src, "type"),
        _get_field(src, "vendor"),
        _get_field(src, "url"),
        _get_field(src, "description"),
    ]
    return " ".join(str(item or "") for item in parts if item)


# -----------------------------
# Config resolve helper'ы
# -----------------------------

def _resolve_prefixes(
    *,
    filter_cfg: dict[str, Any] | None,
    prefixes: Iterable[str] | None,
    allowed_prefixes: Iterable[str] | None,
) -> list[str]:
    """Достаёт allow-префиксы backward-safe из cfg / прямых аргументов."""
    cfg = filter_cfg or {}
    include_rules = cfg.get("include_rules") or {}

    raw = (
        list(prefixes or [])
        or list(allowed_prefixes or [])
        or list(include_rules.get("name_prefixes") or [])
        or list(cfg.get("name_prefixes") or [])
        or list(include_rules.get("allow_name_prefixes") or [])
        or list(cfg.get("allow_name_prefixes") or [])
    )

    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        key = _norm_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _resolve_drop_articles(filter_cfg: dict[str, Any] | None) -> set[str]:
    """Достаёт article-drop list backward-safe."""
    cfg = filter_cfg or {}
    exclude_rules = cfg.get("exclude_rules") or {}
    raw = list(exclude_rules.get("articles") or []) or list(cfg.get("drop_articles") or [])
    return {_norm_text(item) for item in raw if str(item or "").strip()}


def _resolve_drop_rules(filter_cfg: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Достаёт generic drop-rules backward-safe."""
    cfg = filter_cfg or {}
    exclude_rules = cfg.get("exclude_rules") or {}
    return list(exclude_rules.get("rules") or []) or list(cfg.get("drop_rules") or [])


# -----------------------------
# Drop-rule matching
# -----------------------------

def _match_any_of(text_blob_norm: str, values: Iterable[Any]) -> bool:
    """any_of: хотя бы один токен из группы должен встретиться."""
    for value in values:
        token = _norm_text(value)
        if token and token in text_blob_norm:
            return True
    return False


def _match_rule(text_blob_norm: str, rule: dict[str, Any]) -> bool:
    """all_groups: каждая группа должна дать хотя бы одно совпадение."""
    groups = list(rule.get("all_groups") or [])
    if not groups:
        return False

    for group in groups:
        if not isinstance(group, dict):
            return False
        if not _match_any_of(text_blob_norm, group.get("any_of") or []):
            return False
    return True


def _match_allowed_prefix(name: str, prefixes: list[str]) -> str:
    """Проверяет жёсткий startswith по разрешённым префиксам."""
    name_norm = _norm_text(name)
    for prefix in prefixes:
        if name_norm.startswith(_norm_text(prefix)):
            return prefix
    return ""


# -----------------------------
# Главная фильтрация
# -----------------------------

def filter_source_offers(
    source_offers: list[Any],
    *,
    filter_cfg: dict[str, Any] | None = None,
    prefixes: Iterable[str] | None = None,
    allowed_prefixes: Iterable[str] | None = None,
    mode: str = "include",
) -> tuple[list[Any], dict[str, Any]]:
    """Главная фильтрация source-offers для AkCent."""
    mode_norm = str(mode or "include").strip().casefold() or "include"
    if mode_norm != "include":
        raise ValueError(f"AkCent filtering supports only include mode, got: {mode!r}")

    allow_prefixes = _resolve_prefixes(
        filter_cfg=filter_cfg,
        prefixes=prefixes,
        allowed_prefixes=allowed_prefixes,
    )
    if not allow_prefixes:
        raise ValueError("AkCent filtering requires non-empty allow prefix list")

    drop_articles = _resolve_drop_articles(filter_cfg)
    drop_rules = _resolve_drop_rules(filter_cfg)

    kept: list[Any] = []
    rejected_counts: Counter[str] = Counter()
    prefix_hits: Counter[str] = Counter()

    for src in source_offers or []:
        name = str(_get_field(src, "name") or "").strip()
        article = str(_get_field(src, "article") or "").strip()

        if not name:
            rejected_counts["empty_name"] += 1
            continue

        article_norm = _norm_text(article)
        if article_norm and article_norm in drop_articles:
            rejected_counts["drop_article"] += 1
            continue

        matched_prefix = _match_allowed_prefix(name, allow_prefixes)
        if not matched_prefix:
            rejected_counts["name_prefix_not_allowed"] += 1
            continue

        text_blob_norm = _norm_text(_offer_text_blob(src))
        rejected_by_rule = False
        for rule in drop_rules:
            if isinstance(rule, dict) and _match_rule(text_blob_norm, rule):
                rejected_counts["drop_rule"] += 1
                rejected_by_rule = True
                break
        if rejected_by_rule:
            continue

        kept.append(src)
        prefix_hits[matched_prefix] += 1

    report: dict[str, Any] = {
        "mode": "include",
        "before": len(source_offers or []),
        "after": len(kept),
        "rejected_total": max(0, len(source_offers or []) - len(kept)),
        "allowed_prefixes": list(allow_prefixes),
        "allowed_prefix_count": len(allow_prefixes),
        "kept_by_prefix": dict(sorted(prefix_hits.items())),
        "reject_reasons": dict(sorted(rejected_counts.items())),
        "drop_articles": sorted(drop_articles),
        "drop_rules_count": len(drop_rules),
    }
    return kept, report
