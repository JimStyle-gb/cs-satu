# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/comportal/source.py

ComPortal Source — получение и парсинг сырого источника поставщика.

Что делает:
- читает XML/YML-источник поставщика;
- аккуратно диагностирует пустой/битый/HTML-ответ;
- собирает SourceOffer и raw payload для следующих слоёв.

Что не делает:
- не строит final shared offer;
- не переносит supplier-specific repairs в shared core;
- не заменяет builder.py.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import requests

from cs.util import norm_ws
from suppliers.comportal.models import CategoryRecord, ParamItem, SourceOffer

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/xml,text/xml,application/xhtml+xml,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def _preview_text(text: str, limit: int = 240) -> str:
    s = (text or "").replace("\r", " ").replace("\n", " ").strip()
    if len(s) > limit:
        return s[:limit] + "..."
    return s


def _decode_response_text(response: requests.Response) -> str:
    """Надёжно декодировать body даже если response.text пустой или сервер дал кривую кодировку."""
    text = (response.text or "").lstrip("\ufeff").strip()
    if text:
        return text

    raw = response.content or b""
    if not raw:
        return ""

    for enc in (
        response.encoding,
        getattr(response, "apparent_encoding", None),
        "utf-8-sig",
        "utf-8",
        "cp1251",
        "windows-1251",
        "latin-1",
    ):
        if not enc:
            continue
        try:
            text = raw.decode(enc, errors="strict").lstrip("\ufeff").strip()
            if text:
                return text
        except Exception:
            continue

    return raw.decode("utf-8", errors="replace").lstrip("\ufeff").strip()


def _build_runtime_error(
    *,
    url: str,
    response: requests.Response | None,
    message: str,
    body_preview: str = "",
) -> RuntimeError:
    status = response.status_code if response is not None else "?"
    content_type = response.headers.get("Content-Type", "") if response is not None else ""
    content_length = response.headers.get("Content-Length", "") if response is not None else ""

    parts = [message, f"URL={url}", f"status={status}"]
    if content_type:
        parts.append(f"content-type={content_type}")
    if content_length:
        parts.append(f"content-length={content_length}")
    if body_preview:
        parts.append(f"preview={body_preview}")
    return RuntimeError(" | ".join(parts))


def fetch_xml_text(
    url: str,
    *,
    timeout: int = 120,
    login: str | None = None,
    password: str | None = None,
    retries: int = 3,
    retry_sleep: float = 2.0,
) -> str:
    """Скачать source YML/XML и надёжно отловить пустой/HTML/битый ответ."""
    auth = (login, password) if (login and password) else None
    last_response: requests.Response | None = None

    with requests.Session() as session:
        session.headers.update(DEFAULT_HEADERS)

        for attempt in range(1, max(1, retries) + 1):
            try:
                response = session.get(url, timeout=timeout, auth=auth)
                last_response = response
                response.raise_for_status()
                text = _decode_response_text(response)

                if text:
                    low = text[:400].lower()
                    if "<html" in low or "<!doctype html" in low:
                        raise _build_runtime_error(
                            url=url,
                            response=response,
                            message=(
                                "ComPortal source вернул HTML вместо YML/XML. "
                                "Скорее всего не прошла авторизация или поставщик отдал страницу логина/ошибки."
                            ),
                            body_preview=_preview_text(text),
                        )
                    return text

                if attempt < max(1, retries):
                    time.sleep(retry_sleep)
                    continue

            except requests.RequestException as exc:
                if attempt < max(1, retries):
                    time.sleep(retry_sleep)
                    continue
                raise RuntimeError(
                    "ComPortal source не скачался по сети. "
                    f"URL={url} | details={exc}"
                ) from exc

    raise _build_runtime_error(
        url=url,
        response=last_response,
        message=(
            "ComPortal source вернул пустой body после повторных попыток. "
            "Проверь COMPORTAL_LOGIN/COMPORTAL_PASSWORD, доступность source URL "
            "и не отдаёт ли поставщик пустой ответ."
        ),
    )


def get_text(el: ET.Element | None) -> str:
    """Безопасно вытащить text из XML-узла."""
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def parse_xml_root(xml_text: str) -> ET.Element:
    """Распарсить XML root с понятной ошибкой."""
    text = (xml_text or "").lstrip("\ufeff").strip()
    if not text:
        raise RuntimeError("ComPortal source XML пустой после скачивания.")

    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise RuntimeError(
            "ComPortal source не распарсился как XML. "
            f"ParseError: {exc}. Preview: {_preview_text(text)}"
        ) from exc


def iter_offer_elements(root: ET.Element):
    """Итератор source offer nodes."""
    return root.findall(".//offer")


def build_category_index(root: ET.Element) -> dict[str, CategoryRecord]:
    """Построить индекс source categories."""
    out: dict[str, CategoryRecord] = {}

    for cat in root.findall(".//categories/category"):
        cid = norm_ws(cat.get("id") or "")
        if not cid:
            continue
        out[cid] = CategoryRecord(
            category_id=cid,
            name=norm_ws(get_text(cat)),
            parent_id=norm_ws(cat.get("parentId") or ""),
        )

    for cid, rec in out.items():
        chain: list[str] = []
        cur = cid
        seen: set[str] = set()
        root_id = cid

        while cur and cur not in seen and cur in out:
            seen.add(cur)
            cur_rec = out[cur]
            if cur_rec.name:
                chain.append(cur_rec.name)
            root_id = cur
            cur = cur_rec.parent_id

        chain.reverse()
        rec.path = " > ".join([x for x in chain if x])
        rec.root_id = root_id

    return out


def collect_params(offer_el: ET.Element) -> list[ParamItem]:
    """Собрать source params."""
    out: list[ParamItem] = []

    for p in offer_el.findall("param"):
        name = norm_ws(p.get("name") or "")
        value = norm_ws(get_text(p))
        if not name or not value:
            continue
        out.append(ParamItem(name=name, value=value, source="xml"))

    return out


def extract_source_offer(
    offer_el: ET.Element,
    *,
    category_index: dict[str, CategoryRecord],
) -> SourceOffer:
    """Собрать один SourceOffer."""
    category_id = norm_ws(get_text(offer_el.find("categoryId")))
    cat = category_index.get(category_id)

    return SourceOffer(
        raw_id=norm_ws(offer_el.get("id") or ""),
        vendor_code=norm_ws(get_text(offer_el.find("vendorCode"))),
        category_id=category_id,
        category_name=cat.name if cat else "",
        category_path=cat.path if cat else "",
        category_root_id=cat.root_id if cat else "",
        name=norm_ws(get_text(offer_el.find("name"))),
        available_attr=(offer_el.get("available") or "").strip(),
        available_tag=norm_ws(get_text(offer_el.find("available"))),
        vendor=norm_ws(get_text(offer_el.find("vendor"))),
        description=get_text(offer_el.find("description")),
        price_text=get_text(offer_el.find("price")),
        currency_id=norm_ws(get_text(offer_el.find("currencyId"))),
        url=norm_ws(get_text(offer_el.find("url"))),
        active=norm_ws(get_text(offer_el.find("active"))),
        delivery=norm_ws(get_text(offer_el.find("delivery"))),
        picture_urls=[
            norm_ws(get_text(p))
            for p in offer_el.findall("picture")
            if norm_ws(get_text(p))
        ],
        params=collect_params(offer_el),
        offer_el=offer_el,
    )


def load_source_bundle(
    *,
    url: str,
    timeout: int = 120,
    login: str | None = None,
    password: str | None = None,
) -> tuple[dict[str, CategoryRecord], list[SourceOffer]]:
    """Загрузить categories + offers."""
    xml_text = fetch_xml_text(url, timeout=timeout, login=login, password=password)
    root = parse_xml_root(xml_text)
    category_index = build_category_index(root)
    offers = [
        extract_source_offer(el, category_index=category_index)
        for el in iter_offer_elements(root)
    ]
    return category_index, offers


__all__ = [
    "fetch_xml_text",
    "get_text",
    "parse_xml_root",
    "iter_offer_elements",
    "build_category_index",
    "collect_params",
    "extract_source_offer",
    "load_source_bundle",
]
