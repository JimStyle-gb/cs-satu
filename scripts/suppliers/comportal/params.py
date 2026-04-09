# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/comportal/params.py

Канонический params-модуль для ComPortal.
Пока это безопасный wrapper поверх текущего params_xml.py,
чтобы выровнять имя файла по шаблону CS без риска сломать рабочий extractor.
"""

from .params_xml import *  # noqa: F401,F403
