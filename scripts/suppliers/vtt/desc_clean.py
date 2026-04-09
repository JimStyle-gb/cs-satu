# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/vtt/desc_clean.py

VTT Desc Clean — narrative-clean слой supplier-layer.

Что делает:
- чистит supplier-body от служебного и шумового текста;
- сохраняет полезный narrative для raw/native_desc;
- подготавливает безопасный plain-text для display/extract слоя.

Что не делает:
- не участвует в compat repair;
- не заменяет главный params extractor;
- не строит final HTML description.
"""
from __future__ import annotations

import re

from .normalize import norm_ws

SERVICE_DESC_RE = re.compile(
    r"(?:^|[.;,\n ])(?:Артикул|Штрих-?код|Вендор|Категория|Подкатегория|В упаковке, штук|"
    r"Местный склад, штук|Местный, до новой поставки, дней|Склад Москва, штук|"
    r"Москва, до новой поставки, дней)\s*[:\-][^.;\n]*",
    re.I,
)

def clean_native_description(desc_body: str) -> str:
    body = norm_ws(desc_body)
    if not body:
        return ""
    body = SERVICE_DESC_RE.sub(" ", body)
    body = re.sub(r"\s{2,}", " ", body).strip(" ,.;")
    return norm_ws(body)
