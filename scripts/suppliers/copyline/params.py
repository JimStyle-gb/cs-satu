# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/copyline/params.py

Канонический params-модуль для CopyLine.
Пока это безопасный wrapper поверх текущего params_page.py,
чтобы выровнять имя файла по шаблону CS без риска сломать рабочий extractor.
"""

from .params_page import *  # noqa: F401,F403
