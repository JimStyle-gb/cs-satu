# -*- coding: utf-8 -*-
"""
Path: scripts/build_price.py

Корневой источник дерева категорий: scripts/cs/config/price_categories.yml
Никаких legacy/remap-костылей.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import yaml
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"Не установлен PyYAML: {exc}")

BASE_DIR = Path('.')
DOCS_DIR = BASE_DIR / 'docs'
RAW_DOCS_DIR = DOCS_DIR / 'raw'
CONFIG_DIR = BASE_DIR / 'scripts' / 'cs' / 'config'

OUTPUT_FILE = DOCS_DIR / 'Price.yml'
UNMAPPED_FILE = RAW_DOCS_DIR / 'price_satu_unmapped_offers.txt'
AUDIT_FILE = RAW_DOCS_DIR / 'price_satu_portal_audit.txt'

PRICE_CATEGORIES_FILE = CONFIG_DIR / 'price_categories.yml'
SATU_PORTAL_CATEGORIES_FILE = CONFIG_DIR / 'satu_portal_categories.yml'
PRICE_PORTAL_MAP_FILE = CONFIG_DIR / 'price_portal_map.yml'
PRICE_PORTAL_OVERRIDES_FILE = CONFIG_DIR / 'price_portal_overrides.yml'

PLACEHOLDER_PICTURE = 'https://placehold.co/800x800/png?text=No+Photo'
TZ = ZoneInfo('Asia/Almaty')

FINAL_SOURCES = [
    ('AkCent', DOCS_DIR / 'akcent.yml'),
    ('AlStyle', DOCS_DIR / 'alstyle.yml'),
    ('ComPortal', DOCS_DIR / 'comportal.yml'),
    ('CopyLine', DOCS_DIR / 'copyline.yml'),
    ('VTT', DOCS_DIR / 'vtt.yml'),
]
EXPECTED_SUPPLIERS = [name for name, _ in FINAL_SOURCES]


@dataclass
class OfferInfo:
    supplier: str
    offer_id: str
    vendor_code: str
    category_id: str
    name: str
    available: bool
    price: str
    pictures: list[str]
    block: str
    portal_category_id: str | None


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f'Не найден файл: {path.as_posix()}')
    with path.open('r', encoding='utf-8') as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f'Ожидался YAML-словарь: {path.as_posix()}')
    return data


def _load_categories() -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    data = _read_yaml(PRICE_CATEGORIES_FILE)
    top_groups: list[tuple[str, str]] = []
    leaf_groups: list[tuple[str, str, str]] = []
    for item in data.get('top_groups', []) or []:
        top_groups.append((str(item['id']).strip(), str(item['name']).strip()))
    for item in data.get('leaf_groups', []) or []:
        leaf_groups.append((str(item['id']).strip(), str(item['parent_id']).strip(), str(item['name']).strip()))
    return top_groups, leaf_groups


TOP_GROUPS, LEAF_GROUPS = _load_categories()
VALID_CATEGORY_IDS = {cid for cid, _ in TOP_GROUPS} | {cid for cid, _, _ in LEAF_GROUPS}
LEAF_CATEGORY_IDS = {cid for cid, _, _ in LEAF_GROUPS}


def _norm(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '').strip().lower())


def _find_current_satu_file() -> str:
    raw_dir = BASE_DIR / 'data' / 'portal' / 'satu' / 'raw'
    if not raw_dir.exists():
        return 'актуальный файл из data/portal/satu/raw/'
    files = sorted(
        [p for p in raw_dir.iterdir() if p.is_file() and p.suffix.lower() in {'.xls', '.xlsx'}],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0].name if files else 'актуальный файл из data/portal/satu/raw/'


def _offer_blocks(text: str) -> list[str]:
    return re.findall(r'(<offer\b.*?</offer>)', text, flags=re.S)


def _extract_tag(block: str, tag: str) -> str:
    m = re.search(fr'<{tag}>(.*?)</{tag}>', block, flags=re.S)
    return m.group(1) if m else ''


def _extract_all_tags(block: str, tag: str) -> list[str]:
    return re.findall(fr'<{tag}>(.*?)</{tag}>', block, flags=re.S)


def _extract_offer_id(block: str) -> str:
    m = re.search(r'<offer\b[^>]*id="([^"]+)"', block)
    return m.group(1) if m else ''


def _extract_available(block: str) -> bool:
    m = re.search(r'<offer\b[^>]*available="([^"]+)"', block)
    return (m.group(1).strip().lower() == 'true') if m else False


def _extract_feed_meta_body(text: str, source_name: str) -> str:
    m = re.search(r'<!--FEED_META\n(.*?)\n-->', text, flags=re.S)
    if not m:
        raise ValueError(f'[{source_name}] Не найден FEED_META')
    return m.group(1).rstrip()


def _extract_supplier_summary(meta_body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in meta_body.splitlines():
        if '|' not in line:
            continue
        key, value = line.split('|', 1)
        out[key.strip()] = value.strip()
    return out


def _build_portal_registry() -> dict[str, dict[str, Any]]:
    data = _read_yaml(SATU_PORTAL_CATEGORIES_FILE)
    registry: dict[str, dict[str, Any]] = {}
    for item in data.get('categories', []) or []:
        if not isinstance(item, dict):
            continue
        portal_id = str(item.get('portal_id', '')).strip()
        if portal_id:
            registry[portal_id] = item
    return registry


def _build_default_map(portal_registry: dict[str, dict[str, Any]]) -> dict[str, str]:
    data = _read_yaml(PRICE_PORTAL_MAP_FILE)
    out: dict[str, str] = {}
    for item in data.get('mappings', []) or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get('cs_category_id', '')).strip()
        portal_id = str(item.get('default_portal_id', '')).strip()
        if not cid or not portal_id:
            continue
        if portal_id not in portal_registry:
            raise ValueError(f'В price_portal_map.yml указан portal_id={portal_id}, которого нет в satu_portal_categories.yml')
        out[cid] = portal_id
    return out


def _build_overrides(portal_registry: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    data = _read_yaml(PRICE_PORTAL_OVERRIDES_FILE)
    result: list[dict[str, Any]] = []
    for item in data.get('overrides', []) or []:
        if not isinstance(item, dict):
            continue
        portal_id = str(item.get('portal_category_id', '')).strip()
        if portal_id and portal_id not in portal_registry:
            raise ValueError(f'В price_portal_overrides.yml указан portal_category_id={portal_id}, которого нет в satu_portal_categories.yml')
        result.append(item)
    result.sort(key=lambda x: int(x.get('priority', 999999)))
    return result


def _parse_params(block: str) -> list[tuple[str, str]]:
    return [(name.strip(), value.strip()) for name, value in re.findall(r'<param name="([^"]+)">(.*?)</param>', block, flags=re.S)]


def _override_for_offer(*, supplier: str, category_id: str, name: str, vendor: str, params: list[tuple[str, str]], overrides: list[dict[str, Any]]) -> str | None:
    name_n = _norm(name)
    vendor_n = _norm(vendor)
    params_norm = [(_norm(k), _norm(v)) for k, v in params]
    for rule in overrides:
        if str(rule.get('cs_category_id', '')).strip() not in {'', category_id}:
            continue
        if str(rule.get('supplier', '')).strip() not in {'', supplier}:
            continue
        matched = False
        for needle in rule.get('name_contains', []) or []:
            needle_n = _norm(str(needle))
            if needle_n and needle_n in name_n:
                matched = True
                break
        if not matched:
            for needle in rule.get('vendor_contains', []) or []:
                needle_n = _norm(str(needle))
                if needle_n and needle_n in vendor_n:
                    matched = True
                    break
        if not matched:
            for cond in rule.get('params', []) or []:
                if not isinstance(cond, dict):
                    continue
                target_name = _norm(str(cond.get('name', '')))
                contains = _norm(str(cond.get('contains', '')))
                if not target_name or not contains:
                    continue
                for pname, pvalue in params_norm:
                    if pname == target_name and contains in pvalue:
                        matched = True
                        break
                if matched:
                    break
        if matched:
            portal_id = str(rule.get('portal_category_id', '')).strip()
            return portal_id or None
    return None


def _inject_portal_category_id(block: str, portal_category_id: str) -> str:
    if '<portal_category_id>' in block:
        return re.sub(r'<portal_category_id>.*?</portal_category_id>', f'<portal_category_id>{portal_category_id}</portal_category_id>', block, flags=re.S)
    return re.sub(r'(<categoryId>.*?</categoryId>)', r'\1\n      <portal_category_id>' + portal_category_id + '</portal_category_id>', block, count=1, flags=re.S)


def _load_offers(portal_registry: dict[str, dict[str, Any]], default_map: dict[str, str], overrides: list[dict[str, Any]]) -> tuple[list[OfferInfo], list[str], dict[str, int], Counter[str], int, int]:
    offers: list[OfferInfo] = []
    feed_meta_blocks: list[str] = []
    supplier_after_counts: dict[str, int] = {}
    status_counts: Counter[str] = Counter()
    price_100 = 0
    placeholder_count = 0
    missing_sources: list[str] = []

    for supplier_name, path in FINAL_SOURCES:
        if not path.exists():
            missing_sources.append(supplier_name)
            continue
        text = path.read_text(encoding='utf-8')
        meta_body = _extract_feed_meta_body(text, supplier_name)
        feed_meta_blocks.append(meta_body)
        summary = _extract_supplier_summary(meta_body)
        supplier_after_counts[supplier_name] = int(summary.get('Сколько товаров у поставщика после фильтра', '0') or 0)

        for block in _offer_blocks(text):
            offer_id = _extract_offer_id(block)
            category_id = _extract_tag(block, 'categoryId').strip()
            if category_id not in LEAF_CATEGORY_IDS:
                raise ValueError(f'[{supplier_name}] [{offer_id}] Неизвестный categoryId={category_id}')

            vendor_code = _extract_tag(block, 'vendorCode').strip()
            name = _extract_tag(block, 'name').strip()
            price = _extract_tag(block, 'price').strip()
            vendor = _extract_tag(block, 'vendor').strip()
            pictures = [p.strip() for p in _extract_all_tags(block, 'picture') if p.strip()]
            params = _parse_params(block)
            available = _extract_available(block)

            if price == '100':
                price_100 += 1
            if any(p == PLACEHOLDER_PICTURE for p in pictures):
                placeholder_count += 1

            portal_category_id = _override_for_offer(
                supplier=supplier_name,
                category_id=category_id,
                name=name,
                vendor=vendor,
                params=params,
                overrides=overrides,
            )
            if portal_category_id:
                block = _inject_portal_category_id(block, portal_category_id)
            elif category_id not in default_map:
                raise ValueError(f'[{supplier_name}] [{offer_id}] Нет portal_id для categoryId={category_id}')

            offers.append(OfferInfo(
                supplier=supplier_name,
                offer_id=offer_id,
                vendor_code=vendor_code,
                category_id=category_id,
                name=name,
                available=available,
                price=price,
                pictures=pictures,
                block=block,
                portal_category_id=portal_category_id,
            ))
            status_counts['true' if available else 'false'] += 1

    if missing_sources:
        raise FileNotFoundError('Не найдены final-файлы поставщиков: ' + ', '.join(missing_sources))
    return offers, feed_meta_blocks, supplier_after_counts, status_counts, price_100, placeholder_count


def _build_categories_xml(default_map: dict[str, str]) -> tuple[str, int, int, int]:
    lines: list[str] = ['    <categories>']
    total_categories = 0
    mapped_leaf = 0
    for cid, name in TOP_GROUPS:
        lines.append(f'    <category id="{cid}">{name}</category>')
        total_categories += 1
    for cid, parent_id, name in LEAF_GROUPS:
        portal_attr = ''
        portal_id = default_map.get(cid, '')
        if portal_id:
            portal_attr = f' portal_id="{portal_id}"'
            mapped_leaf += 1
        lines.append(f'    <category id="{cid}" parentId="{parent_id}"{portal_attr}>{name}</category>')
        total_categories += 1
    lines.append('    </categories>')
    return '\n'.join(lines), total_categories, len(LEAF_GROUPS), mapped_leaf


def _write_unmapped_report(unmapped: list[OfferInfo]) -> None:
    UNMAPPED_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not unmapped:
        UNMAPPED_FILE.write_text('Все товары получили категорию Satu.\n', encoding='utf-8')
        return
    lines = []
    for offer in unmapped:
        lines.append(f'{offer.offer_id} | {offer.supplier} | {offer.category_id} | {offer.name} | не найдено соответствие')
    UNMAPPED_FILE.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _write_audit_report(*, total_offers: int, total_categories: int, leaf_categories: int, mapped_leaf: int, default_mapped: int, override_mapped: int, unmapped_count: int, satu_file_name: str) -> None:
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TZ)
    lines = [
        'Итог привязки Price к категориям Satu',
        f'Время сборки (Алматы)                      | {now:%Y-%m-%d %H:%M:%S}',
        f'Источник категорий Satu                    | {satu_file_name}',
        f'Сколько категорий всего                    | {total_categories}',
        f'Сколько рабочих подкатегорий               | {leaf_categories}',
        f'Сколько подкатегорий привязано к Satu      | {mapped_leaf}',
        f'Сколько товаров получили категорию Satu по умолчанию | {default_mapped}',
        f'Сколько товаров получили отдельную категорию Satu   | {override_mapped}',
        f'Сколько товаров без категории Satu         | {unmapped_count}',
        f'Статус привязки к категориям Satu          | {"УСПЕШНО" if unmapped_count == 0 else "НЕУСПЕШНО"}',
    ]
    AUDIT_FILE.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _render_price_feed(*, offers: list[OfferInfo], feed_meta_blocks: list[str], total_categories: int, leaf_categories: int, mapped_leaf: int, status_counts: Counter[str], price_100: int, placeholder_count: int, satu_file_name: str, categories_xml: str) -> str:
    now = datetime.now(TZ)
    next_run = (now + timedelta(days=1)).replace(hour=4, minute=30, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)

    price_meta = [
        'Price',
        f'Время сборки (Алматы)                 | {now:%Y-%m-%d %H:%M:%S}',
        f'Ближайшая сборка (Алматы)             | {next_run:%Y-%m-%d %H:%M:%S}',
        'Расписание (Алматы)                   | ежедневно в 04:30',
        f'Сколько поставщиков в Price           | {len(EXPECTED_SUPPLIERS)}',
        f'Порядок поставщиков                   | {", ".join(EXPECTED_SUPPLIERS)}',
        f'Сколько товаров в Price всего         | {len(offers)}',
        f'Сколько товаров есть в наличии (true) | {status_counts["true"]}',
        f'Сколько товаров нет в наличии (false) | {status_counts["false"]}',
        f'Сколько товаров с ценой 100           | {price_100}',
        f'Сколько товаров с заглушкой фото      | {placeholder_count}',
        'Сколько товаров без categoryId        | 0',
        f'Сколько дублей offer id               | {len(offers) - len({o.offer_id for o in offers})}',
        f'Сколько дублей vendorCode             | {len(offers) - len({o.vendor_code for o in offers})}',
        f'Источник категорий Satu               | {satu_file_name}',
        f'Сколько категорий всего               | {total_categories}',
        f'Сколько рабочих подкатегорий          | {leaf_categories}',
        f'Сколько подкатегорий привязано к Satu | {mapped_leaf}',
        'Сколько товаров без категории Satu    | 0',
        'Проверка привязки к категориям Satu   | УСПЕШНО',
        'Статус проверки Price                 | УСПЕШНО',
    ]
    feed_meta = '\n\n'.join(feed_meta_blocks + ['\n'.join(price_meta)])

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<!DOCTYPE yml_catalog SYSTEM "shops.dtd">',
        f'<yml_catalog date="{now:%Y-%m-%d %H:%M}">',
        '  <shop>',
        '    <name>Complex Solutions Ltd</name>',
        '    <company>Complex Solutions Ltd</company>',
        '    <url>https://complex-solutions.kz/</url>',
        '    <currencies>',
        '      <currency id="KZT" rate="1"/>',
        '    </currencies>',
        '',
        '    <!--FEED_META',
        feed_meta,
        '-->',
        '',
        categories_xml,
        '',
        '    <offers>',
    ]
    for offer in offers:
        block = offer.block.strip('\n')
        indented = '\n'.join('      ' + line if line.strip() else '' for line in block.splitlines())
        lines.append(indented)
        lines.append('')
    lines.extend(['    </offers>', '  </shop>', '</yml_catalog>', ''])
    return '\n'.join(lines)


def main() -> int:
    portal_registry = _build_portal_registry()
    default_map = _build_default_map(portal_registry)
    overrides = _build_overrides(portal_registry)
    offers, feed_meta_blocks, _, status_counts, price_100, placeholder_count = _load_offers(portal_registry, default_map, overrides)

    offer_ids = [o.offer_id for o in offers]
    vendor_codes = [o.vendor_code for o in offers]
    if len(offer_ids) != len(set(offer_ids)):
        raise ValueError('Обнаружены дубли offer id')
    if len(vendor_codes) != len(set(vendor_codes)):
        raise ValueError('Обнаружены дубли vendorCode')

    categories_xml, total_categories, leaf_categories, mapped_leaf = _build_categories_xml(default_map)
    unmapped = [o for o in offers if not o.portal_category_id and o.category_id not in default_map]
    satu_file_name = _find_current_satu_file()
    _write_unmapped_report(unmapped)
    _write_audit_report(
        total_offers=len(offers),
        total_categories=total_categories,
        leaf_categories=leaf_categories,
        mapped_leaf=mapped_leaf,
        default_mapped=len(offers) - len([o for o in offers if o.portal_category_id]),
        override_mapped=len([o for o in offers if o.portal_category_id]),
        unmapped_count=len(unmapped),
        satu_file_name=satu_file_name,
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        _render_price_feed(
            offers=offers,
            feed_meta_blocks=feed_meta_blocks,
            total_categories=total_categories,
            leaf_categories=leaf_categories,
            mapped_leaf=mapped_leaf,
            status_counts=status_counts,
            price_100=price_100,
            placeholder_count=placeholder_count,
            satu_file_name=satu_file_name,
            categories_xml=categories_xml,
        ),
        encoding='utf-8',
    )
    print(f'[PRICE] OK: {OUTPUT_FILE.as_posix()}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover
        print(f'[PRICE] ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)
