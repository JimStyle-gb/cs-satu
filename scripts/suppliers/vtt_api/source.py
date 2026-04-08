# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/vtt_api/source.py
SOAP source-layer для VTT_api.
Только auth + вызов API + возврат сырого payload.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping

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


def _bool_from_any(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no"}


def _coerce_cfg(cfg: ApiConfig | Mapping[str, Any] | None) -> ApiConfig:
    if cfg is None:
        return ApiConfig.from_env()
    if isinstance(cfg, ApiConfig):
        return cfg
    if isinstance(cfg, Mapping):
        wsdl = str(cfg.get("wsdl") or cfg.get("VTT_API_WSDL") or os.getenv("VTT_API_WSDL") or os.getenv("VTT_WSDL") or "").strip()
        login = str(cfg.get("login") or cfg.get("VTT_LOGIN") or os.getenv("VTT_LOGIN") or "").strip()
        password = str(cfg.get("password") or cfg.get("VTT_PASSWORD") or os.getenv("VTT_PASSWORD") or "").strip()
        timeout_raw = cfg.get("timeout_s", cfg.get("timeout", os.getenv("VTT_API_TIMEOUT") or 45))
        verify_raw = cfg.get("verify_ssl", cfg.get("verify", os.getenv("VTT_API_VERIFY_SSL") or True))
        timeout_s = int(str(timeout_raw).strip() or 45)
        verify_ssl = _bool_from_any(verify_raw, True)
        if not wsdl:
            raise RuntimeError("VTT_api: не задан VTT_API_WSDL/VTT_WSDL")
        if not login or not password:
            raise RuntimeError("VTT_api: не заданы VTT_LOGIN/VTT_PASSWORD")
        return ApiConfig(wsdl=wsdl, login=login, password=password, timeout_s=timeout_s, verify_ssl=verify_ssl)
    raise TypeError(f"VTT_api: неподдерживаемый тип cfg: {type(cfg)!r}")


def _client(cfg: ApiConfig | Mapping[str, Any] | None) -> Any:
    cfg2 = _coerce_cfg(cfg)
    if Client is None or Transport is None:
        raise RuntimeError("VTT_api: zeep не установлен. Добавь pip install zeep")
    session = Session()
    session.verify = cfg2.verify_ssl
    session.headers.update({"User-Agent": "CS-Template-VTT-api/1.0"})
    transport = Transport(session=session, timeout=cfg2.timeout_s)
    return Client(wsdl=cfg2.wsdl, transport=transport)


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


def fetch_items(cfg: ApiConfig | Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg2 = _coerce_cfg(cfg)
    client = _client(cfg2)
    raw = _try_get_items(client.service, cfg2.login, cfg2.password)
    items = _as_list(raw)
    if not items:
        raise RuntimeError("VTT_api: API вернул пустой каталог или неизвестную структуру ответа")
    return items


def load_items(cfg: ApiConfig | Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Backward-safe alias для build_vtt_api.py."""
    return fetch_items(cfg)
