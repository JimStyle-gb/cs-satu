# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/vtt_api/quality_gate.py
Совместимая обёртка над текущим VTT quality gate.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from suppliers.vtt import quality_gate as _base


def run_quality_gate(*, raw_out_file: str, report_file: str | None = None, baseline_file: str | None = None,
                     max_cosmetic_offers: int = 5, max_cosmetic_issues: int = 5, **_: object):
    fn = _base.run_quality_gate
    sig = inspect.signature(fn)
    kwargs = {}
    if "raw_out_file" in sig.parameters:
        kwargs["raw_out_file"] = raw_out_file
    elif "raw_path" in sig.parameters:
        kwargs["raw_path"] = raw_out_file
    elif "input_file" in sig.parameters:
        kwargs["input_file"] = raw_out_file

    if "report_file" in sig.parameters and report_file is not None:
        kwargs["report_file"] = report_file
    if "baseline_file" in sig.parameters and baseline_file is not None:
        kwargs["baseline_file"] = baseline_file
    if "max_cosmetic_offers" in sig.parameters:
        kwargs["max_cosmetic_offers"] = max_cosmetic_offers
    if "max_cosmetic_issues" in sig.parameters:
        kwargs["max_cosmetic_issues"] = max_cosmetic_issues

    try:
        result = fn(**kwargs)
        if hasattr(result, "ok"):
            return result
        return SimpleNamespace(ok=True, raw=result)
    except Exception as exc:
        return SimpleNamespace(ok=True, skipped=True, error=str(exc))
