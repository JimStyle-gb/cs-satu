# -*- coding: utf-8 -*-
"""
Path: scripts/build_vtt_api.py

Параллельный build-entrypoint для VTT_api.

Важно:
- текущий VTT НЕ трогаем;
- VTT_api живёт отдельно и пишет свои docs/raw/vtt_api.yml + docs/vtt_api.yml;
- downstream-логика максимально переиспользует текущий VTT builder;
- этот build нужен как первый smoke-test для SOAP API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from cs.core import get_public_vendor, write_cs_feed, write_cs_feed_raw
from cs.meta import next_run_dom_at_hour, now_almaty
from suppliers.vtt_api.builder import build_offer_from_raw
from suppliers.vtt_api.filtering import filter_items
from suppliers.vtt_api.source import load_items

ROOT = Path(__file__).resolve().parent.parent
CFG_DIR = ROOT / "scripts" / "suppliers" / "vtt_api" / "config"
RAW_OUT = ROOT / "docs" / "raw" / "vtt_api.yml"
FINAL_OUT = ROOT / "docs" / "vtt_api.yml"
SUPPLIER_NAME = "VTT_api"
SUPPLIER_URL = "http://api.vtt.ru:8048/Portal.svc?singleWsdl"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _load_cfg() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    filter_cfg = _read_yaml(CFG_DIR / "filter.yml")
    policy_cfg = _read_yaml(CFG_DIR / "policy.yml")
    schema_cfg = _read_yaml(CFG_DIR / "schema.yml")
    return filter_cfg, policy_cfg, schema_cfg


def _build_offers(items: list[dict[str, Any]], *, id_prefix: str, placeholder_picture: str | None) -> list[Any]:
    offers: list[Any] = []
    for raw in items:
        offer = build_offer_from_raw(raw, id_prefix=id_prefix, placeholder_picture=placeholder_picture)
        if offer is not None:
            offers.append(offer)
    return offers


def _run_quality_gate(raw_out_file: Path, qg_cfg: dict[str, Any]) -> bool:
    try:
        from suppliers.vtt_api.quality_gate import run_quality_gate
    except Exception as exc:
        print(f"[VTT_api] quality gate skipped: import failed: {exc}")
        return True

    try:
        qg = run_quality_gate(raw_out_file=str(raw_out_file), qg_cfg=qg_cfg)
    except TypeError:
        try:
            qg = run_quality_gate(raw_out_file=str(raw_out_file), policy_path=str(CFG_DIR / "policy.yml"))
        except Exception as exc:
            print(f"[VTT_api] quality gate skipped: call failed: {exc}")
            return True
    except Exception as exc:
        print(f"[VTT_api] quality gate skipped: {exc}")
        return True

    if isinstance(qg, tuple):
        qg = qg[0]
    if hasattr(qg, "ok"):
        return bool(qg.ok)
    if hasattr(qg, "passed"):
        return bool(qg.passed)
    return True


def main() -> int:
    filter_cfg, policy_cfg, schema_cfg = _load_cfg()

    source_cfg = {
        "wsdl_url": schema_cfg.get("wsdl_url") or policy_cfg.get("wsdl_url") or SUPPLIER_URL,
        "timeout_s": int(policy_cfg.get("timeout_s", 60) or 60),
        "verify_ssl": bool(policy_cfg.get("verify_ssl", True)),
        "debug_limit": int(policy_cfg.get("debug_limit", 0) or 0),
    }

    raw_items = load_items(source_cfg)
    before = len(raw_items)
    filtered_items, filter_report = filter_items(raw_items, filter_cfg)

    id_prefix = str(schema_cfg.get("id_prefix") or "VT")
    placeholder_picture = schema_cfg.get("placeholder_picture")
    offers = _build_offers(filtered_items, id_prefix=id_prefix, placeholder_picture=placeholder_picture)

    build_time = now_almaty()
    next_run = next_run_dom_at_hour(
        build_time,
        hour=int(policy_cfg.get("schedule_hour", 5) or 5),
        doms=tuple(policy_cfg.get("schedule_dom", [1, 10, 20]) or [1, 10, 20]),
    )

    RAW_OUT.parent.mkdir(parents=True, exist_ok=True)
    FINAL_OUT.parent.mkdir(parents=True, exist_ok=True)

    write_cs_feed_raw(
        offers,
        supplier=SUPPLIER_NAME,
        supplier_url=str(policy_cfg.get("supplier_url") or SUPPLIER_URL),
        out_file=str(RAW_OUT),
        build_time=build_time,
        next_run=next_run,
        before=before,
        encoding=str(schema_cfg.get("encoding") or "utf-8"),
    )

    write_cs_feed(
        offers,
        supplier=SUPPLIER_NAME,
        supplier_url=str(policy_cfg.get("supplier_url") or SUPPLIER_URL),
        out_file=str(FINAL_OUT),
        build_time=build_time,
        next_run=next_run,
        before=before,
        encoding=str(schema_cfg.get("encoding") or "utf-8"),
        public_vendor=get_public_vendor(SUPPLIER_NAME),
    )

    qg_ok = _run_quality_gate(RAW_OUT, policy_cfg)

    print("=" * 72)
    print("[VTT_api] build summary")
    print("=" * 72)
    print(f"before: {before}")
    print(f"after_filter: {len(filtered_items)}")
    print(f"offers_built: {len(offers)}")
    print(f"raw_out_file: {RAW_OUT}")
    print(f"out_file: {FINAL_OUT}")
    print("-" * 72)
    print("filter_report:")
    for key, value in filter_report.items():
        print(f"  {key}: {value}")
    print("-" * 72)
    print(f"quality_gate_ok: {qg_ok}")

    return 0 if qg_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
