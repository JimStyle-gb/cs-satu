# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/vtt/diagnostics.py

VTT Diagnostics — операционные сводки и служебный вывод прогонов.

Что делает:
- печатает стабильный build summary для orchestrator;
- форматирует отчёты прогона без изменения данных;
- оставляет служебные helper-функции для диагностики.

Что не делает:
- не фильтрует offers;
- не меняет supplier-логику;
"""
Печатает стабильный summary прогона VTT."""
    print("=" * _SUMMARY_WIDTH)
    print("[VTT] build summary")
    print("=" * _SUMMARY_WIDTH)
    print(f"version: {version}")
    print(f"before: {before}")
    print(f"after:  {after}")
    print(f"raw_out_file: {raw_out_file}")
    print(f"out_file: {out_file}")
    print("-" * _SUMMARY_WIDTH)
    print(f"quality_gate_ok:       {_safe_bool(_get_qg_attr(qg, 'ok', True))}")
    print(f"quality_gate_report:   {_safe_text(_get_qg_attr(qg, 'report_path', _get_qg_attr(qg, 'report_file', '')))}")
    print(f"quality_gate_critical: {_safe_int(_get_qg_attr(qg, 'critical_count', 0))}")
    print(f"quality_gate_cosmetic: {_safe_int(_get_qg_attr(qg, 'cosmetic_count', 0))}")
    print(f"availability_true:     {_safe_int(availability_true)}")
    print(f"availability_false:    {_safe_int(availability_false)}")
    print("=" * _SUMMARY_WIDTH)
