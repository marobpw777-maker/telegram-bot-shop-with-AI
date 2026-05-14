# core/telegram_utils.py
import logging
from telegram import Message, Bot
from telegram.error import BadRequest

logger = logging.getLogger("core.telegram_utils")

async def safe_edit_message_text(query, text: str, **kwargs):
    """
    Попытаться edit_message_text, если BadRequest: try edit_message_caption, else send_message fallback.
    query — CallbackQuery
    """
    chat_id = query.message.chat_id if query and query.message else None
    try:
        await query.edit_message_text(text, **kwargs)
        return True
    except BadRequest as bre:
        msg = str(bre).lower()
        logger.warning("safe_edit_message_text BadRequest: %s", bre)
        # media-only message — try edit caption
        try:
            if "no text" in msg or "there is no text" in msg or "message to edit" in msg:
                await query.edit_message_caption(text, **kwargs)
                return True
        except Exception as e:
            logger.warning("safe_edit_message_text edit_message_caption failed: %s", e)
        # fallback: send normal message
        try:
            if chat_id:
                await query.message.bot.send_message(chat_id=chat_id, text=text)
                return True
        except Exception as e:
            logger.exception("safe_edit_message_text fallback send_message failed: %s", e)
        return False
    except Exception as e:
        logger.exception("safe_edit_message_text unexpected exception: %s", e)
        return False
