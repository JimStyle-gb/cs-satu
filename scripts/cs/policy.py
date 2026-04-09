# -*- coding: utf-8 -*-
"""
Path: scripts/cs/policy.py

CS Policy — общий backward-safe policy-слой.

Что делает:
- хранит общий dataclass SupplierPolicy;
- даёт backward-safe API для старых импортов;
- фиксирует, что shared core не должен быть supplier-aware.

Что не делает:
- не хранит supplier-specific policy;
- не принимает решения по конкретным товарам;
- не заменяет supplier-layer и builder-логику.
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
