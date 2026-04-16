# -*- coding: utf-8 -*-
"""
Path: scripts/build_akcent.py

AkCent orchestrator layer.

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

from cs.core import get_public_vendor, write_cs_feed, write_cs_feed_raw
from cs.meta import next_run_at_time, now_almaty
from cs.qg_report import QualityGateResult, make_quality_gate_result
from suppliers.akcent.builder import build_offers
from suppliers.akcent.diagnostics import print_build_summary
from suppliers.akcent.filtering import filter_source_offers
from suppliers.akcent.quality_gate import run_quality_gate
from suppliers.akcent.source import fetch_source_root, iter_source_offers

BUILD_AKCENT_VERSION = "build_akcent_v73_canonical_contracts"
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
PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ----------------------------- config helpers -----------------------------

def _resolve_path(path_like: str | Path) -> Path:
    path = Path(path_like)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


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
    return filter_source_offers(
        source_offers,
        filter_cfg=filter_cfg,
        prefixes=_filter_prefixes_from_cfg(filter_cfg),
        allowed_prefixes=_filter_prefixes_from_cfg(filter_cfg),
        mode=str(filter_cfg.get("mode") or "include"),
    )



def _call_builder(filtered_offers: list[Any], *, schema_cfg: dict[str, Any], policy_cfg: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    placeholder_picture = (
        os.getenv("PLACEHOLDER_PICTURE")
        or policy_cfg.get("placeholder_picture")
        or PLACEHOLDER_DEFAULT
    )
    id_prefix = str(policy_cfg.get("id_prefix") or "AC").strip() or "AC"
    vendor_blacklist = {
        str(x).casefold()
        for x in (policy_cfg.get("vendor_blacklist_casefold") or [])
        if str(x).strip()
    }
    return build_offers(
        filtered_offers,
        schema_cfg=schema_cfg,
        policy_cfg=policy_cfg,
        placeholder_picture=placeholder_picture,
        id_prefix=id_prefix,
        vendor_blacklist=vendor_blacklist,
    )


# ------------------------------ qg helpers --------------------------------

def _run_quality_gate(*, out_file: str, raw_out_file: str, policy_cfg: dict[str, Any]) -> QualityGateResult:
    qg_cfg = policy_cfg.get("quality_gate") or {}
    if not bool(qg_cfg.get("enabled", True)):
        return make_quality_gate_result(ok=True, summary="[AkCent quality_gate] PASS | disabled")

    baseline_path = _resolve_path(
        os.getenv("AKCENT_QUALITY_BASELINE")
        or qg_cfg.get("baseline_file")
        or qg_cfg.get("baseline_path")
        or QUALITY_BASELINE_DEFAULT
    )
    report_path = _resolve_path(
        os.getenv("AKCENT_QUALITY_REPORT")
        or qg_cfg.get("report_file")
        or qg_cfg.get("report_path")
        or QUALITY_REPORT_DEFAULT
    )
    enforce = bool(qg_cfg.get("enforce", True))
    freeze_current = bool(qg_cfg.get("freeze_current_as_baseline", False)) or _env_truthy(
        "AKCENT_QUALITY_FREEZE_BASELINE"
    )
    max_cosmetic_offers = _safe_int(
        os.getenv(
            "AKCENT_QUALITY_MAX_COSMETIC_OFFERS",
            os.getenv(
                "AKCENT_QUALITY_MAX_NEW_COSMETIC_OFFERS",
                str(qg_cfg.get("max_new_cosmetic_offers", 5)),
            ),
        ),
        5,
    )
    max_cosmetic_issues = _safe_int(
        os.getenv(
            "AKCENT_QUALITY_MAX_COSMETIC_ISSUES",
            os.getenv(
                "AKCENT_QUALITY_MAX_NEW_COSMETIC_ISSUES",
                str(qg_cfg.get("max_new_cosmetic_issues", 5)),
            ),
        ),
        5,
    )

    result = run_quality_gate(
        feed_path=raw_out_file,
        baseline_path=str(baseline_path),
        report_path=str(report_path),
        max_new_cosmetic_offers=max_cosmetic_offers,
        max_new_cosmetic_issues=max_cosmetic_issues,
        enforce=enforce,
        freeze_current_as_baseline=freeze_current,
    )
    if result.summary:
        print(result.summary)
    if not result.ok:
        raise SystemExit(1)
    return result


# -------------------------------- entrypoint ------------------------------

def main() -> int:
    url = os.getenv("AKCENT_URL", AKCENT_URL_DEFAULT)
    out_file = str(
        _resolve_path(os.getenv("AKCENT_OUT", os.getenv("AKCENT_OUT_FILE", AKCENT_OUT_DEFAULT)))
    )
    raw_out_file = str(
        _resolve_path(
            os.getenv("AKCENT_RAW_OUT", os.getenv("AKCENT_RAW_OUT_FILE", AKCENT_RAW_OUT_DEFAULT))
        )
    )
    cfg_dir = _resolve_path(os.getenv("AKCENT_CFG_DIR", CFG_DIR_DEFAULT))

    filter_cfg, schema_cfg, policy_cfg = _load_supplier_config(cfg_dir)
    supplier_name = str(policy_cfg.get("supplier") or "AkCent").strip() or "AkCent"
    encoding = str(policy_cfg.get("output_encoding") or "utf-8").strip() or "utf-8"
    schedule_hour = _safe_int(
        policy_cfg.get("schedule_hour_almaty") or policy_cfg.get("next_run_hour_local"),
        22,
    )
    schedule_minute = _safe_int(policy_cfg.get("schedule_minute_almaty"), 30)

    build_time = now_almaty()
    next_run = next_run_at_time(build_time, hour=schedule_hour, minute=schedule_minute)

    root = fetch_source_root(url)
    source_offers = list(iter_source_offers(root))
    before = len(source_offers)

    filtered_offers, filter_report = _call_filter(source_offers, filter_cfg=filter_cfg)
    out_offers, build_report = _call_builder(
        filtered_offers,
        schema_cfg=schema_cfg,
        policy_cfg=policy_cfg,
    )
    after = len(out_offers)

    write_cs_feed_raw(
        out_offers,
        supplier=supplier_name,
        supplier_url=url,
        out_file=raw_out_file,
        build_time=build_time,
        next_run=next_run,
        before=before,
        encoding=encoding,
    )
    write_cs_feed(
        out_offers,
        supplier=supplier_name,
        supplier_url=url,
        out_file=out_file,
        build_time=build_time,
        next_run=next_run,
        before=before,
        encoding=encoding,
        public_vendor=get_public_vendor(supplier_name),
    )
    print_build_summary(
        supplier=supplier_name,
        version=BUILD_AKCENT_VERSION,
        before=before,
        after=after,
        filter_report=filter_report,
        build_report=build_report,
        out_file=out_file,
        raw_out_file=raw_out_file,
    )
    _run_quality_gate(out_file=out_file, raw_out_file=raw_out_file, policy_cfg=policy_cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
