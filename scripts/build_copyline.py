# -*- coding: utf-8 -*-
"""
Path: scripts/build_copyline.py

CopyLine adapter (CL) — thin orchestrator under CS-template.

Что делает:
- грузит supplier config: filter / policy;
- читает индекс товаров поставщика;
- фильтрует ассортимент;
- догружает product pages и собирает raw offers;
- пишет raw feed;
- пишет final feed;
- запускает supplier-side quality gate.

Важно:
- supplier-specific логика остаётся только в suppliers/copyline/*;
- build_copyline.py не должен знать parsing-логику страниц CopyLine;
- next_run для CopyLine считается строго по дням месяца 1/10/20 через shared cs.meta.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List

import yaml

from cs.core import get_public_vendor, write_cs_feed, write_cs_feed_raw
from cs.meta import next_run_dom_at_hour, now_almaty

from suppliers.copyline.builder import build_offer_from_page
from suppliers.copyline.filtering import filter_product_index
from suppliers.copyline.quality_gate import run_quality_gate
from suppliers.copyline.source import fetch_product_index, parse_product_page


BUILD_COPYLINE_VERSION = "build_copyline_v13_fix_qg_policy_and_next_run_dom"

SUPPLIER_NAME_DEFAULT = "CopyLine"
SUPPLIER_URL_DEFAULT = os.getenv("SUPPLIER_URL", "https://copyline.kz/goods.html")
OUT_FILE_DEFAULT = os.getenv("OUT_FILE", "docs/copyline.yml")
RAW_OUT_FILE_DEFAULT = os.getenv("RAW_OUT_FILE", "docs/raw/copyline.yml")
OUTPUT_ENCODING_DEFAULT = (os.getenv("OUTPUT_ENCODING", "utf-8") or "utf-8").strip() or "utf-8"
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "6") or "6")
MAX_CRAWL_MINUTES = int(os.getenv("MAX_CRAWL_MINUTES", "60") or "60")

CFG_DIR_DEFAULT = "scripts/suppliers/copyline/config"
FILTER_FILE_DEFAULT = "filter.yml"
POLICY_FILE_DEFAULT = "policy.yml"

COPYLINE_QG_BASELINE_DEFAULT = "scripts/suppliers/copyline/config/quality_gate_baseline.yml"
COPYLINE_QG_REPORT_DEFAULT = "docs/raw/copyline_quality_gate.txt"


# ----------------------------- config helpers -----------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _load_supplier_config(cfg_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    filter_cfg = _read_yaml(cfg_dir / FILTER_FILE_DEFAULT)
    policy_cfg = _read_yaml(cfg_dir / POLICY_FILE_DEFAULT)
    return filter_cfg, policy_cfg


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _load_param_priority(policy_cfg: dict[str, Any]) -> tuple[str, ...]:
    raw = policy_cfg.get("param_priority") or []
    out: list[str] = []
    for item in raw:
        s = str(item or "").strip()
        if s:
            out.append(s)
    return tuple(out)


def _resolve_dom_list(policy_cfg: dict[str, Any]) -> tuple[int, ...]:
    raw = (
        policy_cfg.get("schedule_days_of_month")
        or policy_cfg.get("next_run_days_of_month")
        or [1, 10, 20]
    )
    out: list[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except Exception:
            continue
    return tuple(out or [1, 10, 20])




# ----------------------------- crawl helpers ------------------------------

def _build_offers(filtered_index: list[dict[str, Any]]) -> list[Any]:
    out_offers: List[Any] = []
    seen_oids: set[str] = set()
    deadline = datetime.utcnow() + timedelta(minutes=MAX_CRAWL_MINUTES)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(parse_product_page, item["url"]): item for item in filtered_index}
        for future in as_completed(futures):
            if datetime.utcnow() > deadline:
                break
            page = future.result()
            if not page:
                continue
            offer = build_offer_from_page(page, fallback_title=(futures[future].get("title") or ""))
            if not offer or offer.oid in seen_oids:
                continue
            seen_oids.add(offer.oid)
            out_offers.append(offer)

    out_offers.sort(key=lambda offer: offer.oid)
    return out_offers


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
    after = len(out_offers)
    in_true = sum(1 for offer in out_offers if getattr(offer, "available", False))
    in_false = after - in_true

    print("=" * 72)
    print("[CopyLine] build summary")
    print("=" * 72)
    print(f"version: {version}")
    print(f"before: {before}")
    print(f"after:  {after}")
    print(f"raw_out_file: {raw_out_file}")
    print(f"out_file: {out_file}")
    print("-" * 72)
    print("filter_report:")
    for key, value in filter_report.items():
        print(f"  {key}: {value}")
    print("-" * 72)
    print(f"quality_gate_ok:   {qg.get('ok')}")
    print(f"quality_gate_report: {qg.get('report_path') or qg.get('report_file')}")
    print(f"availability_true:  {in_true}")
    print(f"availability_false: {in_false}")
    print("=" * 72)




# -------------------------------- entrypoint ------------------------------

def main() -> int:
    cfg_dir = Path(os.getenv("COPYLINE_CFG_DIR", CFG_DIR_DEFAULT))
    filter_cfg, policy_cfg = _load_supplier_config(cfg_dir)

    supplier_name = str(policy_cfg.get("supplier") or SUPPLIER_NAME_DEFAULT).strip() or SUPPLIER_NAME_DEFAULT
    supplier_url = os.getenv("SUPPLIER_URL", SUPPLIER_URL_DEFAULT)
    out_file = os.getenv("OUT_FILE", OUT_FILE_DEFAULT)
    raw_out_file = os.getenv("RAW_OUT_FILE", RAW_OUT_FILE_DEFAULT)
    output_encoding = os.getenv("OUTPUT_ENCODING", OUTPUT_ENCODING_DEFAULT)

    hour = _safe_int(
        policy_cfg.get("schedule_hour_almaty")
        or policy_cfg.get("next_run_hour_local"),
        4,
    )
    dom = _resolve_dom_list(policy_cfg)

    build_time = now_almaty().replace(tzinfo=None)
    next_run = next_run_dom_at_hour(build_time, hour, dom)

    index = fetch_product_index()
    before = len(index)

    filtered_index, filter_report = filter_product_index(
        index,
        include_prefixes=filter_cfg.get("include_prefixes") or [],
    )

    out_offers = _build_offers(filtered_index)

    write_cs_feed_raw(
        out_offers,
        supplier=supplier_name,
        supplier_url=supplier_url,
        out_file=raw_out_file,
        build_time=build_time,
        next_run=next_run,
        before=before,
        encoding=output_encoding,
    )

    public_vendor = get_public_vendor(supplier_name)
    write_cs_feed(
        out_offers,
        supplier=supplier_name,
        supplier_url=supplier_url,
        out_file=out_file,
        build_time=build_time,
        next_run=next_run,
        before=before,
        encoding=output_encoding,
        public_vendor=public_vendor,
        param_priority=_load_param_priority(policy_cfg),
    )

    qg_cfg = policy_cfg.get("quality_gate") or {}
    qg = run_quality_gate(
        feed_path=raw_out_file,
        policy_path=str(cfg_dir / POLICY_FILE_DEFAULT),
        baseline_path=(
            os.getenv("COPYLINE_QG_BASELINE")
            or qg_cfg.get("baseline_file")
            or qg_cfg.get("baseline_path")
            or COPYLINE_QG_BASELINE_DEFAULT
        ),
        report_path=(
            os.getenv("COPYLINE_QG_REPORT")
            or qg_cfg.get("report_file")
            or qg_cfg.get("report_path")
            or COPYLINE_QG_REPORT_DEFAULT
        ),
    )

    print_build_summary(
        version=BUILD_COPYLINE_VERSION,
        before=before,
        out_offers=out_offers,
        filter_report=filter_report,
        qg=qg,
        out_file=out_file,
        raw_out_file=raw_out_file,
    )

    if not qg.get("ok", True):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
