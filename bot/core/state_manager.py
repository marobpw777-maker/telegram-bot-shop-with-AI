# core/state_manager.py
"""
State manager with MySQL backend (primary) and in-memory fallback.

- Exposes:
    - state_manager.set_user_state(user_id, state)
    - state_manager.get_user_state(user_id) -> Optional[str]
    - state_manager.clear_user_state(user_id)
    - state_manager.set_input_state(user_id, state)
    - state_manager.get_input_state(user_id) -> Optional[str]
    - state_manager.clear_input_state(user_id)
    - state_manager.cleanup_expired()

- Backward-compatible dict-like proxies:
    - state_manager.user_states  (dict-like)
    - state_manager.user_input_states (dict-like)

Environment variables required for MySQL usage:
    YOOKASSA_RELAY_DB_HOST
    YOOKASSA_RELAY_DB_USER
    YOOKASSA_RELAY_DB_PASS
    YOOKASSA_RELAY_DB_NAME
Optional:
    SESSION_TTL_SECONDS (default 1800)

If MySQL is not available or connection fails, module falls back to in-memory storage.
"""
from __future__ import annotations
import os
import logging
from typing import Optional, Any, Iterator, Tuple, Dict
import datetime

logger = logging.getLogger(__name__)

SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", "1800"))

# Try import pymysql
try:
    import pymysql
    import pymysql.cursors
except Exception:
    pymysql = None

# ---------- Dict-like proxy for compatibility ----------
class _StateDictProxy:
    """
    Dict-like proxy for 'fsm' or 'input' states.
    Minimal mapping API: get, __getitem__, __setitem__, __delitem__, __contains__, keys, items, to_dict
    """
    def __init__(self, manager: "StateManager", kind: str):
        if kind not in ("fsm", "input"):
            raise ValueError("kind must be 'fsm' or 'input'")
        self._manager = manager
        self._kind = kind

    def get(self, user_id: int, default: Any = None) -> Any:
        if self._kind == "fsm":
            val = self._manager.get_user_state(int(user_id))
        else:
            val = self._manager.get_input_state(int(user_id))
        return val if val is not None else default

    def __getitem__(self, user_id: int) -> Any:
        val = self.get(int(user_id))
        if val is None:
            raise KeyError(user_id)
        return val

    def __setitem__(self, user_id: int, value: str) -> None:
        if self._kind == "fsm":
            self._manager.set_user_state(int(user_id), str(value))
        else:
            self._manager.set_input_state(int(user_id), str(value))

    def __delitem__(self, user_id: int) -> None:
        if self._kind == "fsm":
            self._manager.clear_user_state(int(user_id))
        else:
            self._manager.clear_input_state(int(user_id))

    def __contains__(self, user_id: int) -> bool:
        return self.get(int(user_id)) is not None

    def keys(self) -> Iterator[int]:
        """Return iterator of user_id with active (non-expired) states."""
        if self._manager._db_available:
            conn = None
            try:
                conn = self._manager._get_connection()
                with conn.cursor() as cur:
                    field = "state" if self._kind == "fsm" else "input_state"
                    sql = f"SELECT user_id FROM bot_user_states WHERE {field} IS NOT NULL AND expires_at > %s"
                    cur.execute(sql, (datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),))
                    rows = cur.fetchall()
                    for r in rows:
                        # cursor returns tuples (user_id,)
                        yield int(r[0])
            except Exception as e:
                logger.exception("StateProxy.keys DB error: %s", e)
                return iter(())
            finally:
                if conn:
                    conn.close()
        else:
            # memory fallback
            mem = self._manager._memory_states if self._kind == "fsm" else self._manager._memory_input_states
            now = datetime.datetime.utcnow().timestamp()
            # cleanup expired entries on the fly
            to_del = []
            for uid, (state, expires_ts) in list(mem.items()):
                if expires_ts and expires_ts <= now:
                    to_del.append(uid)
                else:
                    yield uid
            for uid in to_del:
                mem.pop(uid, None)

    def items(self) -> Iterator[Tuple[int, Any]]:
        for k in self.keys():
            yield (k, self.get(k))

    def to_dict(self) -> Dict[int, Any]:
        return dict(self.items())

# ---------- StateManager ----------
class StateManager:
    def __init__(self):
        # DB config read from env
        self.db_config = {
            "host": os.getenv("YOOKASSA_RELAY_DB_HOST"),
            "user": os.getenv("YOOKASSA_RELAY_DB_USER"),
            "password": os.getenv("YOOKASSA_RELAY_DB_PASS"),
            "database": os.getenv("YOOKASSA_RELAY_DB_NAME"),
            "charset": "utf8mb4",
            "cursorclass": pymysql.cursors.Cursor if pymysql else None,
            "connect_timeout": 5
        }

        # in-memory fallback stores: user_id -> (state, expires_ts)
        self._memory_states: Dict[int, Tuple[str, float]] = {}
        self._memory_input_states: Dict[int, Tuple[str, float]] = {}

        # detect db availability
        self._db_available = False
        if pymysql and all([self.db_config.get("host"), self.db_config.get("user"), self.db_config.get("database")]):
            try:
                conn = self._get_connection()
                conn.close()
                self._db_available = True
            except Exception as e:
                logger.warning("StateManager: MySQL connection failed, falling back to memory. Error: %s", e)
                self._db_available = False
        else:
            logger.info("StateManager: pymysql not available or DB config missing; using in-memory states")

        # create table if possible
        if self._db_available:
            try:
                self._init_table()
            except Exception as e:
                logger.exception("StateManager: failed to init table, falling back to memory: %s", e)
                self._db_available = False

        # compatibility proxies
        self.user_states = _StateDictProxy(self, "fsm")
        self.user_input_states = _StateDictProxy(self, "input")

        logger.info("StateManager initialized (db_available=%s, session_ttl=%s)", self._db_available, SESSION_TTL)

    def _get_connection(self):
        """Open a new pymysql connection. Caller must close it."""
        if not pymysql:
            raise RuntimeError("pymysql not installed")
        cfg = {k: v for k, v in self.db_config.items() if v is not None}
        return pymysql.connect(**cfg)

    def _init_table(self):
        """Create bot_user_states table if not exists."""
        conn = None
        try:
            conn = self._get_connection()
            with conn.cursor() as cur:
                cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_user_states (
                    user_id BIGINT PRIMARY KEY,
                    state VARCHAR(255) DEFAULT NULL,
                    input_state VARCHAR(255) DEFAULT NULL,
                    expires_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_expires (expires_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """)
                conn.commit()
        finally:
            if conn:
                conn.close()

    # --- FSM methods ---
    def set_user_state(self, user_id: int, state: str):
        expires_dt = (datetime.datetime.utcnow() + datetime.timedelta(seconds=SESSION_TTL)).strftime("%Y-%m-%d %H:%M:%S")
        if self._db_available:
            conn = None
            try:
                conn = self._get_connection()
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO bot_user_states (user_id, state, expires_at)
                        VALUES (%s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                          state = VALUES(state),
                          expires_at = VALUES(expires_at)
                    """, (int(user_id), str(state), expires_dt))
                    conn.commit()
                    return
            except Exception as e:
                logger.exception("set_user_state DB error, falling back to memory: %s", e)
                # fallthrough to memory
            finally:
                if conn:
                    conn.close()

        # memory fallback
        self._memory_states[int(user_id)] = (str(state), datetime.datetime.utcnow().timestamp() + SESSION_TTL)

    def get_user_state(self, user_id: int) -> Optional[str]:
        if self._db_available:
            conn = None
            try:
                conn = self._get_connection()
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT state FROM bot_user_states
                        WHERE user_id = %s AND expires_at > %s
                    """, (int(user_id), datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
                    row = cur.fetchone()
                    if row:
                        # row is tuple (state,)
                        return row[0]
                    return None
            except Exception as e:
                logger.exception("get_user_state DB error, falling back to memory: %s", e)
            finally:
                if conn:
                    conn.close()

        # memory fallback
        entry = self._memory_states.get(int(user_id))
        if not entry:
            return None
        state, expires_ts = entry
        if expires_ts <= datetime.datetime.utcnow().timestamp():
            # expired
            self._memory_states.pop(int(user_id), None)
            return None
        return state

    def clear_user_state(self, user_id: int):
        if self._db_available:
            conn = None
            try:
                conn = self._get_connection()
                with conn.cursor() as cur:
                    cur.execute("UPDATE bot_user_states SET state = NULL WHERE user_id = %s", (int(user_id),))
                    conn.commit()
                    return
            except Exception as e:
                logger.exception("clear_user_state DB error, falling back to memory: %s", e)
            finally:
                if conn:
                    conn.close()

        # memory fallback
        self._memory_states.pop(int(user_id), None)

    # --- Input-state methods ---
    def set_input_state(self, user_id: int, state: str):
        expires_dt = (datetime.datetime.utcnow() + datetime.timedelta(seconds=SESSION_TTL)).strftime("%Y-%m-%d %H:%M:%S")
        if self._db_available:
            conn = None
            try:
                conn = self._get_connection()
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO bot_user_states (user_id, input_state, expires_at)
                        VALUES (%s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                          input_state = VALUES(input_state),
                          expires_at = VALUES(expires_at)
                    """, (int(user_id), str(state), expires_dt))
                    conn.commit()
                    return
            except Exception as e:
                logger.exception("set_input_state DB error, falling back to memory: %s", e)
            finally:
                if conn:
                    conn.close()

        # memory fallback
        self._memory_input_states[int(user_id)] = (str(state), datetime.datetime.utcnow().timestamp() + SESSION_TTL)

    def get_input_state(self, user_id: int) -> Optional[str]:
        if self._db_available:
            conn = None
            try:
                conn = self._get_connection()
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT input_state FROM bot_user_states
                        WHERE user_id = %s AND expires_at > %s
                    """, (int(user_id), datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
                    row = cur.fetchone()
                    if row:
                        return row[0]
                    return None
            except Exception as e:
                logger.exception("get_input_state DB error, falling back to memory: %s", e)
            finally:
                if conn:
                    conn.close()

        entry = self._memory_input_states.get(int(user_id))
        if not entry:
            return None
        state, expires_ts = entry
        if expires_ts <= datetime.datetime.utcnow().timestamp():
            self._memory_input_states.pop(int(user_id), None)
            return None
        return state

    def clear_input_state(self, user_id: int):
        if self._db_available:
            conn = None
            try:
                conn = self._get_connection()
                with conn.cursor() as cur:
                    cur.execute("UPDATE bot_user_states SET input_state = NULL WHERE user_id = %s", (int(user_id),))
                    conn.commit()
                    return
            except Exception as e:
                logger.exception("clear_input_state DB error, falling back to memory: %s", e)
            finally:
                if conn:
                    conn.close()

        self._memory_input_states.pop(int(user_id), None)

    # Cleanup expired rows in DB (should be scheduled periodically)
    def cleanup_expired(self) -> None:
        if self._db_available:
            conn = None
            try:
                conn = self._get_connection()
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM bot_user_states WHERE expires_at <= %s", (datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),))
                    deleted = cur.rowcount
                    conn.commit()
                    if deleted:
                        logger.info("StateManager: cleaned %s expired sessions", deleted)
            except Exception as e:
                logger.exception("cleanup_expired DB error: %s", e)
            finally:
                if conn:
                    conn.close()
        else:
            # In-memory cleanup
            now_ts = datetime.datetime.utcnow().timestamp()
            for d in (self._memory_states, self._memory_input_states):
                to_del = [uid for uid, (_, exp) in d.items() if exp <= now_ts]
                for uid in to_del:
                    d.pop(uid, None)

# create global instance
state_manager = StateManager()
