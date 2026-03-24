# 🎙️ Voice Note Alchemist

Телеграм-бот, который превращает голосовые сообщения в текст — и при желании суммаризирует их.

Отправь боту голосовое сообщение или аудиофайл — получи расшифровку, краткое содержание и `.txt` файл у себя на Google Диске. Без облаков, без OAuth, без кредитной карты.

---

## ⚡ Быстрый старт (Google Colab)

Скопируй и запусти в одной ячейке:

```python
import subprocess, os

# Установить зависимости
subprocess.run(["pip", "install", "-q",
    "python-telegram-bot==21.5", "faster-whisper", "httpx", "python-dotenv"], check=True)

# Перейти в папку бота
os.chdir("/content/ouroboros_repo/voice_alchemist")

# Настроить переменные
os.environ["TELEGRAM_TOKEN"] = "YOUR_BOT_TOKEN_HERE"

# Опционально:
# os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-..."  # для суммаризации
# os.environ["ALLOWED_TELEGRAM_ID"] = "123456789"    # ограничить доступ

# Запустить
exec(open("bot.py").read())
main()
```

Бот работает, пока ячейка активна. При перезапуске Colab — просто выполни ячейку снова.

---

## 📦 Поддерживаемые форматы

| Формат | Расширение |
|--------|-----------|
| Голосовые сообщения Telegram | `.ogg` |
| MP3 | `.mp3` |
| M4A / AAC | `.m4a`, `.aac` |
| WAV | `.wav` |
| WebM | `.webm` |

**Максимальный размер файла:** 20 МБ.

---

## ⚙️ Переменные окружения

| Переменная | Обязательная | По умолчанию | Описание |
|-----------|:---:|-------------|----------|
| `TELEGRAM_TOKEN` | ✅ | — | Токен бота (получить у [@BotFather](https://t.me/BotFather)) |
| `ALLOWED_TELEGRAM_ID` | ❌ | открытый доступ | Telegram user ID, которому разрешён доступ. Остальные получат «⛔ Access denied.» |
| `OPENROUTER_API_KEY` | ❌ | — | Ключ OpenRouter — включает автосуммаризацию для длинных расшифровок |
| `OUROBOROS_MODEL_LIGHT` | ❌ | `google/gemini-2.0-flash-001` | Модель для суммаризации |
| `WHISPER_MODEL` | ❌ | `tiny` | Размер модели Whisper: `tiny`, `small`, `medium`, `large-v3` |
| `SUMMARY_MIN_CHARS` | ❌ | `500` | Минимальная длина расшифровки (символы) для запуска суммаризации |
| `DRIVE_ROOT` | ❌ | `/content/drive/MyDrive/Ouroboros` | Путь к папке Ouroboros на Google Диске |

---

## 💾 Куда сохраняются результаты

```
MyDrive/Ouroboros/voice_alchemist/processed/
└── 2026-03-24_123456789_987654321.txt
    ├── Заголовок (дата, user_id, message_id)
    ├── Полный текст расшифровки
    └── Краткое содержание (если суммаризация включена)
```

Файлы можно найти через Google Диск → `Мой диск / Ouroboros / voice_alchemist / processed`.

---

## 🤖 Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и описание возможностей |
| `/help` | Справка, форматы, лимиты |
| _(голосовое сообщение)_ | Расшифровать и сохранить |
| _(аудиофайл)_ | Расшифровать и сохранить |

---

## ⚡ Скорость и ограничения

| Модель | Тип вычислений | Скорость (CPU) | Точность |
|--------|:---:|:----|:----|
| `tiny` | int8 | ~1.8x realtime ✅ | Базовая |
| `base` | int8 | <0.5x realtime ❌ | Лучше |
| `small` | int8 | Требует GPU | Хорошая |
| `medium` | int8 | Требует GPU | Высокая |
| `large-v3` | int8 | Требует GPU | Наилучшая |

**На бесплатном Colab (только CPU):** используй `tiny`. 1 минута аудио ≈ 33 секунды обработки.

**На Colab с T4 GPU:** можно использовать `small` или `medium` — несколько секунд на минуту аудио.

---

## 🔧 Структура файлов

```
voice_alchemist/
├── bot.py           ← Telegram-бот (главный файл)
├── transcribe.py    ← Модуль расшифровки (faster-whisper)
├── requirements.txt ← Зависимости
├── .env.example     ← Шаблон для .env
├── .gitignore       ← .env и логи исключены из git
└── README.md        ← Этот файл
```
