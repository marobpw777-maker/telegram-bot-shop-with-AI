# 🚀 Инструкция по запуску бота

## 📋 Быстрая справка

**Текущая версия:** 3.4  
**Статус:** ✅ PRODUCTION READY  
**Режим работы:** Polling (локальная разработка)

---

## ⚡ Быстрый старт (для опытных)

```bash
# Перейти в директорию бота
cd путь/к/папке/bot

# Запустить бота
python main.py
```

---

## 📖 Подробная инструкция

### Шаг 1: Откройте терминал

**Windows PowerShell:**
1. Нажмите `Win + R`
2. Введите `powershell`
3. Нажмите `Enter`

**Или через проводник:**
1. Откройте папку с ботом
2. Зажмите `Shift` и кликните правой кнопкой мыши
3. Выберите "Открыть окно PowerShell здесь"

---

### Шаг 2: Перейдите в директорию бота

```powershell
cd путь/к/папке/bot
```

> **Совет:** В PowerShell можно использовать автодополнение по `Tab`

---

### Шаг 3: Проверьте зависимости (один раз)

```powershell
# Проверка версии Python
python --version
# Должно быть: Python 3.13.x

# Проверка установленных пакетов
pip list | findstr python-telegram-bot
# Должно быть: python-telegram-bot >= 22.7
```

Если пакеты не установлены:
```powershell
pip install python-telegram-bot python-dotenv httpx aiohttp requests PyMySQL yookassa cryptography
```

---

### Шаг 4: Запустите бота

```powershell
python main.py
```

---

### Шаг 5: Проверьте работу

Вы должны увидеть в логе:
```
INFO - 🚀 Запуск бота Solid Simple (main.py)
INFO - ✅ База данных успешно инициализирована
INFO - Registering legal_handlers...
INFO - ✅ Все обработчики зарегистрированы. Запуск polling...
INFO - ✅ Бот успешно запущен и post_init выполняется.
```

---

## 🔧 Управление ботом

### ▶️ Запуск

```powershell
# Способ 1: Из текущей директории
python main.py

# Способ 2: С полным путём
python "путь/к/папке/bot/main.py"
```

### ⏹️ Остановка

**Способ 1 (рекомендуется):**
- Нажмите `Ctrl + C` в терминале
- Подтвердите остановку если потребуется

**Способ 2 (принудительно):**
```powershell
# Найти процесс Python
Get-Process python

# Остановить все процессы Python
Stop-Process -Name python -Force

# Или выбрать только процессы из вашей папки
Get-Process python | Where-Object {$_.Path -like "*MyProject*"} | Stop-Process -Force
```

**Способ 3 (через диспетчер задач):**
1. `Ctrl + Shift + Esc` → Диспетчер задач
2. Найдите "Python" или "python.exe"
3. Правой кнопкой → "Снять задачу"

---

## 🎯 Режимы работы

### Локальная разработка (текущий режим)

**Конфигурация в .env:**
```ini
MODE=polling
DATABASE_TYPE=sqlite
```

**Что работает:**
- ✅ Telegram Bot API (polling)
- ✅ SQLite база данных
- ✅ AI-консультанты (GigaChat)
- ✅ YooKassa payments (через MySQL relay)

**Ограничения:**
- ⚠️ MySQL недоступен локально (fallback на память)
- ⚠️ GigaChat может возвращать 402/404 (это нормально)

---

### Production сервер (будущий режим)

**Конфигурация в .env:**
```ini
MODE=webhook
DATABASE_TYPE=mysql
WEBHOOK_URL=https://solidsimple.ru/webhook
```

**Что будет работать:**
- ✅ Webhook вместо polling
- ✅ MySQL база данных
- ✅ SSL сертификат
- ✅ Прямая интеграция YooKassa

---

## 📊 Диагностика

### Проверка конфигурации

```powershell
# Проверить .env файл
cat .env | Select-String "BOT_TOKEN|MODE"

# Протестировать конфигурацию
python -c "from core.config import validate_config; print('OK' if validate_config() else 'ERROR')"
```

### Проверка базы данных

```powershell
# Проверить наличие SQLite базы
ls shop.db

# Проверить структуру таблиц
python -c "import sqlite3; conn = sqlite3.connect('shop.db'); print([t[0] for t in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()])"
```

### Проверка логов

```powershell
# Последние 20 строк лога
Get-Content logs/bot.log -Tail 20

# Следить за логом в реальном времени
Get-Content logs/bot.log -Wait -Tail 10
```

---

## 🐛 Решение проблем

### Ошибка: "No module named 'telegram'"

**Решение:**
```powershell
pip install python-telegram-bot
```

### Ошибка: "BOT_TOKEN not configured"

**Решение:**
1. Проверьте файл `.env`
2. Убедитесь что `BOT_TOKEN` указан
3. Перезапустите бота

### Ошибка: "Address already in use"

**Причина:** Бот уже запущен

**Решение:**
```powershell
# Остановить существующий процесс
Get-Process python | Stop-Process -Force

# Запустить заново
python main.py
```

### Ошибка: "SQLite error"

**Решение:**
```powershell
# Проверить права доступа к файлу БД
ls shop.db

# Если файл заблокирован - перезапустить бота
```

### Бот не отвечает на команды

**Проверка:**
1. Убедитесь что бот запущен (смотрите логи)
2. Проверьте BOT_TOKEN в .env
3. Попробуйте `/start` в Telegram
4. Проверьте интернет-соединение

---

## 📝 Логи

### Расположение логов

```
bot/logs/bot.log
```

### Уровни логирования

- `DEBUG` - детальная отладочная информация
- `INFO` - общая информация о работе
- `WARNING` - предупреждения
- `ERROR` - ошибки

### Просмотр логов

```powershell
# Все логи
cat logs/bot.log

# Только ошибки
Select-String -Path logs/bot.log -Pattern "ERROR"

# Только INFO
Select-String -Path logs/bot.log -Pattern "INFO"

# Следить в реальном времени
Get-Content logs/bot.log -Wait
```

---

## 🔐 Безопасность

### Проверка секретов

Перед запуском убедитесь что секреты не "засвечены":

```powershell
# Проверка логов на наличие секретов
Select-String -Path logs/*.log -Pattern "BOT_TOKEN=|YOOKASSA_SECRET="
# Не должно найти совпадений!
```

### Защита .env

```powershell
# Проверить что .env не попадает в git
git status

# Если видите .env в списке - добавьте в .gitignore
echo ".env" >> .gitignore
```

---

## 🎓 Советы

### Автостарт при разработке

Создайте скрипт `start_bot.bat`:
```batch
@echo off
cd /d "%~dp0"
python main.py
pause
```

Запускайте двойным кликом!

### Несколько окружений

Для разных сред используйте разные .env файлы:

```bash
.env.development  # локальная разработка
.env.staging      # тестовый сервер
.env.production   # боевой сервер
```

Переключение:
```powershell
Copy-Item .env.production .env
python main.py
```

### Мониторинг ресурсов

```powershell
# Использование памяти
Get-Process python | Select-Object Name, CPU, WorkingSet

# Активные подключения
Get-NetTCPConnection | Where-Object {$_.OwningProcess -eq (Get-Process python).Id}
```

---

## 📞 Чек-лист перед запуском

Перед каждым запуском проверяйте:

- [ ] Файл `.env` существует и заполнен
- [ ] `BOT_TOKEN` указан и корректен
- [ ] Зависимости установлены (`pip list`)
- [ ] База данных `shop.db` существует
- [ ] Нет других запущенных процессов бота
- [ ] Интернет-соединение активно

---

## 🚀 Production деплой

Когда будете готовы к развертыванию на сервере:

1. Следуйте инструкции в [`DEPLOY.md`](DEPLOY.md)
2. Время развертывания: 15-30 минут
3. Требуется: VPS/хостинг, домен, SSL сертификат

---

## 📚 Дополнительная документация

- [`README.md`](README.md) - основная документация
- [`ARCHITECTURE.md`](ARCHITECTURE.md) - архитектура проекта
- [`CODE_REVIEW_REPORT.md`](CODE_REVIEW_REPORT.md) - отчет об исправлениях
- [`TEST_REPORT.md`](TEST_REPORT.md) - отчет о тестировании
- [`PROJECT_SUMMARY.md`](PROJECT_SUMMARY.md) - резюме проекта

---

## 💡 Команды для быстрой справки

```powershell
# Запустить бота
python main.py

# Остановить бота
Ctrl+C  # в терминале

# Проверить статус
Get-Process python

# Посмотреть логи
Get-Content logs/bot.log -Tail 20

# Перезапустить
Get-Process python | Stop-Process -Force; python main.py

# Проверить конфигурацию
python -c "from core.config import validate_config; validate_config()"
```

---

**Готово!** 🎉

Теперь вы можете самостоятельно запускать и останавливать бота.

**Вопросы?** Смотрите документацию или логируйте ошибки в `logs/bot.log`.

---

**Версия инструкции:** 1.0  
**Дата:** 28.03.2026  
**Статус:** ✅ Актуально для ver. 3.4
