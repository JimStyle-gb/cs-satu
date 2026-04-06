# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/vtt/desc_clean.py

VTT description clean layer.

Роль файла:
- чистит только native narrative/body поставщика;
- убирает служебные хвосты, которые не должны попадать в display-body;
- не заменяет extractor и не должен извлекать характеристики.
"""

from __future__ import annotations

import re

from .normalize import norm_ws

# Служебные supplier-поля, которые не должны жить в narrative body.
SERVICE_DESC_RE = re.compile(
    r"(?:^|[.;,\n ])(?:Артикул|Штрих-?код|Вендор|Категория|Подкатегория|В упаковке, штук|"
    r"Местный склад, штук|Местный, до новой поставки, дней|Склад Москва, штук|"
    r"Москва, до новой поставки, дней)\s*[:\-][^.;\n]*",
    re.I,
)


def clean_native_description(desc_body: str) -> str:
    """Очистка narrative body без extractor-логики и без supplier guessing."""
    body = norm_ws(desc_body)
    if not body:
        return ""
    body = SERVICE_DESC_RE.sub(" ", body)
    body = re.sub(r"\s{2,}", " ", body).strip(" ,.;")
    return norm_ws(body)
