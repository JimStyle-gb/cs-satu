# -*- coding: utf-8 -*-
"""
Path: scripts/cs/policy.py

CS Policy — shared policy defaults layer.
Здесь только единый общий policy-объект для shared core.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupplierPolicy:
    code: str = "*"
    always_true_available: bool = False
    drop_desc_specs_pairs: bool = False
    enable_enrich_from_desc: bool = False
    enable_enrich_from_name_desc: bool = False
    enable_auto_compat: bool = False
    enable_apply_color_from_name: bool = False
    enable_split_params_for_chars: bool = False
    enable_clean_params: bool = False


DEFAULT_POLICY = SupplierPolicy(code="*")


def get_supplier_policy(oid: str) -> SupplierPolicy:
    _ = oid
    return DEFAULT_POLICY


__all__ = ["SupplierPolicy", "DEFAULT_POLICY", "get_supplier_policy"]
