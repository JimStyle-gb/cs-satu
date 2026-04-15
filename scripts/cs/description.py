from __future__ import annotations

from html import escape
import re
from typing import Any, Iterable, Mapping

WHATSAPP_URL = (
    "https://api.whatsapp.com/send/?phone=77073270501&amp;text&amp;type=phone_number&amp;app_absent=0"
)

PAYMENT_ITEMS = [
    '<strong>Безналичный</strong> расчёт для <u>юридических лиц</u>',
    '<strong>Удалённая оплата</strong> по <span style="color:#8B0000;"><strong>KASPI</strong></span> счёту для <u>физических лиц</u>',
]

DELIVERY_ITEMS = [
    '<em><strong>ДОСТАВКА</strong> в «квадрате» г. Алматы — БЕСПЛАТНО!</em>',
    '<em><strong>ДОСТАВКА</strong> по Казахстану до 5 кг — 5 000 тг. | 3–7 рабочих дней</em>',
    '<em><strong>ОТПРАВИМ</strong> товар любой курьерской компанией!</em>',
    '<em><strong>ОТПРАВИМ</strong> товар автобусом через автовокзал «САЙРАН»</em>',
]

INTRO_FACT_KEYS = [
    "Тип",
    "Бренд",
    "Линейка",
    "Модель",
    "Процессор",
    "Оперативная память",
    "Накопитель",
    "Экран",
    "Разрешение",
    "Видеокарта",
    "Операционная система",
    "Совместимость",
    "Назначение",
]

FEATURE_RULES = [
    (("Процессор",), lambda v: f"Процессор: {v}"),
    (("Оперативная память",), lambda v: f"Оперативная память: {v}"),
    (("Накопитель",), lambda v: f"Накопитель: {v}"),
    (("Экран",), lambda v: f"Экран: {v}"),
    (("Разрешение",), lambda v: f"Разрешение: {v}"),
    (("Видеокарта",), lambda v: f"Видеокарта: {v}"),
    (("Операционная система",), lambda v: f"Операционная система: {v}"),
    (("Wi-Fi", "WiFi"), lambda v: f"Wi-Fi: {v}"),
    (("Bluetooth",), lambda v: f"Bluetooth: {v}"),
    (("Гарантия",), lambda v: f"Гарантия: {v}"),
    (("Совместимость",), lambda v: f"Совместимость: {v}"),
]

_PRIORITY_KEYS = [
    "Бренд",
    "Линейка",
    "Модель",
    "Тип",
    "Назначение",
    "Совместимость",
    "Процессор",
    "Оперативная память",
    "Накопитель",
    "Экран",
    "Разрешение",
    "Видеокарта",
    "Операционная система",
    "Wi-Fi",
    "Bluetooth",
    "Гарантия",
]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return " ".join(text.split())


def _coalesce(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _escape_text(value: Any) -> str:
    return escape(_clean_text(value), quote=True)


def _escape_attr(value: Any) -> str:
    return escape(_clean_text(value), quote=True)


def _iter_characteristics(raw: Any) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    if raw is None:
        return items

    if isinstance(raw, Mapping):
        for key, value in raw.items():
            k = _clean_text(key)
            v = _clean_text(value)
            if k and v:
                items.append((k, v))
        return items

    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            if item is None:
                continue
            if isinstance(item, Mapping):
                key = _coalesce(item.get("name"), item.get("key"), item.get("title"), item.get("label"))
                value = _coalesce(item.get("value"), item.get("text"), item.get("val"))
                if key and value:
                    items.append((key, value))
                continue
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                key = _clean_text(item[0])
                value = _clean_text(item[1])
                if key and value:
                    items.append((key, value))
                continue
            text = _clean_text(item)
            if text:
                items.append(("Характеристика", text))
        return items

    text = _clean_text(raw)
    if text:
        items.append(("Характеристика", text))
    return items


def _dedupe_characteristics(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for key, value in items:
        pair = (_clean_text(key).casefold(), _clean_text(value).casefold())
        if pair in seen:
            continue
        seen.add(pair)
        result.append((_clean_text(key), _clean_text(value)))
    return result


def _sort_characteristics(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    rank = {key.casefold(): idx for idx, key in enumerate(_PRIORITY_KEYS)}
    return sorted(
        _dedupe_characteristics(items),
        key=lambda kv: (rank.get(kv[0].casefold(), 999), kv[0].casefold(), kv[1].casefold()),
    )


def _render_list(items: Iterable[str], *, style: str = "") -> str:
    body = "".join(f"<li>{item}</li>" for item in items if _clean_text(item))
    if not body:
        return ""
    if style:
        return f'<ul style="{style}">{body}</ul>'
    return f"<ul>{body}</ul>"


def _render_characteristics(items: list[tuple[str, str]]) -> str:
    if not items:
        return ""
    body = "".join(
        f"<li><strong>{_escape_text(key)}:</strong> {_escape_text(value)}</li>"
        for key, value in items
    )
    return f"<h3>Характеристики</h3>\n<ul>{body}</ul>"


def build_chars_block(*args: Any, **kwargs: Any) -> str:
    raw = kwargs.get("characteristics", kwargs.get("params", kwargs.get("specs", kwargs.get("features"))))
    if raw is None and args:
        raw = args[0]
    return _render_characteristics(_sort_characteristics(_iter_characteristics(raw)))


def _chars_map(items: list[tuple[str, str]]) -> dict[str, str]:
    data: dict[str, str] = {}
    for key, value in items:
        k = _clean_text(key)
        v = _clean_text(value)
        if k and v and k not in data:
            data[k] = v
    return data


def _strip_trailing_punct(text: str) -> str:
    return re.sub(r"[\s\.,;:!\-–—]+$", "", text.strip())


def _clip_sentence(text: str, limit: int = 420) -> str:
    text = _clean_text(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    for sep in [". ", "! ", "? ", "; "]:
        idx = cut.rfind(sep)
        if idx >= max(80, limit // 2):
            return cut[: idx + 1].strip()
    return _strip_trailing_punct(cut) + "…"


def _safe_intro_from_chars(name: str, chars: dict[str, str]) -> str:
    facts: list[str] = []
    for key in INTRO_FACT_KEYS:
        value = chars.get(key)
        if value:
            if key in {"Бренд", "Линейка", "Модель"}:
                continue
            facts.append(f"{key.lower()}: {value}")
        if len(facts) >= 2:
            break
    if facts:
        return f"{_strip_trailing_punct(name)} — {'; '.join(facts)}."
    return f"{_strip_trailing_punct(name)}."


def _build_intro(name: str, main_text: str, chars: dict[str, str]) -> str:
    clean_name = _clean_text(name)
    clean_text = _clean_text(main_text)
    if clean_text:
        name_cf = clean_name.casefold()
        text_cf = clean_text.casefold()
        if text_cf != name_cf and text_cf != f"{name_cf}.":
            return _clip_sentence(clean_text)
    return _safe_intro_from_chars(clean_name, chars)


def _build_fact_bullets(chars: dict[str, str]) -> list[str]:
    bullets: list[str] = []
    seen: set[str] = set()
    for keys, renderer in FEATURE_RULES:
        value = ""
        for key in keys:
            value = chars.get(key, "")
            if value:
                break
        if not value:
            continue
        bullet = _clean_text(renderer(value))
        key_norm = bullet.casefold()
        if bullet and key_norm not in seen:
            seen.add(key_norm)
            bullets.append(bullet)
        if len(bullets) >= 4:
            break
    return bullets if len(bullets) >= 2 else []


def _render_whatsapp_block() -> str:
    return "\n".join(
        [
            '<div style="margin:14px 0;padding:14px 16px;border:1px solid #E7D6B7;border-radius:10px;background:#FFF4DD;text-align:center;">',
            '<p style="margin:0 0 8px 0;"><strong>Есть вопросы по товару?</strong></p>',
            '<p style="margin:0 0 10px 0;">Напишите нам в WhatsApp, чтобы уточнить наличие, сроки поставки и комплектацию.</p>',
            '<p style="margin:0;">',
            (
                f'<a href="{_escape_attr(WHATSAPP_URL)}" '
                'style="display:inline-block;padding:10px 18px;border-radius:8px;text-decoration:none;'
                'background:#187A43;border:1px solid #146737;color:#FFFFFF;">'
                '<strong>💬 Написать в WhatsApp</strong></a>'
            ),
            '</p>',
            '</div>',
        ]
    )


def _render_payment_delivery_block() -> str:
    return "\n".join(
        [
            '<div style="margin:12px 0;padding:10px 12px;border:1px solid #E7D6B7;border-radius:10px;background:#FCF8F1;color:#3F3527;font-family:Verdana, Tahoma, Arial, sans-serif;line-height:1.4;font-size:12px;">',
            '<h3 style="margin:0 0 6px 0;color:#3F3527;font-family:inherit;font-size:14px;line-height:1.25;">Оплата</h3>',
            '<ul style="margin:0;padding-left:16px;">' + ''.join(f'<li>{item}</li>' for item in PAYMENT_ITEMS) + '</ul>',
            '<hr style="border:none;height:1px;background:#E7D6B7;margin:10px 0;" />',
            '<h3 style="margin:0 0 6px 0;color:#3F3527;font-family:inherit;font-size:14px;line-height:1.25;">Доставка по Алматы и Казахстану</h3>',
            '<ul style="margin:0;padding-left:16px;">' + ''.join(f'<li>{item}</li>' for item in DELIVERY_ITEMS) + '</ul>',
            '</div>',
        ]
    )


def _render_description(name: str, main_text: str, characteristics: Any = None) -> str:
    chars_items = _sort_characteristics(_iter_characteristics(characteristics))
    chars = _chars_map(chars_items)
    safe_name = _clean_text(name)
    intro = _build_intro(safe_name, main_text, chars)
    bullets = _build_fact_bullets(chars)
    chars_html = _render_characteristics(chars_items)

    parts: list[str] = []
    if safe_name:
        parts.append(f"<h3>{_escape_text(safe_name)}</h3>")
    if intro:
        parts.append(f"<p>{_escape_text(intro)}</p>")
    if bullets:
        parts.append('<p><strong>Преимущества модели:</strong></p>')
        parts.append(_render_list((_escape_text(item) for item in bullets)))
    if chars_html:
        parts.append(chars_html)
    parts.append(_render_whatsapp_block())
    parts.append(_render_payment_delivery_block())
    return "\n\n".join(part for part in parts if part)


def build_description(*args: Any, **kwargs: Any) -> str:
    name = _coalesce(
        args[0] if len(args) > 0 else None,
        kwargs.get("name"),
        kwargs.get("title"),
        kwargs.get("product_name"),
    )
    main_text = _coalesce(
        args[1] if len(args) > 1 else None,
        kwargs.get("main_text"),
        kwargs.get("description"),
        kwargs.get("text"),
        kwargs.get("body"),
        kwargs.get("desc"),
    )
    characteristics = (
        args[2]
        if len(args) > 2
        else kwargs.get("characteristics", kwargs.get("params", kwargs.get("specs", kwargs.get("features"))))
    )
    return _render_description(name=name, main_text=main_text, characteristics=characteristics)
