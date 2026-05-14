"""
Main entrypoint for Solid Simple bot.
"""
import os
import logging
import asyncio
from dotenv import load_dotenv

load_dotenv()

from core.config import BOT_TOKEN, ADMIN_IDS
from core.database import db
from core.hybrid_cart import hybrid_cart
from core.gigachat_assistant import warmup_gigachat
from core.logging_filters import setup_secure_logging  # 🔐 Безопасное логирование

# safe optional import (telegram payments may be absent)
try:
    from core.telegram_payments import telegram_payments
except Exception:
    telegram_payments = None

from handlers.checkout_handlers import setup_checkout_handlers
from handlers.navigation_handlers import setup_navigation_handlers
from handlers.payment_handlers import setup_payment_handlers
from handlers import ai_handlers

from telegram.ext import Application, MessageHandler, filters, PreCheckoutQueryHandler, PicklePersistence, CallbackQueryHandler

from handlers.legal_handlers import handlers as legal_handlers

# optional pymysql check
try:
    import pymysql
    HAVE_PYMYSQL = True
except Exception:
    HAVE_PYMYSQL = False

POLL_INTERVAL = int(os.getenv("YOOKASSA_RELAY_POLL_INTERVAL", "20"))

# 🔐 Настройка безопасного логирования (скрывает секреты)
setup_secure_logging(logger_name='solid_simple', enable_pii_filter=False)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("solid_simple")


async def post_init(application):
    logger.info("✅ Бот успешно запущен и post_init выполняется.")

    # Прогрев GigaChat
    try:
        await warmup_gigachat()
        logger.info("GigaChat warmed up successfully.")
    except Exception as e:
        logger.exception("Ошибка при прогреве GigaChat: %s", e)

    for admin_id in ADMIN_IDS:
        try:
            await application.bot.send_message(chat_id=admin_id, text="🚀 Бот Solid Simple запущен!\n\nЯ начал работу.")
        except Exception:
            logger.exception("Не удалось отправить сообщение админу %s", admin_id)

    if HAVE_PYMYSQL:
        try:
            application.create_task(poll_yookassa_notifications(application))
            logger.info("🔁 YooKassa poller scheduled.")
        except Exception:
            logger.exception("Не удалось запустить poller")


async def poll_yookassa_notifications(application):
    try:
        import pymysql
    except Exception:
        logger.warning("pymysql not available, poller exiting.")
        return

    while True:
        try:
            conn = pymysql.connect(host=os.getenv("YOOKASSA_RELAY_DB_HOST", "localhost"),
                                   user=os.getenv("YOOKASSA_RELAY_DB_USER", "root"),
                                   password=os.getenv("YOOKASSA_RELAY_DB_PASS", ""),
                                   db=os.getenv("YOOKASSA_RELAY_DB_NAME", ""),
                                   charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM yookassa_notifications WHERE processed=0 ORDER BY id ASC LIMIT 50")
                    rows = cur.fetchall()
                    if rows:
                        logger.info("⚡ Найдено %d новых уведомлений от YooKassa", len(rows))
                    for row in rows:
                        nid = row.get('id')
                        text = f"YooKassa notif id={nid}, event={row.get('event_type')}"
                        for admin in ADMIN_IDS:
                            try:
                                await application.bot.send_message(chat_id=admin, text=text)
                            except Exception:
                                logger.exception("Failed send notif to admin %s", admin)
                        try:
                            cur.execute("UPDATE yookassa_notifications SET processed=1, processed_at=NOW(), processed_by=%s WHERE id=%s",
                                        ("bot-poller", nid))
                            conn.commit()
                        except Exception:
                            logger.exception("Failed to mark notif processed %s", nid)
            finally:
                conn.close()
        except Exception:
            logger.exception("Error in poller loop")
        await asyncio.sleep(POLL_INTERVAL)


def check_environment() -> bool:
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if missing:
        logger.error("Missing env vars: %s", missing)
        return False
    return True


def log_all_handlers(application):
    """Выводит в лог все зарегистрированные обработчики для диагностики"""
    logger.info("=" * 60)
    logger.info("REGISTERED HANDLERS:")
    logger.info("=" * 60)
    
    for group, handlers_list in sorted(application.handlers.items()):
        logger.info(f"\n--- GROUP {group} ---")
        for i, handler in enumerate(handlers_list, 1):
            handler_type = type(handler).__name__
            
            # Получаем pattern
            pattern = "no pattern"
            if hasattr(handler, 'pattern'):
                if isinstance(handler.pattern, str):
                    pattern = handler.pattern
                else:
                    try:
                        pattern = str(handler.pattern)
                    except:
                        pattern = "complex pattern"
            
            # Получаем callback имя
            callback_name = "unknown"
            if hasattr(handler, 'callback'):
                if hasattr(handler.callback, '__name__'):
                    callback_name = handler.callback.__name__
                elif hasattr(handler.callback, '__class__'):
                    callback_name = handler.callback.__class__.__name__
            
            logger.info(f"  {i}. {handler_type}")
            logger.info(f"     pattern: {pattern}")
            logger.info(f"     callback: {callback_name}")
    
    logger.info("=" * 60)


def main():
    try:
        if not check_environment():
            return

        logger.info("🚀 Запуск бота Solid Simple (main.py)")

        try:
            db.init_db()
        except Exception:
            logger.exception("Ошибка инициализации БД")

        try:
            hybrid_cart.migrate_all_carts()
        except Exception:
            logger.exception("Ошибка миграции корзин")

        # Создаём приложение
        persistence = PicklePersistence(filepath="bot_persistence")
        application = Application.builder().token(BOT_TOKEN).persistence(persistence).post_init(post_init).build()

        # ============ РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ В ПРАВИЛЬНОМ ПОРЯДКЕ ============
        
        # 1. СНАЧАЛА legal_handlers (group=0) — самый высокий приоритет
        logger.info("Registering legal_handlers...")
        try:
            for h in legal_handlers:
                application.add_handler(h, group=0)
                logger.info(f"  Added: {type(h).__name__}")
        except Exception:
            logger.exception("Ошибка регистрации legal handlers")

        # 2. ПОТОМ checkout_handlers (group=0)
        logger.info("Registering checkout_handlers...")
        try:
            setup_checkout_handlers(application)
        except Exception:
            logger.exception("Ошибка регистрации checkout handlers")

        # 3. ПОТОМ navigation_handlers (group=1)
        logger.info("Registering navigation_handlers...")
        try:
            setup_navigation_handlers(application)
        except Exception:
            logger.exception("Ошибка регистрации navigation handlers")

        # 4. ПОТОМ payment_handlers (group=2)
        logger.info("Registering payment_handlers...")
        try:
            setup_payment_handlers(application)
        except Exception:
            logger.exception("Ошибка регистрации payment handlers")

        # 5. ПОТОМ AI handlers (group=3)
        logger.info("Registering AI handlers...")
        try:
            ai_handlers.setup_ai_handlers(application)
        except Exception:
            logger.exception("Ошибка регистрации AI handlers")

        # 6. Telegram Payments (group=4)
        logger.info("Registering telegram payment handlers...")
        try:
            if telegram_payments:
                application.add_handler(PreCheckoutQueryHandler(telegram_payments.handle_pre_checkout), group=4)
                application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, telegram_payments.handle_successful_payment), group=4)
        except Exception:
            logger.exception("Error registering telegram payment handlers")

        # ============ ДИАГНОСТИКА ============
        log_all_handlers(application)

        logger.info("✅ Все обработчики зарегистрированы. Запуск polling...")
        application.run_polling(allowed_updates=['message', 'callback_query', 'pre_checkout_query', 'shipping_query'])

    except Exception:
        logger.exception("Critical failure in main")
    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()