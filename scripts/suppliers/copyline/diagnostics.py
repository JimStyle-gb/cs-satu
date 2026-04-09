# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/copyline/diagnostics.py

CopyLine Diagnostics — служебный summary-слой поставщика.

Что делает:
- печатает стабильный build summary для orchestrator;
- выносит diagnostics-логику из build_copyline.py;
- оставляет единый формат вывода для прогона поставщика.

Что не делает:
- не фильтрует и не меняет offers;
- не содержит parsing-логики supplier-layer;
- не подменяет builder и quality gate.
"""

from __future__ import annotations

from typing import Any

_SUMMARY_WIDTH = 72


def _print_filter_report(filter_report: dict[str, Any]) -> None:
    """Напечатать filter_report в стабильном виде."""
    print("filter_report:")
    for key, value in filter_report.items():
        print(f"  {key}: {value}")


def print_build_summary(
    *,
    version: str,
    before: int,
    out_offers: list[Any],
    filter_report: dict[str, Any],
    qg: dict[str, Any],
    out_file: str,
    raw_out_file: str,
) -> None:
    """Напечатать итоговый summary по сборке."""
    after = len(out_offers)
    in_true = sum(1 for offer in out_offers if getattr(offer, "available", False))
    in_false = after - in_true

    print("=" * _SUMMARY_WIDTH)
    print("[CopyLine] build summary")
    print("=" * _SUMMARY_WIDTH)
    print(f"version: {version}")
    print(f"before: {before}")
    print(f"after:  {after}")
    print(f"raw_out_file: {raw_out_file}")
    print(f"out_file: {out_file}")
    print("-" * _SUMMARY_WIDTH)
    _print_filter_report(filter_report)
    print("-" * _SUMMARY_WIDTH)
    print(f"quality_gate_ok:   {qg.get('ok')}")
    print(f"quality_gate_report: {qg.get('report_path') or qg.get('report_file')}")
    print(f"availability_true:  {in_true}")
    print(f"availability_false: {in_false}")
    print("=" * _SUMMARY_WIDTH)
