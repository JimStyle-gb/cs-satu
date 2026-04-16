# supplier-feeds

Единый шаблон выгрузок для Satu.kz под несколько поставщиков.

## Что это за проект

Проект собирает и поддерживает товарные YML-выгрузки по схеме:

`supplier-layer -> shared core -> Price -> checker`

Где:
- каждый supplier-layer готовит максимально чистый `raw`;
- shared core применяет только общие правила для всех поставщиков;
- `Price` собирает итоговую общую выгрузку;
- `checker` проверяет итоговый `Price.yml`, пишет отчёты и отправляет Telegram-статус.

Главный принцип проекта:
- supplier-specific логика живёт только в `scripts/suppliers/<supplier>/...`
- shared-логика живёт только в `scripts/cs/...`

Core не должен лечить уникальные косяки одного поставщика.
Supplier-layer не должен хранить общие правила Satu.

---

## Актуальные поставщики

Сейчас в проекте используются:
- AlStyle
- AkCent
- CopyLine
- ComPortal
- VTT

`Price` собирается из финальных выгрузок этих поставщиков.

---

## Ключевые результаты сборки

### Raw supplier feed
Путь:
- `docs/raw/<supplier>.yml`

Это результат supplier-layer до общего финального слоя.

### Final supplier feed
Путь:
- `docs/<supplier>.yml`

Это supplier raw после shared core.

### Общий Price
Путь:
- `docs/Price.yml`

Это главный итоговый YML для импорта в Satu.

### Checker reports
Пути:
- `docs/raw/price_checker_report.txt`
- `docs/raw/price_checker_details.txt`
- `docs/raw/price_checker_last_success.json`

---

## Структура проекта

### `scripts/`
Вся логика проекта.

### `scripts/cs/`
Общий shared-layer.

Основные роли:
- `core.py` — общий final/export слой
- `writer.py` — запись YML, header/footer, FEED_META
- `validators.py` — общие проверки final feed
- `pricing.py` — единые правила цены
- `meta.py` — время/следующий запуск/Алматы
- `description.py` — общий builder описаний
- `category_map.py` — общий resolver внутренних категорий
- `qg_report.py` — единый формат quality gate reports
- `util.py` — общие helper-функции

### `scripts/suppliers/<supplier>/`
Supplier-layer конкретного поставщика.

Ожидаемые роли файлов:
- `source.py` — загрузка / чтение источника
- `filtering.py` — supplier-specific фильтрация
- `normalize.py` — supplier-specific нормализация
- `params.py` — сбор и чистка параметров
- `desc_clean.py` / `desc_extract.py` — работа с описанием
- `pictures.py` — картинки
- `builder.py` — orchestration supplier raw offer
- `models.py` — typed DTO / dataclasses
- `quality_gate.py` — supplier-level quality checks
- `diagnostics.py` — supplier diagnostics / summaries

### `docs/`
Финальные выгрузки и `Price.yml`.

### `docs/raw/`
Raw supplier feeds, quality gate reports, checker reports и техническая диагностика.

### `.github/workflows/`
GitHub Actions workflows для build/check процессов.

### `data/portal/satu/`
Исходные данные для portal-category mapping.

---

## Основные правила проекта

### 1. Shared core — только общий слой
В core допустимы только общие правила, одинаковые для всех поставщиков.

Примеры того, что допустимо в core:
- final clamp имени для Satu
- лимит картинок только в final XML
- trim `Совместимость` только на XML-export
- общая валидация final feed
- общий category resolve
- общий writer / FEED_META / validation

Примеры того, чего в core быть не должно:
- supplier-specific remap
- supplier-specific vendor fixes
- supplier-specific cleanup description/params
- supplier-specific business-policy

### 2. Raw должен быть максимально чистым
Supplier-layer должен по максимуму отдавать уже чистый `raw`.
Final core не должен превращаться в место, где исправляются уникальные supplier-ошибки.

### 3. Price — главный конечный артефакт
Именно `docs/Price.yml` считается главным продуктом проекта.
Все промежуточные supplier final feeds существуют ради него.

### 4. Checker — это диагностика, а не бизнес-логика
`build_price_checker.py` должен только проверять, сравнивать baseline, писать отчёты и слать уведомления.

### 5. FEED_META не трогать без явной причины
FEED_META — часть стабильной структуры проекта.
Менять его нужно только осознанно.

---

## Текущие workflow-расписания (по файлам workflows)

Алматы, Asia/Almaty:
- AkCent — 22:30 ежедневно
- AlStyle — 23:30 ежедневно
- ComPortal — 00:30 ежедневно
- CopyLine — 01:30 в дни `1,10,20`
- VTT — 02:30 в дни `1,10,20`
- Price — 04:30 ежедневно
- Check_Price — 05:30 ежедневно

Если расписание меняется, его нужно синхронно поддерживать:
- в workflow-файлах
- в мета-логике, если она пишет next run / FEED_META

---

## Важные пути

### Build scripts
- `scripts/build_alstyle.py`
- `scripts/build_akcent.py`
- `scripts/build_copyline.py`
- `scripts/build_comportal.py`
- `scripts/build_vtt.py`
- `scripts/build_price.py`
- `scripts/build_price_checker.py`

### Final outputs
- `docs/alstyle.yml`
- `docs/akcent.yml`
- `docs/copyline.yml`
- `docs/comportal.yml`
- `docs/vtt.yml`
- `docs/Price.yml`

### Raw outputs
- `docs/raw/alstyle.yml`
- `docs/raw/akcent.yml`
- `docs/raw/copyline.yml`
- `docs/raw/comportal.yml`
- `docs/raw/vtt.yml`

### Price technical outputs
- `docs/raw/category_id_unresolved.txt`
- `docs/raw/price_satu_unmapped_offers.txt`
- `docs/raw/price_satu_portal_audit.txt`
- `docs/raw/price_checker_report.txt`
- `docs/raw/price_checker_details.txt`
- `docs/raw/price_checker_last_success.json`

---

## Локальный запуск

Запускать из корня репозитория.

### Supplier builds
```bash
python scripts/build_alstyle.py
python scripts/build_akcent.py
python scripts/build_copyline.py
python scripts/build_comportal.py
python scripts/build_vtt.py
```

### Price
```bash
python scripts/build_price.py
```

### Checker
```bash
python scripts/build_price_checker.py
```

---

## Переменные окружения и секреты

### ComPortal
- `COMPORTAL_LOGIN`
- `COMPORTAL_PASSWORD`

### VTT
- `VTT_LOGIN`
- `VTT_PASSWORD`

### Telegram checker
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### Общие
- `TZ=Asia/Almaty`

---

## Зависимости

Проект ориентирован на Python 3.11.

Основные внешние пакеты:
- `requests`
- `beautifulsoup4`
- `lxml`
- `PyYAML`
- `python-dateutil`
- `openpyxl`

Если в репозитории есть `requirements.txt`, workflows ставят зависимости из него.
Если файла нет, workflows используют fallback-установку этих пакетов напрямую.

---

## Что нельзя делать без причины

- нельзя переносить supplier-specific логику в `scripts/cs/core.py`
- нельзя руками править `docs/*.yml` и `docs/raw/*.yml` как постоянное решение
- нельзя ломать FEED_META ради косметики
- нельзя лечить supplier data общими костылями в core
- нельзя обновлять quality gate baseline без осознанного решения

---

## Что считается хорошим состоянием проекта

Проект в хорошем состоянии, когда:
- supplier raw максимально чистый;
- final feeds валидны;
- `Price.yml` собирается без дублей и дыр;
- checker пишет понятный отчёт;
- supplier-specific логика остаётся в supplier-layer;
- shared core остаётся только общим слоем.

---

## Быстрая handoff-выжимка

Если новый чат или новый человек продолжает работу по проекту, базовая модель такая:

1. Сначала supplier-layer готовит raw.
2. Потом shared core делает только общие post-processing шаги.
3. Потом `build_price.py` собирает общий `Price.yml`.
4. Потом `build_price_checker.py` проверяет итог и пишет отчёты.

Главный ориентир по архитектуре:
- supplier-specific только в `scripts/suppliers/...`
- shared только в `scripts/cs/...`
- `Price.yml` — главный итоговый артефакт
- checker — только диагностика и контроль качества
