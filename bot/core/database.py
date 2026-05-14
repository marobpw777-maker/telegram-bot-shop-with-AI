# core/database.py
import sqlite3
import json
import logging
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class Database:
    """Класс для работы с базой данных SQLite"""

    def __init__(self, db_path: str = "shop_database.db"):
        self.db_path = Path(db_path)
        self.connection: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Получение соединения с базой данных (ленивая инициализация)."""
        if self.connection is None:
            # allow access из разных потоков (понадобится при вебхуках/async)
            self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
        return self.connection

    def close(self):
        """Закрыть соединение с БД."""
        try:
            if self.connection:
                self.connection.close()
                self.connection = None
                logger.info("🔒 Соединение с БД закрыто")
        except Exception as e:
            logger.exception(f"❌ Ошибка при закрытии соединения БД: {e}")

    def __del__(self):
        # Пытаемся корректно закрыть при сборщике мусора
        try:
            self.close()
        except Exception:
            pass

    def init_db(self):
        """
        Инициализация базы данных и создание таблиц.
        Внимание: мы **не удаляем** существующие таблицы, вместо этого выполняем миграции (если требуется).
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Таблица пользователей
            cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                phone TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

            # Таблица товаров в корзине
            cursor.execute('''CREATE TABLE IF NOT EXISTS cart_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                product_id TEXT,
                variant_id TEXT,
                quantity INTEGER DEFAULT 1,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )''')

            # Таблица заказов (создаем, если нет)
            cursor.execute('''CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                order_data TEXT,
                total_amount REAL,
                status TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                contact_info TEXT,
                delivery_method TEXT,
                delivery_address TEXT,
                pvz_address TEXT,  -- НОВОЕ ПОЛЕ: для адреса ПВЗ Яндекс
                payment_method TEXT DEFAULT 'cash',
                payment_id TEXT,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )''')

            # Таблица для хранения состояний пользователей (для навигации)
            cursor.execute('''CREATE TABLE IF NOT EXISTS user_states (
                user_id INTEGER PRIMARY KEY,
                current_slide TEXT DEFAULT 'S01',
                previous_slides TEXT DEFAULT '[]',
                temp_data TEXT DEFAULT '{}',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )''')

            # Индексы для ускорения поиска
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_cart_user ON cart_items (user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_orders_user ON orders (user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_orders_payment_id ON orders (payment_id)')

            conn.commit()

            # Выполняем простую миграцию схемы: если добавили поле payment_id позже — убедимся, что оно есть
            self._ensure_column_exists('orders', 'payment_id', 'TEXT')
            # НОВОЕ: убедимся, что поле pvz_address существует
            self._ensure_column_exists('orders', 'pvz_address', 'TEXT')

            logger.info("✅ База данных успешно инициализирована и мигрирована (при необходимости)")

        except Exception as e:
            logger.exception(f"❌ Ошибка инициализации базы данных: {e}")
            raise

    # -------------------------
    # НИЗКОУРОВНЕВЫЕ ВСПОМОГАТЕЛИ
    # -------------------------
    def _ensure_column_exists(self, table: str, column: str, col_type: str = 'TEXT'):
        """
        Убедиться, что колонка существует в таблице, и если нет — добавить.
        SQLite позволяет ALTER TABLE ... ADD COLUMN.
        """
        try:
            # Валидация идентификаторов для защиты от SQL-инъекции
            import re
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table):
                raise ValueError(f"Invalid table name: {table}")
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', column):
                raise ValueError(f"Invalid column name: {column}")
            if col_type.upper() not in ('TEXT', 'INTEGER', 'REAL', 'BLOB', 'NUMERIC', 'BOOLEAN', 'DATETIME'):
                raise ValueError(f"Invalid column type: {col_type}")
            
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info({table})")
            cols = [row['name'] for row in cursor.fetchall()]
            if column not in cols:
                logger.info(f"ℹ️ Добавляем колонку {column} в {table}")
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                conn.commit()
        except Exception as e:
            logger.exception(f"❌ Ошибка при проверке/добавлении колонки {column} в {table}: {e}")

    def execute(self, query: str, params: tuple = ()):
        """Выполнение SQL-запроса (без возврата результатов). Возвращает True/False."""
        try:
            with self._lock:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
            return True
        except Exception as e:
            logger.exception(f"❌ Ошибка выполнения запроса: {e}\nQuery: {query}\nParams: {params}")
            return False

    def execute_return_lastrowid(self, query: str, params: tuple = ()):
        """
        Выполнение INSERT запроса и возврат lastrowid.
        Удобно для операций создания заказа.
        """
        try:
            with self._lock:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.exception(f"❌ Ошибка выполнения запроса (lastrowid): {e}\nQuery: {query}\nParams: {params}")
            return None

    def fetch_one(self, query: str, params: tuple = ()):
        """Получение одна строки (sqlite3.Row)"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchone()
        except Exception as e:
            logger.exception(f"❌ Ошибка получения данных: {e}\nQuery: {query}\nParams: {params}")
            return None

    def fetch_all(self, query: str, params: tuple = ()):
        """Получение всех строк (list of sqlite3.Row)"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()
        except Exception as e:
            logger.exception(f"❌ Ошибка получения данных: {e}\nQuery: {query}\nParams: {params}")
            return []

    # Удобные обёртки, чтобы вернуть dict вместо sqlite3.Row (при необходимости)
    def fetch_one_dict(self, query: str, params: tuple = ()):
        row = self.fetch_one(query, params)
        return dict(row) if row else None

    def fetch_all_dicts(self, query: str, params: tuple = ()):
        rows = self.fetch_all(query, params)
        return [dict(r) for r in rows] if rows else []

    # -------------------------
    # CRUD и логика приложения
    # -------------------------
    def add_user(self, user_id: int, username: str = "", first_name: str = "", last_name: str = ""):
        """Добавление/обновление пользователя"""
        try:
            with self._lock:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, last_active)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (user_id, username, first_name, last_name))
                cursor.execute('''
                    INSERT OR IGNORE INTO user_states (user_id)
                    VALUES (?)
                ''', (user_id,))
                conn.commit()
            logger.debug(f"✅ Пользователь {user_id} добавлен/обновлен")
        except Exception as e:
            logger.exception(f"❌ Ошибка добавления пользователя {user_id}: {e}")

    def add_to_cart(self, user_id: int, product_id: str, variant_id: str = None, quantity: int = 1):
        """Добавление товара в корзину"""
        try:
            with self._lock:
                conn = self.get_connection()
                cursor = conn.cursor()

                if variant_id:
                    cursor.execute('''SELECT id, quantity FROM cart_items
                                      WHERE user_id = ? AND product_id = ? AND variant_id = ?''',
                                   (user_id, product_id, variant_id))
                else:
                    cursor.execute('''SELECT id, quantity FROM cart_items
                                      WHERE user_id = ? AND product_id = ? AND variant_id IS NULL''',
                                   (user_id, product_id))

                existing_item = cursor.fetchone()

                if existing_item:
                    new_quantity = existing_item['quantity'] + quantity
                    cursor.execute('UPDATE cart_items SET quantity = ? WHERE id = ?', (new_quantity, existing_item['id']))
                else:
                    cursor.execute('INSERT INTO cart_items (user_id, product_id, variant_id, quantity) VALUES (?, ?, ?, ?)',
                                   (user_id, product_id, variant_id, quantity))

                conn.commit()
            logger.debug(f"✅ Товар {product_id} добавлен в корзину пользователя {user_id}")
            return True
        except Exception as e:
            logger.exception(f"❌ Ошибка добавления в корзину: {e}")
            return False

    def get_cart_items(self, user_id: int) -> List[Dict[str, Any]]:
        """Получение товаров в корзине пользователя"""
        try:
            rows = self.fetch_all('SELECT product_id, variant_id, quantity FROM cart_items WHERE user_id = ?', (user_id,))
            return [dict(r) for r in rows] if rows else []
        except Exception as e:
            logger.exception(f"❌ Ошибка получения корзины: {e}")
            return []

    def remove_from_cart(self, user_id: int, product_id: str, variant_id: str = None):
        """Удаление товара из корзины"""
        try:
            with self._lock:
                conn = self.get_connection()
                cursor = conn.cursor()
                if variant_id:
                    cursor.execute('DELETE FROM cart_items WHERE user_id = ? AND product_id = ? AND variant_id = ?',
                                   (user_id, product_id, variant_id))
                else:
                    cursor.execute('DELETE FROM cart_items WHERE user_id = ? AND product_id = ? AND variant_id IS NULL',
                                   (user_id, product_id))
                conn.commit()
            logger.debug(f"✅ Товар {product_id} удален из корзины пользователя {user_id}")
            return True
        except Exception as e:
            logger.exception(f"❌ Ошибка удаления из корзины: {e}")
            return False

    def clear_cart(self, user_id: int):
        """Очистка корзины пользователя"""
        try:
            with self._lock:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('DELETE FROM cart_items WHERE user_id = ?', (user_id,))
                conn.commit()
            logger.debug(f"✅ Корзина пользователя {user_id} очищена")
            return True
        except Exception as e:
            logger.exception(f"❌ Ошибка очистки корзины: {e}")
            return False

    def update_user_state(self, user_id: int, current_slide: str, previous_slides: List[str] = None):
        """Обновление состояния пользователя с улучшенной логикой истории"""
        try:
            with self._lock:
                conn = self.get_connection()
                cursor = conn.cursor()

                if previous_slides is None:
                    cursor.execute('SELECT previous_slides FROM user_states WHERE user_id = ?', (user_id,))
                    result = cursor.fetchone()
                    if result and result['previous_slides']:
                        previous_slides = json.loads(result['previous_slides'])
                    else:
                        previous_slides = []

                # Фильтруем историю: убираем товары из основной истории навигации
                filtered_slides = [slide for slide in previous_slides if not slide.startswith('product:')]
                
                # Добавляем текущий слайд в историю только если это не товар
                if current_slide != 'S01' and current_slide not in filtered_slides and not current_slide.startswith('product:'):
                    filtered_slides.append(current_slide)

                # Ограничиваем историю
                if len(filtered_slides) > 10:
                    filtered_slides = filtered_slides[-10:]

                cursor.execute('''
                    INSERT OR REPLACE INTO user_states
                    (user_id, current_slide, previous_slides, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ''', (user_id, current_slide, json.dumps(filtered_slides, ensure_ascii=False)))
                conn.commit()
                
            logger.debug(f"✅ Состояние пользователя {user_id} обновлено: {current_slide}")
            logger.debug(f"📚 История навигации: {filtered_slides}")
            
        except Exception as e:
            logger.exception(f"❌ Ошибка обновления состояния пользователя: {e}")

    def get_user_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получение состояния пользователя"""
        try:
            row = self.fetch_one('SELECT * FROM user_states WHERE user_id = ?', (user_id,))
            if row:
                state = dict(row)
                state['previous_slides'] = json.loads(state.get('previous_slides') or '[]')
                return state
            return None
        except Exception as e:
            logger.exception(f"❌ Ошибка получения состояния пользователя: {e}")
            return None

    # -------------------------
    # МЕТОДЫ ОРДЕРОВ / ОПЕРАЦИЙ
    # -------------------------
    def create_order(self, user_id: int, cart_data: dict, total: float,
                     contact_info: dict = None, delivery_method: str = None,
                     delivery_address: str = None, pvz_address: str = None,  # ← ДОБАВЛЕН pvz_address
                     payment_method: str = 'cash', payment_id: str = None) -> int:
        """Создание заказа с расширенными данными (включая payment_id). Возвращает order_id или -1."""
        
        # 🔥 НОВАЯ ПРОВЕРКА: убедимся, что есть контакты для отправки чека
        if contact_info:
            email = contact_info.get('email')
            phone = contact_info.get('phone')
            if not email and not phone:
                logger.error(f"❌ Попытка создать заказ без контактов для пользователя {user_id}")
                return -1
        else:
            logger.error(f"❌ Попытка создать заказ без contact_info для пользователя {user_id}")
            return -1
        
        try:
            with self._lock:
                conn = self.get_connection()
                cursor = conn.cursor()

                cart_json = json.dumps(cart_data, ensure_ascii=False)
                contact_json = json.dumps(contact_info or {}, ensure_ascii=False)

                cursor.execute('''
                    INSERT INTO orders
                    (user_id, order_data, total_amount, contact_info, delivery_method,
                     delivery_address, pvz_address, payment_method, payment_id, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
                ''', (user_id, cart_json, total, contact_json, delivery_method,
                      delivery_address, pvz_address, payment_method, payment_id))

                order_id = cursor.lastrowid
                conn.commit()

            logger.info(f"✅ Создан заказ #{order_id} для пользователя {user_id} на сумму {total} руб. "
                       f"Доставка: {delivery_method}, Адрес ПВЗ: {pvz_address}, Payment ID: {payment_id}")
            return order_id
        except Exception as e:
            logger.exception(f"❌ Ошибка создания заказа: {e}")
            return -1

    def update_order_payment_id(self, order_id: int, payment_id: str):
        """Обновление payment_id для заказа"""
        try:
            success = self.execute("UPDATE orders SET payment_id = ?, updated_at = CURRENT_TIMESTAMP WHERE order_id = ?", (payment_id, order_id))
            if success:
                logger.info(f"✅ Обновлен payment_id для заказа #{order_id}: {payment_id}")
            else:
                logger.error(f"❌ Не удалось обновить payment_id для заказа #{order_id}")
            return success
        except Exception as e:
            logger.exception(f"❌ Ошибка обновления payment_id: {e}")
            return False

    def get_user_orders(self, user_id: int):
        """Получить историю заказов пользователя"""
        try:
            rows = self.fetch_all('SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
            orders = []
            for row in rows:
                order = dict(row)
                if order.get('order_data'):
                    order['order_data'] = json.loads(order['order_data'])
                if order.get('contact_info'):
                    order['contact_info'] = json.loads(order['contact_info'])
                orders.append(order)
            return orders
        except Exception as e:
            logger.exception(f"❌ Ошибка получения заказов пользователя: {e}")
            return []

    def get_orders_by_status(self, status: str = None):
        """Получить заказы по статусу"""
        try:
            if status:
                rows = self.fetch_all('SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC', (status,))
            else:
                rows = self.fetch_all('SELECT * FROM orders ORDER BY created_at DESC')

            orders = []
            for row in rows:
                order = dict(row)
                if order.get('order_data'):
                    order['order_data'] = json.loads(order['order_data'])
                if order.get('contact_info'):
                    order['contact_info'] = json.loads(order['contact_info'])
                orders.append(order)
            return orders
        except Exception as e:
            logger.exception(f"❌ Ошибка получения заказов по статусу: {e}")
            return []

    def update_order_status(self, order_id: int, status: str):
        """Обновить статус заказа"""
        try:
            success = self.execute("UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE order_id = ?", (status, order_id))
            if success:
                logger.info(f"📝 Обновлен статус заказа #{order_id} на '{status}'")
                return True
            else:
                logger.error(f"❌ Не удалось обновить статус заказа #{order_id}")
                return False
        except Exception as e:
            logger.exception(f"❌ Ошибка обновления статуса заказа: {e}")
            return False

    def get_cart_count(self, user_id: int) -> int:
        """Получить общее количество единиц товара в корзине (СУММА quantity)"""
        try:
            row = self.fetch_one('SELECT SUM(quantity) as total FROM cart_items WHERE user_id = ?', (user_id,))
            return int(row['total']) if row and row['total'] is not None else 0
        except Exception as e:
            logger.exception(f"❌ Ошибка подсчета корзины: {e}")
            return 0

    def update_cart_quantity(self, user_id: int, product_id: str, quantity: int, variant_id: str = None) -> bool:
        """Обновить количество товара в корзине с учетом варианта"""
        try:
            if quantity <= 0:
                return self.remove_from_cart(user_id, product_id, variant_id)
            
            with self._lock:
                conn = self.get_connection()
                cursor = conn.cursor()
                if variant_id:
                    cursor.execute(
                        'UPDATE cart_items SET quantity = ? WHERE user_id = ? AND product_id = ? AND variant_id = ?',
                        (quantity, user_id, product_id, variant_id)
                    )
                else:
                    cursor.execute(
                        'UPDATE cart_items SET quantity = ? WHERE user_id = ? AND product_id = ? AND variant_id IS NULL',
                        (quantity, user_id, product_id)
                    )
                conn.commit()
            logger.info(f"✅ Обновлено количество товара {product_id} (variant={variant_id}) для пользователя {user_id}: {quantity}")
            return True
        except Exception as e:
            logger.exception(f"❌ Ошибка обновления количества товара: {e}")
            return False

    def get_order_by_payment_id(self, payment_id: str):
        """Получить заказ по payment_id"""
        try:
            row = self.fetch_one('SELECT * FROM orders WHERE payment_id = ?', (payment_id,))
            if row:
                order = dict(row)
                if order.get('order_data'):
                    order['order_data'] = json.loads(order['order_data'])
                if order.get('contact_info'):
                    order['contact_info'] = json.loads(order['contact_info'])
                return order
            return None
        except Exception as e:
            logger.exception(f"❌ Ошибка получения заказа по payment_id: {e}")
            return None

    def get_order_by_id(self, order_id: int):
        """Получить заказ по internal order_id"""
        try:
            row = self.fetch_one('SELECT * FROM orders WHERE order_id = ?', (order_id,))
            if row:
                order = dict(row)
                if order.get('order_data'):
                    order['order_data'] = json.loads(order['order_data'])
                if order.get('contact_info'):
                    order['contact_info'] = json.loads(order['contact_info'])
                return order
            return None
        except Exception as e:
            logger.exception(f"❌ Ошибка получения заказа по id: {e}")
            return None

    def get_recent_orders(self, limit: int = 10):
        """Получить последние заказы"""
        try:
            rows = self.fetch_all('SELECT * FROM orders ORDER BY created_at DESC LIMIT ?', (limit,))
            orders = []
            for row in rows:
                order = dict(row)
                if order.get('order_data'):
                    order['order_data'] = json.loads(order['order_data'])
                if order.get('contact_info'):
                    order['contact_info'] = json.loads(order['contact_info'])
                orders.append(order)
            return orders
        except Exception as e:
            logger.exception(f"❌ Ошибка получения последних заказов: {e}")
            return []

    def get_orders_count_by_status(self, status: str = None):
        """Получить количество заказов по статусу"""
        try:
            if status:
                row = self.fetch_one('SELECT COUNT(*) as count FROM orders WHERE status = ?', (status,))
            else:
                row = self.fetch_one('SELECT COUNT(*) as count FROM orders')
            return int(row['count']) if row and row['count'] is not None else 0
        except Exception as e:
            logger.exception(f"❌ Ошибка подсчета заказов: {e}")
            return 0


# Глобальный экземпляр базы данных
db = Database()