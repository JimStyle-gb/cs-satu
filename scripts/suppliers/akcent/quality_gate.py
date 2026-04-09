# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/akcent/quality_gate.py

AkCent Quality Gate — quality gate поставщика.

Что делает:
- читает raw-feed и считает critical/cosmetic tails;
- отделяет known/new cosmetic через baseline;
- пишет канонический quality gate report;
- возвращает стабильный результат для build_akcent.py.

Что не делает:
- не чинит и не мутирует feed;
- не переносит supplier-specific классы ошибок в shared core;
- не подменяет builder и report writer.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from html import unescape
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Any

import yaml

from cs.qg_report import write_quality_gate_report

_WS_RE = re.compile(r"\s+")
_PRICE_NUM_RE = re.compile(r"-?\d+")
_COMPAT_LABEL_LEAK_RE = re.compile(
    r"(?iu)\b(?:Характеристики|Модель|Совместимые\s+модели|Поддерживаемые\s+модели|"
    r"Поддерживаемые\s+продукты|Тип\s+печати|Цвет(?:\s+печати)?)\b"
)
_RE_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL | re.IGNORECASE)
_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_TEMPLATE_H3 = re.compile(
    r"(?is)<h3>\s*(?:Характеристики|Оплата\s+и\s+доставка|Оплата|Доставка)\s*</h3>"
)
_DESC_SUPPLIER_HEADER_RE = re.compile(
    r"(?iu)(?:^|\n)\s*(?:Описание|Комплектация|Технические\s+характеристики|"
    r"Общие\s+характеристики|Общие\s+характерстики|Общие\s+параметры|"
    r"Основные\s+преимущества|Технические\s+параметры)\s*(?::|$)"
)

_BANNED_PARAM_KEYS = {
    "normal",
    "from",
    "to",
    "артикул",
    "штрихкод",
    "код товара",
    "sku",
    "offer_id",
    "сопутствующие товары",
}

_GENERIC_VENDOR_TOKENS = {
    "c13t55",
    "емкость",
    "ёмкость",
    "картридж",
    "чернила",
    "экономичный",
    "доска",
    "панель",
    "дисплей",
    "интерактивная",
    "интерактивный",
    "ламинатор",
    "монитор",
    "мфу",
    "переплетчик",
    "пленка",
    "плёнка",
    "плоттер",
    "принтер",
    "проектор",
    "сканер",
    "шредер",
    "экран",
}

_VENDOR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?iu)\bHP\b"), "HP"),
    (re.compile(r"(?iu)\bCanon\b"), "Canon"),
    (re.compile(r"(?iu)\bEpson\b"), "Epson"),
    (re.compile(r"(?iu)\bSMART\b"), "SMART"),
    (re.compile(r"(?iu)\bMr\.?\s*Pixel\b"), "Mr.Pixel"),
    (re.compile(r"(?iu)\bFellowes\b"), "Fellowes"),
    (re.compile(r"(?iu)\bXerox\b"), "Xerox"),
    (re.compile(r"(?iu)\bKyocera\b"), "Kyocera"),
    (re.compile(r"(?iu)\bRicoh\b"), "Ricoh"),
    (re.compile(r"(?iu)\bBrother\b"), "Brother"),
    (re.compile(r"(?iu)\bPantum\b"), "Pantum"),
]

_OID_VENDOR_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?iu)^ACC13T"), "Epson"),
    (re.compile(r"(?iu)^ACC12C"), "Epson"),
    (re.compile(r"(?iu)^ACSBID-"), "SMART"),
]

@dataclass(frozen=True)
class QualityIssue:
    severity: str
    rule: str
    oid: str
    name: str
    details: str

def _norm_ws(value: Any) -> str:
    s = unescape(str(value or "")).replace("\xa0", " ").strip()
    s = _WS_RE.sub(" ", s)
    return s.strip()

def _cf(value: Any) -> str:
    return _norm_ws(value).casefold().replace("ё", "е")

def _read_yaml(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

def _write_yaml(path: str, data: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

def _offer_params(offer_el: ET.Element) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for p in offer_el.findall("param"):
        key = _norm_ws(p.get("name") or "")
        val = _norm_ws("".join(p.itertext()))
        if key and val:
            out[key].append(val)
    return dict(out)

def _safe_price_int(text: str) -> int | None:
    m = _PRICE_NUM_RE.search(_norm_ws(text))
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None

def _description_plain_for_gate(desc_html: str) -> str:
    html = desc_html or ""
    html = _RE_HTML_COMMENT.sub(" ", html)
    html = _RE_TEMPLATE_H3.sub(" ", html)
    html = _RE_HTML_TAG.sub("\n", html)
    text = unescape(html).replace(" ", " ")
    lines: list[str] = []
    for raw in text.splitlines():
        line = _WS_RE.sub(" ", raw).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)

def _infer_vendor_candidate(*, oid: str, name: str, desc_plain: str, params: dict[str, list[str]]) -> str:
    for rx, vendor in _OID_VENDOR_HINTS:
        if rx.search(oid):
            return vendor

    texts: list[str] = [name, desc_plain]
    for key in ("Модель", "Для бренда", "Для устройства", "Совместимость"):
        texts.extend(params.get(key, []))

    joined = "\n".join(x for x in texts if x)
    for rx, vendor in _VENDOR_PATTERNS:
        if rx.search(joined):
            return vendor

    return ""

def _is_suspicious_vendor(*, vendor: str, name: str, desc_plain: str, params: dict[str, list[str]], oid: str) -> bool:
    v = _cf(vendor)
    if v:
        if v in _GENERIC_VENDOR_TOKENS:
            return True
        name_cf = _cf(name)
        if name_cf:
            first_token = name_cf.split(" ", 1)[0]
            if first_token in _GENERIC_VENDOR_TOKENS and v == first_token:
                return True
        if v in {"интерактивная", "интерактивный", "экономичный"}:
            return True
        return False

    inferred = _infer_vendor_candidate(
        oid=oid,
        name=name,
        desc_plain=desc_plain,
        params=params,
    )
    return not bool(inferred)

def _detect_issues(feed_path: str) -> list[QualityIssue]:
    xml_text = Path(feed_path).read_text(encoding="utf-8", errors="ignore")
    root = ET.fromstring(xml_text)

    issues: list[QualityIssue] = []

    for offer in root.findall(".//offer"):
        oid = _norm_ws(offer.get("id") or "")
        name = _norm_ws(offer.findtext("name") or "")
        vendor = _norm_ws(offer.findtext("vendor") or "")
        price_text = _norm_ws(offer.findtext("price") or "")
        desc_html = offer.findtext("description") or ""
        params = _offer_params(offer)

        price_int = _safe_price_int(price_text)
        if price_int is None or price_int <= 0:
            issues.append(QualityIssue("critical", "invalid_price", oid, name, price_text or "empty"))

        for key in params:
            if _cf(key) in _BANNED_PARAM_KEYS:
                issues.append(QualityIssue("critical", "banned_param_key", oid, name, key))

        if "oaicite" in desc_html or "contentReference" in desc_html:
            issues.append(QualityIssue("critical", "desc_oaicite_leak", oid, name, "oaicite/contentReference"))
            desc_plain = ""
        else:
            desc_plain = _description_plain_for_gate(desc_html)
            if _DESC_SUPPLIER_HEADER_RE.search(desc_plain):
                issues.append(QualityIssue("cosmetic", "desc_header_leak", oid, name, "supplier header in description"))

        if _is_suspicious_vendor(
            vendor=vendor,
            name=name,
            desc_plain=desc_plain,
            params=params,
            oid=oid,
        ):
            issues.append(QualityIssue("cosmetic", "suspicious_vendor", oid, name, vendor or "empty"))

        compat_values = params.get("Совместимость", []) + params.get("Для устройства", [])
        for compat in compat_values:
            if _COMPAT_LABEL_LEAK_RE.search(compat):
                issues.append(QualityIssue("cosmetic", "compat_label_leak", oid, name, compat[:200]))
                break

    deduped: dict[tuple[str, str, str], QualityIssue] = {}
    for issue in issues:
        deduped[(issue.severity, issue.rule, issue.oid)] = issue

    return sorted(deduped.values(), key=lambda x: (x.severity, x.rule, x.oid))

def _load_cosmetic_baseline(baseline_path: str) -> dict[str, set[str]]:
    data = _read_yaml(baseline_path)
    raw = data.get("accepted_cosmetic") or {}
    out: dict[str, set[str]] = {}
    for rule, oids in raw.items():
        out[str(rule)] = {str(x).strip() for x in (oids or []) if str(x).strip()}
    return out

def _make_baseline_payload(cosmetic: list[QualityIssue]) -> dict[str, Any]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for issue in cosmetic:
        grouped[issue.rule].append(issue.oid)

    payload: dict[str, Any] = {"schema_version": 1, "accepted_cosmetic": {}}
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
    max_cosmetic_offers: int,
    max_cosmetic_issues: int,
    passed: bool,
    baseline_file: str,
    frozen: bool,
    enforce: bool,
) -> None:
    write_quality_gate_report(
        path,
        supplier="akcent",
        passed=passed,
        enforce=enforce,
        baseline_file=baseline_file,
        freeze_current_as_baseline=frozen,
        critical=critical,
        cosmetic=cosmetic,
        known_cosmetic=known_cosmetic,
        new_cosmetic=new_cosmetic,
        max_cosmetic_offers=max_cosmetic_offers,
        max_cosmetic_issues=max_cosmetic_issues,
    )

def run_quality_gate(
    *,
    feed_path: str,
    baseline_path: str,
    report_path: str,
    max_new_cosmetic_offers: int = 5,
    max_new_cosmetic_issues: int = 5,
    enforce: bool = True,
    freeze_current_as_baseline: bool = False,
    **_legacy_unused: object,
) -> tuple[bool, str]:
    issues = _detect_issues(feed_path)
    critical = [x for x in issues if x.severity == "critical"]
    cosmetic = [x for x in issues if x.severity == "cosmetic"]

    accepted_cosmetic = _load_cosmetic_baseline(baseline_path)
    known_cosmetic: list[QualityIssue] = []
    new_cosmetic: list[QualityIssue] = []
    for issue in cosmetic:
        if issue.oid in accepted_cosmetic.get(issue.rule, set()):
            known_cosmetic.append(issue)
        else:
            new_cosmetic.append(issue)

    if freeze_current_as_baseline:
        _write_yaml(baseline_path, _make_baseline_payload(cosmetic))

    cosmetic_offer_count = len({x.oid for x in cosmetic})
    passed = (
        len(critical) == 0
        and cosmetic_offer_count <= int(max_new_cosmetic_offers)
        and len(cosmetic) <= int(max_new_cosmetic_issues)
    )

    _write_report(
        report_path,
        critical=critical,
        cosmetic=cosmetic,
        known_cosmetic=known_cosmetic,
        new_cosmetic=new_cosmetic,
        max_cosmetic_offers=int(max_new_cosmetic_offers),
        max_cosmetic_issues=int(max_new_cosmetic_issues),
        passed=passed,
        baseline_file=baseline_path,
        frozen=freeze_current_as_baseline,
        enforce=enforce,
    )

    summary = (
        f"[AkCent quality_gate] {'PASS' if passed else 'FAIL'} | "
        f"critical={len(critical)} | cosmetic={len(cosmetic)} | "
        f"cosmetic_offers={cosmetic_offer_count} | report={report_path}"
    )

    if enforce and not passed:
        return False, summary
    return True, summary
