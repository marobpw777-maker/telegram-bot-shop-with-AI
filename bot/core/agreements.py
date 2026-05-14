# core/agreements.py
import logging
import json
import os
import sqlite3
from typing import Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

# Путь к SQLite для хранения согласий локально
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
AGREEMENTS_DB_PATH = DATA_DIR / "agreements.db"

def _get_sqlite_conn():
    """Подключение к SQLite для локального хранения согласий"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(AGREEMENTS_DB_PATH))
    conn.row_factory = sqlite3.Row
    # Создаём таблицу если нет
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_agreements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL UNIQUE,
            agreement_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            agreement_version VARCHAR(64),
            privacy_url VARCHAR(1024),
            offer_url VARCHAR(1024),
            user_ip VARCHAR(64),
            user_agent VARCHAR(255),
            extra TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn

def _get_mysql_conn():
    """Прямое подключение к MySQL"""
    import pymysql
    from pymysql.cursors import DictCursor
    
    host = os.getenv("YOOKASSA_RELAY_DB_HOST", "localhost")
    user = os.getenv("YOOKASSA_RELAY_DB_USER", "")
    password = os.getenv("YOOKASSA_RELAY_DB_PASS", "")
    database = os.getenv("YOOKASSA_RELAY_DB_NAME", "")
    port = int(os.getenv("YOOKASSA_RELAY_DB_PORT", "3306"))
    
    return pymysql.connect(
        host=host, user=user, password=password,
        db=database, port=port,
        charset="utf8mb4", cursorclass=DictCursor,
        autocommit=True,
    )


def ensure_agreements_table():
    """Создаёт таблицу в MySQL"""
    conn = _get_mysql_conn()
    q = """
    CREATE TABLE IF NOT EXISTS user_agreements (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      telegram_id BIGINT NOT NULL UNIQUE,
      agreement_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      agreement_version VARCHAR(64),
      privacy_url VARCHAR(1024),
      offer_url VARCHAR(1024),
      user_ip VARCHAR(64),
      user_agent VARCHAR(255),
      extra JSON,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      INDEX (telegram_id)
    ) ENGINE=InnoDB CHARSET=utf8mb4;
    """
    try:
        cur = conn.cursor()
        cur.execute(q)
        cur.close()
        conn.close()
        return True
    except Exception:
        logger.exception("ensure_agreements_table failed")
        return False


def user_has_agreement(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Проверяет соглашение в MySQL, с fallback на SQLite"""
    # Пробуем MySQL
    try:
        conn = _get_mysql_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM user_agreements WHERE telegram_id = %s", (telegram_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return row
    except Exception:
        logger.debug("MySQL unavailable for agreements, using SQLite fallback")
    
    # Fallback на SQLite
    try:
        conn = _get_sqlite_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM user_agreements WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else None
    except Exception:
        logger.exception("user_has_agreement SQLite error")
        return None


def set_user_agreement(telegram_id: int, **kwargs) -> bool:
    """Сохраняет соглашение в MySQL, с fallback на SQLite"""
    # Пробуем MySQL
    try:
        conn = _get_mysql_conn()
        cur = conn.cursor()
        # UPSERT: вставить или обновить
        cur.execute("""
            INSERT INTO user_agreements 
            (telegram_id, agreement_version, privacy_url, offer_url, user_ip, user_agent, extra)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              agreement_date = CURRENT_TIMESTAMP,
              agreement_version = VALUES(agreement_version),
              privacy_url = VALUES(privacy_url),
              offer_url = VALUES(offer_url),
              user_ip = VALUES(user_ip),
              user_agent = VALUES(user_agent),
              extra = VALUES(extra)
        """, (
            telegram_id,
            kwargs.get('agreement_version'),
            kwargs.get('privacy_url'),
            kwargs.get('offer_url'),
            kwargs.get('user_ip'),
            kwargs.get('user_agent'),
            json.dumps(kwargs.get('extra')) if kwargs.get('extra') else None
        ))
        cur.close()
        conn.close()
        return True
    except Exception:
        logger.debug("MySQL unavailable for agreements, using SQLite fallback")
    
    # Fallback на SQLite
    try:
        conn = _get_sqlite_conn()
        cur = conn.cursor()
        # SQLite UPSERT через INSERT OR REPLACE
        extra_json = json.dumps(kwargs.get('extra'), ensure_ascii=False) if kwargs.get('extra') else None
        cur.execute("""
            INSERT OR REPLACE INTO user_agreements 
            (telegram_id, agreement_version, privacy_url, offer_url, user_ip, user_agent, extra, agreement_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            telegram_id,
            kwargs.get('agreement_version'),
            kwargs.get('privacy_url'),
            kwargs.get('offer_url'),
            kwargs.get('user_ip'),
            kwargs.get('user_agent'),
            extra_json
        ))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"✅ Согласие пользователя {telegram_id} сохранено в SQLite (version={kwargs.get('agreement_version')})")
        return True
    except Exception as e:
        logger.exception(f"set_user_agreement SQLite error: {e}")
        return False


def anonymize_user_pii(telegram_id: int) -> Dict[str, bool]:
    """
    Удаляет запись о согласии пользователя из таблицы user_agreements (MySQL + SQLite).
    Возвращает словарь с результатами.
    """
    results = {}
    
    # Пробуем удалить из MySQL
    try:
        conn = _get_mysql_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM user_agreements WHERE telegram_id = %s", (telegram_id,))
        affected = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        results['user_agreements_mysql'] = affected > 0
        if affected > 0:
            logger.info(f"Удалена запись согласия для пользователя {telegram_id} из MySQL")
    except Exception as e:
        logger.debug(f"MySQL недоступен для удаления согласия: {e}")
        results['user_agreements_mysql'] = False
    
    # Удаляем из SQLite (fallback)
    try:
        conn = _get_sqlite_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM user_agreements WHERE telegram_id = ?", (telegram_id,))
        affected = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        results['user_agreements_sqlite'] = affected > 0
        if affected > 0:
            logger.info(f"Удалена запись согласия для пользователя {telegram_id} из SQLite")
    except Exception as e:
        logger.exception(f"Ошибка при удалении из SQLite: {e}")
        results['user_agreements_sqlite'] = False

    return results