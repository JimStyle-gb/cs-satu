# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/vtt/quality_gate.py

VTT quality gate.

Роль файла:
- проверяет final feed после supplier-layer и shared core;
- разделяет blocking и allowed cosmetic tails;
- пишет единый отчёт через shared cs.qg_report writer.

Что файл делает:
- ловит empty_vendor, oaicite leaks, decimal_k_resource;
- сохраняет placeholder_picture как допустимый known cosmetic tail;
- возвращает backward-safe QualityGateResult для build_vtt.py.

Что файл НЕ делает:
- не чинит raw feed;
- не заменяет normalize/builder/pictures слой.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from html import unescape
from pathlib import Path
import re
import xml.etree.ElementTree as ET

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

from cs.qg_report import write_quality_gate_report


PLACEHOLDER_URL = "https://placehold.co/800x800/png?text=No+Photo"
QUALITY_BASELINE_DEFAULT = "scripts/suppliers/vtt/config/quality_gate_baseline.yml"
QUALITY_REPORT_DEFAULT = "docs/raw/vtt_quality_gate.txt"
_DECIMAL_K_RE = re.compile(r"^\d+(?:[.,]\d+)+K$", re.I)
_WS_RE = re.compile(r"\s+")
_RULES_EXCLUDED_FROM_ENFORCE = {"placeholder_picture"}
_RULES_TREATED_AS_ALLOWED_KNOWN = {"placeholder_picture"}


@dataclass(frozen=True)
class QualityIssue:
    severity: str
    rule: str
    oid: str
    name: str
    details: str


@dataclass(frozen=True)
class QualityGateResult:
    ok: bool
    report_path: str
    critical_count: int
    cosmetic_count: int


def _norm_ws(s: str) -> str:
    s2 = unescape(s or "")
    s2 = s2.replace("\u00a0", " ").strip()
    s2 = _WS_RE.sub(" ", s2).strip()
    return s2


def _read_yaml(path: str | None) -> dict:
    if not path:
        return {}
    p = Path(path)
    if not p.exists() or yaml is None:
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _write_yaml(path: str | None, data: dict) -> None:
    if not path or yaml is None:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _offer_params(offer_el: ET.Element) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for p in offer_el.findall("param"):
        k = _norm_ws(p.get("name") or "")
        v = _norm_ws("".join(p.itertext()))
        if k and v:
            out[k].append(v)
    return dict(out)


def _make_issue(severity: str, rule: str, oid: str, name: str, details: str) -> QualityIssue:
    return QualityIssue(
        severity=severity,
        rule=rule,
        oid=_norm_ws(oid),
        name=_norm_ws(name),
        details=_norm_ws(details),
    )


def _detect_issues(feed_path: str) -> tuple[list[QualityIssue], int]:
    xml_path = Path(feed_path)
    xml_text = xml_path.read_text(encoding="utf-8", errors="ignore")
    root = ET.fromstring(xml_text)

    issues: list[QualityIssue] = []
    offers = root.findall(".//offer")
    offer_count = len(offers)

    for offer in offers:
        oid = _norm_ws(offer.get("id") or "")
        name = _norm_ws(offer.findtext("name") or "")
        vendor = _norm_ws(offer.findtext("vendor") or "")
        desc_html = offer.findtext("description") or ""
        params = _offer_params(offer)

        if not vendor:
            issues.append(_make_issue("critical", "empty_vendor", oid, name, ""))

        for pic in offer.findall("picture"):
            url = _norm_ws("".join(pic.itertext()))
            if url == PLACEHOLDER_URL:
                issues.append(_make_issue("cosmetic", "placeholder_picture", oid, name, url))

        for resource in params.get("Ресурс", []):
            if _DECIMAL_K_RE.match(resource):
                issues.append(_make_issue("cosmetic", "decimal_k_resource", oid, name, resource))

        if "oaicite" in desc_html or "contentReference" in desc_html:
            issues.append(_make_issue("critical", "desc_oaicite_leak", oid, name, "oaicite/contentReference"))

    deduped: dict[tuple[str, str, str, str], QualityIssue] = {}
    for issue in issues:
        deduped[(issue.severity, issue.rule, issue.oid, issue.details)] = issue

    return sorted(deduped.values(), key=lambda x: (x.severity, x.rule, x.oid, x.details)), offer_count


def _load_cosmetic_baseline(baseline_path: str | None) -> dict[str, set[str]]:
    data = _read_yaml(baseline_path)
    raw = data.get("accepted_cosmetic") or {}
    out: dict[str, set[str]] = {}
    for rule, oids in raw.items():
        out[str(rule)] = {str(x).strip() for x in (oids or []) if str(x).strip()}
    return out


def _make_baseline_payload(cosmetic: list[QualityIssue]) -> dict:
    grouped: dict[str, list[str]] = defaultdict(list)
    for issue in cosmetic:
        if issue.rule in _RULES_TREATED_AS_ALLOWED_KNOWN:
            continue
        grouped[issue.rule].append(issue.oid)
    payload = {"schema_version": 1, "accepted_cosmetic": {}}
    for rule in sorted(grouped):
        payload["accepted_cosmetic"][rule] = sorted(set(grouped[rule]))
    return payload


def _write_report(
    path: str,
    *,
    critical: list[QualityIssue],
    cosmetic: list[QualityIssue],
    known_cosmetic: list[QualityIssue],
    new_cosmetic: list[QualityIssue],
    baseline_path: str,
    freeze_current_as_baseline: bool,
    enforce: bool,
    max_cosmetic_offers: int,
    max_cosmetic_issues: int,
    passed: bool,
) -> None:
    write_quality_gate_report(
        path,
        supplier="vtt",
        passed=passed,
        enforce=enforce,
        baseline_file=baseline_path,
        freeze_current_as_baseline=freeze_current_as_baseline,
        critical=critical,
        cosmetic=cosmetic,
        known_cosmetic=known_cosmetic,
        new_cosmetic=new_cosmetic,
        max_cosmetic_offers=int(max_cosmetic_offers),
        max_cosmetic_issues=int(max_cosmetic_issues),
    )


def run_quality_gate(

    *,
    feed_path: str,
    report_path: str | None = None,
    baseline_path: str | None = None,
    max_new_cosmetic_offers: int = 5,
    max_new_cosmetic_issues: int = 5,
    enforce: bool = True,
    freeze_current_as_baseline: bool = False,
) -> QualityGateResult:
    report_path = str(report_path or QUALITY_REPORT_DEFAULT)
    baseline_path = str(baseline_path or QUALITY_BASELINE_DEFAULT)

    issues, _offer_count = _detect_issues(feed_path)
    critical = [x for x in issues if x.severity == "critical"]
    cosmetic = [x for x in issues if x.severity == "cosmetic"]

    if freeze_current_as_baseline:
        _write_yaml(baseline_path, _make_baseline_payload(cosmetic))

    baseline = _load_cosmetic_baseline(baseline_path)

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

    _write_report(
        report_path,
        critical=critical,
        cosmetic=cosmetic,
        known_cosmetic=known_cosmetic,
        new_cosmetic=new_cosmetic,
        baseline_path=baseline_path,
        freeze_current_as_baseline=freeze_current_as_baseline,
        enforce=enforce,
        max_cosmetic_offers=int(max_new_cosmetic_offers),
        max_cosmetic_issues=int(max_new_cosmetic_issues),
        passed=passed,
    )
    ok = True if not enforce else passed
    return QualityGateResult(
        ok=ok,
        report_path=report_path,
        critical_count=len(critical),
        cosmetic_count=len(cosmetic),
    )
