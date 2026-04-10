# -*- coding: utf-8 -*-
"""
Path: scripts/cs/qg_report.py

CS Quality Gate Report — shared report writer layer.

Что делает:
- пишет единый quality gate report;
- держит канонические секции supplier QG-отчётов;
- даёт единый контракт QualityGateResult для всех supplier quality gate.

Что не делает:
- не анализирует XML сам по себе;
- не подменяет quality_gate.py.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

_ALIAS_MAP = {
    "report_file": "report_path",
    "baseline_file": "baseline_path",
    "cosmetic_total_count": "cosmetic_count",
}


def _ru_rule_comment(rule: str) -> str:
    comments = {
        "invalid_price": "Цена невалидна после финальной сборки",
        "banned_param_key": "В финал просочился запрещённый параметр",
        "desc_oaicite_leak": "В описание попала служебная метка",
        "compat_label_leak": "В совместимость протекли служебные label-блоки",
        "bad_power_key": "В параметрах остался мусорный ключ мощности",
        "heavy_xerox_compat": "Слишком длинная цепочка совместимости Xerox",
        "marketplace_param_leak": "В финал/гейт просочился служебный marketplace-параметр",
        "marketplace_text_in_description": "В описание попал служебный marketplace-текст",
        "tech_block_leak_in_body": "В обычный body протёк техблок 'Характеристики'",
    }
    return comments.get(rule, "Требует ручной проверки")


def _issue_line(issue) -> str:
    details = (issue.details or "").replace("\n", " ").strip()
    if len(details) > 240:
        details = details[:237] + "..."
    return f"{issue.oid} | {issue.rule} | {_ru_rule_comment(issue.rule)} | {details}"


def _section(lines: list[str], title: str, issues: Iterable) -> None:
    lines.append("")
    lines.append(f"{title}:")
    items = list(issues)
    if not items:
        lines.append("# Ошибок в этой секции нет")
        return
    for issue in items:
        lines.append(_issue_line(issue))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().casefold()
    if not s:
        return default
    return s in {"1", "true", "yes", "y", "on"}


def _safe_text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _default_summary(*, ok: bool, critical_count: int, cosmetic_count: int, report_path: str) -> str:
    return (
        f"[quality_gate] {'PASS' if ok else 'FAIL'} | "
        f"critical={critical_count} | "
        f"cosmetic={cosmetic_count} | "
        f"report={report_path}"
    )


@dataclass(frozen=True)
class QualityGateResult:
    ok: bool
    report_path: str = ""
    baseline_path: str = ""
    critical_count: int = 0
    cosmetic_count: int = 0
    cosmetic_offer_count: int = 0
    known_cosmetic_count: int = 0
    new_cosmetic_count: int = 0
    enforce: bool = True
    threshold_ok: bool = True
    max_cosmetic_offers: int = 5
    max_cosmetic_issues: int = 5
    summary: str = ""
    critical_preview: tuple[str, ...] = ()

    @property
    def report_file(self) -> str:
        return self.report_path

    @property
    def baseline_file(self) -> str:
        return self.baseline_path

    @property
    def cosmetic_total_count(self) -> int:
        return self.cosmetic_count

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        alias = _ALIAS_MAP.get(key)
        if alias and hasattr(self, alias):
            return getattr(self, alias)
        return default

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "report_path": self.report_path,
            "report_file": self.report_file,
            "baseline_path": self.baseline_path,
            "baseline_file": self.baseline_file,
            "critical_count": self.critical_count,
            "cosmetic_count": self.cosmetic_count,
            "cosmetic_total_count": self.cosmetic_total_count,
            "cosmetic_offer_count": self.cosmetic_offer_count,
            "known_cosmetic_count": self.known_cosmetic_count,
            "new_cosmetic_count": self.new_cosmetic_count,
            "enforce": self.enforce,
            "threshold_ok": self.threshold_ok,
            "max_cosmetic_offers": self.max_cosmetic_offers,
            "max_cosmetic_issues": self.max_cosmetic_issues,
            "summary": self.summary,
            "critical_preview": list(self.critical_preview),
        }

    def __iter__(self):
        yield self.ok
        yield self.summary

    def __bool__(self) -> bool:
        return bool(self.ok)


def make_quality_gate_result(
    *,
    ok: bool,
    report_path: str = "",
    baseline_path: str = "",
    critical_count: int = 0,
    cosmetic_count: int = 0,
    cosmetic_offer_count: int = 0,
    known_cosmetic_count: int = 0,
    new_cosmetic_count: int = 0,
    enforce: bool = True,
    threshold_ok: bool | None = None,
    max_cosmetic_offers: int = 5,
    max_cosmetic_issues: int = 5,
    summary: str = "",
    critical_preview: Iterable[str] | None = None,
) -> QualityGateResult:
    report_path = _safe_text(report_path)
    baseline_path = _safe_text(baseline_path)
    critical_count = _safe_int(critical_count)
    cosmetic_count = _safe_int(cosmetic_count)
    cosmetic_offer_count = _safe_int(cosmetic_offer_count)
    known_cosmetic_count = _safe_int(known_cosmetic_count)
    new_cosmetic_count = _safe_int(new_cosmetic_count)
    max_cosmetic_offers = _safe_int(max_cosmetic_offers, 5)
    max_cosmetic_issues = _safe_int(max_cosmetic_issues, 5)
    if threshold_ok is None:
        threshold_ok = bool(ok)
    summary = _safe_text(
        summary,
        _default_summary(
            ok=bool(ok),
            critical_count=critical_count,
            cosmetic_count=cosmetic_count,
            report_path=report_path,
        ),
    )
    preview = tuple(_safe_text(x) for x in (critical_preview or []) if _safe_text(x))
    return QualityGateResult(
        ok=bool(ok),
        report_path=report_path,
        baseline_path=baseline_path,
        critical_count=critical_count,
        cosmetic_count=cosmetic_count,
        cosmetic_offer_count=cosmetic_offer_count,
        known_cosmetic_count=known_cosmetic_count,
        new_cosmetic_count=new_cosmetic_count,
        enforce=bool(enforce),
        threshold_ok=bool(threshold_ok),
        max_cosmetic_offers=max_cosmetic_offers,
        max_cosmetic_issues=max_cosmetic_issues,
        summary=summary,
        critical_preview=preview,
    )


def coerce_quality_gate_result(
    result: Any,
    *,
    report_path: str = "",
    baseline_path: str = "",
    summary: str = "",
    enforce: bool = True,
) -> QualityGateResult:
    if isinstance(result, QualityGateResult):
        updates: dict[str, Any] = {}
        if report_path and not result.report_path:
            updates["report_path"] = report_path
        if baseline_path and not result.baseline_path:
            updates["baseline_path"] = baseline_path
        if summary and not result.summary:
            updates["summary"] = summary
        if updates:
            return replace(result, **updates)
        return result

    if isinstance(result, tuple) and len(result) >= 2 and isinstance(result[0], bool):
        return make_quality_gate_result(
            ok=result[0],
            report_path=report_path,
            baseline_path=baseline_path,
            summary=_safe_text(result[1], summary),
            enforce=enforce,
            threshold_ok=bool(result[0]),
        )

    if isinstance(result, dict):
        return make_quality_gate_result(
            ok=_safe_bool(result.get("ok"), True),
            report_path=_safe_text(result.get("report_path") or result.get("report_file"), report_path),
            baseline_path=_safe_text(result.get("baseline_path") or result.get("baseline_file"), baseline_path),
            critical_count=result.get("critical_count", 0),
            cosmetic_count=result.get("cosmetic_count", result.get("cosmetic_total_count", 0)),
            cosmetic_offer_count=result.get("cosmetic_offer_count", 0),
            known_cosmetic_count=result.get("known_cosmetic_count", 0),
            new_cosmetic_count=result.get("new_cosmetic_count", 0),
            enforce=_safe_bool(result.get("enforce"), enforce),
            threshold_ok=result.get("threshold_ok", result.get("ok", True)),
            max_cosmetic_offers=result.get("max_cosmetic_offers", 5),
            max_cosmetic_issues=result.get("max_cosmetic_issues", 5),
            summary=_safe_text(result.get("summary"), summary),
            critical_preview=result.get("critical_preview") or [],
        )

    if hasattr(result, "ok"):
        return make_quality_gate_result(
            ok=_safe_bool(getattr(result, "ok", True), True),
            report_path=_safe_text(getattr(result, "report_path", getattr(result, "report_file", "")), report_path),
            baseline_path=_safe_text(getattr(result, "baseline_path", getattr(result, "baseline_file", "")), baseline_path),
            critical_count=getattr(result, "critical_count", 0),
            cosmetic_count=getattr(result, "cosmetic_count", getattr(result, "cosmetic_total_count", 0)),
            cosmetic_offer_count=getattr(result, "cosmetic_offer_count", 0),
            known_cosmetic_count=getattr(result, "known_cosmetic_count", 0),
            new_cosmetic_count=getattr(result, "new_cosmetic_count", 0),
            enforce=_safe_bool(getattr(result, "enforce", enforce), enforce),
            threshold_ok=getattr(result, "threshold_ok", getattr(result, "ok", True)),
            max_cosmetic_offers=getattr(result, "max_cosmetic_offers", 5),
            max_cosmetic_issues=getattr(result, "max_cosmetic_issues", 5),
            summary=_safe_text(getattr(result, "summary", ""), summary),
            critical_preview=getattr(result, "critical_preview", []) or [],
        )

    if isinstance(result, bool):
        return make_quality_gate_result(
            ok=result,
            report_path=report_path,
            baseline_path=baseline_path,
            summary=summary,
            enforce=enforce,
            threshold_ok=result,
        )

    return make_quality_gate_result(
        ok=True,
        report_path=report_path,
        baseline_path=baseline_path,
        summary=summary,
        enforce=enforce,
        threshold_ok=True,
    )


def write_quality_gate_report(
    path: str,
    *,
    supplier: str,
    passed: bool,
    enforce: bool,
    baseline_file: str,
    freeze_current_as_baseline: bool,
    critical: list,
    cosmetic: list,
    known_cosmetic: list,
    new_cosmetic: list,
    max_cosmetic_offers: int,
    max_cosmetic_issues: int,
) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    cosmetic_offer_count = len({x.oid for x in cosmetic})
    known_offer_count = len({x.oid for x in known_cosmetic})
    new_offer_count = len({x.oid for x in new_cosmetic})

    lines: list[str] = []
    lines.append("# Итог проверки quality gate")
    lines.append(f"QUALITY_GATE: {'PASS' if passed else 'FAIL'}")
    lines.append("# PASS = можно выпускать | FAIL = есть блокирующие проблемы")
    lines.append(f"enforce: {'true' if enforce else 'false'}")
    lines.append("# true = quality gate реально валит сборку")
    lines.append(f"supplier: {supplier}")
    lines.append("# Поставщик, для которого построен отчёт")
    lines.append(f"report_file: {path}")
    lines.append("# Куда записан этот отчёт")
    lines.append(f"baseline_file: {baseline_file}")
    lines.append("# Базовый файл для сравнения известных cosmetic-проблем")
    lines.append(f"freeze_current_as_baseline: {'yes' if freeze_current_as_baseline else 'no'}")
    lines.append("# yes = текущие cosmetic-хвосты сохранены как baseline-снимок")
    lines.append(f"critical_count: {len(critical)}")
    lines.append("# Сколько найдено критичных проблем")
    lines.append(f"cosmetic_total_count: {len(cosmetic)}")
    lines.append("# Общее число некритичных проблем")
    lines.append(f"cosmetic_offer_count: {cosmetic_offer_count}")
    lines.append("# В скольких товарах есть cosmetic-проблемы")
    lines.append(f"known_cosmetic_count: {len(known_cosmetic)}")
    lines.append("# Сколько cosmetic-проблем уже известны по baseline")
    lines.append(f"known_cosmetic_offer_count: {known_offer_count}")
    lines.append("# В скольких товарах есть уже известные cosmetic-проблемы")
    lines.append(f"new_cosmetic_count: {len(new_cosmetic)}")
    lines.append("# Сколько найдено новых cosmetic-проблем")
    lines.append(f"new_cosmetic_offer_count: {new_offer_count}")
    lines.append("# В скольких товарах появились новые cosmetic-проблемы")
    lines.append(f"max_cosmetic_offers: {int(max_cosmetic_offers)}")
    lines.append("# Допустимый максимум товаров с cosmetic-проблемами")
    lines.append(f"max_cosmetic_issues: {int(max_cosmetic_issues)}")
    lines.append("# Допустимый максимум cosmetic-проблем всего")

    _section(lines, "CRITICAL", critical)
    _section(lines, "COSMETIC TOTAL", cosmetic)
    _section(lines, "NEW COSMETIC", new_cosmetic)
    _section(lines, "KNOWN COSMETIC", known_cosmetic)

    p.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
