# core/notifications.py
# -*- coding: utf-8 -*-
import logging
import html
import json
import os
import subprocess
from typing import Optional, Any, Dict, List
from datetime import datetime, timedelta

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from core.config import ADMIN_IDS, BOT_TOKEN, config
from core.database import db

logger = logging.getLogger(__name__)


def esc_html(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def _truncate(text: str, limit: int = 2000) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit-1] + "…"


def _format_money(v) -> str:
    try:
        return f"{float(v):.2f}"
    except Exception:
        return str(v or "0")


def _build_detailed_items_text(items: List[Dict]) -> (str, float):
    """Детальное описание товаров с артикулами и характеристиками"""
    lines = []
    total = 0.0
    if not items:
        return "📦 <i>Нет данных по товарам</i>", 0.0
    
    for it in items:
        # Основная информация о товаре
        title = it.get("title") or it.get("ProductTitle") or it.get("name") or "Без названия"
        article = it.get("article") or it.get("sku") or it.get("vendor_code") or "—"
        
        # Количество
        try:
            qty = int(it.get("qty") or it.get("quantity") or 1)
        except Exception:
            qty = 1
            
        # Цена и сумма
        price = it.get("price") or it.get("unit_price") or it.get("unit") or it.get("amount") or 0
        try:
            price_f = float(price)
        except Exception:
            price_f = 0.0
            
        subtotal = it.get("total") or (price_f * qty)
        try:
            subtotal_f = float(subtotal)
        except Exception:
            subtotal_f = price_f * qty
            
        total += subtotal_f
        
        # Дополнительные характеристики
        size = it.get("size") or it.get("размер") or None
        color = it.get("color") or it.get("цвет") or it.get("colour") or None
        weight = it.get("weight") or it.get("вес") or None
        dimensions = it.get("dimensions") or it.get("габариты") or None
        
        # Формируем строку товара
        item_line = f"• <b>{esc_html(title)}</b>"
        if article and article != "—":
            item_line += f" (арт: {esc_html(article)})"
        
        item_line += f" — {qty} шт × {esc_html(_format_money(price_f))} ₽ = {esc_html(_format_money(subtotal_f))} ₽"
        
        # Добавляем характеристики если есть
        characteristics = []
        if size:
            characteristics.append(f"размер: {esc_html(size)}")
        if color:
            characteristics.append(f"цвет: {esc_html(color)}")
        if weight:
            characteristics.append(f"вес: {esc_html(weight)}")
        if dimensions:
            characteristics.append(f"габариты: {esc_html(dimensions)}")
            
        if characteristics:
            item_line += f"\n  📋 {', '.join(characteristics)}"
            
        lines.append(item_line)
    
    return "\n".join(lines), total


def _build_delivery_details(order: Dict) -> str:
    """Детальная информация о доставке для администратора"""
    delivery_method = order.get("delivery_method") or "unknown"
    delivery_cfg = _cfg_value("DELIVERY_METHODS", {}) or {}
    delivery_name = delivery_cfg.get(delivery_method, {}).get("name") if isinstance(delivery_cfg, dict) else delivery_method
    
    lines = [f"🚚 <b>Тип доставки:</b> {esc_html(delivery_name)}"]
    
    # Информация в зависимости от типа доставки
    if delivery_method == "yandex_pickup":
        pvz_id = order.get("pvz_id") or order.get("yandex_pvz_id") or "-"
        pvz_address = order.get("pvz_address") or order.get("yandex_pvz_address") or "-"
        pvz_workhours = order.get("pvz_workhours") or order.get("work_hours") or "-"
        
        lines.extend([
            f"🏪 <b>ID ПВЗ:</b> {esc_html(pvz_id)}",
            f"📍 <b>Адрес ПВЗ:</b> {esc_html(pvz_address)}",
            f"🕒 <b>Часы работы:</b> {esc_html(pvz_workhours)}"
        ])
        
    elif delivery_method == "courier":
        delivery_address = order.get("delivery_address") or "-"
        delivery_date = order.get("delivery_date") or order.get("desired_date") or "-"
        delivery_time = order.get("delivery_time") or order.get("desired_time") or "-"
        entrance = order.get("entrance") or order.get("подъезд") or "-"
        floor = order.get("floor") or order.get("этаж") or "-"
        intercom = order.get("intercom") or order.get("домофон") or "-"
        apartment = order.get("apartment") or order.get("квартира") or "-"
        
        lines.extend([
            f"🏠 <b>Адрес доставки:</b> {esc_html(delivery_address)}",
            f"📅 <b>Желаемая дата:</b> {esc_html(delivery_date)}",
            f"⏰ <b>Желаемое время:</b> {esc_html(delivery_time)}"
        ])
        
        # Дополнительные детали адреса
        address_details = []
        if entrance and entrance != "-":
            address_details.append(f"подъезд {esc_html(entrance)}")
        if floor and floor != "-":
            address_details.append(f"этаж {esc_html(floor)}")
        if intercom and intercom != "-":
            address_details.append(f"домофон {esc_html(intercom)}")
        if apartment and apartment != "-":
            address_details.append(f"кв. {esc_html(apartment)}")
            
        if address_details:
            lines.append(f"🔍 <b>Детали адреса:</b> {', '.join(address_details)}")
            
    else:  # самовывоз
        pickup_address = order.get("pickup_address") or _cfg_value("CONTACT_INFO", {}).get("address", "-")
        pickup_workhours = order.get("pickup_workhours") or _cfg_value("CONTACT_INFO", {}).get("work_hours", "-")
        
        lines.extend([
            f"🏢 <b>Адрес самовывоза:</b> {esc_html(pickup_address)}",
            f"🕒 <b>Часы работы:</b> {esc_html(pickup_workhours)}"
        ])
    
    # Стоимость доставки
    delivery_cost = order.get("delivery_cost") or order.get("shipping_cost") or 0
    try:
        delivery_cost_f = float(delivery_cost)
        if delivery_cost_f > 0:
            lines.append(f"💰 <b>Стоимость доставки:</b> {esc_html(_format_money(delivery_cost_f))} ₽")
    except Exception:
        pass
    
    return "\n".join(lines)


def _get_customer_order_confirmation(order: Dict) -> str:
    """
    Генерирует современный текст подтверждения заказа для покупателя в стиле 2026 года
    с учетом психологии и трендов копирайтинга
    """
    order_id = order.get('id') or order.get('order_id') or '—'
    total = order.get('total', 0)
    delivery_method = order.get('delivery_method', 'pickup')
    
    # Форматируем дату и время
    current_time = datetime.now().strftime("%d.%m.%Y в %H:%M")
    expiry_date = (datetime.now() + timedelta(hours=48)).strftime("%d.%m.%Y до %H:%M")
    
    # Информация о товарах
    items_text = ""
    items = order.get('items') or []
    if items:
        items_lines = []
        for item in items:
            title = item.get('title') or item.get('name') or 'Товар'
            qty = item.get('qty') or item.get('quantity') or 1
            items_lines.append(f"• {title} — {qty} шт.")
        items_text = "\n".join(items_lines) + "\n\n"
    
    # Базовый текст (универсальная часть)
    base_text = (
        f"✨ <b>ВАШ ЗАКАЗ ПРИНЯТ!</b>\n\n"
        
        f"📦 <b>Заказ №{order_id}</b>\n"
        f"🕐 Создан: {current_time}\n\n"
        
        f"🛍️ <b>Состав заказа:</b>\n"
        f"{items_text}"
        
        f"💎 <b>Итого к оплате:</b> {_format_money(total)} ₽\n\n"
        
        "🎯 <b>Статус:</b> Передан в сборку\n"
        "⏱️ <b>Сборка:</b> 15-30 минут\n\n"
    )
    
    # Текст в зависимости от способа доставки
    if delivery_method == "pickup":
        delivery_text = (
            f"🏢 <b>САМОВЫВОЗ</b>\n\n"
            f"📍 <b>Адрес получения:</b>\n"
            f"г. Москва, ул. Валовая, дом 33\n"
            f"(между 1 и 2 подъездом, метро Добрынинская)\n\n"
            
            f"🕒 <b>График работы:</b>\n"
            f"• Пн-Пт: 10:00 - 20:00\n"
            f"• Сб-Вс: 11:00 - 18:00\n\n"
            
            f"⏳ <b>Срок резерва:</b> 48 часов\n"
            f"📅 <b>Заберите до:</b> {expiry_date}\n\n"
            
            f"💫 <b>Что дальше?</b>\n"
            f"Мы уже начали собирать ваш заказ! Вы получите уведомление, когда заказ будет готов к выдаче.\n\n"
            
            f"📞 <b>При получении:</b>\n"
            f"Назовите номер заказа и покажите этот чат\n\n"
        )
        
    elif delivery_method == "yandex_pickup":
        pvz_address = order.get("pvz_address", "выбранный пункт выдачи")
        delivery_text = (
            f"📦 <b>ДОСТАВКА В ПВЗ</b>\n\n"
            f"📍 <b>Пункт выдачи:</b>\n{pvz_address}\n\n"
            
            f"🚚 <b>Срок доставки:</b> 1-2 рабочих дня\n"
            f"📦 <b>Срок хранения:</b> 7 дней\n\n"
            
            f"💫 <b>Что дальше?</b>\n"
            f"1. Собираем заказ (15-30 мин)\n"
            f"2. Передаем в службу доставки\n"
            f"3. Вы получаете трек-номер\n"
            f"4. Забираете в ПВЗ\n\n"
            
            f"📱 <b>Вы получите:</b>\n"
            f"• SMS с кодом получения\n"
            f"• Уведомление о готовности\n\n"
        )
        
    elif delivery_method == "courier":
        delivery_address = order.get("delivery_address", "указанный адрес")
        delivery_date = order.get("delivery_date", "ближайшая возможная")
        delivery_text = (
            f"🚗 <b>КУРЬЕРСКАЯ ДОСТАВКА</b>\n\n"
            f"🏠 <b>Адрес доставки:</b>\n{delivery_address}\n\n"
            
            f"📅 <b>Дата доставки:</b> {delivery_date}\n"
            f"⏰ <b>Время:</b> Согласуем при подтверждении\n\n"
            
            f"💫 <b>Что дальше?</b>\n"
            f"1. Собираем заказ (15-30 мин)\n"
            f"2. Связываемся для подтверждения времени\n"
            f"3. Курьер звонит за 30 минут\n"
            f"4. Бесконтактная передача заказа\n\n"
            
            f"📞 <b>При получении:</b>\n"
            f"Назовите номер заказа и покажите этот чат\n\n"
        )
    
    else:
        delivery_text = (
            f"📦 <b>ИНФОРМАЦИЯ О ДОСТАВКЕ</b>\n\n"
            f"Мы свяжемся с вами в течение 15 минут для уточнения деталей доставки.\n\n"
        )
    
    # Заключительная часть
    footer = (
        "🌟 <b>Почему выбирают нас?</b>\n"
        "• 🚀 Мгновенная сборка заказов\n"
        "• 💎 Гарантия качества всех товаров\n" 
        "• 📱 Онлайн-отслеживание статуса\n"
        "• 🎁 Персональный подход к каждому клиенту\n\n"
        
        "❤️ <b>Благодарим за доверие!</b>\n"
        "Мы уже начали собирать ваш заказ с особой заботой.\n\n"
        
        "📞 <b>Контакты поддержки:</b>\n"
        "Telegram: @solidsimple_support\n"
        "Телефон: +7 (495) 123-45-67\n"
        "Email: info@solidsimple.ru\n\n"
        
        "⏳ <b>Статус заказа</b> вы можете отслеживать в этом чате. "
        "Мы будем держать вас в курсе на каждом этапе!"
    )
    
    return base_text + delivery_text + footer


def _cfg_value(name: str, default=None):
    """Get config value safely from config object or dict."""
    try:
        if hasattr(config, name):
            return getattr(config, name)
        if isinstance(config, dict):
            return config.get(name, default)
    except Exception:
        pass
    return default


ADMIN_PANEL_URL = _cfg_value("ADMIN_PANEL_URL", None)
SUPPORT_CHAT = _cfg_value("SUPPORT_CHAT", None)


def _send_via_sendmail(to: str, subject: str, body: str) -> bool:
    """
    Отправка почты через локальный sendmail
    Возвращает True если отправлено успешно
    """
    try:
        import platform
        if platform.system() == 'Windows':
            logger.warning("sendmail not available on Windows, skipping email send")
            return False
        
        sendmail_path = '/usr/sbin/sendmail'
        
        # Формируем email сообщение
        from_email = os.getenv('SMTP_FROM', 'info@solidsimple.ru')
        msg = f"From: {from_email}\nTo: {to}\nSubject: {subject}\nContent-Type: text/plain; charset=utf-8\n\n{body}"
        
        # Отправляем через sendmail
        p = subprocess.run(
            [sendmail_path, '-t', '-i'], 
            input=msg.encode('utf-8'), 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            timeout=30
        )
        
        if p.returncode == 0:
            logger.info(f"Email sent via sendmail to {to}")
            return True
        else:
            logger.warning(f"Sendmail failed: exit={p.returncode} stderr={p.stderr.decode('utf-8', 'ignore')[:200]}")
            return False
            
    except Exception as e:
        logger.exception(f"Sendmail error: {e}")
        return False


# -------------------------
# Helpers for resolving telegram handle
# -------------------------
async def _resolve_telegram_handle(bot, order) -> Optional[str]:
    """
    Попробовать получить @username:
     1) из order['contact_info']['telegram']
     2) если нет — по order['user_id'] через bot.get_chat(user_id)
    Возвращает строку вида '@username' или None.
    """
    try:
        contact = order.get("contact_info") or {}
        tg = contact.get("telegram") or contact.get("tg") or contact.get("username")
        if tg:
            tg = str(tg).strip()
            if tg and not tg.startswith("@"):
                tg = "@" + tg
            return tg

        uid = order.get("user_id") or order.get("customer_id") or order.get("user")
        if uid:
            try:
                uid_int = int(uid)
            except Exception:
                uid_int = None
            if uid_int:
                try:
                    user = await bot.get_chat(uid_int)  # may raise if bot never spoke with user
                    if getattr(user, "username", None):
                        return "@" + user.username
                except Exception:
                    return None
    except Exception:
        return None
    return None


# -------------------------
# Синхронная часть (используется poller в фоне)
# -------------------------
def notify_admins_text_via_api(text: str):
    """Синхронно отправляет текст админам через Telegram HTTP API (requests)."""
    try:
        if not BOT_TOKEN or not ADMIN_IDS:
            logger.warning("notify_admins_text_via_api: BOT_TOKEN or ADMIN_IDS missing")
            return
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        for admin in ADMIN_IDS:
            try:
                params = {"chat_id": admin, "text": text, "parse_mode": "HTML"}
                if ADMIN_PANEL_URL:
                    markup = {"inline_keyboard": [[{"text": "Открыть админку", "url": ADMIN_PANEL_URL}]]}
                    params["reply_markup"] = json.dumps(markup, ensure_ascii=False)
                r = requests.post(url, data=params, timeout=15)
                if r.status_code != 200:
                    logger.error("notify_admins_text_via_api: failed for %s: %s %s", admin, r.status_code, r.text[:200])
            except Exception:
                logger.exception("notify_admins_text_via_api: error sending to %s", admin)
    except Exception:
        logger.exception("notify_admins_text_via_api: unexpected error")


def send_email_copy(subject: str, body: str, to_addresses: Optional[List[str]] = None) -> bool:
    """
    Отправляет простое письмо на ADMIN_EMAIL через sendmail (synchronous).
    Возвращает True при успехе.
    """
    try:
        default_to = os.getenv("ADMIN_EMAIL", "info@solidsimple.ru")
        to_list = to_addresses or [default_to]
        
        success = False
        for to_address in to_list:
            if _send_via_sendmail(to_address, subject, body):
                success = True
                logger.info(f"send_email_copy: sent email to {to_address}")
            else:
                logger.warning(f"send_email_copy: failed to send email to {to_address}")
        
        return success
        
    except Exception:
        logger.exception("send_email_copy: failed to send email")
        return False


def notify_admins_about_order_via_api(order_id: int, payment_id: Optional[str] = None, payment_info: Optional[Dict] = None):
    """
    Синхронный нотификатор (для poller'а).
    Формирует текст по заказу и шлёт через HTTP API.
    """
    try:
        order = None
        try:
            order = db.get_order_by_id(order_id)
        except Exception:
            order = None

        if order:
            od = order.get("order_data") or {}
            if isinstance(od, dict) and od.get("items"):
                items = od.get("items", [])
            else:
                items = order.get("items") or []
            items_text, items_total = _build_detailed_items_text(items)

            cust = order.get("contact_info") or {}
            name = cust.get("name") or order.get("customer_name") or "-"
            phone = cust.get("phone") or order.get("customer_phone") or "-"
            email = cust.get("email") or order.get("customer_email") or "-"

            user_id_val = order.get("user_id") or order.get("customer_id") or order.get("user")
            user_id_line = f"\n<b>UserID:</b> {esc_html(user_id_val)}\n\n" if user_id_val else "\n\n"

            # Детали доставки
            delivery_details = _build_delivery_details(order)

            total = order.get("total") or items_total
            
            # Комментарий к заказу
            comment = order.get("comment") or cust.get("comment") or "-"
            if comment != "-":
                comment_text = f"📝 <b>Комментарий к заказу:</b> {esc_html(comment)}\n\n"
            else:
                comment_text = ""

            # Добавляем срок резервации для администратора
            expiry_info = ""
            delivery_method = order.get("delivery_method", "pickup")
            if delivery_method == "pickup":
                expiry_date = (datetime.now() + timedelta(hours=48)).strftime("%d.%m.%Y %H:%M")
                expiry_info = f"⏰ <b>Срок резерва до:</b> {expiry_date}\n\n"

            text = (
                f"📦 <b>НОВЫЙ ЗАКАЗ #{esc_html(order_id)}</b>\n\n"
                f"👤 <b>Клиент:</b> {esc_html(name)}\n"
                f"📞 <b>Телефон:</b> {esc_html(phone)}\n"
                f"✉️ <b>Email:</b> {esc_html(email)}"
                f"{user_id_line}"
                f"{expiry_info}"
                f"{delivery_details}\n\n"
                f"{comment_text}"
                f"<b>📋 СОСТАВ ЗАКАЗА:</b>\n{_truncate(items_text, 2000)}\n\n"
                f"💰 <b>СУММА ЗАКАЗА:</b> {esc_html(_format_money(total))} ₽\n"
                f"💳 <b>Оплата:</b> {esc_html(order.get('payment_method') or (payment_id and 'онлайн') or 'не указано')}\n"
                f"🔖 <b>Payment ID:</b> {esc_html(payment_id or order.get('payment_id') or '-')}\n\n"
                f"✅ <b>Требует срочной сборки!</b>\n"
                f"⏱️ <b>Время сборки:</b> 15-30 минут"
            )

            reply_markup = None
            if ADMIN_PANEL_URL:
                try:
                    url = f"{ADMIN_PANEL_URL}?order_id={order_id}"
                    reply_markup = {"inline_keyboard": [[{"text": "Открыть в админке", "url": url}]]}
                except Exception:
                    reply_markup = None

            try:
                api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                for admin in (ADMIN_IDS or []):
                    params = {"chat_id": admin, "text": text, "parse_mode": "HTML"}
                    if reply_markup:
                        params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
                    r = requests.post(api_url, data=params, timeout=15)
                    if r.status_code != 200:
                        logger.error("notify_admins_about_order_via_api: send failed %s -> %s %s", admin, r.status_code, r.text[:200])
            except Exception:
                logger.exception("notify_admins_about_order_via_api: HTTP send error")

            # send copy via email as well (non-blocking from caller)
            try:
                email_subject = f"СРОЧНО: Новый заказ #{order_id} — требует сборки"
                email_body = text + "\n\n(отправлено автоматически)"
                # note: synchronous; caller (poller) may run this in executor
                send_email_copy(email_subject, email_body, to_addresses=[os.getenv("ADMIN_EMAIL", "info@solidsimple.ru")])
            except Exception:
                logger.exception("notify_admins_about_order_via_api: failed to send email copy")

            return

        # fallback
        fallback = f"YooKassa event for order {order_id}, payment {payment_id}, info: {str(payment_info)[:1500]}"
        notify_admins_text_via_api(fallback)

    except Exception:
        logger.exception("notify_admins_about_order_via_api: unexpected error")


# -------------------------
# Асинхронная часть (для async handler'ов)
# -------------------------
async def _send_to_admins_async(bot, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    try:
        if not ADMIN_IDS:
            logger.warning("_send_to_admins_async: no ADMIN_IDS configured")
            return
        for admin in ADMIN_IDS:
            try:
                await bot.send_message(chat_id=admin, text=text, parse_mode="HTML", reply_markup=reply_markup)
            except Exception:
                logger.exception("Failed to send async admin message to %s", admin)
    except Exception:
        logger.exception("_send_to_admins_async error")


async def notify_admins_about_order(bot, order_id: int, order_data: Optional[Dict] = None, total: Optional[float] = None):
    """
    Async notification about new order (used from handlers).
    """
    try:
        order = order_data
        if not order:
            try:
                order = db.get_order_by_id(order_id)
            except Exception:
                order = None

        if not order:
            text = f"📦 <b>Новый заказ #{esc_html(order_id)}</b>\n\nДетали заказа не найдены."
            await _send_to_admins_async(bot, text)
            return

        od = order.get("order_data") or {}
        if isinstance(od, dict) and od.get("items"):
            items = od.get("items", [])
        else:
            items = order.get("items") or []

        items_text, items_total = _build_detailed_items_text(items)
        cust = order.get("contact_info") or {}
        name = cust.get("name") or order.get("customer_name") or "-"
        phone = cust.get("phone") or order.get("customer_phone") or "-"
        email = cust.get("email") or order.get("customer_email") or "-"
        
        # Детали доставки
        delivery_details = _build_delivery_details(order)

        total_amount = order.get("total") or total or items_total

        # attempt to resolve telegram handle async
        telegram_handle = await _resolve_telegram_handle(bot, order)
        if telegram_handle:
            name_display = f"{esc_html(name)} ({esc_html(telegram_handle)})"
        else:
            uid = order.get("user_id") or order.get("customer_id") or order.get("user")
            if uid:
                name_display = f"{esc_html(name)} (id:{esc_html(uid)})"
            else:
                name_display = esc_html(name)

        # Комментарий к заказу
        comment = order.get("comment") or cust.get("comment") or "-"
        if comment != "-":
            comment_text = f"📝 <b>Комментарий к заказу:</b> {esc_html(comment)}\n\n"
        else:
            comment_text = ""

        # Добавляем срок резервации для администратора
        expiry_info = ""
        delivery_method = order.get("delivery_method", "pickup")
        if delivery_method == "pickup":
            expiry_date = (datetime.now() + timedelta(hours=48)).strftime("%d.%m.%Y %H:%M")
            expiry_info = f"⏰ <b>Срок резерва до:</b> {expiry_date}\n\n"

        text = (
            f"📦 <b>НОВЫЙ ЗАКАЗ #{esc_html(order_id)}</b>\n\n"
            f"👤 <b>Клиент:</b> {name_display}\n"
            f"📞 <b>Телефон:</b> {esc_html(phone)}\n"
            f"✉️ <b>Email:</b> {esc_html(email)}\n\n"
            f"{expiry_info}"
            f"{delivery_details}\n\n"
            f"{comment_text}"
            f"<b>📋 СОСТАВ ЗАКАЗА:</b>\n{_truncate(items_text, 2000)}\n\n"
            f"💰 <b>СУММА ЗАКАЗА:</b> {esc_html(_format_money(total_amount))} ₽\n"
            f"💳 <b>Оплата:</b> {esc_html(order.get('payment_method') or '-')}\n"
        )

        reply_markup = None
        if ADMIN_PANEL_URL:
            url = f"{ADMIN_PANEL_URL}?order_id={order_id}"
            try:
                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть в админке", url=url)]])
            except Exception:
                reply_markup = None

        await _send_to_admins_async(bot, text + "\n\n✅ Требует срочной сборки! ⏱️ 15-30 минут", reply_markup=reply_markup)

    except Exception:
        logger.exception("notify_admins_about_order error (async)")


async def notify_admins_about_payment(bot, order_id: int, payment_id: str, order_data: Optional[Dict] = None):
    """
    Async: уведомление об успешной оплате.
    """
    try:
        # try to assemble basic header with client info
        name_display = "-"
        phone = "-"
        email = "-"

        if order_data:
            cust = order_data.get("contact_info") or {}
            name = cust.get("name") or "-"
            phone = cust.get("phone") or "-"
            email = cust.get("email") or "-"
            tg = cust.get("telegram") or cust.get("username")
            if tg and not tg.startswith("@"):
                tg = "@" + tg
            if tg:
                name_display = f"{esc_html(name)} ({esc_html(tg)})"
            else:
                name_display = esc_html(name)
        else:
            try:
                order = db.get_order_by_payment_id(payment_id)
            except Exception:
                order = None
            if order:
                cust = order.get("contact_info") or {}
                name = cust.get("name") or order.get("customer_name") or "-"
                phone = cust.get("phone") or order.get("customer_phone") or "-"
                email = cust.get("email") or order.get("customer_email") or "-"
                tg = await _resolve_telegram_handle(bot, order)
                if tg:
                    name_display = f"{esc_html(name)} ({esc_html(tg)})"
                else:
                    name_display = esc_html(name)

        # Детали доставки для оплаченного заказа
        delivery_details = ""
        if order_data:
            delivery_details = _build_delivery_details(order_data) + "\n\n"
        else:
            try:
                order = db.get_order_by_payment_id(payment_id)
            except Exception:
                order = None
            if order:
                delivery_details = _build_delivery_details(order) + "\n\n"

        text = (
            "💳 <b>ОПЛАЧЕН ЗАКАЗ!</b>\n\n"
            f"📋 <b>Заказ:</b> #{esc_html(order_id)}\n"
            f"👤 <b>Клиент:</b> {name_display}\n"
            f"📞 <b>Телефон:</b> {esc_html(phone)}\n"
            f"✉️ <b>Email:</b> {esc_html(email)}\n\n"
            f"{delivery_details}"
        )

        if order_data:
            items = order_data.get("items") or []
            items_text, _ = _build_detailed_items_text(items)
            if items_text:
                text += "<b>Товары:</b>\n" + _truncate(items_text, 1500) + "\n\n"
        else:
            try:
                order = db.get_order_by_payment_id(payment_id)
            except Exception:
                order = None
            if order:
                od = order.get("order_data") or {}
                items = od.get("items") if isinstance(od, dict) else order.get("items") or []
                items_text, _ = _build_detailed_items_text(items)
                if items_text:
                    text += "<b>Товары:</b>\n" + _truncate(items_text, 1500) + "\n\n"

        reply_markup = None
        if ADMIN_PANEL_URL:
            url = f"{ADMIN_PANEL_URL}?order_id={order_id}"
            try:
                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть в админке", url=url)]])
            except Exception:
                reply_markup = None

        text += "✅ <b>Требует срочной сборки и отправки!</b>"

        await _send_to_admins_async(bot, text, reply_markup=reply_markup)

    except Exception:
        logger.exception("notify_admins_about_payment error (async)")


async def notify_customer_about_order(bot, user_id: int, order_data: Dict):
    """
    Async: отправка современного уведомления покупателю об успешном оформлении заказа.
    Использует психологически продуманный текст в стиле 2026 года.
    """
    try:
        text = _get_customer_order_confirmation(order_data)
        
        await bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML"
        )
        
        logger.info(f"Sent modern customer notification for order {order_data.get('id')} to user {user_id}")
        
    except Exception as e:
        logger.exception(f"Failed to send modern customer notification: {e}")
        # Fallback на упрощенное сообщение
        try:
            fallback_text = (
                f"✅ Заказ принят!\n\n"
                f"📦 Номер: #{order_data.get('id', '—')}\n"
                f"💎 Сумма: {_format_money(order_data.get('total', 0))} руб.\n\n"
                f"Мы уже начали сборку вашего заказа!\n"
                f"Срок резерва: 48 часов\n\n"
                f"Следите за статусом в этом чате 📱"
            )
            await bot.send_message(chat_id=user_id, text=fallback_text)
        except Exception:
            logger.exception("Failed to send fallback customer notification")


def send_customer_email_notification(order_data: Dict, to_email: str):
    """
    Отправка современного email уведомления покупателю о заказе.
    """
    try:
        order_id = order_data.get('id', '')
        subject = f"✅ Ваш заказ #{order_id} принят! SolidSimple"
        
        # Создаем текстовую версию для email
        html_content = _get_customer_order_confirmation(order_data)
        
        # Конвертируем HTML в красивый plain text
        text_content = html_content
        # Убираем HTML теги
        import re
        text_content = re.sub(r'<[^>]+>', '', text_content)
        # Заменяем эмодзи на текстовые аналоги
        emoji_replacements = {
            "✨": "*",
            "📦": "->",
            "🕐": "Время:",
            "🛍️": "Товары:",
            "💎": "Сумма:",
            "🎯": "Статус:",
            "⏱️": "Сборка:",
            "🏢": "САМОВЫВОЗ",
            "📍": "Адрес:",
            "🕒": "График:",
            "⏳": "Срок резерва:",
            "📅": "Заберите до:",
            "💫": "Что дальше?",
            "📞": "При получении:",
            "📦": "ДОСТАВКА В ПВЗ",
            "🚚": "Срок доставки:",
            "📱": "Вы получите:",
            "🚗": "КУРЬЕРСКАЯ ДОСТАВКА",
            "🏠": "Адрес доставки:",
            "🌟": "Почему выбирают нас?",
            "❤️": "Благодарим за доверие!",
            "📞": "Контакты поддержки:",
            "⏳": "Статус заказа:"
        }
        
        for emoji, replacement in emoji_replacements.items():
            text_content = text_content.replace(emoji, replacement)
        
        # Добавляем персональное обращение
        text_content = f"Уважаемый клиент!\n\n{text_content}"
        
        success = _send_via_sendmail(to_email, subject, text_content)
        
        if success:
            logger.info(f"Sent modern customer email notification for order {order_data.get('id')} to {to_email}")
        else:
            logger.warning(f"Failed to send modern customer email notification for order {order_data.get('id')}")
            
        return success
        
    except Exception as e:
        logger.exception(f"Error sending modern customer email notification: {e}")
        return False


async def notify_customer_about_order_ready(bot, user_id: int, order_id: int, delivery_method: str):
    """
    Уведомление покупателя о готовности заказа.
    """
    try:
        if delivery_method == "pickup":
            text = (
                f"🎉 <b>ЗАКАЗ ГОТОВ К ВЫДАЧЕ!</b>\n\n"
                f"📦 <b>Заказ №{order_id}</b> собран и ожидает вас!\n\n"
                f"🏢 <b>Адрес самовывоза:</b>\n"
                f"г. Москва, ул. Валовая, дом 33\n"
                f"(между 1 и 2 подъездом, метро Добрынинская)\n\n"
                f"🕒 <b>График работы:</b>\n"
                f"• Пн-Пт: 10:00 - 20:00\n"
                f"• Сб-Вс: 11:00 - 18:00\n\n"
                f"⏳ <b>Не забудьте:</b> Заказ будет ждать вас 48 часов\n\n"
                f"📞 <b>При получении:</b> Назовите номер заказа\n\n"
                f"❤️ <b>Ждем вас!</b>"
            )
        else:
            text = (
                f"🎉 <b>ЗАКАЗ ГОТОВ!</b>\n\n"
                f"📦 <b>Заказ №{order_id}</b> собран и передан в доставку.\n\n"
                f"📱 Следите за обновлениями статуса в этом чате.\n\n"
                f"❤️ <b>Спасибо за выбор нашей компании!</b>"
            )
        
        await bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML"
        )
        
        logger.info(f"Sent order ready notification for order {order_id} to user {user_id}")
        
    except Exception as e:
        logger.exception(f"Failed to send order ready notification: {e}")