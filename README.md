# CS-шаблон Satu.kz — cs-satu

## Назначение проекта

Этот репозиторий собирает supplier-фиды и итоговый `Price.yml` для Satu.kz.

Главная идея проекта:

- каждый supplier-layer должен отдавать максимально чистый `raw`
- shared core должен делать только общие post-processing правила
- итоговый `Price.yml` агрегирует всех поставщиков
- checker проверяет итоговую выгрузку и пишет отчёты

Проект должен быть переносимым: архив репозитория должен позволять поднять сборку в другой среде без потери структуры и логики.

---

## Актуальные поставщики

Текущая рабочая картина проекта:

- **AkCent**
- **AlStyle**
- **CopyLine**
- **ComPortal**
- **VTT**

`NVPrint` в текущей картине не считается актуальным активным поставщиком.

---

## Архитектурный принцип

### 1. Supplier-layer

Каждый supplier-package живёт в:

`scripts/suppliers/<supplier>/`

И отвечает за:

- получение source-данных
- supplier-specific filtering
- supplier-specific normalize
- params / compat / pictures / description extraction
- build raw offers
- supplier quality gate

### 2. Shared core

Общий слой живёт в:

`scripts/cs/`

И отвечает только за общую логику:

- category resolve
- final export
- общие safety-ограничения Satu
- общие validators
- общая pricing/meta/writer-логика

### 3. Price

Файл:

`docs/Price.yml`

Это главный итоговый агрегированный YML для импорта в Satu.

### 4. Checker

Файл:

`scripts/build_price_checker.py`

Проверяет итоговый `Price.yml`, считает статистику, пишет отчёты и используется как контроль итоговой выгрузки.

---

## Главные правила проекта

### Shared core не должен:
- лечить supplier-specific косяки одного поставщика
- хранить vendor-specific hacks, если их надо чинить в supplier-layer
- раздуваться под исторические костыли без необходимости

### Supplier-layer должен:
- максимально очищать raw до передачи в shared layer
- хранить supplier-specific policy и нормализацию внутри supplier package
- не тащить общие Satu-правила в свою логику

### Final-слой должен:
- соблюдать лимиты Satu
- валидно собирать XML/YML
- не ломать структуру товара
- резать только final export значения, если правило относится именно к финальной выгрузке

---

## Структура репозитория

### Важные папки

- `.github/workflows/` — workflow сборок
- `scripts/` — основная логика
- `scripts/cs/` — shared core
- `scripts/suppliers/` — supplier packages
- `data/` — конфиги, вспомогательные данные, portal categories и т.д.
- `docs/` — итоговые final YML / XML
- `docs/raw/` — raw supplier YML, отчёты, quality gate, checker outputs

### Важные файлы

- `scripts/build_akcent.py`
- `scripts/build_alstyle.py`
- `scripts/build_copyline.py`
- `scripts/build_comportal.py`
- `scripts/build_vtt.py`
- `scripts/build_price.py`
- `scripts/build_price_checker.py`
- `requirements.txt`

---

## Что за что отвечает

### `scripts/build_<supplier>.py`
Orchestrator конкретного поставщика:
- запускает supplier pipeline
- пишет raw
- прогоняет final layer
- запускает quality gate

### `scripts/build_price.py`
Собирает итоговый `Price.yml` из final supplier feeds и публикует зеркальные XML-копии.

### `scripts/build_price_checker.py`
Проверяет итоговый `Price.yml`, строит summary/details отчёты и baseline comparison.

### `scripts/cs/core.py`
Главный shared final/export слой.

### `scripts/cs/category_map.py`
Общий category resolver.

### `scripts/cs/writer.py`
Пишет общий YML/XML-выход.

### `scripts/cs/meta.py`
Служебная meta-логика и расчёты next run.

### `scripts/cs/pricing.py`
Общая логика цены.

### `scripts/cs/validators.py`
Общие final validators.
