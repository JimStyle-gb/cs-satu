# -*- coding: utf-8 -*-
"""
Path: scripts/cs/policy.py

CS Policy — shared policy defaults layer.

Что делает:
- держит shared common defaults;
- сохраняет backward-safe API для policy-layer;

Что не делает:
- не содержит supplier-aware repair logic;
- не подменяет supplier raw/policy config.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class SupplierPolicy:
    code: str = "*"
    always_true_available: bool = False
    drop_desc_specs_pairs: bool = False

    # Все supplier-repair флаги в shared core по умолчанию выключены.
    enable_enrich_from_desc: bool = False
    enable_enrich_from_name_desc: bool = False
    enable_auto_compat: bool = False
    enable_apply_color_from_name: bool = False
    enable_split_params_for_chars: bool = False
    enable_clean_params: bool = False

# -----------------------------
# Backward-safe API
# -----------------------------

def _supplier_code_from_oid(oid: str) -> str:
    oid_u = (oid or "").upper()
    return oid_u[:2] if len(oid_u) >= 2 else oid_u

def get_supplier_policy(oid: str) -> SupplierPolicy:
    _ = oid
    # Shared core больше не определяет поведение по поставщику.
    # Возвращаем единый общий policy-объект только для backward compatibility.
    return SupplierPolicy(code="*")
