# Архитектура YooKassa Webhook Relay

## 📋 Общая схема работы

```
┌─────────────────────────────────────────────────────────────┐
│                     YooKassa API                            │
│  (отправляет HTTPS уведомления о платежах)                  │
└──────────────────┬──────────────────────────────────────────┘
                   │ HTTPS POST
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  solidsimple.ru/public_html/yookassa/webhook.php           │
│  ─────────────────────────────────────────────────────      │
│  ✅ Принимает уведомления по HTTPS (SSL сертификат)         │
│  ✅ Проверяет подлинность (HMAC/Token/Header)               │
│  ✅ Сохраняет в MySQL базу (yookassa_notifications)         │
│  ✅ Создаёт резервную копию в JSON-файлы (очередь)          │
└──────────────────┬──────────────────────────────────────────┘
                   │ Запись в БД
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  MySQL База Данных                                         │
│  Таблица: yookassa_notifications                           │
│  - id, payment_id, event_type, payload, order_id           │
│  - processed (0/1), processed_at, processed_by             │
│  - UNIQUE KEY uq_payment (payment_id) ← идемпотентность    │
└──────────────────┬──────────────────────────────────────────┘
                   │ Polling (каждые 5 сек)
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  Бот (Python) → core/yookassa_processor.py                 │
│  ─────────────────────────────────────────────────────      │
│  ✅ Опрашивает MySQL: SELECT WHERE processed=0              │
│  ✅ Распарсивает payload                                    │
│  ✅ Обновляет SQLite (order.payment_id, status)             │
│  ✅ Уведомляет админов (Telegram)                           │
│  ✅ Отправляет чек клиенту (Email)                          │
│  ✅ Помечает как processed=1                                │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  SQLite База Данных (shop.db)                              │
│  Таблица: "order"                                          │
│  - payment_id ← обновляется из MySQL                       │
│  - status = 'paid'                                         │
│  - order_data (JSON с товарами)                            │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔐 Почему такая архитектура?

### Проблема:
- **YooKassa требует HTTPS** для webhook URL
- **Бесплатный хостинг (ВАШ_ХОСТИНГ.ru)** не позволяет установить SSL сертификат
- **Решение:** Использовать domain с SSL (solidsimple.ru) как прокси

### Решение:
1. **Webhook.php на solidsimple.ru** принимает HTTPS уведомления
2. **Сохраняет в общую MySQL БД** (доступна с обоих доменов)
3. **Бот на ВАШ_ХОСТИНГ.ru** забирает уведомления из MySQL
4. **Обрабатывает платежи** и обновляет SQLite

---

## 🗄️ Структура таблицы `yookassa_notifications`

```sql
CREATE TABLE IF NOT EXISTS yookassa_notifications (
  id INT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  payment_id VARCHAR(255),
  event_type VARCHAR(255),
  payload LONGTEXT,
  order_id VARCHAR(255),
  processed TINYINT(1) DEFAULT 0,
  processed_at TIMESTAMP NULL,
  processed_by VARCHAR(100) NULL,
  UNIQUE KEY uq_payment (payment_id)  ← Защита от дублей!
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### Поля:
- `id` - уникальный идентификатор записи
- `created_at` - время получения уведомления
- `payment_id` - ID платежа из YooKassa
- `event_type` - тип события (payment.succeeded, payment.canceled, etc.)
- `payload` - полный JSON уведомление
- `order_id` - ID заказа из metadata
- `processed` - флаг обработки (0/1)
- `processed_at` - время обработки
- `processed_by` - кто обработал (username бота)

---

## 🔐 Аутентификация webhook.php

Webhook поддерживает **3 способа проверки подлинности**:

### 1. Token в URL
```
https://ВАШ_ДОМЕН/yookassa/webhook.php?token=ВАШ_WEBHOOK_TOKEN
```

### 2. X-Forward-Secret Header
```php
Headers:
  X-Forward-Secret: F0rw@rd$ecret_2025
```

### 3. HMAC-SHA256 Signature (от YooKassa)
```php
Headers:
  X-Hook-Signature: <base64 или hex подпись>
  
Проверка:
  $computed = hash_hmac('sha256', $raw_body, $yookassa_secret);
  if (hash_equals($computed, $received_signature)) → OK
```

### 4. IP Whitelist (опционально)
```php
YOOKASSA_ALLOWED_IP_RANGES=185.71.76.0/22,185.71.80.0/22
```

---

## 📦 Конфигурация (.env)

### Для webhook.php (ваш_домен/public_html/yookassa/.env):
```env
# Секреты аутентификации
# Генерация: openssl rand -base64 32
YOOKASSA_WEBHOOK_TOKEN=ВАШ_СЛУЧАЙНЫЙ_ТОКЕН

# Генерация: openssl rand -hex 16
FORWARD_SECRET=ВАШ_СЛУЧАЙНЫЙ_СЕКРЕТ

# Из кабинета ЮKassa → Интеграция → HTTP-уведомления
YOOKASSA_WEBHOOK_SECRET=live_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX

# Доступ к MySQL (общая для обоих доменов)
YOOKASSA_RELAY_DB_HOST=localhost
YOOKASSA_RELAY_DB_USER=ваш_mysql_пользователь
YOOKASSA_RELAY_DB_PASS=ваш_mysql_пароль
YOOKASSA_RELAY_DB_NAME=ваша_mysql_база

# Опционально: Telegram уведомления
BOT_TOKEN=ВАШ_BOT_TOKEN_ОТ_BOTFATHER
ADMIN_IDS=ВАШ_TELEGRAM_ID,ВТОРОЙ_ADMIN_ID
```

### Для бота (public_html/.env):
```env
# Должны СОВПАДАТЬ со значениями в webhook .env!
YOOKASSA_WEBHOOK_TOKEN=ТОТ_ЖЕ_СЛУЧАЙНЫЙ_ТОКЕН
FORWARD_SECRET=ТОТ_ЖЕ_СЛУЧАЙНЫЙ_СЕКРЕТ

# Те же MySQL доступы
YOOKASSA_RELAY_DB_HOST=localhost
YOOKASSA_RELAY_DB_USER=ваш_mysql_пользователь
YOOKASSA_RELAY_DB_PASS=ваш_mysql_пароль
YOOKASSA_RELAY_DB_NAME=ваша_mysql_база

# Интервал опроса (секунды)
YOOKASSA_RELAY_POLL_INTERVAL=5
```

---

## 🔄 Поток данных при оплате

### Шаг 1: Клиент оплачивает заказ
```
Клиент → Telegram Bot → Создаёт платёж YooKassa
                        ↓
                   Payment ID: 2d3f5e6g-7h8i-9j0k
                   Order ID: 12345
```

### Шаг 2: YooKassa отправляет уведомление
```
YooKassa → HTTPS POST → solidsimple.ru/yookassa/webhook.php
         
Payload:
{
  "event": "payment.succeeded",
  "object": {
    "id": "2d3f5e6g-7h8i-9j0k",
    "status": "succeeded",
    "amount": {"value": "1000.00", "currency": "RUB"},
    "metadata": {
      "order_id": "12345",
      "items": [...],
      "contact": {...}
    }
  }
}
```

### Шаг 3: Webhook.php обрабатывает
```php
✅ Проверяет подпись (HMAC или token)
✅ Извлекает payment_id, event_type, payload, order_id
✅ Делает INSERT IGNORE в yookassa_notifications
✅ Если БД недоступна → пишет в JSON-файл (очередь)
✅ Возвращает HTTP 200 "ok" YooKassa
```

### Шаг 4: Бот опрашивает MySQL
```python
# core/yookassa_processor.py
async def poll_yookassa_notifications(application):
    while True:
        conn = _get_mysql_conn()
        cur = conn.cursor()
        
        # Берём 50 необработанных уведомлений
        cur.execute("""
            SELECT * FROM yookassa_notifications 
            WHERE processed=0 
            ORDER BY id ASC 
            LIMIT 50
        """)
        
        rows = cur.fetchall()
        
        for row in rows:
            await process_single_row(application, row)
            
            # Помечаем как обработанное
            cur.execute("""
                UPDATE yookassa_notifications 
                SET processed=1, processed_at=NOW(), processed_by=%s 
                WHERE id=%s
            """, (bot_username, row['id']))
        
        conn.commit()
        await asyncio.sleep(POLL_INTERVAL)  # 5 секунд
```

### Шаг 5: Обработка одного уведомления
```python
async def process_single_row(application, row):
    payload = json.loads(row['payload'])
    order_id = row['order_id']
    payment_id = row['payment_id']
    
    # 1. Обновляем заказ в SQLite
    order = db.get_order(order_id)
    if order:
        db.update_order_payment(order_id, payment_id, 'paid')
    
    # 2. Уведомляем админов
    await notify_admins_about_payment(application.bot, order, payload)
    
    # 3. Отправляем чек клиенту (email)
    if customer_email:
        _send_email_receipt(customer_email, "Чек оплаты", html_body)
    
    # 4. Помечаем как processed
    mark_as_processed(row['id'])
```

---

## 🛡️ Безопасность

### Что защищает webhook.php:

1. **Аутентификация источников**
   - Только YooKassa или доверенные источники могут отправить уведомления
   - 3 метода проверки + IP whitelist

2. **Идемпотентность**
   - `UNIQUE KEY uq_payment (payment_id)` предотвращает дубли
   - `INSERT IGNORE` игнорирует повторные уведомления
   - Флаг `processed` отслеживает обработку

3. **Логирование без секретов**
   - Пишет только preview payload (2000 символов)
   - Не сохраняет токены/секреты в логах
   - Отдельный debug лог для отладки

4. **Fallback очередь**
   - Если MySQL недоступен → пишет JSON в `data/yookassa_queue/`
   - Позже можно обработать вручную

5. **Защита от XSS/Injection**
   - Prepared statements в SQL
   - Экранирование данных
   - Safe logging функций

---

## 🎯 Преимущества этой архитектуры

| Преимущество | Описание |
|-------------|----------|
| **SSL Termination** | Webhook принимает HTTPS, бот работает локально |
| **Гибкость** | Бот может быть где угодно (локально, VPS, бесплатный хостинг) |
| **Надёжность** | Двойное сохранение (MySQL + JSON очередь) |
| **Идемпотентность** | Защита от дублей платежей |
| **Масштабируемость** | Можно несколько ботов, читающих одну БД |
| **Безопасность** | Многоуровневая проверка источников |

---

## 🚀 Миграция на новый сервер

Когда арендуете платный сервер:

### Вариант 1: Оставить webhook на solidsimple.ru
```
✅ Webhook.php остаётся на текущем месте
✅ MySQL остаётся там же
✅ Бот переезжает на новый сервер → читает ту же MySQL
```

**Изменения:**
- Обновить `YOOKASSA_RELAY_DB_HOST` (если MySQL на новом сервере)
- Настроить polling на новом сервере

### Вариант 2: Перенести всё на новый сервер
```
1. Поднять SSL на новом домене
2. Развернуть webhook.php там
3. Перенести MySQL
4. Обновить YooKassa настройки (webhook URL)
```

---

## 📝 Файлы проекта

### Webhook приёмник:
- `solidsimple.ru/public_html/yookassa/webhook.php` - основной файл
- `solidsimple.ru/public_html/yookassa/.env` - конфигурация
- `solidsimple.ru/public_html/yookassa/yookassa_webhook.log` - логи
- `solidsimple.ru/public_html/yookassa/webhook_debug.log` - debug логи
- `solidsimple.ru/public_html/data/yookassa_queue/` - JSON очередь

### Бот процессор:
- `bot/core/yookassa_processor.py` - polling и обработка
- `bot/main.py` - запуск polling задачи
- `bot/.env` - конфигурация бота (создаётся из .env.example)

### База данных:
- MySQL: `ваша_база.yookassa_notifications`
- SQLite: `shop.db.table "order"`

---

## ⚠️ Важные замечания

1. **Нельзя удалять webhook.php** - это критический компонент для приёма платежей!
2. **MySQL должна быть доступна** с обоих доменов
3. **Секреты должны совпадать** в обоих `.env` файлах
4. **Polling interval** ставьте 5-20 секунд (баланс нагрузка/скорость)
5. **Всегда делайте backup** `yookassa_notifications` перед чисткой

---

## 🔧 Отладка

### Проверить последние уведомления:
```sql
SELECT * FROM yookassa_notifications ORDER BY id DESC LIMIT 10;
```

### Найти необработанные:
```sql
SELECT * FROM yookassa_notifications WHERE processed=0;
```

### Посмотреть логи webhook:
```bash
tail -f solidsimple.ru/public_html/yookassa/yookassa_webhook.log
```

### Тестировать webhook локально:
```bash
curl -X POST https://solidsimple.ru/yookassa/webhook.php?token=YOUR_TOKEN \
  -H "Content-Type: application/json" \
  -d '{"event":"payment.succeeded","object":{"id":"test123"}}'
```

---

**Документ создан:** 28.03.2026  
**Версия:** 1.0  
**Автор:** Solid Simple Team
