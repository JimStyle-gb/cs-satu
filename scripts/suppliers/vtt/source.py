# -*- coding: utf-8 -*-
"""
Path: scripts/suppliers/vtt/source.py

VTT Source — source-слой supplier-layer.

Что делает:
- держит login/session/crawl/product-page parsing;
- собирает canonical raw source-данные для builder.py;
- использует filtering.py и params.py как source of truth для своих подпроцессов.

Что не делает:
- не принимает business-решения по final витрине;
- не строит final offers;
- не заменяет builder и quality gate слой.
"""
from __future__ import annotations

import os
import random
import re
import threading
import time
from collections import deque
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from requests import exceptions as req_exc
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import VTTConfig
from .normalize import canon_vendor, norm_ws
from .params import (
    extract_images_from_html,
    extract_meta_desc,
    extract_params_and_desc,
    extract_price_rub,
    extract_sku,
    extract_title,
    extract_title_codes,
)
from .filtering import (
    DEFAULT_ALLOWED_TITLE_PREFIXES,
    DEFAULT_CATEGORY_CODES,
    mk_category_url,
    normalize_listing_title,
    normalize_listing_url,
    product_path_re,
)

try:  # pragma: no cover - новый filtering.py
    from .filtering import resolve_filter_inputs, title_matches_allowed
except Exception:  # pragma: no cover - совместимость со старым filtering.py
    def resolve_filter_inputs(
        *,
        cfg_path: str | Path | None = None,
        env_category_codes: str | None = None,
        env_allowed_prefixes: str | None = None,
    ) -> tuple[list[str], list[str]]:
        cats = [x.strip() for x in (env_category_codes or "").split(",") if x.strip()]
        if not cats:
            cats = list(DEFAULT_CATEGORY_CODES)
        prefixes = [x.strip() for x in (env_allowed_prefixes or "").split(",") if x.strip()]
        if not prefixes:
            prefixes = list(DEFAULT_ALLOWED_TITLE_PREFIXES)
        return cats, prefixes

    def title_matches_allowed(title: str, allowed_prefixes: list[str]) -> bool:
        title_n = normalize_listing_title(title)
        if not title_n:
            return False
        for prefix in allowed_prefixes:
            p = norm_ws(prefix)
            if p and title_n.casefold().startswith(p.casefold()):
                return True
        return False

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_HREF_RE = re.compile(r'''href=["\']([^"\']+)["\']''', re.I)
_ANCHOR_RE = re.compile(r'''<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>''', re.I | re.S)
_META_CSRF_RE = re.compile(r'''<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']''', re.I)
_VENDOR_TOKEN_RE = re.compile(r"\b(?:HP|CANON|XEROX|BROTHER|KYOCERA|SAMSUNG|EPSON|RICOH|KONICA\s+MINOLTA|PANTUM|LEXMARK|OKI|SHARP|PANASONIC|TOSHIBA|DEVELOP|GESTETNER|RISO)\b", re.I)

_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_UNTIL_MONOTONIC = 0.0

def log(msg: str) -> None:
    print(msg, flush=True)

def _sleep_ms(ms: int) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)

def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default

def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default

def _request_jitter_ms() -> int:
    return max(0, _safe_int(os.getenv("VTT_REQUEST_JITTER_MS") or "180", 180))

def _delay_with_jitter_ms(delay_ms: int) -> int:
    base = max(0, int(delay_ms))
    jitter = _request_jitter_ms()
    if jitter <= 0:
        return base
    return base + random.randint(0, jitter)

def _rate_limit_retries() -> int:
    return max(1, _safe_int(os.getenv("VTT_429_RETRIES") or os.getenv("VTT_NETWORK_OUTER_RETRIES") or "5", 5))

def _rate_limit_min_cooldown_s() -> float:
    return max(1.0, _safe_float(os.getenv("VTT_429_MIN_COOLDOWN_S") or "20", 20.0))

def _rate_limit_base_cooldown_s() -> float:
    return max(_rate_limit_min_cooldown_s(), _safe_float(os.getenv("VTT_429_COOLDOWN_S") or "45", 45.0))

def _rate_limit_max_cooldown_s() -> float:
    return max(_rate_limit_base_cooldown_s(), _safe_float(os.getenv("VTT_429_MAX_COOLDOWN_S") or "180", 180.0))

def _parse_retry_after_seconds(resp: requests.Response) -> float | None:
    raw = (resp.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except Exception:
        pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            return None
        return max(0.0, (dt.timestamp() - time.time()))
    except Exception:
        return None

def _set_global_rate_limit_cooldown(seconds: float) -> None:
    if seconds <= 0:
        return
    global _RATE_LIMIT_UNTIL_MONOTONIC
    until = time.monotonic() + seconds
    with _RATE_LIMIT_LOCK:
        if until > _RATE_LIMIT_UNTIL_MONOTONIC:
            _RATE_LIMIT_UNTIL_MONOTONIC = until

def _wait_for_global_rate_limit_window() -> None:
    while True:
        with _RATE_LIMIT_LOCK:
            wait_s = _RATE_LIMIT_UNTIL_MONOTONIC - time.monotonic()
        if wait_s <= 0:
            return
        time.sleep(min(wait_s, 5.0))

def _rate_limit_backoff_s(resp: requests.Response, attempt_no: int) -> float:
    retry_after_s = _parse_retry_after_seconds(resp)
    if retry_after_s is not None:
        return min(_rate_limit_max_cooldown_s(), max(_rate_limit_min_cooldown_s(), retry_after_s))

    step = max(5.0, _safe_float(os.getenv("VTT_429_BACKOFF_STEP_S") or "15", 15.0))
    fallback = _rate_limit_base_cooldown_s() + max(0, attempt_no - 1) * step
    return min(_rate_limit_max_cooldown_s(), max(_rate_limit_min_cooldown_s(), fallback))

def _configure_session(sess: requests.Session, cfg: VTTConfig) -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    sess.headers.update(
        {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.7,en;q=0.5",
            "Connection": "keep-alive",
        }
    )
    return sess

def make_session(cfg: VTTConfig) -> requests.Session:
    return _configure_session(requests.Session(), cfg)

def clone_session_with_cookies(master: requests.Session, cfg: VTTConfig) -> requests.Session:
    sess = make_session(cfg)
    sess.cookies.update(master.cookies)
    return sess

def cfg_from_env() -> VTTConfig:
    base_url = (os.getenv("VTT_BASE_URL") or "https://b2b.vtt.ru/").strip()
    base_url = base_url.rstrip("/") + "/"

    cfg_path = os.getenv("VTT_FILTER_CFG") or "scripts/suppliers/vtt/config/filter.yml"
    categories, allowed_prefixes = resolve_filter_inputs(
        cfg_path=cfg_path,
        env_category_codes=os.getenv("VTT_CATEGORY_CODES"),
        env_allowed_prefixes=os.getenv("VTT_ALLOWED_TITLE_PREFIXES"),
    )

    return VTTConfig(
        base_url=base_url,
        start_url=urljoin(base_url, "/catalog/"),
        login_url=urljoin(base_url, "/validateLogin"),
        login=(os.getenv("VTT_LOGIN") or "").strip(),
        password=(os.getenv("VTT_PASSWORD") or "").strip(),
        timeout_s=_safe_int(os.getenv("VTT_TIMEOUT_S") or "45", 45),
        listing_request_delay_ms=_safe_int(
            os.getenv("VTT_LISTING_REQUEST_DELAY_MS") or os.getenv("VTT_REQUEST_DELAY_MS") or "150",
            150,
        ),
        product_request_delay_ms=_safe_int(os.getenv("VTT_PRODUCT_REQUEST_DELAY_MS") or "700", 700),
        max_listing_pages=_safe_int(os.getenv("VTT_MAX_LISTING_PAGES") or "5000", 5000),
        max_workers=_safe_int(os.getenv("VTT_MAX_WORKERS") or "2", 2),
        max_crawl_minutes=_safe_float(os.getenv("VTT_MAX_CRAWL_MINUTES") or "90", 90.0),
        softfail=(os.getenv("VTT_SOFTFAIL") or "false").strip().lower() == "true",
        categories=list(categories),
        allowed_title_prefixes=list(allowed_prefixes),
    )

def _network_outer_retries() -> int:
    return max(1, _safe_int(os.getenv("VTT_NETWORK_OUTER_RETRIES") or "5", 5))

def _network_retry_sleep_s(attempt_no: int) -> float:
    return min(20.0, 2.5 * max(1, attempt_no))

def _request_with_outer_retry(
    sess: requests.Session,
    method: str,
    cfg: VTTConfig,
    url: str,
    *,
    delay_ms: int,
    **kwargs,
) -> requests.Response:
    attempts = max(_network_outer_retries(), _rate_limit_retries())
    last_exc: Exception | None = None
    last_resp: requests.Response | None = None

    for attempt_no in range(1, attempts + 1):
        if attempt_no == 1:
            _wait_for_global_rate_limit_window()
            _sleep_ms(_delay_with_jitter_ms(delay_ms))
        else:
            time.sleep(_network_retry_sleep_s(attempt_no - 1))
            _wait_for_global_rate_limit_window()

        try:
            resp = sess.request(
                method,
                url,
                timeout=cfg.timeout_s,
                allow_redirects=True,
                **kwargs,
            )
            last_resp = resp

            if resp.status_code == 429:
                cooldown_s = _rate_limit_backoff_s(resp, attempt_no)
                _set_global_rate_limit_cooldown(cooldown_s)
                if attempt_no >= attempts:
                    resp.raise_for_status()
                log(
                    f"[VTT] rate limit retry {attempt_no}/{attempts - 1}: "
                    f"{method} {url} :: 429 cooldown={cooldown_s:.1f}s"
                )
                continue

            resp.raise_for_status()
            return resp
        except req_exc.HTTPError as exc:
            last_exc = exc
            status = getattr(exc.response, "status_code", None)
            if status == 429 and exc.response is not None and attempt_no < attempts:
                cooldown_s = _rate_limit_backoff_s(exc.response, attempt_no)
                _set_global_rate_limit_cooldown(cooldown_s)
                log(
                    f"[VTT] rate limit retry {attempt_no}/{attempts - 1}: "
                    f"{method} {url} :: 429 cooldown={cooldown_s:.1f}s"
                )
                continue
            raise
        except (
            req_exc.ConnectTimeout,
            req_exc.ReadTimeout,
            req_exc.ConnectionError,
            req_exc.Timeout,
        ) as exc:
            last_exc = exc
            if attempt_no >= attempts:
                raise
            log(f"[VTT] network retry {attempt_no}/{attempts - 1}: {method} {url} :: {exc}")

    if last_resp is not None:
        last_resp.raise_for_status()
    assert last_exc is not None
    raise last_exc

def _get(sess: requests.Session, cfg: VTTConfig, url: str, *, delay_ms: int) -> requests.Response:
    return _request_with_outer_retry(sess, "GET", cfg, url, delay_ms=delay_ms)

def _post(sess: requests.Session, cfg: VTTConfig, url: str, *, delay_ms: int, **kwargs) -> requests.Response:
    return _request_with_outer_retry(sess, "POST", cfg, url, delay_ms=delay_ms, **kwargs)

def login(sess: requests.Session, cfg: VTTConfig) -> bool:
    if not cfg.login or not cfg.password:
        return False

    home = _get(sess, cfg, urljoin(cfg.base_url, "/"), delay_ms=cfg.listing_request_delay_ms)
    html = home.text or ""
    m = _META_CSRF_RE.search(html)
    token = m.group(1).strip() if m else ""

    headers = {"Referer": urljoin(cfg.base_url, "/")}
    if token:
        headers["X-CSRF-TOKEN"] = token

    _post(
        sess,
        cfg,
        cfg.login_url,
        delay_ms=cfg.listing_request_delay_ms,
        headers=headers,
        data={"login": cfg.login, "password": cfg.password},
    )

    probe = _get(sess, cfg, cfg.start_url, delay_ms=cfg.listing_request_delay_ms)
    return "/catalog" in (probe.url or "")

def _extract_vendor_from_title(title: str) -> str:
    m = _VENDOR_TOKEN_RE.search(title or "")
    if not m:
        return ""
    return canon_vendor(norm_ws(m.group(0)))

def collect_product_index(
    sess: requests.Session,
    cfg: VTTConfig,
    categories: list[str],
    deadline: datetime,
) -> list[dict[str, Any]]:
    allowed_categories = {x.strip() for x in categories if x and x.strip()}
    allowed_prefixes = list(cfg.allowed_title_prefixes or [])
    base_netloc = urlparse(cfg.base_url).netloc
    queue = deque(normalize_listing_url(mk_category_url(cfg.base_url, code)) for code in categories if code)
    seen_listings: set[str] = set()
    product_candidates: dict[str, dict[str, Any]] = {}

    while queue and len(seen_listings) < int(cfg.max_listing_pages) and datetime.utcnow() < deadline:
        url = queue.popleft()
        if not url or url in seen_listings:
            continue
        seen_listings.add(url)

        try:
            resp = _get(sess, cfg, url, delay_ms=cfg.listing_request_delay_ms)
            html = resp.text or ""
        except Exception as exc:
            log(f"[VTT] listing error: {url} :: {exc}")
            continue

        current_cats = {x.strip() for x in parse_qs(urlparse(resp.url).query).get("category", []) if x.strip()}

        for href, inner in _ANCHOR_RE.findall(html):
            href = unescape((href or "").strip())
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            abs_url = urljoin(resp.url, href)
            parsed = urlparse(abs_url)
            if parsed.netloc != base_netloc:
                continue

            if parsed.path.lower().startswith("/catalog/") and not parsed.query and product_path_re(parsed.path):
                rec = product_candidates.setdefault(abs_url, {"source_categories": set(), "listing_titles": set()})
                rec["source_categories"].update(current_cats)
                title_text = normalize_listing_title(inner)
                if title_text:
                    rec["listing_titles"].add(title_text)
                continue

            if parsed.path.lower().startswith("/catalog"):
                qs = parse_qs(parsed.query)
                cat_values = {x.strip() for x in qs.get("category", []) if x.strip()}
                if cat_values and cat_values.issubset(allowed_categories):
                    norm = normalize_listing_url(abs_url)
                    if norm not in seen_listings:
                        queue.append(norm)

        # fallback: ссылки без anchor-text
        for href in _HREF_RE.findall(html):
            href = unescape((href or "").strip())
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            abs_url = urljoin(resp.url, href)
            parsed = urlparse(abs_url)
            if parsed.netloc != base_netloc:
                continue
            if parsed.path.lower().startswith("/catalog/") and not parsed.query and product_path_re(parsed.path):
                rec = product_candidates.setdefault(abs_url, {"source_categories": set(), "listing_titles": set()})
                rec["source_categories"].update(current_cats)

    out: list[dict[str, Any]] = []
    soft_prefix_mismatch = 0

    for url, meta in sorted(product_candidates.items(), key=lambda kv: kv[0]):
        titles = sorted(meta.get("listing_titles") or [])
        if titles and not any(title_matches_allowed(title, allowed_prefixes) for title in titles):
            soft_prefix_mismatch += 1
        out.append(
            {
                "url": url,
                "source_categories": sorted(list(meta.get("source_categories") or [])),
                "listing_titles": titles,
            }
        )

    log(
        f"[VTT] index: listings={len(seen_listings)} products={len(out)} "
        f"soft_prefix_mismatch={soft_prefix_mismatch}"
    )
    return out

def parse_product_page_from_index(
    sess: requests.Session,
    cfg: VTTConfig,
    item: dict[str, Any],
) -> dict[str, Any] | None:
    url = norm_ws(item.get("url"))
    if not url:
        return None

    resp = _get(sess, cfg, url, delay_ms=cfg.product_request_delay_ms)
    html = resp.text or ""

    title = extract_title(html)
    if not title:
        return None

    params, desc_body = extract_params_and_desc(html)
    source_categories = [norm_ws(x) for x in (item.get("source_categories") or []) if norm_ws(x)]
    listing_titles = [norm_ws(x) for x in (item.get("listing_titles") or []) if norm_ws(x)]

    return {
        "url": resp.url,
        "name": title,
        "vendor": _extract_vendor_from_title(title),
        "sku": extract_sku(html),
        "price_rub_raw": extract_price_rub(html),
        "pictures": extract_images_from_html(resp.url, html),
        "params": params,
        "description_meta": extract_meta_desc(html),
        "description_body": desc_body,
        "title_codes": extract_title_codes(title),
        "source_categories": source_categories,
        "category_code": ",".join(source_categories),
        "listing_titles": listing_titles,
    }

__all__ = [
    "VTTConfig",
    "cfg_from_env",
    "make_session",
    "clone_session_with_cookies",
    "login",
    "collect_product_index",
    "parse_product_page_from_index",
    "log",
]
