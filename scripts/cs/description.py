from __future__ import annotations

import re
from html import escape, unescape
from typing import Any, Iterable, Mapping

WHATSAPP_URL = (
    "https://api.whatsapp.com/send/?phone=77073270501&text&type=phone_number&app_absent=0"
)

PAYMENT_ITEMS = [
    '<strong>Безналичный</strong> расчёт для <u>юридических лиц</u>',
    '<strong>Удалённая оплата</strong> по <span style="color:#8b0000"><strong>KASPI</strong></span> счёту для <u>физических лиц</u>',
]

DELIVERY_ITEMS = [
    '<strong>ДОСТАВКА</strong> в «квадрате» г. Алматы — БЕСПЛАТНО!',
    '<strong>ДОСТАВКА</strong> по Казахстану до 5 кг — 5 000 тг. | 3–7 рабочих дней',
    '<strong>ОТПРАВИМ</strong> товар любой курьерской компанией!',
    '<strong>ОТПРАВИМ</strong> товар автобусом через автовокзал «САЙРАН»',
]

DISPLAY_KEY_MAP = {
    "для бренда": "Бренд",
    "бренд": "Бренд",
    "производитель": "Производитель",
    "модельная серия": "Серия",
    "серия": "Серия",
    "линейка": "Линейка",
    "модель": "Модель",
    "тип": "Тип",
    "тип устройства": "Тип устройства",
    "категория": "Категория",
    "назначение": "Назначение",
    "совместимость": "Совместимость",
    "процессор": "Процессор",
    "оперативная память": "Оперативная память",
    "тип памяти": "Тип памяти",
    "накопитель": "Накопитель",
    "диагональ": "Диагональ",
    "разрешение": "Разрешение",
    "экран": "Экран",
    "видеокарта": "Видеокарта",
    "операционная система": "Операционная система",
    "ос": "Операционная система",
    "wi-fi": "Wi-Fi",
    "bluetooth": "Bluetooth",
    "гарантия": "Гарантия",
    "камера": "Камера",
    "адаптер": "Адаптер",
    "аккумулятор": "Аккумулятор",
}

SAFE_CATEGORY_PHRASES = {
    "ноутбук": "для работы, офиса и учебы",
    "монитор": "для работы и повседневного использования",
    "мфу": "для печати, копирования и сканирования",
    "принтер": "для печати документов и изображений",
    "сканер": "для сканирования документов и изображений",
    "картридж": "расходный материал для печати",
    "тонер": "расходный материал для печати",
    "термоблок": "узел фиксации для печатающей техники",
    "фьюзер": "узел фиксации для печатающей техники",
    "проектор": "для вывода изображения",
    "экран": "для вывода изображения",
}

_TITLE_TYPE_PREFIXES = (
    "ноутбук", "мфу", "принтер", "монитор", "сканер", "картридж", "тонер", "термоблок",
    "фьюзер", "проектор", "экран", "драм", "драм-картридж", "драм-юнит", "драм юнит",
    "печатающая головка", "контейнер", "ролик", "ремень", "блок проявки", "девелопер",
)

_DROP_INTRO_PATTERNS = (
    "оплата и доставка",
    "доставка по алматы и казахстану",
    "есть вопросы по товару",
    "написать в whatsapp",
    "доставка в «квадрате»",
)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = unescape(str(value))
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


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


def _strip_tags(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.S)
    text = re.sub(r"<br\s*/?>", ". ", text, flags=re.I)
    text = re.sub(r"</p>|</li>|</h3>|</h2>|</div>", ". ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text


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


def _normalize_key(key: str) -> str:
    norm = _clean_text(key).casefold()
    return DISPLAY_KEY_MAP.get(norm, _clean_text(key))


def _params_map(items: list[tuple[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in items:
        k = _clean_text(key).casefold()
        v = _clean_text(value)
        if k and v and k not in out:
            out[k] = v
    return out


def _title_type_phrase(name: str, params_map: Mapping[str, str]) -> str:
    type_value = _clean_text(params_map.get("тип")) or _clean_text(params_map.get("тип устройства"))
    if type_value:
        return type_value
    lower = _clean_text(name).casefold()
    for prefix in _TITLE_TYPE_PREFIXES:
        if lower.startswith(prefix):
            return prefix.capitalize()
    return ""


def _safe_category_phrase(type_phrase: str) -> str:
    key = _clean_text(type_phrase).casefold()
    return SAFE_CATEGORY_PHRASES.get(key, "")


def _cleanup_intro_source(raw: Any, title: str) -> str:
    text = _strip_tags(raw)
    if not text:
        return ""
    lowered = text.casefold()
    for token in _DROP_INTRO_PATTERNS:
        idx = lowered.find(token)
        if idx > 0:
            text = text[:idx].strip(" .")
            lowered = text.casefold()
    if not text:
        return ""
    if _clean_text(text).casefold() == _clean_text(title).casefold():
        return ""
    if len(text) > 420:
        cut = max(text.rfind(".", 0, 420), text.rfind(";", 0, 420))
        if cut >= 120:
            text = text[: cut + 1].strip()
        else:
            text = text[:420].rstrip(" ,;:") + "…"
    elif not re.search(r"[.!?…]$", text):
        text += "."
    return text


def _fact_signal_bullets(params_map: Mapping[str, str]) -> list[str]:
    bullets: list[str] = []

    def add(text: str) -> None:
        if text and text not in bullets:
            bullets.append(text)

    ram = _clean_text(params_map.get("оперативная память"))
    mem_type = _clean_text(params_map.get("тип памяти"))
    storage = _clean_text(params_map.get("накопитель"))
    resolution = _clean_text(params_map.get("разрешение"))
    diag = _clean_text(params_map.get("диагональ"))
    screen = _clean_text(params_map.get("экран"))
    os_name = _clean_text(params_map.get("операционная система"))
    wifi = _clean_text(params_map.get("wi-fi"))
    bt = _clean_text(params_map.get("bluetooth"))
    video = _clean_text(params_map.get("видеокарта"))
    warranty = _clean_text(params_map.get("гарантия"))
    cpu = _clean_text(params_map.get("процессор"))

    if cpu:
        add(f"Процессор {cpu}")
    if ram and mem_type:
        add(f"Оперативная память {ram} {mem_type}")
    elif ram:
        add(f"Оперативная память {ram}")
    if storage:
        add(f"Накопитель {storage}")
    if resolution:
        if resolution == "1920x1080":
            add("Разрешение Full HD")
        else:
            add(f"Разрешение {resolution}")
    if diag and not screen:
        add(f"Диагональ экрана {diag}")
    elif screen:
        add(f"Экран {screen}")
    if video:
        add(f"Видеокарта {video}")
    if os_name:
        add(f"Операционная система {os_name}")
    if wifi:
        add(f"Поддержка Wi-Fi {wifi}")
    if bt:
        add(f"Поддержка {bt}")
    if warranty:
        add(f"Гарантия {warranty}")

    return bullets[:4]


def _build_intro(title: str, main_text: str, characteristics: list[tuple[str, str]]) -> str:
    intro = _cleanup_intro_source(main_text, title)
    if intro:
        return intro

    params_map = _params_map(characteristics)
    type_phrase = _title_type_phrase(title, params_map)
    if not type_phrase:
        return f"{_clean_text(title)}."

    usage = _safe_category_phrase(type_phrase)
    if usage:
        return f"{_clean_text(title)} — {type_phrase.lower()} {usage}."
    return f"{_clean_text(title)} — {type_phrase.lower()}."


def _build_fact_bullets(characteristics: list[tuple[str, str]]) -> list[str]:
    items = _fact_signal_bullets(_params_map(characteristics))
    return items if len(items) >= 2 else []


def _build_characteristics_items(characteristics: list[tuple[str, str]], limit: int = 12) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen_labels: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()

    for key, value in characteristics:
        label = _normalize_key(key)
        val = _clean_text(value)
        if not label or not val:
            continue
        pair = (label.casefold(), val.casefold())
        if pair in seen_pairs:
            continue
        if label.casefold() in seen_labels and label not in {"Совместимость", "Дополнительные характеристики"}:
            continue
        seen_pairs.add(pair)
        seen_labels.add(label.casefold())
        out.append((label, val))
        if len(out) >= limit:
            break
    return out


def _render_text_list(items: Iterable[str]) -> str:
    body = "".join(f"<li>{item}</li>" for item in items if _clean_text(item))
    return f'<ul style="margin:0 0 12px 0;padding-left:18px;font-size:14px;line-height:1.5;font-family:inherit;">{body}</ul>' if body else ""


def _render_characteristics(items: list[tuple[str, str]]) -> str:
    if not items:
        return ""
    body = "".join(
        f"<li><strong>{_escape_text(key)}:</strong> {_escape_text(value)}</li>"
        for key, value in items
    )
    return f'<h3 style="margin:14px 0 8px 0;font-size:20px;line-height:1.3;font-family:inherit;color:#2F2F2F;">Характеристики</h3>\n<ul style="margin:0 0 12px 0;padding-left:18px;font-size:14px;line-height:1.5;font-family:inherit;">{body}</ul>'


def build_chars_block(*args: Any, **kwargs: Any) -> str:
    raw = kwargs.get("characteristics", kwargs.get("params", kwargs.get("specs", kwargs.get("features"))))
    if raw is None and args:
        raw = args[0]
    items = _build_characteristics_items(_iter_characteristics(raw))
    return _render_characteristics(items)


def _render_whatsapp_block() -> str:
    return (
        '<div style="text-align:center;margin:10px 0 8px 0;">\n'
        '  <div class="ck-alert ck-alert_theme_green">\n'
        '    <div style="padding:4px 10px;font-family:inherit;">\n'
        '      <span class="ck-alert__title"></span>\n'
        '      <p style="margin:0 0 6px 0;font-size:14px;line-height:1.5;font-family:inherit;"><strong>Просьба отправлять запросы на WhatsApp!</strong></p>\n'
        f'      <p style="margin:0;font-size:14px;line-height:1.5;font-family:inherit;"><a href="{_escape_attr(WHATSAPP_URL)}" style="display:inline-block;padding:6px 12px;border:1px solid #146737;border-radius:6px;color:#146737;text-decoration:none;background:#FFFFFF;font-family:inherit;"><strong>💬 НАПИСАТЬ В WHATSAPP</strong></a></p>\n'
        '    </div>\n'
        '  </div>\n'
        '</div>'
    )


def _render_payment_delivery_block() -> str:
    payment_html = _render_text_list(PAYMENT_ITEMS)
    delivery_html = _render_text_list(DELIVERY_ITEMS)
    return (
        '<div class="ck-alert ck-alert_theme_orange" style="margin:8px 0 0 0;">\n'
        '  <div style="padding:4px 10px;font-family:inherit;">\n'
        '    <span class="ck-alert__title"></span>\n'
        '    <h3 style="text-align:left;margin:0 0 6px 0;font-size:20px;line-height:1.3;font-family:inherit;color:#2F2F2F;">Оплата</h3>\n'
        f'    {payment_html}\n'
        '    <hr style="border:none;height:1px;background:#E7D6B7;margin:8px 0;" />\n'
        '    <h3 style="text-align:left;margin:0 0 6px 0;font-size:20px;line-height:1.3;font-family:inherit;color:#2F2F2F;">Доставка по Алматы и Казахстану</h3>\n'
        f'    {delivery_html}\n'
        '  </div>\n'
        '</div>'
    )

def _render_description(name: str, main_text: str, characteristics: Any = None) -> str:
    title = _clean_text(name)
    chars_raw = _iter_characteristics(characteristics)
    chars_items = _build_characteristics_items(chars_raw)
    intro = _build_intro(title, main_text, chars_raw)
    bullets = _build_fact_bullets(chars_raw)

    parts: list[str] = [f'<div style="font-family:Verdana, Tahoma, Arial, sans-serif;color:#2F2F2F;">\n<h3 style="margin:0 0 10px 0;font-size:20px;line-height:1.3;font-family:inherit;color:#2F2F2F;">{_escape_text(title)}</h3>']
    if intro:
        parts.append(f'<p style="margin:0 0 10px 0;font-size:14px;line-height:1.5;font-family:inherit;">{_escape_text(intro)}</p>')

    if bullets:
        parts.append('<h3 style="margin:14px 0 8px 0;font-size:20px;line-height:1.3;font-family:inherit;color:#2F2F2F;">Преимущества модели</h3>')
        bullet_html = "".join(f"<li>{_escape_text(item)}</li>" for item in bullets)
        parts.append(f'<ul style="margin:0 0 12px 0;padding-left:18px;font-size:14px;line-height:1.5;font-family:inherit;">{bullet_html}</ul>')

    chars_html = _render_characteristics(chars_items)
    if chars_html:
        parts.append(chars_html)

    parts.append(_render_whatsapp_block())
    parts.append(_render_payment_delivery_block())
    parts.append("</div>")
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
