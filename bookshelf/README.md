# 📚 Bookshelf Catalog Bot

Telegram-бот, который распознаёт книги на фото книжной полки и сохраняет их в Google Sheets.

## Как это работает

```
Ты → фото полки → Telegram Bot
                       ↓
              VLM (OpenRouter) — читает корешки
                       ↓
              Google Books API — название, автор, год, ISBN
                       ↓
              Google Sheets (твой Drive) — таблица накапливается
```

## Структура файлов

```
bookshelf/
├── README.md          # Эта инструкция
├── requirements.txt   # Зависимости
├── bot.py             # Основной файл бота
├── vision.py          # OpenRouter VLM интеграция
├── sheets.py          # Google Sheets / gspread
├── books_api.py       # Google Books API
├── oauth_setup.py     # Первичная OAuth авторизация (запустить один раз)
└── .env.example       # Пример переменных окружения
```

## Быстрый старт

### 1. Установить зависимости

```bash
pip install -r bookshelf/requirements.txt
```

### 2. Настроить переменные окружения

Скопировать `.env.example` в `.env` и заполнить:

```bash
cp bookshelf/.env.example bookshelf/.env
```

Нужны:
- `TELEGRAM_BOT_TOKEN` — токен от [@BotFather](https://t.me/BotFather)
- `OPENROUTER_API_KEY` — ключ от [openrouter.ai](https://openrouter.ai)

### 3. Настроить Google Sheets (OAuth)

#### Создать Google Cloud проект

1. Зайти на [console.cloud.google.com](https://console.cloud.google.com)
2. Создать новый проект (например, "Bookshelf Bot")
3. В боковом меню: **APIs & Services → Library**
4. Найти и включить:
   - **Google Sheets API**
   - **Google Drive API**
5. В боковом меню: **APIs & Services → Credentials**
6. Нажать **Create Credentials → OAuth client ID**
7. Application type: **Desktop app**
8. Скачать JSON-файл → сохранить как `bookshelf/credentials.json`

#### Авторизоваться (один раз)

```bash
python bookshelf/oauth_setup.py
```

Скрипт:
- Откроет браузер для авторизации в твоём Google-аккаунте
- Создаст `bookshelf/token.json` (сохраняется автоматически)
- Создаст таблицу "Bookshelf Catalog" в твоём Drive
- Выведет ID таблицы

Скопировать ID таблицы в `.env`:
```
GOOGLE_SHEET_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
```

### 4. Запустить бота

```bash
python bookshelf/bot.py
```

В Colab:
```python
import subprocess
proc = subprocess.Popen(["python", "bookshelf/bot.py"])
```

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и инструкция |
| `/sheet` | Ссылка на таблицу в Google Drive |
| Фото 📷 | Распознать книги и добавить в таблицу |

## Структура таблицы

| Title | Author | Year | ISBN | Genre | Pages | Cover URL | Date Added |
|-------|--------|------|------|-------|-------|-----------|------------|

## VLM модели (в .env)

```
# Бесплатные (могут быть лимиты):
VLM_MODEL=google/gemini-2.0-flash-thinking-exp:free

# Платные (лучше качество):
VLM_MODEL=anthropic/claude-3.5-sonnet
VLM_MODEL=google/gemini-2.0-flash-001
```

## Переменные окружения

| Переменная | Обязательная | Описание |
|-----------|-------------|----------|
| `TELEGRAM_BOT_TOKEN` | ✅ | Токен бота от BotFather |
| `OPENROUTER_API_KEY` | ✅ | API ключ OpenRouter |
| `GOOGLE_SHEET_ID` | ✅ | ID Google Sheets таблицы |
| `VLM_MODEL` | ❌ | VLM модель (по умолчанию: gemini-2.0-flash-thinking-exp:free) |

## Файлы в .gitignore

- `credentials.json` — OAuth credentials (секрет)
- `token.json` — OAuth токен (секрет)
- `.env` — переменные окружения (секрет)
