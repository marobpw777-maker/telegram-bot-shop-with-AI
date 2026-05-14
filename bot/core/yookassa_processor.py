# core/yookassa_processor.py
import asyncio
import json
import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional, Dict, Any

try:
    import pymysql
except Exception:
    pymysql = None

from core.database import db
from core.config import ADMIN_IDS, SITE_PHOTOS_DIR  # импортируем чтобы избежать незнания
from core.notifications import notify_admins_about_payment  # предполагается, что есть
from telegram import Bot

logger = logging.getLogger(__name__)

# MySQL relay config (берём из окружения, main.py использует похожие)
MYSQL_HOST = os.getenv("YOOKASSA_RELAY_DB_HOST", os.getenv("MYSQL_HOST", "localhost"))
MYSQL_USER = os.getenv("YOOKASSA_RELAY_DB_USER", os.getenv("MYSQL_USER", ""))
MYSQL_PASS = os.getenv("YOOKASSA_RELAY_DB_PASS", os.getenv("MYSQL_PASSWORD", ""))
MYSQL_DB = os.getenv("YOOKASSA_RELAY_DB_NAME", os.getenv("MYSQL_DB", ""))

POLL_INTERVAL = int(os.getenv("YOOKASSA_RELAY_POLL_INTERVAL", "5"))

# Optional SMTP email settings (if you want to send receipt to customer)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "no-reply@solidsimple.ru")

# Small helper to open pymysql connection
def _get_mysql_conn():
    if pymysql is None:
        raise RuntimeError("pymysql is not installed. Install with: pip install pymysql")
    return pymysql.connect(host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASS,
                           db=MYSQL_DB, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)

def _format_admin_message(event_type: str, order: Optional[Dict[str, Any]], payload: Dict[str, Any]) -> str:
    """Собрать удобочитаемое сообщение админам."""
    lines = []
    lines.append("🧾 <b>Платёж:</b> " + (event_type or "-"))
    if order:
        lines.append(f"<b>Заказ:</b> #{order.get('order_id')}")
        total = order.get('total_amount') or order.get('total') or 0
        lines.append(f"<b>Сумма:</b> {float(total):.2f} {order.get('currency','RUB') if order.get('currency') else 'RUB'}")
        lines.append(f"<b>Payment ID:</b> {order.get('payment_id') or payload.get('id') or '-'}")
        lines.append(f"<b>Статус:</b> PAID")
    else:
        # fallback: try metadata/order_id from payload
        md = payload.get('metadata') or {}
        oid = md.get('order_id') or payload.get('order_id') or "-"
        amt = payload.get('amount', {}).get('value') if isinstance(payload.get('amount'), dict) else payload.get('amount')
        lines.append(f"<b>Заказ:</b> #{oid}")
        if amt:
            try:
                lines.append(f"<b>Сумма:</b> {float(amt):.2f} RUB")
            except Exception:
                lines.append(f"<b>Сумма:</b> {amt}")
        lines.append(f"<b>Payment ID:</b> {payload.get('id','-')}")
        lines.append(f"<b>Статус:</b> PAID")

    # Items
    items = None
    md = payload.get('metadata') or {}
    # try a few common places
    if md:
        items = md.get('items') or md.get('order_items') or md.get('items_list')
    if not items:
        # maybe items are stored inside order record
        try:
            if order and order.get('order_data'):
                od = order.get('order_data')
                if isinstance(od, str):
                    try:
                        od = json.loads(od)
                    except Exception:
                        od = None
                if isinstance(od, dict):
                    items = od.get('items') or od.get('cart') or od.get('lines')
        except Exception:
            items = None

    lines.append("\n📦 <b>Товары:</b>")
    if items and isinstance(items, (list, tuple)):
        for it in items:
            if isinstance(it, dict):
                title = it.get('title') or it.get('name') or str(it.get('id','item'))
                qty = it.get('qty') or it.get('quantity') or 1
                price = it.get('price') or it.get('amount') or it.get('unit_price') or "-"
                # price may be string
                try:
                    p = float(price)
                    price_str = f"{p:.2f} ₽"
                except Exception:
                    price_str = f"{price} ₽"
                lines.append(f" • {title} — {qty} × {price_str}")
            else:
                lines.append(f" • {str(it)}")
    else:
        lines.append(" • (нет детализированных товаров в metadata)")

    # Contacts
    contact = (md.get('contact') if md else None) or payload.get('customer') or {}
    lines.append("\n👤 <b>Контакты:</b>")
    phone = contact.get('phone') or contact.get('telephone') or "-"
    tg = contact.get('telegram') or contact.get('tg') or "-"
    email = contact.get('email') or "-"
    lines.append(f" • Телефон: {phone}")
    lines.append(f" • Telegram: {tg}")
    lines.append(f" • Email: {email}")

    # Delivery / PVZ
    pvz = (md.get('pvz') if md else None) or md.get('pvz_address') if md else None
    if not pvz:
        # try order.delivery fields
        if order:
            pvz = order.get('pvz_address') or order.get('delivery_address') or None

    lines.append("\n🚚 <b>Доставка:</b>")
    if pvz:
        lines.append(f" • ПВЗ: {pvz}")
    else:
        lines.append(" • (не указана)")

    # admin panel link (if you have site domain and order id)
    site = os.getenv("HOST_URL", "").rstrip("/")
    if site and order and order.get('order_id'):
        lines.append(f"\n🔎 <b>Ссылка в админ-панель:</b> {site}/admin/order/{order.get('order_id')}")
    elif site and md.get('order_id'):
        lines.append(f"\n🔎 <b>Ссылка в админ-панель:</b> {site}/admin/order/{md.get('order_id')}")

    return "\n".join(lines)

def _send_email_receipt(to_email: str, subject: str, html_body: str) -> bool:
    """Простая отправка письма (synchronous). Возвращает True/False."""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        logger.debug("SMTP not configured, skipping email send.")
        return False
    try:
        msg = EmailMessage()
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content("This is HTML receipt. If you see this, your mail client does not render HTML.")
        msg.add_alternative(html_body, subtype="html")

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        logger.info("✅ Receipt sent by email to %s", to_email)
        return True
    except Exception as e:
        logger.exception("❌ Failed to send receipt email: %s", e)
        return False

async def process_single_row(application: Optional[Any], row: Dict[str, Any]):
    """
    Обработать одну запись из yookassa_notifications:
    - распарсить payload
    - найти order_id (metadata/order_id или поле order_id)
    - обновить sqlite order (payment_id/status)
    - уведомить админов (через notify_admins_about_payment)
    - отправить email покупателю (если есть email)
    """
    nid = row.get("id")
    logger.debug("Processing notification id=%s", nid)
    payload_text = row.get("payload") or ""
    event_type = row.get("event_type") or row.get("event") or ""
    payment_id = row.get("payment_id") or ""

    try:
        payload = json.loads(payload_text) if payload_text else {}
    except Exception:
        payload = {}

    # try to get order_id
    order_id = row.get("order_id") or None
    if not order_id:
        # try metadata inside payload
        md = payload.get("metadata") or {}
        order_id = md.get("order_id") or None
    # sometimes order_id may be string: try int when possible
    try:
        if order_id is not None:
            order_id = int(order_id)
    except Exception:
        # leave as is
        pass

    # update sqlite order if we have order_id
    order = None
    if order_id:
        try:
            order = db.get_order_by_id(order_id)
            # update payment_id if present
            payid = payment_id or payload.get("id") or payload.get("payment_id")
            if payid:
                db.update_order_payment_id(order_id, payid)
            # set status to paid
            db.update_order_status(order_id, "paid")
            # refetch order
            order = db.get_order_by_id(order_id)
        except Exception as e:
            logger.exception("DB update failed for order %s: %s", order_id, e)

    # Send admin notification (format message)
    try:
        # if application provided, use its bot; else create temporary bot
        bot = None
        if application:
            bot = application.bot
        else:
            # fallback: create Bot from env if available
            from core.config import BOT_TOKEN
            bot = Bot(token=BOT_TOKEN)

        text = _format_admin_message(event_type or "payment.succeeded", order, payload)
        # notify_admins_about_payment may be async; call it if present
        try:
            # try using helper notify_admins_about_payment
            if callable(notify_admins_about_payment):
                # notify_admins_about_payment может быть coroutine
                res = notify_admins_about_payment(bot, order_id, payment_id)  # may be coroutine
                if asyncio.iscoroutine(res):
                    await res
                else:
                    # если функция отправляет сама — ничего
                    pass
            else:
                # fallback: send messages directly to ADMIN_IDS
                from core.config import ADMIN_IDS
                for aid in ADMIN_IDS:
                    await bot.send_message(chat_id=aid, text=text, parse_mode="HTML")
        except Exception:
            # fallback: send direct messages
            from core.config import ADMIN_IDS
            for aid in ADMIN_IDS:
                try:
                    await bot.send_message(chat_id=aid, text=text, parse_mode="HTML")
                except Exception as e:
                    logger.error("Failed to send admin msg to %s: %s", aid, e)

        logger.info("✅ Admins notified for notif id=%s order=%s", nid, order_id)
    except Exception as e:
        logger.exception("❌ Failed to notify admins for notif id=%s: %s", nid, e)

    # Optionally: send email receipt to customer if email present in metadata/contact
    try:
        md = payload.get("metadata") or {}
        contact = md.get("contact") or {}
        email = contact.get("email") or None
        if email:
            subject = f"Чек по заказу #{order_id or md.get('order_id','-')}"
            # build simple html receipt
            html_lines = ["<h3>Чек по заказу</h3>"]
            html_lines.append(f"<p>Заказ: #{order_id or md.get('order_id','-')}</p>")
            if order and order.get("order_data"):
                # try to show items
                od = order.get("order_data")
                if isinstance(od, str):
                    try:
                        od = json.loads(od)
                    except Exception:
                        od = None
                if isinstance(od, dict):
                    items = od.get("items") or od.get("cart") or []
                    html_lines.append("<ul>")
                    for it in items:
                        title = it.get("title") if isinstance(it, dict) else str(it)
                        qty = it.get("qty") if isinstance(it, dict) else 1
                        price = it.get("price") if isinstance(it, dict) else "-"
                        html_lines.append(f"<li>{title} — {qty} × {price} ₽</li>")
                    html_lines.append("</ul>")
            else:
                # fallback using metadata items
                items = md.get("items") or []
                if items:
                    html_lines.append("<ul>")
                    for it in items:
                        if isinstance(it, dict):
                            html_lines.append(f"<li>{it.get('title','item')} — {it.get('qty',1)} × {it.get('price','-')} ₽</li>")
                        else:
                            html_lines.append(f"<li>{str(it)}</li>")
                    html_lines.append("</ul>")

            html_lines.append(f"<p>Сумма: {order.get('total_amount') if order else md.get('total') or payload.get('amount',{}).get('value','-')}</p>")
            html_body = "\n".join(html_lines)
            # send synchronously (to not block event loop long)
            loop = asyncio.get_running_loop()
            sent = await loop.run_in_executor(None, _send_email_receipt, email, subject, html_body)
            if sent:
                logger.info("✅ Receipt email sent to %s", email)
    except Exception as e:
        logger.exception("❌ Error sending receipt email: %s", e)

async def poll_loop(application=None):
    """Main polling loop. Можно запускать как task: application.create_task(poll_loop(application))"""
    if pymysql is None:
        logger.error("pymysql not available — install it (pip install pymysql) to enable processor.")
        return

    logger.info("🔁 YooKassa processor started (poll interval=%ds)", POLL_INTERVAL)
    while True:
        try:
            conn = _get_mysql_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM yookassa_notifications WHERE processed=0 ORDER BY id ASC LIMIT 50")
                    rows = cur.fetchall()
                    if rows:
                        logger.info("⚡ Found %d new YooKassa notifications", len(rows))
                    for row in rows:
                        try:
                            await process_single_row(application, row)
                        except Exception:
                            logger.exception("Error processing row id=%s", row.get("id"))
                        # mark processed
                        try:
                            cur.execute("UPDATE yookassa_notifications SET processed=1, processed_at=NOW(), processed_by=%s WHERE id=%s",
                                        ("yookassa_processor", row.get("id")))
                            conn.commit()
                        except Exception:
                            logger.exception("Failed to mark notification id=%s processed", row.get("id"))
            finally:
                conn.close()
        except Exception as e:
            logger.exception("Error in YooKassa processor loop: %s", e)

        await asyncio.sleep(POLL_INTERVAL)
