# 📚 Bookshelf Catalog Bot

Telegram-бот, который распознаёт книги на фото книжной полки и сохраняет их в CSV-файл на Google Drive.

## Как это работает

```
Ты → фото полки → Telegram Bot
                       ↓
              VLM (OpenRouter) — читает корешки
                       ↓
              Google Books API — название, автор, год, ISBN
                       ↓
              CSV-файл в твоём Google Drive — таблица накапливается
```

## Запуск в Google Colab

Никакого Google Cloud проекта не нужно. CSV создаётся прямо в Drive,
который уже подключён к Colab.

### Ячейка 1 — Установить зависимости

```python
!pip install -q python-telegram-bot httpx python-dotenv
```

### Ячейка 2 — Задать переменные и запустить бота

```python
import os, subprocess

os.environ["TELEGRAM_BOT_TOKEN"] = "токен_от_BotFather"
os.environ["OPENROUTER_API_KEY"] = "ключ_от_openrouter.ai"

proc = subprocess.Popen(
    ["python", "/content/ouroboros_repo/bookshelf/bot.py"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
)
print("✅ Бот запущен! Напиши /start своему боту в Telegram.")
```

Всё. Бот готов к работе.

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и инструкция |
| `/sheet` | Путь к CSV-файлу в Google Drive |
| Фото 📷 | Распознать книги и добавить в таблицу |

## Структура таблицы (CSV)

| Title | Author | Year | ISBN | Genre | Pages | Cover URL | Date Added | Location |
|-------|--------|------|------|-------|-------|-----------|------------|----------|

Файл сохраняется как `Bookshelf Catalog.csv` в корне Google Drive (`MyDrive/`).
Можно открыть через Google Таблицы для фильтрации и сортировки.

## Переменные окружения

| Переменная | Обязательна | Описание |
|-----------|-------------|----------|
| `TELEGRAM_BOT_TOKEN` | ✅ | Токен от [@BotFather](https://t.me/BotFather) |
| `OPENROUTER_API_KEY` | ✅ | Ключ от [openrouter.ai](https://openrouter.ai) |
| `VLM_MODEL` | ❌ | VLM модель (по умолчанию: `google/gemini-2.0-flash-001`) |
| `CATALOG_FILENAME` | ❌ | Имя CSV-файла (по умолчанию: `Bookshelf Catalog.csv`) |

## VLM модели

```bash
# Быстрые и дешёвые:
VLM_MODEL=google/gemini-2.0-flash-001

# Лучше качество:
VLM_MODEL=anthropic/claude-3.5-sonnet
VLM_MODEL=openai/gpt-4o
```

## Файлы

```
bookshelf/
├── README.md        # Эта инструкция
├── requirements.txt # Зависимости
├── bot.py           # Telegram-бот
├── vision.py        # OpenRouter VLM — распознавание книг
├── sheets.py        # CSV-хранилище на Google Drive
├── books_api.py     # Google Books API — обогащение метаданных
└── .env.example     # Пример переменных
```
