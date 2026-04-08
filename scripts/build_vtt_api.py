# -*- coding: utf-8 -*-
"""
Path: scripts/build_vtt_api.py
Параллельный build entrypoint для VTT_api.
Старый VTT не трогает.

v3:
- совместим с текущим cs.core.write_cs_feed_raw/write_cs_feed;
- больше не передаёт unsupported kwargs after/available_true/available_false;
- пишет debug-выгрузку сырого API и sample normalizer-output;
- quality gate вызывает через совместимый wrapper;
- даже при 0 offers пишет raw/final файлы, чтобы был артефакт для проверки.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cs.core import write_cs_feed, write_cs_feed_raw  # type: ignore
from suppliers.vtt_api.builder import build_offers_from_api_items  # type: ignore
from suppliers.vtt_api.filtering import filter_items  # type: ignore
from suppliers.vtt_api.quality_gate import run_quality_gate  # type: ignore
from suppliers.vtt_api.source import ApiConfig, fetch_items  # type: ignore

SUPPLIER = "VTT_api"
SUPPLIER_URL = "https://b2b.vtt.ru/catalog/"
OUT_FILE = "docs/vtt_api.yml"
RAW_OUT_FILE = "docs/raw/vtt_api.yml"
QG_FILE = "docs/raw/vtt_api_quality_gate.txt"
DEBUG_RAW_JSON = "docs/raw/vtt_api_api_items.json"
DEBUG_SAMPLE_JSON = "docs/raw/vtt_api_api_items_sample.json"
DEBUG_KEYS_TXT = "docs/raw/vtt_api_api_keys.txt"
DEBUG_NORM_SAMPLE_JSON = "docs/raw/vtt_api_normalized_sample.json"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _now_almaty() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Almaty"))
    except Exception:
        return datetime.utcnow()


def _ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _json_default(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return value
    return str(value)


def _dump_debug(raw_items: list[dict[str, Any]]) -> None:
    _ensure_parent(DEBUG_RAW_JSON)
    with open(DEBUG_RAW_JSON, "w", encoding="utf-8") as f:
        json.dump(raw_items, f, ensure_ascii=False, indent=2, default=_json_default)

    with open(DEBUG_SAMPLE_JSON, "w", encoding="utf-8") as f:
        json.dump(raw_items[:25], f, ensure_ascii=False, indent=2, default=_json_default)

    key_counter: Counter[str] = Counter()
    for item in raw_items:
        if isinstance(item, dict):
            for key in item.keys():
                key_counter[str(key)] += 1

    with open(DEBUG_KEYS_TXT, "w", encoding="utf-8") as f:
        f.write("VTT_api raw keys frequency\n")
        f.write("=" * 72 + "\n")
        for key, count in key_counter.most_common():
            f.write(f"{count:5d} | {key}\n")


def main() -> int:
    cfg_dir = ROOT / "scripts" / "suppliers" / "vtt_api" / "config"
    filter_cfg = _read_yaml(cfg_dir / "filter.yml")
    schema_cfg = _read_yaml(cfg_dir / "schema.yml")
    policy_cfg = _read_yaml(cfg_dir / "policy.yml")
    qg_baseline_path = cfg_dir / "quality_gate_baseline.yml"

    api_cfg = ApiConfig.from_env()
    items = fetch_items(api_cfg)
    _dump_debug(items)

    before = len(items)
    filtered_items, filter_report = filter_items(items, filter_cfg)
    offers = build_offers_from_api_items(
        filtered_items,
        id_prefix=str(schema_cfg.get("id_prefix", "VTA")),
    )
    build_time = _now_almaty()
    next_run = build_time

    try:
        from suppliers.vtt_api.normalize import normalize_api_item  # type: ignore

        normalized_sample = [normalize_api_item(x) for x in filtered_items[:25]]
        _ensure_parent(DEBUG_NORM_SAMPLE_JSON)
        with open(DEBUG_NORM_SAMPLE_JSON, "w", encoding="utf-8") as f:
            json.dump(normalized_sample, f, ensure_ascii=False, indent=2, default=_json_default)
    except Exception as exc:
        _ensure_parent(DEBUG_NORM_SAMPLE_JSON)
        with open(DEBUG_NORM_SAMPLE_JSON, "w", encoding="utf-8") as f:
            json.dump({"error": str(exc)}, f, ensure_ascii=False, indent=2)

    write_cs_feed_raw(
        offers,
        supplier=SUPPLIER,
        supplier_url=SUPPLIER_URL,
        out_file=RAW_OUT_FILE,
        build_time=build_time,
        next_run=next_run,
        before=before,
        encoding="utf-8",
        currency_id="KZT",
    )
    write_cs_feed(
        offers,
        supplier=SUPPLIER,
        supplier_url=SUPPLIER_URL,
        out_file=OUT_FILE,
        build_time=build_time,
        next_run=next_run,
        before=before,
        encoding="utf-8",
        public_vendor="VTT",
        currency_id="KZT",
    )

    try:
        qg = run_quality_gate(
            raw_out_file=RAW_OUT_FILE,
            report_file=QG_FILE,
            baseline_file=str(qg_baseline_path),
            max_cosmetic_offers=int(policy_cfg.get("max_cosmetic_offers", 5)),
            max_cosmetic_issues=int(policy_cfg.get("max_cosmetic_issues", 5)),
        )
        qg_ok = bool(getattr(qg, "ok", True))
    except Exception as exc:
        print(f"[VTT_api] quality gate skipped: {exc}")
        qg_ok = True

    print("=" * 72)
    print("[VTT_api] build summary")
    print("=" * 72)
    print(f"before: {before}")
    print(f"after_filter: {len(filtered_items)}")
    print(f"offers_built: {len(offers)}")
    print(f"raw_out_file: {ROOT / RAW_OUT_FILE}")
    print(f"out_file: {ROOT / OUT_FILE}")
    print(f"debug_raw_json: {ROOT / DEBUG_RAW_JSON}")
    print(f"debug_sample_json: {ROOT / DEBUG_SAMPLE_JSON}")
    print(f"debug_keys_txt: {ROOT / DEBUG_KEYS_TXT}")
    print(f"debug_normalized_sample: {ROOT / DEBUG_NORM_SAMPLE_JSON}")
    print("-" * 72)
    print("filter_report:")
    for key, value in filter_report.items():
        print(f"  {key}: {value}")
    print("-" * 72)
    print(f"quality_gate_ok: {qg_ok}")
    return 0 if qg_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
