# CS-шаблон Satu.kz — supplier-feeds

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

`script/suppliers/<supplier>/`

И отвечает за:

- получение source-данных
- supplier-specific filtering
- supplier-specific normalize
- params / compat / pictures / description extraction
- build raw offers
- supplier quality gate

### 2. Shared core

Общий слой живёт в:

`script/cs/`

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

`script/build_price_checker.py`

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
- `docs/` — итоговые final YML
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
Собирает итоговый `Price.yml` из final supplier feeds.

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

---

## Supplier status по состоянию freeze

### AlStyle
Один из самых зрелых supplier-packages. Используется как один из эталонов supplier-layer.

### AkCent
Рабочий supplier, но исторически имел перегруженный orchestrator. Текущий baseline очищен и упрощён.

### CopyLine
Рабочий supplier, но раньше имел переходный dual-contract между source / builder / params. Чистка была направлена на унификацию контрактов.

### ComPortal
По структуре один из самых аккуратных пакетов. Основные хвосты обычно в данных, а не в архитектуре.

### VTT
Самый тяжёлый supplier-package. Здесь больше всего риска по complexity, orchestration и source-layer.

---

## Что уже доведено в проекте

К текущему freeze проект уже прошёл через такие ключевые шаги:

- очистка legacy и мусорных файлов
- выравнивание Price/check workflows
- исправление `build_price.py`
- санитарная чистка `scripts/cs/core.py`
- унификация CopyLine contract-layer
- разгрузка VTT orchestration / filtering / normalize слоя
- упрощение `build_akcent.py`
- санитарная чистка AlStyle и ComPortal
- исправление `build_price_checker.py`
- добавление `requirements.txt`
- обновление `README`

---

## Что важно помнить про Satu

### Главный итоговый файл
Главный файл для импорта:
- `docs/Price.yml`

### Что критично для Satu
- валидный XML/YML
- корректные бренды
- корректные характеристики
- отсутствие длинных параметров, нарушающих лимиты
- корректные `categoryId`
- аккуратный final export

### Что уже закреплено в логике проекта
- финальный слой режет опасные длинные значения в final
- category mapping централизован
- supplier-specific normalizations должны жить в supplier layer
- Price checker нужен как контроль, а не как замена сборке

---

## Локальный запуск

### 1. Установка окружения

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

### 2. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 3. Компиляционная проверка

```bash
python -m compileall -q scripts
```

---

## Основной порядок запусков

Полный порядок:

```bash
python scripts/build_akcent.py
python scripts/build_alstyle.py
python scripts/build_copyline.py
python scripts/build_comportal.py
python scripts/build_vtt.py
python scripts/build_price.py
python scripts/build_price_checker.py
```

Минимальный порядок при последних критичных правках Price/final-слоя:

```bash
python scripts/build_akcent.py
python scripts/build_alstyle.py
python scripts/build_comportal.py
python scripts/build_price.py
```

---

## Что считать успешным результатом

Проект считается в рабочем состоянии, если:

- `scripts/` компилируется без ошибок
- supplier builds не падают
- `docs/Price.yml` собирается
- `docs/Price.yml` валиден
- `scripts/build_price_checker.py` отрабатывает
- `docs/raw/price_checker_report.txt` формируется

---

## Freeze / восстановление проекта

Чтобы считать проект действительно завершённым и переносимым, в архиве должны быть:

- последний рабочий архив репозитория
- последний рабочий `Price.yml`
- этот `README.md`
- `requirements.txt`

Рекомендуемый состав freeze-папки:

```text
CS_SATU_FINAL_FREEZE/
├── repo_last_working.zip
├── Price_last_working.yml
├── README.md
├── requirements.txt
└── notes/
```

### Минимум для восстановления
1. Распаковать архив
2. Установить зависимости
3. Запустить supplier builds
4. Запустить `build_price.py`
5. Запустить `build_price_checker.py`

---

## Что не надо делать без причины

- не трогать `robots.txt`, если нет реальной проблемы индексации
- не менять пагинацию без конкретной причины
- не плодить пустые разделы сайта
- не включать пустые новости/статьи/баннеры ради вида
- не тащить supplier-specific hacks обратно в shared core
- не переписывать рабочий pipeline без нужды

---

## Практический принцип развития проекта

Правильный порядок развития:

1. Чистый импорт без ошибок
2. Сильные карточки и данные
3. Уровень магазина, успешные заказы, отзывы
4. Управляемый ProSale
5. SEO-контент и статьи
6. Масштабирование сильных категорий

Главная цель — не “настроить всё подряд”, а добиться:
- чистого Price
- сильной конверсии
- роста доверия
- управляемой аналитики

---

## Связанный проект: рабочий кабинет Satu

Этот репозиторий связан с рабочим кабинетом Satu.

Текущий общий вывод по кабинету:
- база уже собрана
- техника и индексация находятся на хорошем уровне
- основной потенциал роста дальше лежит в:
  - карточках товаров
  - успешных заказах и отзывах
  - конверсии
  - управляемом ProSale
  - SEO-контенте

То есть дальнейший рост зависит уже не столько от структуры репо, сколько от качества данных и работы магазина в Satu.

---

## Итог

Текущий freeze этого репозитория можно считать рабочей стабильной базой для проекта CS-шаблон Satu.kz.

Основная логика проекта:

`supplier-layer -> shared core -> Price -> checker`

Именно так проект должен восприниматься и восстанавливаться в будущем.
