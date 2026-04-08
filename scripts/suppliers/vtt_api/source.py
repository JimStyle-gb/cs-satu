# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/vtt_api/source.py
SOAP source-layer для VTT_api.
Только auth + вызов API + возврат сырого payload.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import requests
from requests import Session

try:
    from zeep import Client
    from zeep.helpers import serialize_object
    from zeep.transports import Transport
except Exception:  # pragma: no cover
    Client = None  # type: ignore
    serialize_object = None  # type: ignore
    Transport = None  # type: ignore


@dataclass(slots=True)
class ApiConfig:
    wsdl: str
    login: str
    password: str
    timeout_s: int = 45
    verify_ssl: bool = True

    @classmethod
    def from_env(cls) -> "ApiConfig":
        wsdl = (os.getenv("VTT_API_WSDL") or os.getenv("VTT_WSDL") or "").strip()
        login = (os.getenv("VTT_LOGIN") or "").strip()
        password = (os.getenv("VTT_PASSWORD") or "").strip()
        timeout_s = int((os.getenv("VTT_API_TIMEOUT") or "45").strip() or 45)
        verify_ssl = (os.getenv("VTT_API_VERIFY_SSL") or "true").strip().lower() not in {"0", "false", "no"}
        if not wsdl:
            raise RuntimeError("VTT_api: не задан VTT_API_WSDL/VTT_WSDL")
        if not login or not password:
            raise RuntimeError("VTT_api: не заданы VTT_LOGIN/VTT_PASSWORD")
        return cls(wsdl=wsdl, login=login, password=password, timeout_s=timeout_s, verify_ssl=verify_ssl)


def _client(cfg: ApiConfig) -> Any:
    if Client is None or Transport is None:
        raise RuntimeError("VTT_api: zeep не установлен. Добавь pip install zeep")
    session = Session()
    session.verify = cfg.verify_ssl
    session.headers.update({"User-Agent": "CS-Template-VTT-api/1.0"})
    transport = Transport(session=session, timeout=cfg.timeout_s)
    return Client(wsdl=cfg.wsdl, transport=transport)


def _try_get_items(service: Any, login: str, password: str) -> Any:
    candidates = [
        ((login, password), {}),
        ((), {"login": login, "password": password}),
        ((), {"Login": login, "Password": password}),
        ((), {"userLogin": login, "userPassword": password}),
        ((), {"UserLogin": login, "UserPassword": password}),
    ]
    last_error: Exception | None = None
    for args, kwargs in candidates:
        try:
            return service.GetItems(*args, **kwargs)
        except Exception as exc:  # pragma: no cover
            last_error = exc
            continue
    raise RuntimeError(f"VTT_api: GetItems не удалось вызвать: {last_error}")


def _as_list(payload: Any) -> list[dict[str, Any]]:
    if serialize_object is not None:
        payload = serialize_object(payload)
    if payload is None:
        return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("items", "Items", "item", "Item", "rows", "Rows", "products", "Products"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
        return [payload]
    return []


def fetch_items(cfg: ApiConfig) -> list[dict[str, Any]]:
    client = _client(cfg)
    raw = _try_get_items(client.service, cfg.login, cfg.password)
    items = _as_list(raw)
    if not items:
        raise RuntimeError("VTT_api: API вернул пустой каталог или неизвестную структуру ответа")
    return items
