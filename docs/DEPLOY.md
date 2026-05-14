# 🚀 Инструкция по быстрому деплою на сервер

## ⏱️ Время развёртывания: 15-30 минут

Эта инструкция предполагает, что вы уже протестировали бота локально и теперь хотите развернуть его на платном сервере.

---

## 📋 Предварительные требования

### Что должно быть готово ДО начала:

✅ **Сервер арендован** (VPS или хостинг с поддержкой Python + MySQL + SSL)  
✅ **Домен привязан** (например, `solidsimple.ru`)  
✅ **SSL сертификат установлен** (Let's Encrypt - бесплатно)  
✅ **MySQL создан** (или будете использовать внешний)  
✅ **Telegram Bot Token получен** (@BotFather)  
✅ **YooKassa магазин зарегистрирован**  

---

## 📦 Этап 1: Подготовка файлов (5 минут)

### 1.1 Скопируйте проект на сервер

```bash
# На вашем компьютере
scp -r solid-simple-bot user@your-server.com:/var/www/solid-simple-bot
```

Или через Git:

```bash
# На сервере
cd /var/www
git clone git@github.com:username/solid-simple-bot.git
```

### 1.2 Создайте структуру папок

```bash
cd /var/www/solid-simple-bot

# Проверка структуры
ls -la
# Должны увидеть:
# - main.py
# - core/
# - handlers/
# - data/
# - .env.example
```

---

## 🔧 Этап 2: Настройка окружения (10 минут)

### 2.1 Установите зависимости

```bash
# Создайте виртуальное окружение
python3 -m venv .venv

# Активируйте
source .venv/bin/activate

# Установите зависимости
pip install -r requirements.txt
```

> **Примечание:** Если `requirements.txt` нет, используйте список из README.md

### 2.2 Создайте .env файл

```bash
# Скопируйте пример
cp .env.example .env

# Отредактируйте под себя
nano .env
```

### 2.3 Заполните .env

```ini
# === TELEGRAM ===
BOT_TOKEN=ВАШ_BOT_TOKEN_ОТ_BOTFATHER
ADMIN_IDS=ВАШ_TELEGRAM_ID,ВТОРОЙ_ADMIN_ID

# === SHOP CONFIG ===
SHOP_NAME=Solid Simple
CURRENCY=RUB

# === DATABASE ===
DATABASE_TYPE=mysql  # или sqlite для тестов
MYSQL_HOST=localhost
MYSQL_USER=ваш_mysql_пользователь
MYSQL_PASS=ваш_надёжный_пароль
MYSQL_DB=название_вашей_бд

# === YOOKASSA ===
YOOKASSA_SHOP_ID=ваш_shop_id
YOOKASSA_SECRET_KEY=ваш_secret_key
YOOKASSA_WEBHOOK_TOKEN=случайная_строка
FORWARD_SECRET=случайная_строка
YOOKASSA_WEBHOOK_SECRET=из_кабинета_Yookassa

# === WEBHOOK MODE ===
MODE=webhook  # polling для локальной разработки
WEBHOOK_PORT=8443
WEBHOOK_URL=https://solidsimple.ru/webhook
SSL_CERT=/etc/letsencrypt/live/solidsimple.ru/fullchain.pem
SSL_KEY=/etc/letsencrypt/live/solidsimple.ru/privkey.pem

# === LOGS ===
LOG_LEVEL=INFO
```

### 2.4 Инициализируйте базу данных

```bash
# Создайте MySQL БД
mysql -u root -p
CREATE DATABASE solid_simple CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'solid_simple'@'localhost' IDENTIFIED BY 'пароль';
GRANT ALL PRIVILEGES ON solid_simple.* TO 'solid_simple'@'localhost';
FLUSH PRIVILEGES;
EXIT;

# Или импортируйте дамп (если есть)
mysql -u root -p solid_simple < dump.sql
```

---

## 🔐 Этап 3: Настройка SSL и Nginx (10 минут)

### 3.1 Установите SSL сертификат (Let's Encrypt)

```bash
# Установите Certbot
sudo apt update
sudo apt install certbot python3-certbot-nginx

# Получите сертификат
sudo certbot --nginx -d solidsimple.ru -d www.solidsimple.ru
```

Certbot автоматически:
- Создаст сертификаты в `/etc/letsencrypt/live/solidsimple.ru/`
- Настроит Nginx для HTTPS
- Настроит автопродление

### 3.2 Проверьте конфиг Nginx

```bash
sudo nano /etc/nginx/sites-available/solidsimple.ru
```

Должно быть примерно так:

```nginx
server {
    listen 80;
    server_name solidsimple.ru www.solidsimple.ru;
    
    # Redirect HTTP → HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name solidsimple.ru www.solidsimple.ru;
    
    ssl_certificate /etc/letsencrypt/live/solidsimple.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/solidsimple.ru/privkey.pem;
    
    root /var/www/solid-simple-bot;
    
    location /webhook {
        proxy_pass http://127.0.0.1:8443;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    location /yookassa {
        # PHP webhook для YooKassa (если используете)
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/php-fpm.sock;
    }
}
```

### 3.3 Перезапустите Nginx

```bash
sudo nginx -t
sudo systemctl restart nginx
```

---

## 🤖 Этап 4: Настройка и запуск бота (5 минут)

### 4.1 Создайте systemd сервис

```bash
sudo nano /etc/systemd/system/solid-simple-bot.service
```

Вставьте содержимое:

```ini
[Unit]
Description=Solid Simple Telegram Bot
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/var/www/solid-simple-bot
Environment="PATH=/var/www/solid-simple-bot/.venv/bin"
ExecStart=/var/www/solid-simple-bot/.venv/bin/python main.py
Restart=always
RestartSec=10

# Logging
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 4.2 Включите и запустите сервис

```bash
# Перечитайте systemd
sudo systemctl daemon-reload

# Включите автозагрузку
sudo systemctl enable solid-simple-bot

# Запустите
sudo systemctl start solid-simple-bot

# Проверьте статус
sudo systemctl status solid-simple-bot
```

### 4.3 Настройте webhook в Telegram

```bash
# Установите webhook URL
curl -X POST "https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook?url=https://solidsimple.ru/webhook"
```

Или через Python:

```bash
python -c "
import requests
TOKEN = 'YOUR_BOT_TOKEN'
URL = 'https://solidsimple.ru/webhook'
r = requests.get(f'https://api.telegram.org/bot{TOKEN}/setWebhook?url={URL}')
print(r.json())
"
```

---

## ✅ Этап 5: Проверка работы (5 минут)

### 5.1 Проверьте логи

```bash
# Логи systemd
sudo journalctl -u solid-simple-bot -f

# Логи бота (если есть)
tail -f /var/www/solid-simple-bot/logs/bot.log
```

### 5.2 Протестируйте бота

1. Откройте Telegram
2. Найдите своего бота
3. Нажмите `/start`
4. Проверьте ответ

### 5.3 Проверьте webhook

```bash
# Проверка статуса webhook
curl "https://api.telegram.org/botYOUR_BOT_TOKEN/getWebhookInfo"
```

Должно вернуть:

```json
{
  "ok": true,
  "result": {
    "url": "https://solidsimple.ru/webhook",
    "has_custom_certificate": false,
    "pending_update_count": 0,
    "last_error_date": 0,
    "last_error_message": "",
    "max_connections": 40,
    "ip_address": "X.X.X.X"
  }
}
```

### 5.4 Тестовый платёж

1. Создайте тестовый заказ в боте
2. Оплатите через YooKassa (тестовый режим)
3. Проверьте логи webhook:
   ```bash
   tail -f /var/www/solid-simple-bot/yookassa/yookassa_webhook.log
   ```
4. Убедитесь что платеж обработан

---

## 🔍 Troubleshooting

### Бот не отвечает

```bash
# Проверьте статус
sudo systemctl status solid-simple-bot

# Перезапустите
sudo systemctl restart solid-simple-bot

# Проверьте логи
sudo journalctl -u solid-simple-bot -n 50
```

### Ошибка SSL

```bash
# Проверьте сертификат
sudo certbot certificates

# Продлите если нужно
sudo certbot renew
```

### Webhook не работает

```bash
# Проверьте что порт открыт
sudo netstat -tlnp | grep 8443

# Проверьте Nginx
sudo nginx -t
sudo systemctl status nginx

# Проверьте firewall
sudo ufw status
sudo ufw allow 8443/tcp
```

### MySQL не подключается

```bash
# Проверьте что MySQL запущен
sudo systemctl status mysql

# Проверьте доступы
mysql -u solid_simple -p -h localhost solid_simple
```

---

## 📊 Мониторинг

### Настройте логирование

```bash
# Создайте директорию для логов
sudo mkdir -p /var/log/solid-simple-bot
sudo chown www-data:www-data /var/log/solid-simple-bot

# Настройте logrotate
sudo nano /etc/logrotate.d/solid-simple-bot
```

```
/var/log/solid-simple-bot/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 www-data www-data
    postrotate
        systemctl reload solid-simple-bot > /dev/null 2>&1 || true
    endscript
}
```

### Автоматический рестарт при ошибках

Systemd уже настроен на автоматический рестарт (`Restart=always`).

Проверьте настройки:

```bash
systemctl show solid-simple-bot | grep Restart
```

---

## 🎯 Следующие шаги после деплоя

### 1. Настройте резервное копирование

```bash
# Скрипт backup
sudo nano /usr/local/bin/backup-solid-simple.sh
```

```bash
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
mysqldump -u solid_simple -pPASSWORD solid_simple > /backups/solid_simple_$DATE.sql
tar -czf /backups/solid_simple_files_$DATE.tar.gz /var/www/solid-simple-bot/data
find /backups -name "solid_simple_*.sql" -mtime +7 -delete
find /backups -name "solid_simple_*.tar.gz" -mtime +7 -delete
```

```bash
# Cron для ежедневного backup
crontab -e
# 0 3 * * * /usr/local/bin/backup-solid-simple.sh
```

### 2. Настройте мониторинг

Установите simple monitoring:

```bash
# Установите htop, nmon
sudo apt install htop nmon

# Или более продвинутый NetData
bash <(curl -Ss https://my-netdata.io/kickstart.sh)
```

### 3. Обновление бота

```bash
# Если через Git
cd /var/www/solid-simple-bot
git pull origin main
sudo systemctl restart solid-simple-bot

# Если вручную
# Скопируйте файлы через SCP и перезапустите
```

---

## 📝 Чек-лист успешного деплоя

- [ ] Сервер настроен (Python 3.13+, MySQL, Nginx)
- [ ] SSL сертификат установлен и работает
- [ ] Зависимости установлены (`pip install -r requirements.txt`)
- [ ] `.env` заполнен правильными значениями
- [ ] База данных создана и доступна
- [ ] Systemd сервис создан и запущен
- [ ] Webhook URL установлен в Telegram
- [ ] Бот отвечает на `/start`
- [ ] Тестовый платёж прошёл успешно
- [ ] Логи пишутся без ошибок
- [ ] Резервное копирование настроено

---

## 🆘 Если что-то пошло не так

### Контакты для поддержки:

- **Telegram Bot API:** https://t.me/BotSupport
- **YooKassa техподдержка:** https://yookassa.ru/support
- **Python-telegram-bot:** https://docs.python-telegram-bot.org/

### Полезные команды для диагностики:

```bash
# Полная проверка системы
sudo systemctl status solid-simple-bot
sudo journalctl -u solid-simple-bot -n 100
curl -I https://solidsimple.ru
mysql -u solid_simple -p -e "SHOW TABLES;" solid_simple
python3 -c "import telegram; print(telegram.__version__)"
```

---

**Версия инструкции:** 1.0  
**Последнее обновление:** 28.03.2026  
**Время развёртывания:** 15-30 минут
