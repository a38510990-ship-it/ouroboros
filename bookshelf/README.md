# 📚 Bookshelf Catalog Bot

Telegram-бот для каталогизации книжных полок. Отправь фото — получи список книг в Google Sheets.

## Как это работает

```
Фото полки (Telegram)
    ↓
VLM (OpenRouter)        ← распознаёт названия и авторов с корешков
    ↓
Google Books API        ← обогащает: год, ISBN, жанр, страницы, обложка
    ↓
Google Sheets           ← добавляет новые книги (дедупликация по ISBN/Title+Author)
    ↓
Ответ в Telegram        ← "Добавлено 5 книг: [список]"
```

## Структура таблицы Google Sheets

| Title | Author | Year | ISBN | Genre | Pages | Cover URL | Date Added |
|-------|--------|------|------|-------|-------|-----------|------------|

## Setup

### 1. Зависимости

```bash
pip install -r bookshelf/requirements.txt
```

### 2. Telegram Bot Token

1. Напиши [@BotFather](https://t.me/botfather) в Telegram
2. `/newbot` → следуй инструкциям
3. Скопируй токен

### 3. OpenRouter API Key

1. Зарегистрируйся на [openrouter.ai](https://openrouter.ai)
2. Создай API key в настройках

### 4. Google Cloud + OAuth (один раз)

#### 4.1. Создай Google Cloud проект

1. Зайди на [console.cloud.google.com](https://console.cloud.google.com)
2. Создай новый проект (или выбери существующий)
3. В поиске найди **"Google Sheets API"** → Enable
4. В поиске найди **"Google Drive API"** → Enable

#### 4.2. Создай OAuth credentials

1. Перейди в **APIs & Services → Credentials**
2. Нажми **Create Credentials → OAuth client ID**
3. Тип: **Desktop application**
4. Скачай JSON файл
5. Переименуй его в `credentials.json` и положи в папку `bookshelf/`

> ⚠️ Если видишь "This app isn't verified" — нажми "Continue" (это нормально для личных проектов)

#### 4.3. Первичная авторизация

```bash
cd bookshelf
python oauth_setup.py
```

Откроется браузер → разреши доступ → в папке появится `token.json`.

После этого бот может работать без браузера.

### 5. Переменные окружения

```bash
cp bookshelf/.env.example bookshelf/.env
# Отредактируй bookshelf/.env
```

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
OPENROUTER_API_KEY=sk-or-v1-...
GOOGLE_SHEET_NAME=Bookshelf Catalog
VLM_MODEL=google/gemini-2.0-flash-001
```

### 6. Запуск

```bash
python bookshelf/bot.py
```

**В Colab:**

```python
import subprocess, os
os.chdir('/content/ouroboros_repo')
!pip install -r bookshelf/requirements.txt -q

# Запустить OAuth (один раз, через ngrok или локально)
# !python bookshelf/oauth_setup.py

# Запустить бота
!python bookshelf/bot.py
```

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и инструкция |
| `/sheet` | Ссылка на Google Sheets таблицу |
| Фото | Распознать книги и добавить в таблицу |

## Поддерживаемые VLM модели

| Модель | Качество | Цена |
|--------|----------|------|
| `google/gemini-2.0-flash-001` | Хорошее | Дёшево |
| `anthropic/claude-3.5-sonnet` | Отличное | Средне |
| `openai/gpt-4o` | Отличное | Средне |
| `google/gemini-2.5-pro-preview` | Лучшее | Дорого |

## Дедупликация

Бот проверяет каждую найденную книгу перед добавлением:
1. Сначала по **ISBN** (точное совпадение)
2. Затем по **Title + Author** (нечёткое совпадение, нижний регистр)

Уже существующие книги не добавляются повторно.

## Файлы (не коммитятся)

- `bookshelf/.env` — секреты
- `bookshelf/token.json` — OAuth токен (генерируется oauth_setup.py)
- `bookshelf/credentials.json` — Google OAuth credentials (скачивается из Google Cloud)
