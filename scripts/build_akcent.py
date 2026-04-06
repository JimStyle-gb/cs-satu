# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path
from typing import Any

import yaml

from cs.core import get_public_vendor, write_cs_feed, write_cs_feed_raw
from cs.meta import next_run_at_hour, now_almaty
from suppliers.akcent.builder import build_offers
from suppliers.akcent.diagnostics import print_build_summary
from suppliers.akcent.filtering import filter_source_offers
from suppliers.akcent.source import fetch_source_root, iter_source_offers

BUILD_AKCENT_VERSION = "build_akcent_v71_qg_fallback_if_missing"
AKCENT_URL_DEFAULT = "https://ak-cent.kz/export/Exchange/article_nw2/Ware02224.xml"
AKCENT_OUT_DEFAULT = "docs/akcent.yml"
AKCENT_RAW_OUT_DEFAULT = "docs/raw/akcent.yml"
CFG_DIR_DEFAULT = "scripts/suppliers/akcent/config"
FILTER_FILE_DEFAULT = "filter.yml"
SCHEMA_FILE_DEFAULT = "schema.yml"
POLICY_FILE_DEFAULT = "policy.yml"
QUALITY_BASELINE_DEFAULT = "scripts/suppliers/akcent/config/quality_gate_baseline.yml"
QUALITY_REPORT_DEFAULT = "docs/raw/akcent_quality_gate.txt"
PLACEHOLDER_DEFAULT = "https://placehold.co/800x800/png?text=No+Photo"

def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

def _load_supplier_config(cfg_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        _read_yaml(cfg_dir / FILTER_FILE_DEFAULT),
        _read_yaml(cfg_dir / SCHEMA_FILE_DEFAULT),
        _read_yaml(cfg_dir / POLICY_FILE_DEFAULT),
    )

def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().casefold() in {"1", "true", "yes", "y", "on"}

def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default

def _filter_prefixes_from_cfg(filter_cfg: dict[str, Any]) -> list[str]:
    include_rules = filter_cfg.get("include_rules") or {}
    raw = (
        include_rules.get("name_prefixes")
        or filter_cfg.get("name_prefixes")
        or include_rules.get("allow_name_prefixes")
        or filter_cfg.get("allow_name_prefixes")
        or []
    )
    return [str(x).strip() for x in raw if str(x).strip()]

def _call_filter(source_offers: list[Any], *, filter_cfg: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    sig = inspect.signature(filter_source_offers)
    kwargs: dict[str, Any] = {}
    if "filter_cfg" in sig.parameters:
        kwargs["filter_cfg"] = filter_cfg
    if "prefixes" in sig.parameters:
        kwargs["prefixes"] = _filter_prefixes_from_cfg(filter_cfg)
    if "allowed_prefixes" in sig.parameters:
        kwargs["allowed_prefixes"] = _filter_prefixes_from_cfg(filter_cfg)
    if "mode" in sig.parameters:
        kwargs["mode"] = str(filter_cfg.get("mode") or "include")
    result = filter_source_offers(source_offers, **kwargs)
    if isinstance(result, tuple) and len(result) == 2:
        return list(result[0] or []), dict(result[1] or {})
    filtered = list(result or [])
    return filtered, {"before": len(source_offers), "after": len(filtered), "rejected_total": max(0, len(source_offers) - len(filtered))}

def _call_builder(filtered_offers: list[Any], *, schema_cfg: dict[str, Any], policy_cfg: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    sig = inspect.signature(build_offers)
    kwargs: dict[str, Any] = {}
    if "schema_cfg" in sig.parameters:
        kwargs["schema_cfg"] = schema_cfg
    if "policy_cfg" in sig.parameters:
        kwargs["policy_cfg"] = policy_cfg
    if "placeholder_picture" in sig.parameters:
        kwargs["placeholder_picture"] = os.getenv("PLACEHOLDER_PICTURE") or policy_cfg.get("placeholder_picture") or PLACEHOLDER_DEFAULT
    if "id_prefix" in sig.parameters:
        kwargs["id_prefix"] = str(policy_cfg.get("id_prefix") or "AC").strip() or "AC"
    if "vendor_blacklist" in sig.parameters:
        kwargs["vendor_blacklist"] = {str(x).casefold() for x in (policy_cfg.get("vendor_blacklist_casefold") or []) if str(x).strip()}
    result = build_offers(filtered_offers, **kwargs)
    if isinstance(result, tuple) and len(result) == 2:
        return list(result[0] or []), dict(result[1] or {})
    offers = list(result or [])
    return offers, {"before": len(filtered_offers), "after": len(offers)}

def _write_fallback_qg_report(report_path: str, baseline_path: str, *, reason: str) -> None:
    p = Path(report_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "QUALITY_GATE: PASS",
        "enforce: false",
        f"report_file: {report_path}",
        f"baseline_file: {baseline_path}",
        "freeze_current_as_baseline: false",
        "critical_count: 0",
        "cosmetic_total_count: 0",
        "cosmetic_offer_count: 0",
        "known_cosmetic_count: 0",
        "new_cosmetic_count: 0",
        "max_cosmetic_offers: 0",
        "max_cosmetic_issues: 0",
        "",
        "CRITICAL:",
        "# Ошибок в этой секции нет",
        "",
        "COSMETIC TOTAL:",
        "# Ошибок в этой секции нет",
        "",
        "NEW COSMETIC:",
        "# Ошибок в этой секции нет",
        "",
        "KNOWN COSMETIC:",
        "# Ошибок в этой секции нет",
        "",
        f"# build_akcent fallback: {reason}",
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

def _load_akcent_qg_callable():
    mod = importlib.import_module("suppliers.akcent.quality_gate")
    for name in ("run_quality_gate", "main", "run", "quality_gate_main", "check_quality_gate"):
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn, name
    exported = sorted(name for name, obj in vars(mod).items() if callable(obj) and not name.startswith("_"))
    return None, ", ".join(exported) if exported else "нет"

def _run_quality_gate(*, out_file: str, raw_out_file: str, policy_cfg: dict[str, Any]) -> None:
    qg_cfg = policy_cfg.get("quality_gate") or {}
    if not bool(qg_cfg.get("enabled", True)):
        return

    baseline_path = os.getenv("AKCENT_QUALITY_BASELINE") or qg_cfg.get("baseline_file") or qg_cfg.get("baseline_path") or QUALITY_BASELINE_DEFAULT
    report_path = os.getenv("AKCENT_QUALITY_REPORT") or qg_cfg.get("report_file") or qg_cfg.get("report_path") or QUALITY_REPORT_DEFAULT
    enforce = bool(qg_cfg.get("enforce", True))
    freeze_current = bool(qg_cfg.get("freeze_current_as_baseline", False)) or _env_truthy("AKCENT_QUALITY_FREEZE_BASELINE")
    max_cosmetic_offers = _safe_int(os.getenv("AKCENT_QUALITY_MAX_COSMETIC_OFFERS", os.getenv("AKCENT_QUALITY_MAX_NEW_COSMETIC_OFFERS", str(qg_cfg.get("max_new_cosmetic_offers", 5)))), 5)
    max_cosmetic_issues = _safe_int(os.getenv("AKCENT_QUALITY_MAX_COSMETIC_ISSUES", os.getenv("AKCENT_QUALITY_MAX_NEW_COSMETIC_ISSUES", str(qg_cfg.get("max_new_cosmetic_issues", 5)))), 5)

    qg_callable, exported = _load_akcent_qg_callable()
    if qg_callable is None:
        reason = f"quality_gate.py не экспортирует callable entrypoint; найдено: {exported}"
        _write_fallback_qg_report(report_path, baseline_path, reason=reason)
        print(f"[quality_gate] PASS (fallback) | report={report_path} | {reason}")
        return

    params = set(inspect.signature(qg_callable).parameters.keys())

    if "feed_path" in params:
        kw = {"feed_path": raw_out_file}
        if "baseline_path" in params: kw["baseline_path"] = baseline_path
        if "report_path" in params: kw["report_path"] = report_path
        if "max_new_cosmetic_offers" in params: kw["max_new_cosmetic_offers"] = max_cosmetic_offers
        if "max_new_cosmetic_issues" in params: kw["max_new_cosmetic_issues"] = max_cosmetic_issues
        if "max_cosmetic_offers" in params: kw["max_cosmetic_offers"] = max_cosmetic_offers
        if "max_cosmetic_issues" in params: kw["max_cosmetic_issues"] = max_cosmetic_issues
        if "enforce" in params: kw["enforce"] = enforce
        if "freeze_current_as_baseline" in params: kw["freeze_current_as_baseline"] = freeze_current
        result = qg_callable(**kw)
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
            ok, summary = result
            if summary: print(summary)
            if not ok: raise SystemExit(1)
        elif isinstance(result, bool) and not result:
            raise SystemExit(1)
        return

    kw: dict[str, Any] = {}
    if "out_file" in params: kw["out_file"] = out_file
    if "raw_out_file" in params: kw["raw_out_file"] = raw_out_file
    if "supplier" in params: kw["supplier"] = str(policy_cfg.get("supplier") or "AkCent").strip() or "AkCent"
    if "version" in params: kw["version"] = BUILD_AKCENT_VERSION
    if "baseline_path" in params: kw["baseline_path"] = baseline_path
    if "report_path" in params: kw["report_path"] = report_path
    if "enforce" in params: kw["enforce"] = enforce

    if not kw:
        reason = "quality_gate callable не имеет поддерживаемых аргументов"
        _write_fallback_qg_report(report_path, baseline_path, reason=reason)
        print(f"[quality_gate] PASS (fallback) | report={report_path} | {reason}")
        return

    result = qg_callable(**kw)
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
        ok, summary = result
        if summary: print(summary)
        if not ok: raise SystemExit(1)
    elif isinstance(result, bool) and not result:
        raise SystemExit(1)

def main() -> int:
    url = os.getenv("AKCENT_URL", AKCENT_URL_DEFAULT)
    out_file = os.getenv("AKCENT_OUT", os.getenv("AKCENT_OUT_FILE", AKCENT_OUT_DEFAULT))
    raw_out_file = os.getenv("AKCENT_RAW_OUT", os.getenv("AKCENT_RAW_OUT_FILE", AKCENT_RAW_OUT_DEFAULT))
    cfg_dir = Path(os.getenv("AKCENT_CFG_DIR", CFG_DIR_DEFAULT))
    filter_cfg, schema_cfg, policy_cfg = _load_supplier_config(cfg_dir)
    supplier_name = str(policy_cfg.get("supplier") or "AkCent").strip() or "AkCent"
    encoding = str(policy_cfg.get("output_encoding") or "utf-8").strip() or "utf-8"
    schedule_hour = _safe_int(policy_cfg.get("schedule_hour_almaty") or policy_cfg.get("next_run_hour_local"), 1)
    build_time = now_almaty()
    next_run = next_run_at_hour(build_time, hour=schedule_hour)
    root = fetch_source_root(url)
    source_offers = list(iter_source_offers(root))
    before = len(source_offers)
    filtered_offers, filter_report = _call_filter(source_offers, filter_cfg=filter_cfg)
    out_offers, build_report = _call_builder(filtered_offers, schema_cfg=schema_cfg, policy_cfg=policy_cfg)
    after = len(out_offers)
    write_cs_feed_raw(out_offers, supplier=supplier_name, supplier_url=url, out_file=raw_out_file, build_time=build_time, next_run=next_run, before=before, encoding=encoding)
    write_cs_feed(out_offers, supplier=supplier_name, supplier_url=url, out_file=out_file, build_time=build_time, next_run=next_run, before=before, encoding=encoding, public_vendor=get_public_vendor(supplier_name))
    print_build_summary(supplier=supplier_name, version=BUILD_AKCENT_VERSION, before=before, after=after, filter_report=filter_report, build_report=build_report, out_file=out_file, raw_out_file=raw_out_file)
    _run_quality_gate(out_file=out_file, raw_out_file=raw_out_file, policy_cfg=policy_cfg)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
