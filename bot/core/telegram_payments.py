import logging
import json
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any, Optional

from telegram import LabeledPrice, Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from core.config import PROVIDER_TOKEN, DELIVERY_METHODS, CONTACT_INFO
from core.database import db
from core.hybrid_cart import hybrid_cart
from core.notifications import notify_admins_about_payment

logger = logging.getLogger(__name__)


def _to_decimal_two(val) -> Decimal:
    if isinstance(val, Decimal):
        return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    try:
        return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _to_kopecks_from_decimal(dec: Decimal) -> int:
    return int((dec * Decimal(100)).to_integral_value(rounding=ROUND_HALF_UP))


def _decimal_to_rubles_float(dec: Decimal) -> float:
    return float(dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class TelegramPayments:
    def __init__(self):
        self.provider_token = PROVIDER_TOKEN
        if not self.provider_token:
            logger.warning("⚠️ PROVIDER_TOKEN не задан — Telegram Payments не будут работать")
        else:
            logger.info("✅ TelegramPayments инициализирован")

    def _build_prices_and_provider_receipt(self, session_data: dict):
        """
        Возвращает:
         - prices: List[LabeledPrice] (amount в КОПЕЙКАХ int)
         - provider_data_json: str (receipt для YooKassa) — ВНУТРИ: {"receipt": {...}}
         - total_dec: Decimal (итог в рублях)
        """
        cart = session_data.get("cart_data", {}) or {}
        items = cart.get("items", []) or []
        total_from_session = Decimal(str(cart.get("total", 0))) if cart else Decimal("0.00")

        prices: List[LabeledPrice] = []
        # Устанавливаем tax_system_code = 2 для УСН (доходы)
        receipt = {"items": [], "tax_system_code": 2}
        sum_kopecks = 0

        for it in items:
            title = str(it.get("title") or it.get("ProductTitle") or "Товар")[:128]
            qty = int(it.get("quantity") or it.get("qty") or 1)

            unit_raw = it.get("price") or it.get("unit_price")
            if unit_raw is None:
                total_item_raw = Decimal(str(it.get("total", 0)))
                unit_price_dec = (total_item_raw / qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if qty else Decimal("0.00")
            else:
                unit_price_dec = _to_decimal_two(unit_raw)

            item_total_dec = (unit_price_dec * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            item_kopecks = _to_kopecks_from_decimal(item_total_dec)

            if item_kopecks < 0:
                item_kopecks = 0

            prices.append(LabeledPrice(label=title[:64], amount=item_kopecks))
            sum_kopecks += item_kopecks

            # receipt expects amount.value in RUB (float)
            receipt["items"].append({
                "description": title[:128],
                "quantity": qty,
                "amount": {"value": _decimal_to_rubles_float(item_total_dec), "currency": "RUB"},
                "vat_code": 1,
                "payment_mode": "full_payment",
                "payment_subject": "commodity"
            })

        # delivery handling (if session_data contains delivery info)
        delivery_price = Decimal("0.00")
        delivery_label = "Доставка"
        delivery_method = session_data.get("delivery_method")
        if delivery_method:
            dm = DELIVERY_METHODS.get(delivery_method)
            if dm:
                delivery_label = dm.get("name", delivery_label)
                delivery_price = Decimal(str(dm.get("cost", 0)))
            else:
                logger.warning(f"⚠️ Неизвестный метод доставки: {delivery_method}")

        if delivery_price > 0:
            delivery_kopecks = _to_kopecks_from_decimal(delivery_price)
            prices.append(LabeledPrice(label=delivery_label[:64], amount=delivery_kopecks))
            sum_kopecks += delivery_kopecks
            receipt["items"].append({
                "description": delivery_label[:128],
                "quantity": 1,
                "amount": {"value": _decimal_to_rubles_float(delivery_price), "currency": "RUB"},
                "vat_code": 1,
                "payment_mode": "full_payment",
                "payment_subject": "service"
            })

        total_dec = _to_decimal_two(total_from_session) + _to_decimal_two(str(delivery_price))
        total_kopecks = _to_kopecks_from_decimal(total_dec)

        # корректировка суммы, если разница
        if sum_kopecks != total_kopecks:
            diff = total_kopecks - sum_kopecks
            logger.info("ℹ️ Items sum (kop): %s, expected total (kop): %s, diff: %s", sum_kopecks, total_kopecks, diff)
            if prices:
                last = prices[-1]
                new_amount = last.amount + diff
                if new_amount < 0:
                    logger.error("❌ Корректировка дала отрицательное значение — используем единый элемент оплаты")
                    prices = [LabeledPrice(label="Оплата заказа", amount=total_kopecks)]
                    receipt["items"] = [{
                        "description": "Оплата заказа",
                        "quantity": 1,
                        "amount": {"value": _decimal_to_rubles_float(total_dec), "currency": "RUB"},
                        "vat_code": 1,
                        "payment_mode": "full_payment",
                        "payment_subject": "commodity"
                    }]
                    sum_kopecks = total_kopecks
                else:
                    prices[-1] = LabeledPrice(label=last.label, amount=new_amount)
                    if receipt["items"]:
                        new_last_dec = Decimal(new_amount) / Decimal(100)
                        receipt["items"][-1]["amount"]["value"] = _decimal_to_rubles_float(new_last_dec)
                    sum_kopecks = sum(p.amount for p in prices)
            else:
                prices = [LabeledPrice(label="Оплата заказа", amount=total_kopecks)]
                receipt["items"] = [{
                    "description": "Оплата заказа",
                    "quantity": 1,
                    "amount": {"value": _decimal_to_rubles_float(total_dec), "currency": "RUB"},
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "commodity"
                }]
                sum_kopecks = total_kopecks

        # Добавим customer, если есть
        contact_info = session_data.get("contact_info") or {}
        if contact_info.get("email") or contact_info.get("phone"):
            receipt.setdefault("customer", {})
            if contact_info.get("email"):
                receipt["customer"]["email"] = contact_info.get("email")
            if contact_info.get("phone"):
                receipt["customer"]["phone"] = contact_info.get("phone")

        # Гарантируем наличие tax_system_code перед сериализацией
        receipt.setdefault("tax_system_code", 2)
        provider_data_json = json.dumps({"receipt": receipt}, ensure_ascii=False)

        return prices, provider_data_json, total_dec

    async def send_invoice(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, session_data: dict) -> bool:
        """
        Отправляем send_invoice пользователю.
        Важное изменение: мы создаём заказ в БД (черновик) ДО отправки invoice,
        затем добавляем metadata (order_id + краткая информация о товарах + контакты)
        в provider_data_json, чтобы YooKassa получил metadata и при webhook прислал нам order_id.
        """
        try:
            if not self.provider_token:
                logger.error("❌ PROVIDER_TOKEN не задан")
                if update and getattr(update, "callback_query", None):
                    await update.callback_query.answer("Платежная система не настроена", show_alert=True)
                return False

            # Сначала соберём цены и provider receipt
            prices, provider_data_json, total_dec = self._build_prices_and_provider_receipt(session_data)
            total_kopecks = _to_kopecks_from_decimal(total_dec)

            if not prices:
                logger.error("❌ Пустой prices — отмена отправки")
                return False

            # Создаём (или используем) order_id — сохраняем черновой заказ с итоговой суммой
            order_id = session_data.get("order_id")
            if not order_id:
                try:
                    order_id = db.create_order(
                        user_id=user_id,
                        cart_data=session_data.get("cart_data", {}),
                        total=float(total_dec),
                        contact_info=session_data.get("contact_info", {}),
                        delivery_method=session_data.get("delivery_method"),
                        delivery_address=session_data.get("delivery_address"),
                        pvz_address=session_data.get("pvz_address"),
                        payment_method='online_telegram',
                        payment_id=None
                    )
                    if order_id > 0:
                        session_data['order_id'] = order_id
                except Exception as e:
                    logger.exception("❌ Ошибка создания чернового заказа: %s", e)
                    order_id = None

            # обогащаем provider_data_json метаданами для YooKassa
            try:
                prov = json.loads(provider_data_json) if provider_data_json else {}
            except Exception:
                prov = {}

            prov.setdefault("metadata", {})
            prov["metadata"].update({
                "order_id": order_id,
                "user_id": user_id,
                # краткий набор данных по товарам (без больших описаний)
                "items": [
                    {
                        "id": (it.get("ProductID") or it.get("id") or None),
                        "title": it.get("title") or it.get("ProductTitle") or "",
                        "qty": int(it.get("quantity") or it.get("qty") or 1),
                        "price": str(it.get("price") or it.get("unit_price") or it.get("total") or "")
                    } for it in (session_data.get("cart_data", {}) or {}).get("items", [])
                ],
                "contact": session_data.get("contact_info", {}) or {}
            })
            provider_data_json = json.dumps(prov, ensure_ascii=False)

            # payload — включаем order_id для лучшего трекинга
            payload = f"solid:{order_id or 'noid'}:{uuid.uuid4().hex}"

            # Debug log — поможет в диагностике
            debug_prices = [{"label": p.label, "amount": p.amount} for p in prices]
            debug_payload = {
                "chat_id": user_id,
                "title": (f"🛍️ Заказ #{order_id}" if order_id else "🛍️ Заказ в Solid Simple")[:32],
                "description": (session_data.get("description") or "Оплата заказа")[:255],
                "payload": payload,
                "currency": "RUB",
                "prices": debug_prices,
                "provider_data": json.loads(provider_data_json) if provider_data_json else None,
                "total_kopecks": total_kopecks,
                "order_id": order_id
            }
            logger.info("DEBUG INVOICE PAYLOAD: %s", json.dumps(debug_payload, ensure_ascii=False))

            # Сохраняем для pre_checkout и успешного платежа
            context.user_data['last_invoice_total_kopecks'] = total_kopecks
            context.user_data['last_invoice_prices'] = debug_prices
            context.user_data['last_order_id'] = order_id

            contact_info = session_data.get("contact_info") or {}
            need_email = bool(contact_info.get("email"))
            need_phone = bool(contact_info.get("phone"))

            # Отправляем invoice через Telegram
            try:
                await context.bot.send_invoice(
                    chat_id=user_id,
                    title=debug_payload["title"],
                    description=debug_payload["description"],
                    payload=payload,
                    provider_token=self.provider_token,
                    currency="RUB",
                    prices=prices,
                    start_parameter="solid_simple_order",
                    provider_data=provider_data_json,  # строка JSON
                    need_email=need_email,
                    need_phone_number=need_phone,
                    is_flexible=False
                )
                logger.info("✅ send_invoice с provider_data отправлен (order_id=%s)", order_id)
                return True

            except BadRequest as br:
                # Ловим конкретную ошибку от Telegram — если Currency_total_amount_invalid, пробуем без provider_data
                logger.exception("❌ send_invoice с provider_data упал: %s", br)
                if "Currency_total_amount_invalid" in str(br) or "total" in str(br).lower():
                    logger.warning("⚠️ Попробуем отправить send_invoice без provider_data (fallback)")
                    try:
                        await context.bot.send_invoice(
                            chat_id=user_id,
                            title=debug_payload["title"],
                            description=debug_payload["description"],
                            payload=payload,
                            provider_token=self.provider_token,
                            currency="RUB",
                            prices=prices,
                            start_parameter="solid_simple_order",
                            # provider_data omitted on purpose
                            need_email=need_email,
                            need_phone_number=need_phone,
                            is_flexible=False
                        )
                        logger.info("✅ send_invoice без provider_data отправлен (fallback)")
                        return True
                    except Exception as e2:
                        logger.exception("❌ Fallback send_invoice без provider_data также упал: %s", e2)
                        if update and getattr(update, "callback_query", None):
                            await update.callback_query.answer("Ошибка платежной формы", show_alert=True)
                        return False
                else:
                    logger.exception("❌ send_invoice упал с ошибкой: %s", br)
                    if update and getattr(update, "callback_query", None):
                        await update.callback_query.answer("Ошибка платежной формы", show_alert=True)
                    return False

        except Exception as e:
            logger.exception("❌ Ошибка send_invoice: %s", e)
            if update and getattr(update, "callback_query", None):
                try:
                    await update.callback_query.answer("Внутренняя ошибка", show_alert=True)
                except Exception:
                    pass
            return False
            
    async def handle_pre_checkout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Обработка pre_checkout_query от Telegram.
        Нужно обязательно подтверждать платеж.
        """
        query = update.pre_checkout_query
        if query:
            await query.answer(ok=True)
            # можно добавить логирование
            logger.info(f"✅ PreCheckoutQuery подтверждён: payload={query.invoice_payload}, user={query.from_user.id}")        

    async def handle_successful_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка успешного платежа"""
        try:
            msg = update.message
            if not msg or not getattr(msg, "successful_payment", None):
                return
            sp = msg.successful_payment
            provider_charge_id = sp.provider_payment_charge_id
            amount = sp.total_amount
            user_id = msg.from_user.id

            logger.info("🎉 SuccessfulPayment: user=%s provider_charge_id=%s amount=%s kop", user_id, provider_charge_id, amount)

            order = None
            if provider_charge_id:
                # Попробуем найти заказ, уже привязанный к платежу
                order = db.get_order_by_payment_id(provider_charge_id)

            if not order:
                # Фоллбек: используем последний созданный order_id в user_data
                last_order_id = context.user_data.get('last_order_id')
                if last_order_id:
                    order = db.get_order_by_id(int(last_order_id))
                    if order and provider_charge_id and not order.get('payment_id'):
                        db.update_order_payment_id(order['order_id'], provider_charge_id)

            if not order:
                try:
                    await msg.reply_text("🎉 Платёж получен, но заказ не найден. Свяжитесь с поддержкой: @solid_simple_support")
                except Exception:
                    pass
                if db and hasattr(db, 'get_order_by_payment_id'):
                    # предупредим админов о неопознанном платеже
                    for admin in getattr(__import__("core.config", fromlist=["ADMIN_IDS"]), "ADMIN_IDS"):
                        try:
                            await context.bot.send_message(
                                admin,
                                f"⚠️ Неопознанный платёж: user_id={user_id}, payment_id={provider_charge_id}, amount={amount/100:.2f} RUB"
                            )
                        except Exception as e:
                            logger.error(f"❌ Ошибка отправки уведомления админу {admin}: {e}")
                return

            # Приводим order к dict, если это sqlite3.Row
            if hasattr(order, '_fields'):  # sqlite3.Row
                order = dict(order)

            order_id = order['order_id']
            db.update_order_status(order_id, "paid")
            if provider_charge_id and order.get('payment_id') != provider_charge_id:
                db.update_order_payment_id(order_id, provider_charge_id)

            try:
                # Очистим корзину
                hybrid_cart.clear_cart(order['user_id'])
            except Exception:
                pass

            # Отправляем красивое сообщение покупателю
            try:
                order_details = db.get_order_by_id(order_id)
                total_amount = amount / 100
                contact_info = order_details.get('contact_info', {}) if order_details else {}
                # Убедимся, что contact_info — dict
                if isinstance(contact_info, str):
                    try:
                        contact_info = json.loads(contact_info)
                    except Exception:
                        contact_info = {}

                email = contact_info.get('email') or 'Не указан'
                phone = contact_info.get('phone') or contact_info.get('telephone') or 'Не указан'
                address = order_details.get('delivery_address') or contact_info.get('address') or 'Адрес не указан'
                total_str = f"{total_amount:.2f}"

                # Тёплое сообщение покупателю (вставлено по запросу)
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

                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📦 Смотреть каталог", callback_data="cat:catalog")],
                    [InlineKeyboardButton("🏠 Главная", callback_data="nav:home")],
                    [InlineKeyboardButton("💬 Поддержка", url="https://t.me/solid_simple_support")]
                ])

                await msg.reply_text(customer_text, parse_mode='Markdown', reply_markup=keyboard)
            except Exception as e:
                logger.error(f"❌ Ошибка отправки сообщения покупателю: {e}")
                await msg.reply_text(f"🎉 Оплата подтверждена. Номер заказа: #{order_id}\nСумма: {amount/100:.2f} RUB\nСпасибо!")

            # Отправляем уведомление администраторам с полными деталями
            try:
                logger.info(f"🔔 Отправка уведомления администраторам о платеже заказа #{order_id}")
                await notify_admins_about_payment(context.bot, order_id, provider_charge_id)
                logger.info(f"✅ Уведомление администраторам отправлено для заказа #{order_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки уведомления администраторам: {e}")

        except Exception as e:
            logger.exception("❌ Ошибка handle_successful_payment: %s", e)


# глобальный экземпляр
telegram_payments = TelegramPayments()
