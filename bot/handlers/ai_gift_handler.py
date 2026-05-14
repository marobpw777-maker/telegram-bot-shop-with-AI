# handlers/ai_gift_handler.py
import re
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters, CommandHandler

from core import shop_data
from typing import Optional
from core import telegram_utils
from core import hybrid_cart  # предполагается ваш модуль работы с корзиной

logger = logging.getLogger("handlers.ai_gift_handler")

# simple in-memory state to await clarifying answers (dev use only)
_pending_clarify = {}  # user_id -> dict {type: 'gender', data: {...}}

# detection
def _contains_gift_intent(text: str) -> bool:
    t = text.lower()
    return "подар" in t or "подберите" in t or "подарок" in t or "gift" in t

def _extract_budget(text: str) -> int:
    # try patterns like "до 5000", "5000 руб", "до 5 000", "до 5000р"
    m = re.search(r"до\s*([0-9\s]{3,7})", text)
    if not m:
        m = re.search(r"([0-9]{3,6})\s*(?:руб|р\b|₽)?", text)
    if not m:
        return 0
    try:
        raw = re.sub(r"\s+", "", m.group(1))
        return int(raw)
    except Exception:
        return 0

def _extract_gender(text: str) -> Optional[str]:
    t = text.lower()
    if "жен" in t or "дев" in t or "девуш" in t or "женщ" in t:
        return "female"
    if "муж" in t or "парень" in t or "мужчин" in t:
        return "male"
    return None

def _build_inline_markup_for_products(prod_list):
    buttons = []
    row = []
    for p in prod_list:
        pid = p.get("id")
        title = p.get("title") or p.get("name") or pid
        # individual add button
        cb = f"ADD:{pid}"
        row.append(InlineKeyboardButton(f"Добавить {title}", callback_data=cb))
        # every 2 buttons make a row
        if len(row) >= 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # Add 'add all' button
    ids = ",".join([str(p.get("id")) for p in prod_list])
    buttons.append([InlineKeyboardButton("Добавить все в корзину", callback_data=f"ADD_ALL:{ids}")])
    return InlineKeyboardMarkup(buttons)

async def process_gift_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Entry point to handle messages with gift intent.
    Can be called from your main message handler:
        if _contains_gift_intent(text): await process_gift_request(...)
    """
    user = update.effective_user
    uid = user.id
    text = (update.message.text or "").strip()
    budget = _extract_budget(text)
    gender = _extract_gender(text)

    # if gender unknown -> ask clarifying question and save state
    if not gender:
        # ask quick question
        await update.message.reply_text("Для кого подарок? Это мужчина или женщина (введите «мужчина» или «женщина»)?")
        _pending_clarify[uid] = {"type": "gift_gender", "text": text, "budget": budget}
        return

    # proceed recommend
    products = shop_data.recommend_products_for_gift(user_text=text, budget=budget or None, gender=gender, top_n=6)
    if not products:
        await update.message.reply_text("К сожалению, в каталоге не нашлось подходящих товаров. Попробуйте расширить бюджет или уточнить пожелания.")
        return

    # Compose reply with reasons
    lines = [
        f"Отлично — собрал для вас варианты подарков (бюджет: {'до ' + str(budget) + ' ₽' if budget else 'не указан'}).",
        ""
    ]
    for p in products:
        lines.append(shop_data.format_product_short(p))
    lines.append("")
    lines.append("Нажмите кнопку ниже, чтобы добавить всё в корзину, или добавляйте по одному.")

    text_to_send = "\n".join(lines)
    markup = _build_inline_markup_for_products(products)
    await update.message.reply_text(text_to_send, reply_markup=markup)

async def handle_followup_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    If we were awaiting clarifying answer (gender) — handle it here.
    Use this by registering a generic MessageHandler that delegates to this function first.
    """
    user = update.effective_user
    uid = user.id
    if uid not in _pending_clarify:
        return False  # not a followup - caller should continue normal flow
    st = _pending_clarify.pop(uid)
    t = (update.message.text or "").lower()
    if "муж" in t:
        gender = "male"
    elif "жен" in t or "дев" in t:
        gender = "female"
    else:
        # couldn't parse — ask again
        await update.message.reply_text("Я не расслышал — укажите, пожалуйста: мужчина или женщина?")
        _pending_clarify[uid] = st
        return True

    # call recommend using original text & budget from saved
    await process_gift_request(update, context) if False else None
    # we want to call the recommendation with resolved gender
    user_text = st.get("text") or (update.message.text or "")
    budget = st.get("budget") or 0
    products = shop_data.recommend_products_for_gift(user_text=user_text, budget=budget or None, gender=gender, top_n=6)
    if not products:
        await update.message.reply_text("Не нашлось подходящих товаров, попробуйте уточнить пожелания.")
        return True
    lines = [f"Отлично — по вашим данным я подобрал: (для { 'женщины' if gender=='female' else 'мужчины' })", ""]
    for p in products:
        lines.append(shop_data.format_product_short(p))
    lines.append("")
    lines.append("Кнопки для добавления ниже:")
    text_to_send = "\n".join(lines)
    markup = _build_inline_markup_for_products(products)
    await update.message.reply_text(text_to_send, reply_markup=markup)
    return True

# Callback handler for ADD / ADD_ALL
async def gift_callback_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user = update.effective_user
    uid = user.id
    if data.startswith("ADD:"):
        pid = data.split(":", 1)[1]
        # try to add to cart using your hybrid_cart API (try few possible function names)
        added = False
        try:
            # common API: hybrid_cart.add_to_cart(user_id, product_id, qty)
            hybrid_cart.add_to_cart(uid, pid)
            added = True
        except Exception:
            try:
                hybrid_cart.add_product_to_cart(uid, pid, 1)
                added = True
            except Exception:
                try:
                    hybrid_cart.add(uid, pid, 1)
                    added = True
                except Exception as e:
                    logger.exception("Failed to add product to cart via hybrid_cart: %s", e)
        if added:
            await telegram_utils.safe_edit_message_text(query, f"Товар {pid} добавлен в корзину ✅")
        else:
            # fallback: send instruction
            await telegram_utils.safe_edit_message_text(query, f"Не удалось автоматически добавить {pid}. Отправьте в чат: ADD_TO_CART: {pid}")

    elif data.startswith("ADD_ALL:"):
        ids = data.split(":",1)[1]
        ids_list = [x.strip() for x in ids.split(",") if x.strip()]
        added_any = 0
        for pid in ids_list:
            try:
                hybrid_cart.add_to_cart(uid, pid)
                added_any += 1
            except Exception:
                try:
                    hybrid_cart.add_product_to_cart(uid, pid, 1)
                    added_any += 1
                except Exception:
                    logger.exception("Failed to add %s to cart", pid)
        await telegram_utils.safe_edit_message_text(query, f"Добавлено в корзину: {added_any} товаров.")
    else:
        await telegram_utils.safe_edit_message_text(query, "Нажата неизвестная кнопка.")
