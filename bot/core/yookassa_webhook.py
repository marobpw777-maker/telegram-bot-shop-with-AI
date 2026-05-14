# core/yookassa_webhook.py
"""
DEPRECATED: Этот файл устарел и не используется в production.
Основной webhook для YooKassa находится в solidsimple.ru/public_html/yookassa/webhook.php
и обрабатывается через MySQL relay базу.

Асинхронный вебхук для приема уведомлений YooKassa.
"""

import aiohttp
from aiohttp import web

from core.config import (
    YOOKASSA_SHOP_ID,
    YOOKASSA_SECRET_KEY,
    YOOKASSA_WEBHOOK_PORT,
    YOOKASSA_WEBHOOK_HOST,
    YOOKASSA_ALLOWED_IP_RANGES,
    YOOKASSA_FORWARD_SECRET,
    BOT_TOKEN,
)
from core.database import db
from core.notifications import notify_admins_about_payment
from telegram import Bot

logger = logging.getLogger(__name__)

_bot_instance: Bot | None = None  # set from start_webhook_server


def _ip_is_allowed(remote_addr: str) -> bool:
    """Проверка, что IP отправителя принадлежит YooKassa (по конфигу)."""
    if not remote_addr:
        return False
    try:
        ip = ipaddress.ip_address(remote_addr)
        for net in (YOOKASSA_ALLOWED_IP_RANGES or []):
            try:
                if ip in ipaddress.ip_network(net):
                    return True
            except Exception:
                logger.debug("Bad IP range in config: %s", net)
        return False
    except Exception as e:
        logger.debug("Cannot parse remote addr: %s (%s)", remote_addr, e)
        return False


async def _fetch_payment_from_yookassa(payment_id: str) -> Dict[str, Any]:
    """Асинхронно получить полную информацию о платеже через API YooKassa."""
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY or not payment_id:
        logger.warning("YooKassa credentials or payment_id missing for fetch")
        return {}
    url = f"https://api.yookassa.ru/v3/payments/{payment_id}"
    auth = aiohttp.BasicAuth(login=YOOKASSA_SHOP_ID, password=YOOKASSA_SECRET_KEY)
    try:
        async with aiohttp.ClientSession(auth=auth) as sess:
            async with sess.get(url, timeout=15) as resp:
                text = await resp.text()
                if resp.status == 200:
                    return json.loads(text)
                else:
                    logger.warning("YooKassa GET payment failed: %s %s", resp.status, text)
    except Exception as e:
        logger.exception("Error fetching payment from YooKassa: %s", e)
    return {}


def _extract_event_and_object(payload: Dict[str, Any]) -> tuple[str | None, Dict[str, Any] | None]:
    """
    YooKassa may send different shapes:
    - { "type":"notification", "event":"payment.succeeded", "object":{...} }
    - { "event":"payment.succeeded", "object":{...} }
    We'll normalize.
    """
    if not isinstance(payload, dict):
        return None, None

    # Modern style with type=notification
    if payload.get("type") == "notification":
        event = payload.get("event") or payload.get("event_type")
        obj = payload.get("object") or {}
        return event, obj

    # Simpler style
    if "event" in payload and "object" in payload:
        return payload.get("event"), payload.get("object", {})

    # Fallback: sometimes nested
    # e.g. { "payment": {...}, "event": "payment.succeeded" } — treat payment as object
    if "payment" in payload:
        return payload.get("event") or payload.get("type"), payload.get("payment")

    return None, None


async def handle_yookassa(request: web.Request):
    """
    POST /yookassa/webhook
    Accepts either:
     - direct YooKassa requests (IP-checked), or
     - forwarded requests from your webhook.php that include header 'X-Forward-Secret'
    """
    remote = request.remote or request.headers.get("X-Real-IP") or request.transport.get_extra_info("peername")
    # remote may be tuple if using get_extra_info, normalize to string
    if isinstance(remote, tuple):
        remote = remote[0]

    # 1) Check forward secret header (preferred when using webhook.php forwarding)
    forward_secret = request.headers.get("X-Forward-Secret")
    if YOOKASSA_FORWARD_SECRET and forward_secret and forward_secret == YOOKASSA_FORWARD_SECRET:
        trusted_forward = True
    else:
        trusted_forward = False

    # 2) If not forwarded with secret - check IP ranges
    if not trusted_forward:
        if not _ip_is_allowed(str(remote)):
            logger.warning("Webhook received from non-allowed source: %s (forward_secret=%s)", remote, bool(forward_secret))
            return web.Response(status=403, text="Forbidden")

    try:
        data = await request.json()
    except Exception:
        # maybe raw body
        txt = await request.text()
        try:
            data = json.loads(txt)
        except Exception:
            logger.exception("Invalid json in webhook request (remote=%s)", remote)
            return web.Response(status=400, text="invalid json")

    logger.info("YooKassa webhook received (trusted_forward=%s): %s", trusted_forward, json.dumps(data, ensure_ascii=False)[:1000])

    event, obj = _extract_event_and_object(data)
    if not event or not obj:
        logger.info("Webhook payload doesn't contain event/object, ignoring.")
        return web.Response(status=200, text="OK")

    # Process relevant events
    if event in ("payment.succeeded", "payment.waiting_for_capture", "payment.canceled", "refund.succeeded"):
        payment = obj
        # try different places for payment id and metadata
        payment_id = payment.get("id") or payment.get("payment", {}).get("id")
        metadata = payment.get("metadata") or payment.get("payment", {}).get("metadata") or {}

        # if metadata empty, try to fetch full payment object from YooKassa API
        if not metadata and payment_id:
            fetched = await _fetch_payment_from_yookassa(payment_id)
            metadata = fetched.get("metadata") or {}

        order_id = None
        # metadata may contain numeric or string order_id
        if metadata:
            order_id = metadata.get("order_id") or metadata.get("orderId") or metadata.get("order")

        # If still no order_id - attempt to find in payment.description or other common places
        if not order_id:
            order_id = payment.get("description") or payment.get("receipt", {}).get("metadata", {}).get("order_id")

        if not order_id:
            logger.warning("YooKassa webhook: no order_id found in metadata for payment %s, event=%s", payment_id, event)
            return web.Response(status=200, text="OK")

        # normalize order id to int if possible
        try:
            order_id_int = int(order_id)
        except Exception:
            order_id_int = order_id  # keep as string if not int

        # Update order in local sqlite DB (mark paid/cancelled/whatever)
        try:
            if event == "payment.succeeded":
                # mark paid
                db.update_order_status(order_id_int, "paid")
            elif event == "payment.waiting_for_capture":
                db.update_order_status(order_id_int, "waiting_for_capture")
            elif event == "payment.canceled":
                db.update_order_status(order_id_int, "canceled")
            elif event == "refund.succeeded":
                db.update_order_status(order_id_int, "refunded")

            if payment_id:
                # save payment_id
                db.update_order_payment_id(order_id_int, payment_id)
        except Exception as e:
            logger.exception("Failed to update order in DB for webhook: %s", e)

        # Notify admins via bot (async task, non-blocking)
        try:
            global _bot_instance
            if _bot_instance is None:
                _bot_instance = Bot(token=BOT_TOKEN)
            # notify_admins_about_payment is async — schedule it
            asyncio.create_task(notify_admins_about_payment(_bot_instance, order_id_int, payment_id))
        except Exception as e:
            logger.exception("Failed to notify admins from webhook: %s", e)

    # respond quickly
    return web.Response(status=200, text="OK")


async def start_webhook_server(bot_instance: Bot | None = None):
    """Запустить aiohttp сервер (в background task)."""
    global _bot_instance
    if bot_instance:
        _bot_instance = bot_instance

    app = web.Application()
    app.router.add_post("/yookassa/webhook", handle_yookassa)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=YOOKASSA_WEBHOOK_HOST or "127.0.0.1", port=int(YOOKASSA_WEBHOOK_PORT or 8080))
    await site.start()
    logger.info("✅ YooKassa webhook server started at http://%s:%s/yookassa/webhook (internal)", YOOKASSA_WEBHOOK_HOST, YOOKASSA_WEBHOOK_PORT)

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("YooKassa webhook server stopping...")
        await runner.cleanup()
