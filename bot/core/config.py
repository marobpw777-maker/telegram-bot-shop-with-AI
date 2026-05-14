# core/config.py
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from dotenv import load_dotenv
from typing import Dict, Any

# Load .env
load_dotenv()

# Custom exception for configuration errors
class ConfigurationError(RuntimeError):
    """Исключение для ошибок конфигурации."""
    pass

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
PHOTOS_DIR = BASE_DIR / "photos"
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

for directory in [PHOTOS_DIR, DATA_DIR, LOGS_DIR]:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

# Safe stream handler to avoid UnicodeEncodeError on some Windows consoles
class SafeStreamHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            stream = self.stream
            stream_enc = getattr(stream, "encoding", None) or "utf-8"
            try:
                stream.write(msg + self.terminator)
            except UnicodeEncodeError:
                safe_msg = msg.encode(stream_enc, errors="replace").decode(stream_enc, errors="ignore")
                stream.write(safe_msg + self.terminator)
        except Exception:
            self.handleError(record)

# Logger setup
logger = logging.getLogger("solid_simple")
if not logger.handlers:
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    try:
        file_handler = RotatingFileHandler(LOGS_DIR / "solid_simple.log",
                                           maxBytes=10_000_000, backupCount=5,
                                           encoding="utf-8")
        file_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    except Exception:
        pass

    try:
        stream_handler = SafeStreamHandler(stream=sys.stdout)
        stream_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        stream_handler.setFormatter(stream_formatter)
        logger.addHandler(stream_handler)
    except Exception:
        pass

# Sensitive keys set for redaction
SECRET_KEYS = {
    "BOT_TOKEN",
    "PROVIDER_TOKEN",
    "YOOKASSA_SECRET_KEY",
    "YOOKASSA_SHOP_ID",
    "DB_PASSWORD",
    "YOOKASSA_RELAY_DB_PASS"
}

def redact(value: str | None) -> str:
    if value is None:
        return "<empty>"
    s = str(value)
    if len(s) <= 8:
        return s[:2] + "..." + s[-2:]
    return s[:6] + "..." + s[-4:]

def safe_log_config(cfg: Dict[str, Any]) -> None:
    safe = {}
    for k, v in cfg.items():
        if k in SECRET_KEYS:
            safe[k] = redact(v)
        else:
            if isinstance(v, (list, dict)):
                try:
                    safe[k] = f"<{type(v).__name__} len={len(v)}>"
                except Exception:
                    safe[k] = f"<{type(v).__name__}>"
            else:
                safe[k] = v
    logger.info("Loaded config (safe): %s", safe)

# Helper to load env
def load_env() -> Dict[str, Any]:
    return {
        "BOT_TOKEN": os.getenv("BOT_TOKEN"),
        "PROVIDER_TOKEN": os.getenv("PROVIDER_TOKEN"),
        "YOOKASSA_SHOP_ID": os.getenv("YOOKASSA_SHOP_ID"),
        "YOOKASSA_SECRET_KEY": os.getenv("YOOKASSA_SECRET_KEY"),
        "ADMIN_IDS": os.getenv("ADMIN_IDS", ""),
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
        # Relay DB (site) settings - ВАЖНО: сохраняем вебхуки!
        "YOOKASSA_RELAY_DB_HOST": os.getenv("YOOKASSA_RELAY_DB_HOST", "localhost"),
        "YOOKASSA_RELAY_DB_USER": os.getenv("YOOKASSA_RELAY_DB_USER", "INSERT_YOUR_DB_USER"),
        "YOOKASSA_RELAY_DB_PASS": os.getenv("YOOKASSA_RELAY_DB_PASS", "INSERT_YOUR_DB_PASS"),
        "YOOKASSA_RELAY_DB_NAME": os.getenv("YOOKASSA_RELAY_DB_NAME", "INSERT_YOUR_DB_NAME"),
        "YOOKASSA_RELAY_POLL_INTERVAL": os.getenv("YOOKASSA_RELAY_POLL_INTERVAL", "20"),
        # Webhook token expected on site
        "YOOKASSA_WEBHOOK_TOKEN": os.getenv("YOOKASSA_WEBHOOK_TOKEN", "INSERT_WEBHOOK_SECRET_TOKEN"),
        # Optional forwarding URL (if you want webhook.php to forward to bot endpoint)
        "YOOKASSA_FORWARD_TO_BOT_URL": os.getenv("YOOKASSA_FORWARD_TO_BOT_URL", ""),
        # Other runtime flags
        "PAYMENT_TEST_MODE": os.getenv("PAYMENT_TEST_MODE", "True"),
    }

_env = load_env()

# Backward-compatible values
BOT_TOKEN = _env.get("BOT_TOKEN")
if not BOT_TOKEN or BOT_TOKEN.startswith("your_"):
    logger.warning("BOT_TOKEN not configured in .env (using placeholder).")
    BOT_TOKEN = "INSERT_YOUR_BOT_TOKEN"  # placeholder, please replace in .env

PROVIDER_TOKEN = _env.get("PROVIDER_TOKEN")
if not PROVIDER_TOKEN or PROVIDER_TOKEN.startswith("your_"):
    logger.warning("PROVIDER_TOKEN not configured.")
    PROVIDER_TOKEN = "INSERT_YOUR_PROVIDER_TOKEN"

YOOKASSA_SHOP_ID = _env.get("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = _env.get("YOOKASSA_SECRET_KEY")
if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
    logger.warning("YooKassa credentials not configured; payments will be in test mode.")
    YOOKASSA_SHOP_ID = YOOKASSA_SHOP_ID or "test_shop_id"
    YOOKASSA_SECRET_KEY = YOOKASSA_SECRET_KEY or "test_secret_key"

# Relay / DB for webhook relay on the site (where webhook writes notifications) - ВАЖНО: сохраняем вебхуки!
YOOKASSA_RELAY_DB_HOST = _env.get("YOOKASSA_RELAY_DB_HOST")
YOOKASSA_RELAY_DB_USER = _env.get("YOOKASSA_RELAY_DB_USER")
YOOKASSA_RELAY_DB_PASS = _env.get("YOOKASSA_RELAY_DB_PASS")
YOOKASSA_RELAY_DB_NAME = _env.get("YOOKASSA_RELAY_DB_NAME")
YOOKASSA_RELAY_POLL_INTERVAL = int(_env.get("YOOKASSA_RELAY_POLL_INTERVAL", "20"))
YOOKASSA_WEBHOOK_TOKEN = _env.get("YOOKASSA_WEBHOOK_TOKEN")
YOOKASSA_FORWARD_TO_BOT_URL = _env.get("YOOKASSA_FORWARD_TO_BOT_URL")

# YooKassa Webhook Server Configuration (для yookassa_webhook.py)
YOOKASSA_WEBHOOK_HOST = _env.get("YOOKASSA_WEBHOOK_HOST", "127.0.0.1")
YOOKASSA_WEBHOOK_PORT = int(_env.get("YOOKASSA_WEBHOOK_PORT", "8080"))
YOOKASSA_ALLOWED_IP_RANGES = _env.get("YOOKASSA_ALLOWED_IP_RANGES", "").split(",") if _env.get("YOOKASSA_ALLOWED_IP_RANGES") else []
YOOKASSA_FORWARD_SECRET = _env.get("YOOKASSA_FORWARD_SECRET", "")

# Mall settings
CONFIG_VERSION = "2.2"
LOG_LEVEL = _env.get("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Default categories (kept from previous version)
CATEGORIES = {
    "АВТОПАРФЮМ": "АВТОПАРФЮМ",
    "АРОМАРОЛЛЕРЫ": "Аромароллеры",
    "БАЛЬЗАМ ДЛЯ ГУБ": "БАЛЬЗАМ ДЛЯ ГУБ",
    "БАТТЕР ДЛЯ ТЕЛА": "БАТТЕР ДЛЯ ТЕЛА",
    "БОМБОЧКА ДЛЯ ВАННЫ": "БОМБОЧКА ДЛЯ ВАННЫ",
    "ДИФФУЗОРЫ": "ДИФФУЗОРЫ",
    "МАССАЖНОЕ МАСЛО": "Массажное масло",
    "ПЧЕЛИНАЯ СВЕЧА": "ПЧЕЛИНАЯ СВЕЧА",
    "САШЭ": "САШЭ",
    "СВЕЧА В БЕТОНЕ": "СВЕЧА В БЕТОНЕ",
    "СОЛЬ ДЛЯ ВАННЫ": "СОЛЬ ДЛЯ ВАННЫ",
    "СЫВОРОТКА ДЛЯ ЛИЦА": "Сыворотка для лица",
    "ТВЕРДЫЕ ДУХИ": "Твердые духи",
    "ТВЕРДЫЙ КОНДИЦИОНЕР": "ТВЕРДЫЙ КОНДИЦИОНЕР",
    "ТВЕРДЫЙ ШАМПУНЬ": "ТВЕРДЫЙ ШАМПУНЬ",
    "АКЦИИ": "Акции",
    "ЛОГО": "Лого"
}

# Parse ADMIN_IDS safely
ADMIN_IDS = []
_admins_raw = _env.get("ADMIN_IDS", "")
if _admins_raw:
    try:
        ADMIN_IDS = [int(x.strip()) for x in _admins_raw.split(",") if x.strip()]
        logger.info("Loaded admin IDs (count=%d)", len(ADMIN_IDS))
    except Exception as e:
        logger.error("Invalid ADMIN_IDS format: %s", e)
        ADMIN_IDS = []
else:
    logger.warning("ADMIN_IDS not configured in .env")

# Shop info
SHOP_NAME = os.getenv("SHOP_NAME", "Solid Simple")
CURRENCY = os.getenv("CURRENCY", "RUB")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@solid_simple_support")
SITE_PHOTOS_DIR = PHOTOS_DIR / "site"

# DB and payments flags
DATABASE_PATH = DATA_DIR / "shop_database.db"
ENABLE_PAYMENTS = bool(YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY and YOOKASSA_SHOP_ID != "test_shop_id")
ENABLE_TELEGRAM_PAYMENTS = bool(PROVIDER_TOKEN and not PROVIDER_TOKEN.startswith("TEST:"))
PAYMENT_TEST_MODE = str(_env.get("PAYMENT_TEST_MODE", "True")).lower() == "true"

# 🔥 ВАЖНОЕ ИСПРАВЛЕНИЕ: Добавляем emoji и description из старого config.py
DELIVERY_METHODS = {
    "self_pickup": {
        "name": "Самовывоз",
        "description": "Забрать заказ сами в удобное время",  # ← ДОБАВЛЕНО
        "cost": 0, 
        "time": "2-4 часа",
        "emoji": "🏪"  # ← ДОБАВЛЕНО
    },
    "yandex_pickup": {
        "name": "ПВЗ Яндекс", 
        "description": "Доставка в пункт выдачи заказов",  # ← ДОБАВЛЕНО
        "cost": 300,
        "time": "1-3 дня", 
        "requires_address": True,
        "emoji": "📦"  # ← ДОБАВЛЕНО
    },
    "courier": {
        "name": "Курьер",
        "description": "Привезем прямо к вашей двери",  # ← ДОБАВЛЕНО  
        "cost": 600,
        "time": "1-2 дня",
        "emoji": "🚗"  # ← ДОБАВЛЕНО
    }
}

CHECKOUT_MESSAGES = {
    'start': "Превосходный выбор!",
    'contacts': "Шаг 1 из 3: Ваши контактные данные", 
    'delivery': "Шаг 2 из 3: Выберите способ получения",
    'address': "Куда доставить ваш заказ?",
    'payment': "Проверьте ваш заказ", 
    'success': "Заказ оплачен! Спасибо за доверие!",
    'pvz_address': "Введите адрес ПВЗ Яндекс, откуда вам удобно забрать заказ:"
}

CONTACT_INFO = {
    "phone": os.getenv("CONTACT_PHONE", "+7 (XXX) XXX-XX-XX"),
    "email": os.getenv("CONTACT_EMAIL", "info@solidsimple.ru"), 
    "address": os.getenv("CONTACT_ADDRESS", "Москва, ул. Примерная, д. 123"),
    "work_hours": os.getenv("CONTACT_HOURS", "Ежедневно с 10:00 до 20:00"),
    "support_telegram": SUPPORT_USERNAME
}

# 🔥 PAYMENT_PROVIDER_TOKEN только из .env (без хардкода!)
PAYMENT_PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN")

# Валидация критичных секретов
def _validate_critical_secrets():
    """Проверка наличия обязательных секретов."""
    critical_missing = []
    
    if not BOT_TOKEN or BOT_TOKEN == "INSERT_YOUR_BOT_TOKEN":
        critical_missing.append("BOT_TOKEN")
    
    # PROVIDER_TOKEN важен для Telegram Payments
    if not PAYMENT_PROVIDER_TOKEN:
        logger.warning(
            "⚠️ PROVIDER_TOKEN не настроен в .env. "
            "Telegram Payments не будет работать. "
            "Получите токен в @BotFather или @ShopBot."
        )
    
    if critical_missing:
        raise ConfigurationError(
            f"Критические секреты отсутствуют в .env: {', '.join(critical_missing)}\n"
            "Скопируйте .env.example в .env и заполните значения."
        )

def validate_config() -> bool:
    errors = []
    warnings = []

    # Сначала проверим критические секреты
    try:
        _validate_critical_secrets()
    except ConfigurationError as e:
        logger.error(str(e))
        return False

    if not BOT_TOKEN or BOT_TOKEN == "INSERT_YOUR_BOT_TOKEN":
        errors.append("BOT_TOKEN not configured in .env")

    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY or YOOKASSA_SHOP_ID == "test_shop_id":
        warnings.append("YooKassa not configured - real payments will not work")

    if not PROVIDER_TOKEN or PROVIDER_TOKEN == "INSERT_YOUR_PROVIDER_TOKEN":
        warnings.append("PROVIDER_TOKEN not configured - Telegram payments will not work")

    json_path = DATA_DIR / "shop_data.json"
    if not json_path.exists():
        warnings.append(f"shop_data.json not found: {json_path}")

    for w in warnings:
        logger.warning("⚠️ %s", w)
    for e in errors:
        logger.error("❌ %s", e)

    try:
        cfg = {
            "BOT_TOKEN": BOT_TOKEN,
            "PROVIDER_TOKEN": PROVIDER_TOKEN,
            "YOOKASSA_SHOP_ID": YOOKASSA_SHOP_ID,
            "ADMIN_IDS": ADMIN_IDS,
            "DATABASE_PATH": str(DATABASE_PATH),
            "ENABLE_PAYMENTS": ENABLE_PAYMENTS,
            "ENABLE_TELEGRAM_PAYMENTS": ENABLE_TELEGRAM_PAYMENTS,
            "YOOKASSA_RELAY_DB_HOST": YOOKASSA_RELAY_DB_HOST,
            "YOOKASSA_RELAY_DB_NAME": YOOKASSA_RELAY_DB_NAME,
            "PAYMENT_TEST_MODE": PAYMENT_TEST_MODE
        }
        safe_log_config(cfg)
    except Exception:
        pass

    return len(errors) == 0

if __name__ != "__main__":
    validate_config()

# Backward-compatible Config class
class Config:
    def __init__(self):
        self.BOT_TOKEN = BOT_TOKEN
        self.BASE_DIR = BASE_DIR
        self.PHOTOS_DIR = PHOTOS_DIR
        self.DATA_DIR = DATA_DIR
        self.LOGS_DIR = LOGS_DIR
        self.CATEGORIES = CATEGORIES
        self.ADMIN_IDS = ADMIN_IDS
        self.SHOP_NAME = SHOP_NAME
        self.CURRENCY = CURRENCY
        self.SUPPORT_USERNAME = SUPPORT_USERNAME
        self.SITE_PHOTOS_DIR = SITE_PHOTOS_DIR
        self.LOG_LEVEL = LOG_LEVEL
        self.LOG_FORMAT = LOG_FORMAT  # ← ДОБАВЛЕНО
        self.DATABASE_PATH = DATABASE_PATH
        self.ENABLE_PAYMENTS = ENABLE_PAYMENTS
        self.ENABLE_TELEGRAM_PAYMENTS = ENABLE_TELEGRAM_PAYMENTS
        self.PAYMENT_TEST_MODE = PAYMENT_TEST_MODE
        self.YOOKASSA_SHOP_ID = YOOKASSA_SHOP_ID
        self.YOOKASSA_SECRET_KEY = YOOKASSA_SECRET_KEY
        self.PROVIDER_TOKEN = PROVIDER_TOKEN
        self.DELIVERY_METHODS = DELIVERY_METHODS
        self.CONTACT_INFO = CONTACT_INFO
        self.CHECKOUT_MESSAGES = CHECKOUT_MESSAGES  # ← ДОБАВЛЕНО
        self.PAYMENT_PROVIDER_TOKEN = PAYMENT_PROVIDER_TOKEN  # ← ДОБАВЛЕНО
        # 🔥 ВАЖНО: сохраняем вебхуки!
        self.YOOKASSA_RELAY_DB_HOST = YOOKASSA_RELAY_DB_HOST
        self.YOOKASSA_RELAY_DB_USER = YOOKASSA_RELAY_DB_USER
        self.YOOKASSA_RELAY_DB_PASS = YOOKASSA_RELAY_DB_PASS
        self.YOOKASSA_RELAY_DB_NAME = YOOKASSA_RELAY_DB_NAME
        self.YOOKASSA_RELAY_POLL_INTERVAL = YOOKASSA_RELAY_POLL_INTERVAL
        self.YOOKASSA_WEBHOOK_TOKEN = YOOKASSA_WEBHOOK_TOKEN
        self.YOOKASSA_FORWARD_TO_BOT_URL = YOOKASSA_FORWARD_TO_BOT_URL
        # Webhook server settings
        self.YOOKASSA_WEBHOOK_HOST = YOOKASSA_WEBHOOK_HOST
        self.YOOKASSA_WEBHOOK_PORT = YOOKASSA_WEBHOOK_PORT
        self.YOOKASSA_ALLOWED_IP_RANGES = YOOKASSA_ALLOWED_IP_RANGES
        self.YOOKASSA_FORWARD_SECRET = YOOKASSA_FORWARD_SECRET

config = Config()
SHOP_DATA = {}
logger.info("Configuration loaded (v%s)", CONFIG_VERSION)
logger.info("Payments: YooKassa=%s, Telegram=%s",
            "OK" if ENABLE_PAYMENTS else "NO",
            "OK" if ENABLE_TELEGRAM_PAYMENTS else "NO")
logger.info("Admins: %d", len(ADMIN_IDS))
logger.info("Webhook relay: %s", "ENABLED" if YOOKASSA_RELAY_DB_HOST else "DISABLED")