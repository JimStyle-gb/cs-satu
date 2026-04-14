from __future__ import annotations

from html import escape
from typing import Any, Iterable, Mapping

WHATSAPP_URL = (
    "https://api.whatsapp.com/send/?phone=77073270501&amp;text&amp;type=phone_number&amp;app_absent=0"
)

PAYMENT_ITEMS = [
    '<strong>Безналичный</strong> расчёт для <u>юридических лиц</u>',
    '<strong>Удалённая оплата</strong> по <span style="color:#8b0000"><strong>KASPI</strong></span> счёту для <u>физических лиц</u>',
]

DELIVERY_ITEMS = [
    '<em><strong>ДОСТАВКА</strong> в «квадрате» г. Алматы — БЕСПЛАТНО!</em>',
    '<em><strong>ДОСТАВКА</strong> по Казахстану до 5 кг — 5 000 тг. | 3–7 рабочих дней</em>',
    '<em><strong>ОТПРАВИМ</strong> товар любой курьерской компанией!</em>',
    '<em><strong>ОТПРАВИМ</strong> товар автобусом через автовокзал «САЙРАН»</em>',
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

def _render_list(items: Iterable[str]) -> str:
    body = "".join(f"<li>{item}</li>" for item in items if _clean_text(item))
    return f"<ul>{body}</ul>" if body else ""

def _render_characteristics(items: list[tuple[str, str]]) -> str:
    if not items:
        return ""
    body = "".join(
        f"<li><strong>{_escape_text(key)}:</strong> {_escape_text(value)}</li>"
        for key, value in items
    )
    return f"<h3>Характеристики</h3>\n<ul>{body}</ul>"

# Совместимость с shared core.
def build_chars_block(*args: Any, **kwargs: Any) -> str:
    raw = kwargs.get("characteristics", kwargs.get("params", kwargs.get("specs", kwargs.get("features"))))
    if raw is None and args:
        raw = args[0]
    return _render_characteristics(_iter_characteristics(raw))

def build_characteristics_block(*args: Any, **kwargs: Any) -> str:
    return build_chars_block(*args, **kwargs)

def render_chars_block(*args: Any, **kwargs: Any) -> str:
    return build_chars_block(*args, **kwargs)

def _render_description(name: str, main_text: str, characteristics: Any = None) -> str:
    safe_name = _escape_text(name)
    safe_text = _escape_text(main_text)
    chars_html = build_chars_block(characteristics)

    parts = [f"<h3>{safe_name}</h3>"]
    if safe_text:
        parts.append(f"<p>{safe_text}</p>")

    parts.extend(
        [
            "<hr />",
            (
                f'<p style="text-align:center">'
                f'<a href="{_escape_attr(WHATSAPP_URL)}">💬 Написать в WhatsApp</a>'
                f"</p>"
            ),
        ]
    )

    if chars_html:
        parts.extend(["<hr />", chars_html])

    parts.extend(
        [
            "<hr />",
            "<h3>Оплата</h3>",
            _render_list(PAYMENT_ITEMS),
            "<hr />",
            "<h3>Доставка по Алматы и Казахстану</h3>",
            _render_list(DELIVERY_ITEMS),
        ]
    )

    return "\n".join(part for part in parts if part)

# Совместимость с разными вызовами из shared core.
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

