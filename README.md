# 🛍️Telegram Shop Bot

> **Production-ready** Telegram бот-магазин с AI-консультантом, оплатой через YooKassa и интеграцией GigaChat.

[![Python](https://img.shields.io/badge/Python-3.13%2B-blue)](https://python.org)
[![python-telegram-bot](https://img.shields.io/badge/python--telegram--bot-21.10-green)](https://github.com/python-telegram-bot/python-telegram-bot)
[![YooKassa](https://img.shields.io/badge/Payment-YooKassa-purple)](https://yookassa.ru)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## 🌟 Возможности

| Функция | Описание |
|---------|----------|
| 🛒 **Магазин** | Каталог товаров, корзина, оформление заказов |
| 💳 **Оплата** | YooKassa (банковские карты) + Telegram Payments |
| 🤖 **AI-консультант** | GigaChat — отвечает на вопросы о товарах |
| 📦 **Управление заказами** | Уведомления админу, статусы заказов |
| 📋 **GDPR/Соглашения** | Пользовательское соглашение, политика конфиденциальности |
| 📸 **Медиа** | Фото и видео товаров |
| 🔔 **Уведомления** | Email-чеки, Telegram-уведомления |
| 🗄️ **БД** | SQLite (разработка) / MySQL (production) |
| 🔐 **Безопасность** | Фильтрация PII в логах, хранение секретов через .env |

---

## 📁 Структура проекта

```
solid-simple-bot/
├── bot/                          # Python Telegram Bot
│   ├── main.py                   # Точка входа
│   ├── requirements.txt          # Зависимости
│   ├── .env.example              # Шаблон конфигурации
│   ├── .gitignore                # Игнорируемые файлы
│   ├── grk.mp4                   # Приветственное видео
│   ├── core/                     # Ядро приложения
│   │   ├── config.py             # Конфигурация
│   │   ├── database.py           # SQLite операции
│   │   ├── yookassa_processor.py # Обработка платежей YooKassa
│   │   ├── gigachat_assistant.py # AI через GigaChat API
│   │   ├── notifications.py      # Email + Telegram уведомления
│   │   ├── state_manager.py      # Управление состояниями
│   │   ├── hybrid_cart.py        # Корзина (hybrid: память + SQLite)
│   │   ├── logging_filters.py    # 🔐 Безопасное логирование
│   │   └── ...                   # Прочие модули
│   ├── handlers/                 # Обработчики событий
│   │   ├── navigation_handlers.py
│   │   ├── checkout_handlers.py
│   │   ├── payment_handlers.py
│   │   ├── ai_handlers.py
│   │   └── legal_handlers.py
│   ├── keyboards/                # Клавиатуры бота
│   ├── messages/                 # Шаблоны сообщений
│   ├── data/                     # Данные магазина
│   │   └── shop_data.json        # Каталог товаров (JSON)
│   ├── photos/                   # Фото товаров (не в репо)
│   └── logs/                     # Логи (не в репо)
│
├── webhook/                      # PHP webhook для YooKassa
│   └── yookassa/
│       ├── webhook.php           # Приёмник уведомлений
│       ├── check_yookassa.php    # Диагностика БД
│       ├── .env.example          # Шаблон конфигурации webhook
│       └── .htaccess             # Защита директории
│
├── docs/                         # Документация
│   ├── ARCHITECTURE.md           # Архитектура системы
│   ├── DEPLOY.md                 # Деплой на сервер
│   ├── CHANGELOG.md              # История изменений
│   ├── PROJECT_SUMMARY.md        # Резюме проекта
│   └── YOOKASSA_WEBHOOK_ARCHITECTURE.md
│
├── .gitignore                    # Глобальный .gitignore
└── README.md                     # Этот файл
```

---

## ⚡ Быстрый старт

### 1. Клонировать репозиторий

```bash
git clone https://github.com/YOUR_USERNAME/solid-simple-bot.git
cd solid-simple-bot/bot
```

### 2. Установить зависимости

```bash
pip install -r requirements.txt
```

### 3. Настроить конфигурацию

```bash
# Скопировать шаблон
cp .env.example .env

# Заполнить своими значениями
nano .env   # Linux/Mac
notepad .env  # Windows
```

### 4. Запустить

```bash
python main.py
```

---

## 🔧 Конфигурация (.env)

Скопируйте `.env.example` в `.env` и заполните:

| Переменная | Описание | Обязательно |
|-----------|----------|-------------|
| `BOT_TOKEN` | Токен бота от @BotFather | ✅ Да |
| `ADMIN_IDS` | Telegram ID администраторов | ✅ Да |
| `YOOKASSA_SHOP_ID` | Shop ID из кабинета ЮKassa | 💳 Для оплаты |
| `YOOKASSA_SECRET_KEY` | Секретный ключ ЮKassa | 💳 Для оплаты |
| `GIGACHAT_AUTH_KEY` | Ключ GigaChat из SberDevices | 🤖 Для AI |
| `YOOKASSA_RELAY_DB_*` | Данные MySQL для webhook | 🔔 Для уведомлений |
| `SMTP_*` | SMTP данные для email-чеков | 📧 Опционально |

> **⚠️ Важно:** Никогда не коммитьте `.env` в Git!

---

## 🏗️ Архитектура платежей

```
YooKassa API
    │
    ▼ HTTPS POST
webhook.php (на домене с SSL)
    │
    ▼ INSERT
MySQL: yookassa_notifications
    │
    ▼ Polling (каждые 20 сек)
Python Bot: yookassa_processor.py
    │
    ├── Обновляет SQLite (статус заказа)
    ├── Уведомляет администратора
    └── Отправляет email-чек клиенту
```

Подробнее: [YOOKASSA_WEBHOOK_ARCHITECTURE.md](docs/YOOKASSA_WEBHOOK_ARCHITECTURE.md)

---

## 📋 Требования

- **Python** 3.13+
- **pip** пакеты из `requirements.txt`
- **Telegram Bot Token** (получить у @BotFather)
- **YooKassa аккаунт** (для приёма платежей)
- **MySQL** (для production webhook relay)
- **Веб-хостинг с PHP + SSL** (для webhook.php)

---

## 🚀 Деплой на сервер

Подробная инструкция: [docs/DEPLOY.md](docs/DEPLOY.md)

Краткая схема:
1. Загрузить `bot/` на сервер (Python-хостинг или VPS)
2. Загрузить `webhook/yookassa/` на хостинг с PHP + SSL
3. Настроить `.env` на сервере
4. Запустить через `systemd` или `screen`

---

## 📦 Добавление товаров

Каталог товаров хранится в `bot/data/shop_data.json`.

Структура товара:
```json
{
  "id": "product_001",
  "name": "Название товара",
  "price": 1500,
  "description": "Описание...",
  "category": "Категория",
  "photos": ["photo_id_1", "photo_id_2"]
}
```

---

## 🔐 Безопасность

- ✅ Все секреты — только в `.env` (не в коде)
- ✅ `.env` добавлен в `.gitignore`
- ✅ Логи фильтруют PII и токены (`logging_filters.py`)
- ✅ Prepared statements для всех SQL-запросов
- ✅ Webhook аутентифицируется по токену
- ✅ Базы данных клиентов не в репозитории

---

## 📚 Документация

| Файл | Описание |
|------|----------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Архитектура всей системы |
| [DEPLOY.md](docs/DEPLOY.md) | Инструкция по деплою |
| [CHANGELOG.md](docs/CHANGELOG.md) | История версий |
| [PROJECT_SUMMARY.md](docs/PROJECT_SUMMARY.md) | Резюме проекта |
| [bot/HOW_TO_START.md](bot/HOW_TO_START.md) | Быстрый запуск |

---

## 🤝 Технологии

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) 21.10
- [YooKassa Python SDK](https://github.com/yoomoney/yookassa-sdk-python) 3.1
- [GigaChat API](https://developers.sber.ru/portal/products/gigachat) (Сбер)
- SQLite / MySQL
- PHP 8.x (webhook)

---

**Версия:** 3.4 | **Статус:** ✅ Production Ready
