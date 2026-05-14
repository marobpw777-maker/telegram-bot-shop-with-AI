import logging
import uuid
from decimal import Decimal, ROUND_HALF_UP

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.ext import ContextTypes, CallbackQueryHandler

from core.hybrid_cart import hybrid_cart
from core.payment_system import YooKassaPayment
from core.database import db
from core.checkout_flow import checkout_manager
from core.notifications import notify_admins_about_order

logger = logging.getLogger(__name__)

payment_system = YooKassaPayment()

# --- Вспомогательные функции ---

def _fmt_amount(value) -> str:
    """Форматируем сумму в строку с двумя знаками (RUB)."""
    # Используем Decimal для корректного округления
    dec = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{dec:.2f}"

def _build_receipt_items(cart_items):
    """
    Преобразует элементы корзины в структуру items для receipt по требованиям ЮKassa.
    Каждый элемент:
    {
      "description": "...",
      "quantity": "1",
      "amount": {"value": "100.00", "currency": "RUB"},
      "vat_code": 1,
      "payment_mode": "full_payment",
      "payment_subject": "commodity"
    }
    """
    items = []
    for it in cart_items:
        title = it.get("title") or it.get("ProductTitle") or "Товар"
        qty = int(it.get("quantity") or it.get("qty") or 1)
        # Попытка взять price или unit_price — иначе берём total/qty
        price = it.get("price") or it.get("unit_price")
        if price is None:
            total = float(it.get("total", 0))
            price = total / qty if qty else total
        amount_value = _fmt_amount(float(price))
        items.append({
            "description": str(title)[:128],
            "quantity": str(qty),
            "amount": {"value": amount_value, "currency": "RUB"},
            "vat_code": int(it.get("vat_code", 1)),  # default vat_code = 1
            "payment_mode": it.get("payment_mode", "full_payment"),
            "payment_subject": it.get("payment_subject", "commodity")
        })
    return items

async def _create_order_in_db(user_id, cart_data, session, payment_method='online', payment_id=None):
    """
    Создаёт заказ в БД с начальным статусом 'pending' (для online).
    Возвращает order_id или None.
    """
    try:
        order_id = db.create_order(
            user_id=user_id,
            cart_data=cart_data,
            total=cart_data.get("total", 0),
            contact_info=session.get("contact_info", {}),
            delivery_method=session.get("delivery_method", "pickup"),
            delivery_address=session.get("delivery_address"),
            payment_method=payment_method,
            payment_id=payment_id  # если DB поддерживает этот парамет
        )
        # Если create_order вернул None или 0 — логируем ошибку
        if not order_id:
            logger.warning("❗ db.create_order вернул пустое значение order_id")
        return order_id
    except TypeError:
        # Если db.create_order не поддерживает payment_id в аргументах - попробуем без него
        try:
            order_id = db.create_order(
                user_id=user_id,
                cart_data=cart_data,
                total=cart_data.get("total", 0),
                contact_info=session.get("contact_info", {}),
                delivery_method=session.get("delivery_method", "pickup"),
                delivery_address=session.get("delivery_address"),
                payment_method=payment_method
            )
            return order_id
        except Exception as e:
            logger.exception(f"❌ Ошибка создания заказа в БД (fallback): {e}")
            return None
    except Exception as e:
        logger.exception(f"❌ Ошибка создания заказа в БД: {e}")
        return None

async def _save_payment_id_in_db(order_id, payment_id):
    """
    Пробуем сохранть payment_id в заказе несколькими способами (в зависимости от API db).
    Если не получилось — логируем и возвращаем False.
    """
    try:
        # Если есть метод специально для этого — используем
        if hasattr(db, "update_order_payment_id"):
            db.update_order_payment_id(order_id, payment_id)
            return True
        # Попробуем универсальный метод update_order_status - но он меняет только статус
        if hasattr(db, "execute") and callable(db.execute):
            try:
                db.execute("UPDATE orders SET payment_id = ? WHERE order_id = ?", (payment_id, order_id))
                return True
            except Exception:
                pass
        # Попробуем метод run или query
        if hasattr(db, "run") and callable(db.run):
            db.run("UPDATE orders SET payment_id = ? WHERE order_id = ?", (payment_id, order_id))
            return True
        # Попытка через SQL if fetch_one & generic interface
        if hasattr(db, "conn"):
            try:
                cur = db.conn.cursor()
                cur.execute("UPDATE orders SET payment_id = ? WHERE order_id = ?", (payment_id, order_id))
                db.conn.commit()
                return True
            except Exception:
                pass

        logger.warning("⚠️ Не удалось автоматически сохранить payment_id в БД. Реализуйте db.update_order_payment_id(order_id, payment_id).")
        return False
    except Exception as e:
        logger.exception(f"❌ Ошибка при сохранении payment_id в БД: {e}")
        return False

# --- Основные обработчики ---

async def handle_start_online_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Точка входа: пользователь нажал 'Оплатить онлайн'.
    - Создаём запись заказа со статусом pending
    - Формируем чек (items) и создаём платеж в YooKassa
    - Сохраняем payment_id в заказе
    - Отправляем пользователю кнопку оплаты (confirmation_url) и кнопку 'Я оплатил — проверить'
    """
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        cart_data = hybrid_cart.get_cart_items(user_id)
        if not cart_data or not cart_data.get("items"):
            await query.answer("❌ Корзина пуста!", show_alert=True)
            return

        session = checkout_manager.get_session(user_id)
        if not session:
            await query.answer("❌ Сессия оформления не найдена", show_alert=True)
            return

        # 1) Создаём заказ в БД в статусе pending (payment_method=online)
        order_id = await _create_order_in_db(user_id, cart_data, session, payment_method='online', payment_id=None)
        if not order_id:
            await query.answer("❌ Ошибка создания заказа. Попробуйте позже.", show_alert=True)
            return

        # 2) Формируем items для receipt
        items = _build_receipt_items(cart_data.get("items", []))
        customer_email = session.get("contact_info", {}).get("email")
        # 3) Описание для платежа
        description = f"Order #{order_id} — Solid Simple"

        # 4) Создаём платёж через YooKassa
        amount = cart_data.get("total", 0)
        payment_resp = payment_system.create_payment(
            amount=float(amount),
            description=description,
            user_id=user_id,
            order_id=order_id,
            customer_email=customer_email,
            items=items
        )

        if not payment_resp:
            # В случае ошибки удаления/отметки — оставим заказ в pending, сообщим админу
            logger.error("❌ YooKassa.create_payment вернул None")
            await query.edit_message_text("❌ Ошибка при создании платежа. Попробуйте позже.")
            return

        payment_id = payment_resp.get("id")
        confirmation_url = payment_resp.get("confirmation_url")

        # 5) Сохраняем payment_id в базе
        saved = await _save_payment_id_in_db(order_id, payment_id)
        if not saved:
            logger.warning("⚠️ payment_id не удалось сохранить автоматическим методом в БД. Проверьте структуру БД.")

        # Также можно записать в context.user_data для быстрого доступа
        context.user_data['last_payment_id'] = payment_id
        context.user_data['last_order_id'] = order_id

        # 6) Отправляем пользователю ссылку на оплату (Inline-кнопка URL) + кнопку проверить оплату
        buttons = []
        if confirmation_url:
            buttons.append([InlineKeyboardButton("💳 Оплатить сейчас", url=confirmation_url)])
        else:
            # fallback: если нет url (редиректный тип не отдал), даём текст с инструкцией
            await query.message.reply_text("🔗 Ссылка на оплату не получена. Попробуйте снова позже.")

        # Кнопка проверки статуса
        if payment_id:
            buttons.append([InlineKeyboardButton("✅ Я оплатил — проверить", callback_data=f"check_payment:{payment_id}")])

        # Добавим кнопку отмены/альтернативы
        buttons.append([InlineKeyboardButton("💰 Оплата при получении", callback_data="checkout:payment_cash")])
        buttons.append([InlineKeyboardButton("🏠 Главная", callback_data="nav:home")])

        # Убираем reply-клавиатуру если была
        try:
            await query.message.reply_text(
                f"💳 Сумма к оплате: { _fmt_amount(amount) } RUB\n\nНажмите кнопку ниже, чтобы перейти к оплате.",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except Exception:
            # если edit message нужен:
            try:
                await query.edit_message_text(
                    f"💳 Сумма к оплате: { _fmt_amount(amount) } RUB\n\nНажмите кнопку ниже, чтобы перейти к оплате.",
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
            except Exception as e:
                logger.exception(f"Ошибка отправки сообщения с ссылкой на оплату: {e}")
                await query.answer("❌ Ошибка отправки ссылки на оплату", show_alert=True)

        # Логирование
        logger.info(f"🔔 Пользователь {user_id} начал оплату order_id={order_id} payment_id={payment_id}")

    except Exception as e:
        logger.exception(f"❌ Ошибка в handle_start_online_payment: {e}")
        try:
            await update.callback_query.answer("❌ Ошибка при старте оплаты", show_alert=True)
        except Exception:
            pass

async def handle_payment_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка статуса платежа YooKassa (callback: check_payment:<payment_id>)"""
    try:
        query = update.callback_query
        await query.answer()

        data = query.data or ""
        parts = data.split(":", 1)
        if len(parts) != 2:
            await query.answer("❌ Неверный формат проверки платежа", show_alert=True)
            return
        payment_id = parts[1]

        logger.info(f"🔍 Проверка статуса платежа: {payment_id}")
        status = payment_system.check_payment_status(payment_id)

        if not status:
            await query.answer("❌ Не удалось проверить статус платежа. Попробуйте позже.", show_alert=True)
            return

        if status in ("succeeded", "paid"):
            # Находим заказ по payment_id
            order_data = db.fetch_one("SELECT * FROM orders WHERE payment_id = ?", (payment_id,))
            if not order_data:
                await query.answer("❌ Заказ не найден (свяжитесь с поддержкой)", show_alert=True)
                return

            # Приведём order_data к dict, если это sqlite3.Row или namedtuple
            try:
                if hasattr(order_data, "_fields"):
                    order_data = dict(order_data)
            except Exception:
                pass

            order_id = order_data.get("order_id")
            user_id = order_data.get("user_id")

            # Обновляем статус заказа
            try:
                db.update_order_status(order_id, "paid")
            except Exception:
                logger.warning("⚠️ db.update_order_status не сработал (проверьте реализацию)")

            # Очищаем корзину и завершаем сессию
            try:
                hybrid_cart.clear_cart(user_id)
            except Exception:
                pass
            try:
                checkout_manager.complete_checkout(user_id)
            except Exception:
                pass

            # Подготовим данные для пользователю (email/phone/address/total)
            try:
                contact_info = order_data.get("contact_info") or {}
                # contact_info мог быть сохранён как JSON-строка — попытаемся распарсить
                if isinstance(contact_info, str):
                    try:
                        contact_info = json.loads(contact_info)
                    except Exception:
                        contact_info = {}

                email = contact_info.get("email") or order_data.get("customer_email") or "Не указан"
                phone = contact_info.get("phone") or order_data.get("customer_phone") or "Не указан"
                address = order_data.get("delivery_address") or contact_info.get("address") or "Адрес не указан"
                total_val = order_data.get("total") if order_data.get("total") is not None else order_data.get("total_amount") or 0
                total_str = _fmt_amount(total_val)
            except Exception:
                email = "Не указан"
                phone = "Не указан"
                address = "Адрес не указан"
                total_str = _fmt_amount(order_data.get("total", 0))

            # Ответ пользователю — тёплое сообщение (замена стандартного)
            try:
                customer_text = f"""🎉 Ура — ваш заказ принят и оплачен! Огромное спасибо, что выбрали нас — нам очень приятно помогать вам делать дом уютнее 💛

📦 Заказ: *#{order_id}*
📍 Доставка: {address}
📞 Мы свяжемся по телефону: {phone}
✉️ Почта: {email}
💳 Оплачено: *{total_str} ₽*

Как это будет происходить?
Мы собираем и упаковываем ваш заказ вручную. В ближайшие часы с вами свяжется наш менеджер, чтобы согласовать удобную дату и время доставки. Курьер доставит заказ прямо до двери — без трек-номера, зато с человеческим вниманием: приедем в согласованное время и заранее позвоним.

Если хотите ускорить — напишите сюда или в @solid_simple_support, и мы всё уточним.

Спасибо, что доверяете нам. Мы постараемся превзойти ваши ожидания ✨"""
                await query.edit_message_text(
                    customer_text,
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🏠 Главная", callback_data="nav:home")],
                        [InlineKeyboardButton("📦 Каталог", callback_data="cat:catalog")],
                        [InlineKeyboardButton("💬 Поддержка", url="https://t.me/solid_simple_support")]
                    ])
                )
            except Exception as e:
                logger.exception(f"Ошибка отправки уютного сообщения покупателю: {e}")
                # fallback — старое простое сообщение
                try:
                    await query.edit_message_text(
                        "🎉 *Оплата прошла успешно!*\n\n"
                        f"📋 *Заказ:* #{order_id}\n"
                        "✅ *Статус:* оплачен\n\n"
                        "Спасибо! Мы свяжемся с вами для подтверждения доставки.",
                        parse_mode='Markdown',
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🏠 Главная", callback_data="nav:home")],
                            [InlineKeyboardButton("📦 Каталог", callback_data="cat:catalog")]
                        ])
                    )
                except Exception:
                    pass

            # Уведомляем админов
            try:
                await notify_admins_about_payment(context.bot, order_id, payment_id)
            except Exception:
                # В файле handlers/payment_handlers.py есть собственная notify_admins_about_payment ниже,
                # но мы вызываем ту, что доступна в глобальной области (если есть) — иначе локальная сработает.
                try:
                    await notify_admins_about_order(context.bot, order_id, order_data, total_val)
                except Exception:
                    logger.exception("Ошибка уведомления админов после оплаты (fallback)")

        elif status in ("pending", "waiting_for_capture"):
            await query.answer("⏳ Платёж ещё обрабатывается. Проверьте через минуту.", show_alert=True)
        elif status in ("canceled", "failed"):
            await query.edit_message_text(
                "❌ Платёж отменён или не прошёл.\n\n"
                "Вы можете попробовать оплатить снова или выбрать оплату при получении.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Попробовать снова", callback_data="checkout:payment")],
                    [InlineKeyboardButton("💰 Оплата при получении", callback_data="checkout:payment_cash")],
                    [InlineKeyboardButton("🏠 Главная", callback_data="nav:home")]
                ])
            )
        else:
            logger.warning(f"Неожиданный статус от YooKassa: {status}")
            await query.answer(f"Статус: {status}", show_alert=True)

    except Exception as e:
        logger.exception(f"❌ Ошибка в handle_payment_check: {e}")
        try:
            await update.callback_query.answer("❌ Ошибка при проверке платежа", show_alert=True)
        except Exception:
            pass

async def handle_order_without_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Оформление заказа без онлайн-оплаты (дублируется / оставлено для совместимости).
    """
    try:
        # Реализация сохранена из старого файла — делаем делегат
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        cart_data = hybrid_cart.get_cart_items(user_id)
        if not cart_data['items']:
            await query.answer("❌ Корзина пуста!", show_alert=True)
            return

        session = checkout_manager.get_session(user_id)
        if not session:
            await query.answer("❌ Сессия не найдена", show_alert=True)
            return

        order_id = await _create_order_in_db(user_id, cart_data, session, payment_method='cash')
        if not order_id:
            await query.answer("❌ Ошибка при создании заказа", show_alert=True)
            return

        hybrid_cart.clear_cart(user_id)
        checkout_manager.complete_checkout(user_id)

        await query.edit_message_text(
            f"✅ *Заказ #{order_id} принят!*\n\n"
            f"💎 Сумма заказа: {cart_data['total']} руб.\n\n"
            "Мы свяжемся с вами для подтверждения и отправки.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главная", callback_data="nav:home")]])
        )

        # Уведомляем админов
        await notify_admins_about_order(context.bot, order_id, session, cart_data['total'])

    except Exception as e:
        logger.exception(f"❌ Ошибка в handle_order_without_payment (payment_handlers): {e}")
        try:
            await update.callback_query.answer("❌ Ошибка при оформлении заказа", show_alert=True)
        except Exception:
            pass

async def notify_admins_about_payment(bot, order_id: int, payment_id: str):
    """Уведомление админов об успешной оплате (или факте оплаты)"""
    try:
        text = "💳 <b>ОПЛАЧЕН ЗАКАЗ!</b>\n\n"
        text += f"📋 <b>Заказ:</b> #{order_id}\n"
        text += f"💳 <b>Payment ID:</b> {payment_id}\n\n"
        text += "✅ <b>Требует подготовки к отправке!</b>"

        from core.config import ADMIN_IDS
        for admin_id in (ADMIN_IDS or []):
            try:
                await bot.send_message(chat_id=admin_id, text=text, parse_mode='HTML')
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")
    except Exception as e:
        logger.exception(f"❌ Ошибка notify_admins_about_payment: {e}")

def setup_payment_handlers(application):
    """Регистрация обработчиков платежей"""
    # Начало оплаты (кнопка "Оплатить онлайн" должна посылать callback "start_payment:1" или "checkout:payment_online"
    # В навигации — вызываем handle_start_online_payment при выборе "checkout:payment_online".
    application.add_handler(CallbackQueryHandler(handle_start_online_payment, pattern=r'^(checkout:payment_online|start_payment:online)$'), group=0)

    # Проверка статуса платежа
    application.add_handler(CallbackQueryHandler(handle_payment_check, pattern=r"^check_payment:"), group=0)

    # Заказ без онлайн-оплаты
    application.add_handler(CallbackQueryHandler(handle_order_without_payment, pattern=r"^order:without_payment$"), group=0)

    # Обработка возврата (если нужно)
    application.add_handler(CallbackQueryHandler(handle_order_without_payment, pattern=r"^yookassa_return:"), group=0)

    logger.info("✅ Payment handlers успешно зарегистрированы")
