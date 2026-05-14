# 🏗️ Архитектура проекта Solid Simple Bot

## 📊 Двухконтурная архитектура

```
┌─────────────────────────────────────────────────────────────┐
│                   TELEGRAM CLIENTS                          │
│   (пользователи пишут боту)                                 │
└──────────────────┬──────────────────────────────────────────┘
                   │ Telegram API
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  КОНТУР 1: Telegram Bot (ВАШ_ХОСТИНГ.ru)                 │
│  ──────────────────────────────────────────────────────     │
│  ✓ Polling режим (получает сообщения от Telegram)          │
│  ✓ AI-консультанты (GigaChat)                               │
│  ✓ Каталог, корзина, оформление заказов                     │
│  ✓ SQLite база (shop.db) - заказы, пользователи            │
│  ✓ MySQL Client - читает yookassa_notifications            │
└──────────────────┬──────────────────────────────────────────┘
                   │ Чтение MySQL (polling каждые 20 сек)
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  ОБЩАЯ БАЗА ДАННЫХ: MySQL                                   │
│  ──────────────────────────────────────────────────────     │
│  Таблица: yookassa_notifications                            │
│  - payment_id, event_type, payload, order_id               │
│  - processed (флаг обработки)                               │
│  ← Доступ с обоих доменов!                                  │
└──────────────────┬──────────────────────────────────────────┘
                   │ Запись при уведомлении
                   ▲
┌─────────────────────────────────────────────────────────────┐
│  КОНТУР 2: YooKassa Webhook (solidsimple.ru)               │
│  ──────────────────────────────────────────────────────     │
│  ✓ HTTPS (SSL сертификат Let's Encrypt)                     │
│  ✓ webhook.php принимает уведомления от YooKassa           │
│  ✓ Проверка подписи (HMAC-SHA256)                          │
│  ✓ Запись в MySQL + резервная очередь JSON                 │
│  ✓ Идемпотентность (UNIQUE payment_id)                     │
└──────────────────┬──────────────────────────────────────────┘
                   │ HTTPS POST
                   ▼
┌─────────────────────────────────────────────────────────────┐
│                    YOOKASSA API                             │
│   (уведомления о платежах)                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎯 Почему такая архитектура?

### Проблема:
1. **YooKassa требует HTTPS** для webhook URL
2. **Бесплатный хостинг (xsph.ru)** не позволяет установить SSL
3. **Нужно принимать платежи** и обрабатывать их в боте

### Решение:
**Разделить на два независимых контура:**

| Контур | Домен | Задача |
|--------|-------|--------|
| **Bot** | ВАШ_ХОСТИНГ.ru | Общение с пользователями, AI, витрина |
| **Webhook** | solidsimple.ru | Приём HTTPS уведомлений от YooKassa |
| **MySQL** | localhost | Связующее звено (общая БД) |

---

## 🔄 Поток данных

### Сценарий: Клиент оплачивает заказ

```
1. Клиент → Бот → Создаёт заказ №12345
                ↓
         SQLite: order(id=12345, status='pending')
                ↓
         Генерация ссылки на оплату YooKassa
                ↓
2. Клиент → Оплачивает по ссылке
                ↓
3. YooKassa → HTTPS POST → webhook.php (solidsimple.ru)
                ↓
         Проверка HMAC подписи
                ↓
         INSERT INTO yookassa_notifications 
           (payment_id='abc123', order_id='12345', ...)
                ↓
4. Бот (каждые 20 сек) → SELECT FROM yookassa_notifications 
                         WHERE processed=0
                ↓
         Распарсивает payload
                ↓
         UPDATE order SET payment_id='abc123', status='paid'
                ↓
         Уведомление админам
         Email клиенту
                ↓
         UPDATE yookassa_notifications SET processed=1
```

---

## 📁 Структура проекта

```
solid-simple-bot/
├── main.py                           # Точка входа
├── .env                              # Конфиг (не коммить!)
├── .env.server.example              # Пример для сервера
├── requirements.txt                  # Зависимости
│
├── core/                             # Ядро
│   ├── config.py                    # Конфигурация
│   ├── database.py                  # SQLite (заказы, корзина)
│   ├── checkout_flow.py             # Оформление заказа
│   ├── telegram_payments.py         # Telegram Payments
│   ├── yookassa_processor.py        # Обработка платежей из MySQL
│   ├── notifications.py             # Уведомления
│   ├── ai_assistant.py              # AI-консультант
│   └── ...
│
├── handlers/                         # Обработчики
│   ├── navigation_handlers.py       # Кнопки, навигация
│   ├── checkout_handlers.py         # Оформление заказа
│   ├── payment_handlers.py          # Платежи
│   ├── ai_handlers.py               # AI вопросы
│   └── legal_handlers.py            # Оферта, политика
│
├── keyboards/                        # Клавиатуры
│   └── user_keyboards.py
│
├── messages/                         # Тексты
│   └── templates.py
│
├── data/                             # Данные
│   ├── shop_data.json               # Товары, каталог
│   └── voice_cache/                 # (удалено в ver.3.2)
│
├── photos/                           # Изображения
│   ├── site/
│   ├── Лого/
│   └── ...
│
├── logs/                             # Логи
│   ├── bot.log
│   └── ...
│
├── solidsimple.ru/                   # ВЕБХУК КОНТУР
│   └── public_html/
│       └── yookassa/
│           ├── webhook.php          # Приём уведомлений
│           ├── .env                 # Конфиг вебхука
│           └── yookassa_webhook.log # Логи
│
├── shop.db                           # SQLite БД
├── dump.sql                          # Дамп БД
├── DEPLOY.md                         # Инструкция деплоя
├── YOOKASSA_WEBHOOK_ARCHITECTURE.md  # Документация вебхука
└── README.md                         # Основная документация
```

---

## 🔐 Безопасность

### Уровень 1: Webhook (solidsimple.ru)

```php
// webhook.php проверяет:
1. HMAC-SHA256 подпись от YooKassa
2. ИЛИ токен в URL (?token=...)
3. ИЛИ X-Forward-Secret header
4. ИЛИ IP из whitelist
```

### Уровень 2: MySQL Relay

```python
# Бот читает только необработанные уведомления
SELECT * FROM yookassa_notifications WHERE processed=0

# UNIQUE(payment_id) предотвращает дубли
INSERT IGNORE INTO yookassa_notifications (...)
```

### Уровень 3: SQLite

```python
# Транзакции для целостности данных
with db.transaction():
    db.update_order(order_id, payment_id, 'paid')
    db.add_order_items(...)
```

### Уровень 4: Секреты

```bash
# Все секреты в .env (не в коде!)
BOT_TOKEN=...
YOOKASSA_SECRET_KEY=...
FORWARD_SECRET=...

# Права доступа
chmod 600 .env
```

---

## 🛡️ Отказоустойчивость

### Если MySQL недоступна:
```php
// webhook.php пишет в JSON очередь
data/yookassa_queue/notif_payment_id_timestamp.json
```

### Если бот упал:
```bash
# Systemd автоматически перезапустит
Restart=always
RestartSec=10
```

### Если webhook не дошёл:
```sql
-- Можно найти все необработанные
SELECT * FROM yookassa_notifications WHERE processed=0;

-- Обработать вручную
python replay_yookassa.py
```

---

## 📈 Масштабируемость

### Можно легко добавить:

1. **Несколько ботов** → читают одну MySQL
2. **Шардирование SQLite** → разные пользователи → разные файлы shop.db
3. **Redis вместо MySQL** → быстрее polling
4. **WebSocket** → мгновенные уведомления вместо polling
5. **Микросервисы** → выделить AI, платежи, доставку отдельно

---

## ⚙️ Режимы работы

### Локальная разработка:
```ini
MODE=polling
DATABASE_TYPE=sqlite
YOOKASSA_RELAY_DB_HOST=localhost  # или внешний MySQL
```

### Сервер (production):
```ini
MODE=webhook
DATABASE_TYPE=mysql
WEBHOOK_URL=https://solidsimple.ru/webhook
SSL_CERT=/etc/letsencrypt/live/solidsimple.ru/fullchain.pem
```

### Гибридный вариант (текущий):
```
Webhook.php на solidsimple.ru (SSL есть)
Бот на ВАШ_ХОСТИНГ.ru (polling MySQL)
SQLite для заказов + MySQL для платежей
```

---

## 🎓 Ключевые компоненты

### 1. `webhook.php` (PHP)
- Принимает HTTPS уведомления
- Проверяет HMAC подпись
- Пишет в MySQL идемпотентно
- Создаёт JSON backup

### 2. `yookassa_processor.py` (Python)
- Опрашивает MySQL каждые 20 сек
- Распарсивает payload
- Обновляет SQLite
- Уведомляет админов
- Отправляет чеки

### 3. `database.py` (SQLite)
- Хранит заказы
- Корзину пользователей
- Сессии
- Контекст AI

### 4. `main.py`
- Запускает бота
- Регистрирует обработчики
- Запускает polling задачу для YooKassa

---

## 📝 Важные замечания

### ✅ Преимущества:
- Разделение ответственности (webhook ≠ бот)
- SSL termination на отдельном домене
- Идемпотентность платежей
- Резервное сохранение (MySQL + JSON)
- Простота масштабирования

### ⚠️ Ограничения:
- Задержка обработки (polling interval 20 сек)
- Зависимость от доступности MySQL
- Нужна синхронизация секретов между доменами

### 🔧 Улучшения в будущем:
- WebSocket вместо polling
- Redis queue вместо MySQL
- Event-driven архитектура
- GraphQL API для фронтенда

---

**Версия:** 3.2  
**Дата:** 28.03.2026  
**Статус:** Production Ready
