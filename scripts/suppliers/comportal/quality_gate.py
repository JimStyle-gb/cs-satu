# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/comportal/quality_gate.py

ComPortal quality gate.

Роль файла:
- проверяет final feed после supplier-layer и shared core;
- пишет единый quality gate отчёт через shared cs.qg_report writer;
- разделяет blocking и report-only cosmetic tails.

Что файл делает:
- ловит critical классы вроде empty_vendor / empty_price / supplier_vendor_leak;
- сохраняет placeholder_picture как допустимый known cosmetic tail;
- отдельно подсвечивает name/picture хвосты, которые стоит добивать в supplier-layer.

Что файл НЕ делает:
- не чинит normalize/builder/pictures;
- не тащит supplier-specific repair логику в quality gate.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re
import xml.etree.ElementTree as ET

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

from cs.qg_report import write_quality_gate_report


QUALITY_BASELINE_DEFAULT = "scripts/suppliers/comportal/config/quality_gate_baseline.yml"
QUALITY_REPORT_DEFAULT = "docs/raw/comportal_quality_gate.txt"
PLACEHOLDER_URL = "https://placehold.co/800x800/png?text=No+Photo"
_WS_RE = re.compile(r"\s+")
_HTTP_URL_RE = re.compile(r"(?i)^http://")
_DUPLICATE_BRAND_IN_NAME_RE = re.compile(
    r"(?iu)\b(HP|Dell|Eaton|Canon|Xerox|Brother|Kyocera|Ricoh|Epson|Pantum|CyberPower|SMART|ASUS|Acer|Lenovo|Samsung|LG|iiyama|Катюша)\b(?:\s+\1\b)"
)

# Эти правила остаются в cosmetic-отчёте, но:
# 1) не участвуют в enforce
# 2) не считаются new cosmetic деградацией
_RULES_EXCLUDED_FROM_ENFORCE = {"placeholder_picture", "duplicate_brand_in_name", "insecure_http_picture"}
_RULES_TREATED_AS_ALLOWED_KNOWN = {"placeholder_picture"}


@dataclass(frozen=True)
class QualityIssue:
    severity: str
    rule: str
    oid: str
    name: str
    details: str


def _norm_ws(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").strip())


def _read_yaml(path: str | None) -> dict[str, Any]:
    if not path or yaml is None:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _write_yaml(path: str | None, data: dict[str, Any]) -> None:
    if not path or yaml is None:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _make_issue(severity: str, rule: str, oid: str, name: str, details: str) -> QualityIssue:
    return QualityIssue(
        severity=severity,
        rule=rule,
        oid=_norm_ws(oid),
        name=_norm_ws(name),
        details=_norm_ws(details),
    )


def _detect_issues(feed_path: str, schema_path: str | None = None) -> list[QualityIssue]:
    xml_text = Path(feed_path).read_text(encoding="utf-8", errors="ignore")
    root = ET.fromstring(xml_text)

    schema = _read_yaml(schema_path)
    blacklist = {
        str(x).strip().casefold()
        for x in (schema.get("vendor_blacklist_casefold") or [])
        if str(x).strip()
    }
    placeholder = str(schema.get("placeholder_picture") or PLACEHOLDER_URL).strip() or PLACEHOLDER_URL

    issues: list[QualityIssue] = []

    for offer in root.findall(".//offer"):
        oid = _norm_ws(offer.get("id") or "")
        name = _norm_ws(offer.findtext("name") or "")
        vendor = _norm_ws(offer.findtext("vendor") or "")
        desc_html = offer.findtext("description") or ""

        if not vendor:
            issues.append(_make_issue("critical", "empty_vendor", oid, name, ""))

        if vendor and vendor.casefold() in blacklist:
            issues.append(_make_issue("critical", "supplier_vendor_leak", oid, name, vendor))

        if not _norm_ws(offer.findtext("price") or ""):
            issues.append(_make_issue("critical", "empty_price", oid, name, ""))

        if _DUPLICATE_BRAND_IN_NAME_RE.search(name):
            issues.append(_make_issue("cosmetic", "duplicate_brand_in_name", oid, name, name))

        for pic in offer.findall("picture"):
            url = _norm_ws("".join(pic.itertext()))
            if not url:
                continue
            if url == placeholder:
                issues.append(_make_issue("cosmetic", "placeholder_picture", oid, name, url))
            elif _HTTP_URL_RE.search(url):
                issues.append(_make_issue("cosmetic", "insecure_http_picture", oid, name, url))

        if "oaicite" in desc_html or "contentReference" in desc_html:
            issues.append(_make_issue("critical", "desc_oaicite_leak", oid, name, "oaicite/contentReference"))

    deduped: dict[tuple[str, str, str, str], QualityIssue] = {}
    for issue in issues:
        deduped[(issue.severity, issue.rule, issue.oid, issue.details)] = issue
    return sorted(deduped.values(), key=lambda x: (x.severity, x.rule, x.oid, x.details))


def _load_baseline(path: str | None) -> dict[str, set[str]]:
    data = _read_yaml(path)
    raw = data.get("accepted_cosmetic") or {}
    out: dict[str, set[str]] = {}
    for rule, ids in raw.items():
        out[str(rule)] = {str(x).strip() for x in (ids or []) if str(x).strip()}
    return out


def _make_baseline_payload(cosmetic: list[QualityIssue]) -> dict[str, Any]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for issue in cosmetic:
        if issue.rule in _RULES_TREATED_AS_ALLOWED_KNOWN:
            # rule-level allowed tails не засоряют baseline снапшот
            continue
        grouped[issue.rule].append(issue.oid)

    return {
        "schema_version": 1,
        "accepted_cosmetic": {
            rule: sorted(set(oids)) for rule, oids in sorted(grouped.items())
        },
    }


def run_quality_gate(
    *,
    feed_path: str,
    schema_path: str | None = None,
    enforce: bool = True,
    baseline_path: str | None = None,
    report_path: str | None = None,
    max_new_cosmetic_offers: int = 5,
    max_new_cosmetic_issues: int = 5,
    freeze_current_as_baseline: bool = False,
) -> dict[str, object]:
    baseline_path = str(baseline_path or QUALITY_BASELINE_DEFAULT)
    report_path = str(report_path or QUALITY_REPORT_DEFAULT)

    issues = _detect_issues(feed_path, schema_path=schema_path)
    critical = [x for x in issues if x.severity == "critical"]
    cosmetic = [x for x in issues if x.severity == "cosmetic"]

    if freeze_current_as_baseline:
        _write_yaml(baseline_path, _make_baseline_payload(cosmetic))

    baseline = _load_baseline(baseline_path)

    known_cosmetic: list[QualityIssue] = []
    new_cosmetic: list[QualityIssue] = []
    for issue in cosmetic:
        if issue.rule in _RULES_TREATED_AS_ALLOWED_KNOWN:
            known_cosmetic.append(issue)
        elif issue.oid in baseline.get(issue.rule, set()):
            known_cosmetic.append(issue)
        else:
            new_cosmetic.append(issue)

    enforced_new_cosmetic = [x for x in new_cosmetic if x.rule not in _RULES_EXCLUDED_FROM_ENFORCE]

    passed = (
        len(critical) == 0
        and len({x.oid for x in enforced_new_cosmetic}) <= int(max_new_cosmetic_offers)
        and len(enforced_new_cosmetic) <= int(max_new_cosmetic_issues)
    )
    ok = True if not enforce else passed

    write_quality_gate_report(
        report_path,
        supplier="comportal",
        passed=passed,
        enforce=enforce,
        baseline_file=baseline_path,
        freeze_current_as_baseline=freeze_current_as_baseline,
        critical=critical,
        cosmetic=cosmetic,
        known_cosmetic=known_cosmetic,
        new_cosmetic=new_cosmetic,
        max_cosmetic_offers=int(max_new_cosmetic_offers),
        max_cosmetic_issues=int(max_new_cosmetic_issues),
    )

    return {
        "ok": ok,
        "report_file": report_path,
        "baseline_file": baseline_path,
        "critical_count": len(critical),
        "cosmetic_total_count": len(cosmetic),
        "known_cosmetic_count": len(known_cosmetic),
        "new_cosmetic_count": len(new_cosmetic),
        "critical_preview": [
            f"{x.oid} | {x.rule} | {x.details}".strip(" |")
            for x in critical[:20]
        ],
    }
