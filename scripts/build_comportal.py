# -*- coding: utf-8 -*-
"""
Path: scripts/build_comportal.py

ComPortal orchestrator layer.

Что делает:
- грузит supplier config и запускает supplier-layer;
- пишет raw/final feed и запускает quality gate;

Что не делает:
- не хранит supplier parsing/compat/normalize внутри себя;
- не подменяет source.py / builder.py / quality_gate.py.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from cs.core import write_cs_feed, write_cs_feed_raw
from cs.meta import next_run_at_time, now_almaty
from cs.qg_report import QualityGateResult, coerce_quality_gate_result, make_quality_gate_result
from suppliers.comportal.builder import build_offers
from suppliers.comportal.diagnostics import (
    build_watch_source_map,
    make_watch_messages,
    summarize_build_stats,
    summarize_offer_outs,
    summarize_source_offers,
    write_watch_report,
)
from suppliers.comportal.filtering import filter_source_offers, parse_id_set
from suppliers.comportal.quality_gate import run_quality_gate
from suppliers.comportal.source import load_source_bundle

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

BUILD_COMPORTAL_VERSION = "build_comportal_v8_qg_result_unified"
COMPORTAL_URL_DEFAULT = "https://www.comportal.kz/auth/documents/prices/yml-catalog.php"
COMPORTAL_OUT_DEFAULT = "docs/comportal.yml"
COMPORTAL_RAW_OUT_DEFAULT = "docs/raw/comportal.yml"
COMPORTAL_ID_PREFIX = "CP"
COMPORTAL_WATCH_OIDS: set[str] = set()

CFG_DIR_DEFAULT = "scripts/suppliers/comportal/config"
FILTER_FILE_DEFAULT = "filter.yml"
SCHEMA_FILE_DEFAULT = "schema.yml"
POLICY_FILE_DEFAULT = "policy.yml"

WATCH_REPORT_DEFAULT = "docs/raw/comportal_watch.txt"
QUALITY_BASELINE_DEFAULT = "scripts/suppliers/comportal/config/quality_gate_baseline.yml"
QUALITY_REPORT_DEFAULT = "docs/raw/comportal_quality_gate.txt"
PLACEHOLDER_DEFAULT = "https://placehold.co/800x800/png?text=No+Photo"

# -----------------------------
# YAML / env helpers
# -----------------------------

def _read_yaml(path: Path) -> dict[str, Any]:
    """Безопасно прочитать YAML-файл."""
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

def _load_supplier_config(cfg_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Загрузить filter, schema и policy config поставщика."""
    return (
        _read_yaml(cfg_dir / FILTER_FILE_DEFAULT),
        _read_yaml(cfg_dir / SCHEMA_FILE_DEFAULT),
        _read_yaml(cfg_dir / POLICY_FILE_DEFAULT),
    )

def _safe_int(value: Any, default: int) -> int:
    """Безопасно привести значение к int."""
    try:
        return int(value)
    except Exception:
        return default

def _resolve_project_path(raw_value: str | None, default_rel: str) -> Path:
    """Разрешить project-relative путь стабильно от scripts/, а не от cwd."""
    value = str(raw_value or default_rel).strip() or default_rel
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path

# -----------------------------
# Config resolve helpers
# -----------------------------

def _resolve_hour(policy_cfg: dict[str, Any], schema_cfg: dict[str, Any]) -> int:
    """Определить час следующего запуска по Алматы."""
    return _safe_int(
        policy_cfg.get("schedule_hour_almaty")
        or policy_cfg.get("next_run_hour_local")
        or schema_cfg.get("next_run_hour_local"),
        0,
    )

def _resolve_minute(policy_cfg: dict[str, Any], schema_cfg: dict[str, Any]) -> int:
    """Определить минуту следующего запуска по Алматы."""
    return _safe_int(
        policy_cfg.get("schedule_minute_almaty")
        or schema_cfg.get("schedule_minute_almaty"),
        30,
    )

def _resolve_placeholder(schema_cfg: dict[str, Any]) -> str:
    """Определить placeholder picture из schema или default."""
    placeholder = str(schema_cfg.get("placeholder_picture") or PLACEHOLDER_DEFAULT).strip()
    return placeholder or PLACEHOLDER_DEFAULT

def _resolve_vendor_blacklist(schema_cfg: dict[str, Any]) -> set[str]:
    """Определить blacklist vendor-значений из schema."""
    return {
        str(item).strip().casefold()
        for item in (schema_cfg.get("vendor_blacklist_casefold") or [])
        if str(item).strip()
    }

def _resolve_quality_gate(policy_cfg: dict[str, Any], schema_cfg: dict[str, Any]) -> dict[str, Any]:
    """Собрать supplier quality gate config."""
    qg = dict(policy_cfg.get("quality_gate") or {})
    if not qg:
        qg = dict(schema_cfg.get("quality_gate") or {})

    qg.setdefault("enabled", True)
    qg.setdefault("enforce", True)

    baseline = (
        os.getenv("COMPORTAL_QUALITY_BASELINE")
        or qg.get("baseline_file")
        or qg.get("baseline_path")
        or QUALITY_BASELINE_DEFAULT
    )
    report = (
        os.getenv("COMPORTAL_QUALITY_REPORT")
        or qg.get("report_file")
        or qg.get("report_path")
        or QUALITY_REPORT_DEFAULT
    )

    qg["baseline_file"] = str(_resolve_project_path(str(baseline), QUALITY_BASELINE_DEFAULT))
    qg["baseline_path"] = str(_resolve_project_path(str(baseline), QUALITY_BASELINE_DEFAULT))
    qg["report_file"] = str(_resolve_project_path(str(report), QUALITY_REPORT_DEFAULT))
    qg["report_path"] = str(_resolve_project_path(str(report), QUALITY_REPORT_DEFAULT))
    qg["max_new_cosmetic_offers"] = _safe_int(qg.get("max_new_cosmetic_offers"), 5)
    qg["max_new_cosmetic_issues"] = _safe_int(qg.get("max_new_cosmetic_issues"), 5)
    qg["freeze_current_as_baseline"] = bool(qg.get("freeze_current_as_baseline", False))
    return qg

def _resolve_allowed_category_ids(filter_cfg: dict[str, Any]) -> set[str]:
    """Определить include category ids из env или filter config."""
    fallback_ids = {str(item) for item in (filter_cfg.get("allowed_category_ids") or filter_cfg.get("category_ids") or [])}
    return parse_id_set(os.getenv("COMPORTAL_CATEGORY_IDS"), fallback_ids)

def _resolve_excluded_root_ids(filter_cfg: dict[str, Any]) -> set[str]:
    """Определить excluded root ids из env или filter config."""
    fallback_ids = {str(item) for item in (filter_cfg.get("excluded_root_ids") or [])}
    return parse_id_set(os.getenv("COMPORTAL_EXCLUDED_ROOT_IDS"), fallback_ids)

def _resolve_watch_ids() -> set[str]:
    """Определить OID-набор для watch-report."""
    raw = os.getenv("COMPORTAL_WATCH_OIDS", "").strip()
    if not raw:
        return set(COMPORTAL_WATCH_OIDS)
    return {item for item in (chunk.strip() for chunk in raw.replace(";", ",").split(",")) if item}

def _run_quality_gate(*, raw_out_file: str, cfg_dir: Path, qg: dict[str, Any]) -> QualityGateResult:
    """Запустить supplier-side quality gate или вернуть пустой успешный результат."""
    if not qg.get("enabled", True):
        return make_quality_gate_result(
            ok=True,
            report_path=str(qg.get("report_file") or QUALITY_REPORT_DEFAULT),
            baseline_path=str(qg.get("baseline_file") or QUALITY_BASELINE_DEFAULT),
            summary=f"[ComPortal quality_gate] PASS | disabled | report={str(qg.get('report_file') or QUALITY_REPORT_DEFAULT)}",
        )

    schema_path = cfg_dir / SCHEMA_FILE_DEFAULT
    return coerce_quality_gate_result(run_quality_gate(
        feed_path=raw_out_file,
        schema_path=str(schema_path),
        enforce=bool(qg.get("enforce", True)),
        baseline_path=str(qg.get("baseline_path") or qg.get("baseline_file") or QUALITY_BASELINE_DEFAULT),
        report_path=str(qg.get("report_path") or qg.get("report_file") or QUALITY_REPORT_DEFAULT),
        max_new_cosmetic_offers=_safe_int(qg.get("max_new_cosmetic_offers"), 5),
        max_new_cosmetic_issues=_safe_int(qg.get("max_new_cosmetic_issues"), 5),
        freeze_current_as_baseline=bool(qg.get("freeze_current_as_baseline", False)),
    ),
        report_path=str(qg.get("report_path") or qg.get("report_file") or QUALITY_REPORT_DEFAULT),
        baseline_path=str(qg.get("baseline_path") or qg.get("baseline_file") or QUALITY_BASELINE_DEFAULT),
        enforce=bool(qg.get("enforce", True)),
    )

# -----------------------------
# Main orchestration
# -----------------------------

def main() -> int:
    """Запустить сборку поставщика ComPortal."""
    cfg_dir = _resolve_project_path(os.getenv("COMPORTAL_CFG_DIR"), CFG_DIR_DEFAULT)
    filter_cfg, schema_cfg, policy_cfg = _load_supplier_config(cfg_dir)

    url = os.getenv("COMPORTAL_SOURCE_URL", COMPORTAL_URL_DEFAULT).strip() or COMPORTAL_URL_DEFAULT
    out_file = str(_resolve_project_path(os.getenv("COMPORTAL_OUT_FILE"), COMPORTAL_OUT_DEFAULT))
    raw_out_file = str(_resolve_project_path(os.getenv("COMPORTAL_RAW_OUT_FILE"), COMPORTAL_RAW_OUT_DEFAULT))
    watch_report = str(_resolve_project_path(os.getenv("COMPORTAL_WATCH_REPORT"), WATCH_REPORT_DEFAULT))

    login = os.getenv("COMPORTAL_LOGIN", "").strip() or None
    password = os.getenv("COMPORTAL_PASSWORD", "").strip() or None
    timeout = _safe_int(os.getenv("COMPORTAL_TIMEOUT", "120"), 120)

    supplier_name = str(policy_cfg.get("supplier") or schema_cfg.get("supplier") or "ComPortal").strip() or "ComPortal"
    hour = _resolve_hour(policy_cfg, schema_cfg)
    minute = _resolve_minute(policy_cfg, schema_cfg)
    build_time = now_almaty()
    next_run = next_run_at_time(build_time, hour=hour, minute=minute)

    placeholder_picture = _resolve_placeholder(schema_cfg)
    vendor_blacklist = _resolve_vendor_blacklist(schema_cfg)
    qg = _resolve_quality_gate(policy_cfg, schema_cfg)

    schema_cfg.setdefault("placeholder_picture", placeholder_picture)
    schema_cfg.setdefault("vendor_blacklist_casefold", sorted(vendor_blacklist))

    allowed_category_ids = _resolve_allowed_category_ids(filter_cfg)
    excluded_root_ids = _resolve_excluded_root_ids(filter_cfg)
    watch_ids = _resolve_watch_ids()

    _category_index, source_offers = load_source_bundle(
        url=url,
        timeout=timeout,
        login=login,
        password=password,
    )
    before = len(source_offers)

    filtered_offers = filter_source_offers(source_offers, allowed_category_ids, excluded_root_ids)
    watch_source = build_watch_source_map(source_offers, prefix=COMPORTAL_ID_PREFIX, watch_ids=watch_ids)
    out_offers, build_stats = build_offers(filtered_offers, schema=schema_cfg, policy=policy_cfg)
    after = len(out_offers)
    watch_out = {offer.oid for offer in out_offers}

    write_watch_report(
        watch_report,
        make_watch_messages(
            watch_ids=watch_ids,
            watch_source=watch_source,
            watch_out=watch_out,
        ),
    )

    currency_id = str(schema_cfg.get("currency") or "KZT")

    write_cs_feed_raw(
        out_offers,
        supplier=supplier_name,
        supplier_url=url,
        out_file=raw_out_file,
        build_time=build_time,
        next_run=next_run,
        before=before,
        encoding="utf-8",
        currency_id=currency_id,
    )

    changed = write_cs_feed(
        out_offers,
        supplier=supplier_name,
        supplier_url=url,
        out_file=out_file,
        build_time=build_time,
        next_run=next_run,
        before=before,
        encoding="utf-8",
        public_vendor=os.getenv("PUBLIC_VENDOR", "CS").strip() or "CS",
        currency_id=currency_id,
    )

    qg_result = _run_quality_gate(raw_out_file=raw_out_file, cfg_dir=cfg_dir, qg=qg)

    src_summary = summarize_source_offers(source_offers)
    out_summary = summarize_offer_outs(out_offers)
    build_summary = summarize_build_stats(build_stats)

    print(
        f"[build_comportal] OK | version={BUILD_COMPORTAL_VERSION} | "
        f"offers_in={before} | offers_out={after} | "
        f"in_true={out_summary.get('available_true', 0)} | "
        f"in_false={out_summary.get('available_false', 0)} | "
        f"changed={'yes' if changed else 'no'} | file={out_file}"
    )
    print(
        f"[build_comportal] source: with_vendor={src_summary.get('with_vendor', 0)} "
        f"without_vendor={src_summary.get('without_vendor', 0)} "
        f"with_picture={src_summary.get('with_picture', 0)} "
        f"without_picture={src_summary.get('without_picture', 0)}"
    )
    print(
        f"[build_comportal] build: filtered_out={build_summary.get('filtered_out', 0)} "
        f"placeholder_pictures={build_summary.get('placeholder_picture_count', 0)} "
        f"empty_vendor={build_summary.get('empty_vendor_count', 0)}"
    )
    print(
        f"[build_comportal] qg: ok={'yes' if qg_result.get('ok') else 'no'} | "
        f"critical={qg_result.get('critical_count', 0)} | "
        f"cosmetic_total={qg_result.get('cosmetic_total_count', 0)} | "
        f"report={qg_result.get('report_file', QUALITY_REPORT_DEFAULT)}"
    )

    critical_preview = qg_result.get("critical_preview") or []
    if critical_preview:
        print("[build_comportal] qg critical preview:")
        for line in critical_preview:
            print(f"  - {line}")

    return 0 if qg_result.get("ok", True) else 1

if __name__ == "__main__":
    raise SystemExit(main())
