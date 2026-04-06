# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/akcent/quality_gate.py

AkCent supplier layer — quality gate первого контура.
v27:
- логика не меняется;
- описание приведено к текущему проектному правилу:
  supplier-side gate проверяет RAW feed, а не FINAL.

Что делает:
- проверяет raw feed после supplier-layer;
- critical всегда валят сборку;
- cosmetic считаются полностью, baseline нужен только для отчёта;
- freeze_current_as_baseline сохраняет текущее cosmetic-состояние как snapshot;
- логика пока маленькая и предметная, без шума.
"""
