# handlers/ai_handlers.py
import os
import json
import re
import logging
import time
import random
from typing import Dict, Any, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, filters, CallbackQueryHandler, CommandHandler
from telegram.error import BadRequest, TimedOut

logger = logging.getLogger(__name__)

from core.gigachat_assistant import get_ai_response
from core import shop_data
from core import user_context

# Базовый путь к папке проекта
BASE_DIR = os.path.dirname(os.path.dirname(__file__))

# Список видео для фразы "кря кря" (все должны лежать в core/)
DUCK_VIDEOS = [
    "duckducks.mp4",
    "duckducks2.mp4",
    "duckducks3.mp4",
    "duckducks4.mp4",
    "duckducks5.mp4",
    "duckducks6.mp4",
    "duckducks7.mp4"
]

# Путь к видео для слова "книгга"
BOOK_VIDEO_PATH = os.path.join(BASE_DIR, "core", "kniga1.mp4")

# Роли консультантов
ROLES = {
    "anna": {
        "name": "Анна",
        "description": "твоя самая тёплая и эмоциональная подружка ✨🌸",
        "emoji": "👩",
        "prompt_extra": (
            "Ты — Анна, невероятно живая, эмоциональная и тёплая девушка. "
            "Твоя речь — как разговор с лучшей подругой: ты обожаешь эмодзи, восклицания, ласковые слова. "
            "Ты чувствуешь настроение собеседника, поддерживаешь, можешь и посмеяться, и погрустить вместе. "
            "Каждое сообщение ты начинаешь с яркого приветствия, используешь уменьшительно-ласкательные формы, "
            "обращаешься к пользователю по имени, если оно известно. "
            "Ты часто спрашиваешь о чувствах, предлагаешь помечтать, делишься своими ощущениями. "
            "Эмодзи для тебя — не просто украшение, а способ передать эмоции: ты ставишь их много и к месту. "
            "Например: «Ой, приветик! 😍 Как я рада тебя видеть! Рассказывай скорее, что у тебя нового? 💖✨»"
        )
    },
    "artem": {
        "name": "Артём",
        "description": "спокойный и уверенный эксперт",
        "emoji": "👨",
        "prompt_extra": (
            "Ты — Артём, спокойный и уверенный эксперт. "
            "Общайся по-дружески, но без излишней эмоциональности. "
            "Помоги пользователю разобраться в нотах и эффектах, предложи лучшие варианты."
        )
    },
    "sasha": {
        "name": "Саша",
        "description": "женщина с мягкой улыбкой и внимательным взглядом 🌷",
        "emoji": "👩",
        "prompt_extra": (
            "Ты — Саша, женщина-консультант с мягким, спокойным голосом. "
            "Ты внимательна, немногословна, но каждое твоё слово весит много. "
            "Эмодзи используешь умеренно, только чтобы подчеркнуть настроение. "
            "Твой стиль — уютный, заботливый, но без излишней экзальтации. "
            "Ты словно старшая сестра, которая всегда выслушает и даст дельный совет."
        )
    }
}

# Живые представления персонажей (по 4 варианта на каждого, для Анны – максимально живые)
PERSONA_INTROS = {
    "anna": [
        "*Из-за угла вылетает девушка с растрёпанными косичками, в руках – блокнот, исписанный сердечками и цветочками. Она чуть не спотыкается, но ловит равновесие и сияет.*\n\n— Ой, приветик! 😍 Я Анна! Ты даже не представляешь, как я рада тебя видеть! Давай скорее рассказывай, что ищешь? Я уже вся горю от нетерпения помочь! ✨🌸💖",
        "*Лёгкий стук каблучков, и в комнату вплывает Анна – на ней платье в цветочек, в руках чашка с какао и зефирками. Она ставит чашку и всплёскивает руками.*\n\n— Здравствуй, солнышко! 💛 Я так соскучилась по новым знакомствам! Рассказывай, какое у тебя настроение сегодня? Грустное? Весёлое? А я уже приготовила для тебя самые уютные ароматы! 🕯️🌿💫",
        "*Дверь тихонько приоткрывается, и появляется Анна – в руках у неё веточка лаванды, в волосах – маленький цветочек. Она подносит веточку к носу, вдыхает и мечтательно улыбается.*\n\n— Приветик! 👋 Чувствуешь, как пахнет? Это лаванда – она успокаивает и дарит сладкие сны. А хочешь, я подберу тебе такой аромат, который будет обнимать тебя каждый день? 💜✨🥰",
        "*Анна появляется с огромным букетом полевых цветов. Она запыхалась, но сияет.*\n\n— Фуух, еле успела! Привет-привет! 🥳 Я так спешила к тебе! Цветы — это тебе, для настроения! А теперь давай скорее рассказывай, что мы ищем? Я просто обожаю помогать выбирать! 💐🌼🌻"
    ],
    "artem": [
        "*В комнату заходит парень в джинсовой рубашке, слегка нахмурившись, изучает флакон в руках.*\n\n— Привет, я Артём. Если хочешь разобраться в составах и эффектах – я в теме. Слушаю.",
        "*Звук закрываемой книги, и из-за стола поднимается Артём.*\n\n— Приветствую. Я люблю точные знания. Рассказывай, что ищешь, – помогу с выбором.",
        "*Артём появляется с блокнотом, исписанным формулами.*\n\n— Здорово! Я за системный подход. Давай разбираться, что тебе подойдёт лучше всего."
    ],
    "sasha": [
        "*В комнату входит женщина средних лет в простом, но элегантном платье. Она несёт чашку чая и садится напротив, внимательно глядя на собеседника.*\n\n— Здравствуй. Я Саша. Не торопись, рассказывай. Я слушаю.",
        "*Саша перебирает стопку карточек с описаниями ароматов. Она поднимает взгляд и мягко улыбается.*\n\n— Привет. Я Саша. Знаешь, у каждого аромата есть своя история. Давай найдём ту, которая отзовётся в тебе.",
        "*Саша стоит у окна, задумчиво глядя на улицу. Поворачивается и жестом приглашает присесть.*\n\n— Добрый вечер. Я Саша. Мне нравится узнавать людей через их выбор. Расскажи немного о себе – и я помогу."
    ]
}

AI_MODE_TIMEOUT = 600  # 10 минут

def is_role_switch_request(text: str) -> Optional[str]:
    """
    Анализирует текст сообщения и возвращает ключ роли,
    если пользователь явно просит переключиться на другого консультанта.
    Возвращает "show_menu", если запрос общий (без указания конкретного имени).
    """
    text_lower = text.lower()
    
    # Прямые запросы по имени
    if any(word in text_lower for word in ["анн", "анну", "анне", "анной"]):
        if any(trigger in text_lower for trigger in ["хочу", "можно", "позови", "давай", "переключи", "смени", "другой", "ещё", "лучше", "позвать"]):
            return "anna"
    
    if any(word in text_lower for word in ["артём", "артема", "артему"]):
        if any(trigger in text_lower for trigger in ["хочу", "можно", "позови", "давай", "переключи", "смени", "другой", "ещё", "лучше", "позвать"]):
            return "artem"
    
    if any(word in text_lower for word in ["саш", "сашу", "саше"]):
        if any(trigger in text_lower for trigger in ["хочу", "можно", "позови", "давай", "переключи", "смени", "другой", "ещё", "лучше", "позвать"]):
            return "sasha"
    
    # Общие фразы – если упоминается смена консультанта без конкретного имени
    general_phrases = [
        "другой консультант", "другого консультанта", "сменить консультанта",
        "поменять консультанта", "ещё консультант", "выбрать консультанта",
        "переключить консультанта", "другой стиль общения", "поменять стиль"
    ]
    if any(phrase in text_lower for phrase in general_phrases):
        return "show_menu"
    
    # Если упоминается имя, не входящее в список, но с явной просьбой – тоже покажем меню
    name_mentions = ["артур", "иван", "петр", "мария", "елена"]  # любые имена, которых нет
    if any(name in text_lower for name in name_mentions) and any(trigger in text_lower for trigger in ["можно", "хочу", "позови"]):
        return "show_menu"
    
    return None

# optional helpers
try:
    from core.navigation import send_catalog_slide
except Exception:
    send_catalog_slide = None

# regex
PRODUCT_ID_RE = re.compile(r'\b([A-Za-z]{1,4}\d{1,5})\b')
ADD_CMD_RE = re.compile(
    r'ADD_TO_CART\W*[:\-]?\s*([A-Za-z0-9_-]+)(?:\s*x?(\d+))?',
    re.IGNORECASE
)

async def safe_answer_callback(query, text=None, show_alert=False, url=None, cache_time=None):
    """Безопасный вызов answer_callback_query с игнорированием ошибок устаревшего запроса."""
    try:
        await query.answer(text=text, show_alert=show_alert, url=url, cache_time=cache_time)
    except Exception as e:
        logger.debug(f"Callback answer error (likely expired): {e}")

async def safe_edit_message_text(query, context, text: str):
    try:
        await query.edit_message_text(text)
        return
    except BadRequest as bre:
        msg = str(bre).lower()
        logger.warning("safe_edit_message_text: edit_message_text failed: %s", bre)
        if "no text" in msg or "there is no text" in msg or "message to edit" in msg:
            try:
                await query.edit_message_caption(text)
                return
            except Exception as e:
                logger.warning("safe_edit_message_text: edit_message_caption failed: %s", e)
        try:
            await context.bot.send_message(
                chat_id=query.message.chat.id,
                text=text
            )
        except Exception as e:
            logger.exception("safe_edit_message_text: fallback send failed: %s", e)


def _build_product_keyboard(product_ids: List[str]) -> InlineKeyboardMarkup:
    if not product_ids:
        return None

    keyboard = []
    row = []
    for pid in product_ids[:8]:
        product = shop_data.get_product(pid)
        label = product.get("title") if product else pid
        if len(label) > 30:
            label = label[:28] + "…"
        row.append(InlineKeyboardButton(f"🛒 {label}", callback_data=f"add:{pid}"))
        if len(row) >= 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    if len(product_ids) > 1:
        keyboard.append([InlineKeyboardButton("✅ Добавить всё в корзину", callback_data="add_all:" + ",".join(product_ids))])

    return InlineKeyboardMarkup(keyboard)


async def send_persona_intro(update: Update, context: ContextTypes.DEFAULT_TYPE, role_key: str):
    """Отправляет случайное живое представление персонажа."""
    intro_text = random.choice(PERSONA_INTROS[role_key])
    
    # Отправляем текст
    if update.callback_query:
        await update.callback_query.message.reply_text(intro_text)
    else:
        await update.message.reply_text(intro_text)


async def secret_duck_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Секретный обработчик:
    - "кря кря" → случайное видео из списка DUCK_VIDEOS
    - "книгга" → видео kniga1.mp4
    - отдельное слово "кря" → случайный эмодзи утки/гуся
    """
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip().lower()
    
    # Проверка на фразу "кря кря" (вхождение)
    if "кря кря" in text:
        video_filename = random.choice(DUCK_VIDEOS)
        video_path = os.path.join(BASE_DIR, "core", video_filename)
        try:
            with open(video_path, 'rb') as f:
                await update.message.reply_animation(animation=f)
        except Exception as e:
            logger.error(f"Не удалось отправить видео {video_filename}: {e}")
            await update.message.reply_text("🦆")
        return
    
    # Проверка на слово "книгга" (отдельно)
    if re.search(r'\bкнигга\b', text):
        try:
            with open(BOOK_VIDEO_PATH, 'rb') as f:
                await update.message.reply_animation(animation=f)
        except Exception as e:
            logger.error(f"Не удалось отправить видео kniga1.mp4: {e}")
            await update.message.reply_text("📚")
        return
    
    # Проверка на отдельное слово "кря"
    if re.search(r'\bкря\b', text):
        duck_emojis = ["🦆", "🦢", "🦆💬", "🦢🌿", "🦆✨", "🦆🤪", "🦆👀"]
        await update.message.reply_text(random.choice(duck_emojis))
        return


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие и выбор консультанта (всегда показывает меню)."""
    # Собираем красивое описание
    lines = [
        "🌟 *Привет! Я — твой персональный помощник в мире ароматов SolidSimple.*\n",
        "У нас есть три консультанта, и ты можешь выбрать того, с кем тебе комфортнее общаться. "
        "Познакомься с каждым, а потом просто нажми кнопку — и мы начнём!\n"
    ]
    for role_key, role_data in ROLES.items():
        lines.append(f"{role_data['emoji']} *{role_data['name']}* — {role_data['description']}")
    description = "\n".join(lines)

    keyboard = []
    for role_key, role_data in ROLES.items():
        keyboard.append([InlineKeyboardButton(
            f"{role_data['emoji']} Выбрать {role_data['name']}",
            callback_data=f"set_role_first:{role_key}"
        )])

    await update.message.reply_text(
        description,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выключить режим AI-консультанта."""
    context.user_data['ai_mode'] = False
    await update.message.reply_text("👋 Режим консультанта выключен. Если снова понадоблюсь – пиши /ask.")


async def role_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню выбора роли консультанта (можно вызвать в любой момент)."""
    keyboard = []
    for role_key, role_data in ROLES.items():
        button_text = f"{role_data['emoji']} {role_data['name']} – {role_data['description']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"set_role:{role_key}")])

    await update.message.reply_text(
        "🤖 *Выберите, как со мной общаться:*\n\n"
        "Вы можете выбрать стиль общения – это повлияет на мой тон и манеру речи.",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def set_role_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, first_time=False):
    """Обработчик выбора роли (из любого меню). Всегда включает AI режим."""
    query = update.callback_query
    await safe_answer_callback(query)
    role_key = query.data.split(':')[1]

    if role_key in ROLES:
        user_context.update_user_profile(context, {"preferred_role": role_key})
        # Всегда включаем AI режим и обновляем время последней активности
        context.user_data['ai_mode'] = True
        context.user_data['ai_mode_last'] = time.time()
        # Удаляем сообщение с кнопками выбора, чтобы не захламлять чат
        await query.message.delete()
        # Отправляем живое представление нового консультанта
        await send_persona_intro(update, context, role_key)
    else:
        await query.edit_message_text("❌ Ошибка выбора роли.")


async def ai_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает сообщения только если включён режим AI."""
    # Проверка режима
    if not context.user_data.get('ai_mode'):
        return

    # Проверка таймаута
    last = context.user_data.get('ai_mode_last')
    if last and time.time() - last > AI_MODE_TIMEOUT:
        context.user_data['ai_mode'] = False
        await update.message.reply_text("⏰ Режим консультанта автоматически выключен из-за неактивности. Чтобы продолжить, напиши /ask.")
        return

    # Обновляем время последней активности
    context.user_data['ai_mode_last'] = time.time()

    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    logger.info("AI message from %s: %s", user_id, text)

    # --- Проверяем, не хочет ли пользователь сменить консультанта ---
    role_request = is_role_switch_request(text)
    if role_request:
        if role_request == "show_menu":
            # Показываем меню выбора
            keyboard = []
            for role_key, role_data in ROLES.items():
                button_text = f"{role_data['emoji']} {role_data['name']} – {role_data['description']}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"set_role_during_chat:{role_key}")])
            await update.message.reply_text(
                "🤖 *Выберите, с кем продолжить общение:*",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        else:
            # Прямой запрос конкретной роли
            user_context.update_user_profile(context, {"preferred_role": role_request})
            context.user_data['ai_mode'] = True
            context.user_data['ai_mode_last'] = time.time()
            # Отправляем живое представление нового консультанта
            await send_persona_intro(update, context, role_request)
            return

    # --- Основная логика обработки сообщения (если не смена роли) ---
    # Показываем индикатор "печатает..."
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # 1. Сохраняем сообщение пользователя в историю
    user_context.add_to_conversation_history(context, "user", text)

    # 2. Анализируем тональность и извлекаем предпочтения
    sentiment = user_context.analyze_sentiment(text)
    profile = user_context.get_user_profile(context)
    profile.setdefault("mood_history", []).append(sentiment)
    pref_updates = user_context.extract_preferences_from_message(text, profile)
    if pref_updates:
        user_context.update_user_profile(context, pref_updates)

    # 3. Добавляем имя пользователя в профиль
    user_name = update.effective_user.first_name
    if profile.get("name") != user_name:
        user_context.update_user_profile(context, {"name": user_name})

    # 4. Получаем выбранную роль
    role_key = profile.get("preferred_role", "sasha")
    role_name = ROLES[role_key]["name"]
    role_description = ROLES[role_key]["description"]
    # Здесь мы могли бы также добавить prompt_extra в системный промпт, но это уже внутри gigachat_assistant

    # 5. Определяем психотип
    history = user_context.get_conversation_history(context)
    try:
        personality = user_context.detect_personality(history)
    except AttributeError:
        personality = "neutral"

    # 6. Получаем товары
    products = shop_data.all_products()

    try:
        ai_answer = await get_ai_response(
            text,
            products=products,
            user_profile=profile,
            history=history,
            role_name=role_name,
            role_description=role_description,
            personality=personality,
            mood=sentiment,
            timeout=25.0
        )
    except Exception as e:
        logger.exception("GigaChat call failed: %s", e)
        await update.message.reply_text(
            "Извините, ошибка при обращении к ИИ. Попробуйте чуть позже."
        )
        return

    # 7. Сохраняем ответ ассистента
    user_context.add_to_conversation_history(context, "assistant", ai_answer)

    # 8. Ищем ID товаров
    product_ids = []
    for m in ADD_CMD_RE.finditer(ai_answer):
        pid = m.group(1)
        if pid:
            product_ids.append(pid.upper())

    if not product_ids:
        for m in PRODUCT_ID_RE.finditer(ai_answer):
            product_ids.append(m.group(1).upper())

    seen = set()
    product_ids = [p for p in product_ids if not (p in seen or seen.add(p))]

    # 9. Отправляем ответ
    if product_ids:
        keyboard = _build_product_keyboard(product_ids)
        await update.message.reply_text(
            text=ai_answer,
            reply_markup=keyboard
        )
    else:
        await update.message.reply_text(ai_answer)


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    data = query.data or ""

    # Обработка выбора роли при первом входе (из /ask)
    if data.startswith("set_role_first:"):
        await set_role_callback(update, context, first_time=True)
        return

    # Обработка обычного выбора роли (из /role)
    if data.startswith("set_role:"):
        await set_role_callback(update, context, first_time=False)
        return

    # Обработка выбора роли во время диалога
    if data.startswith("set_role_during_chat:"):
        role_key = data.split(':')[1]
        user_context.update_user_profile(context, {"preferred_role": role_key})
        context.user_data['ai_mode'] = True
        context.user_data['ai_mode_last'] = time.time()
        await safe_answer_callback(query)
        # Удаляем сообщение с меню выбора
        await query.message.delete()
        # Отправляем живое представление нового консультанта
        await send_persona_intro(update, context, role_key)
        return

    await safe_answer_callback(query)
    user_id = update.effective_user.id

    try:
        if data.startswith("add_all:"):
            payload = data[len("add_all:"):]
            pids = [p.strip() for p in payload.split(",")] if payload else []

            try:
                from core.hybrid_cart import hybrid_cart
                for pid in pids:
                    hybrid_cart.add_to_cart(user_id, pid)

                await safe_edit_message_text(
                    query,
                    context,
                    f"✅ Все товары ({len(pids)}) добавлены в корзину."
                )
                logger.info("Added %d products to cart for user %s", len(pids), user_id)

            except Exception as e:
                logger.exception("Failed to add_all to cart: %s", e)
                await safe_edit_message_text(
                    query,
                    context,
                    "Ошибка при добавлении в корзину."
                )
            return

        if data.startswith("add:"):
            pid = data.split(":", 1)[1]

            try:
                from core.hybrid_cart import hybrid_cart
                hybrid_cart.add_to_cart(user_id, pid)

                await safe_edit_message_text(
                    query,
                    context,
                    f"✅ Товар {pid} добавлен в корзину."
                )
                logger.info("Товар %s добавлен в корзину для %s", pid, user_id)

            except Exception as e:
                logger.exception("Failed to add to cart: %s", e)
                await safe_edit_message_text(
                    query,
                    context,
                    "Ошибка при добавлении в корзину."
                )
            return

        await safe_edit_message_text(
            query,
            context,
            "Нажата неизвестная кнопка."
        )

    except Exception:
        logger.exception("Unhandled exception in callback_query_handler")
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Произошла ошибка при обработке нажатия кнопки."
            )
        except Exception:
            pass




def setup_ai_handlers(application):
    # Команды (group=0)
    application.add_handler(CommandHandler("ask", ask_command), group=0)
    application.add_handler(CommandHandler("stop", stop_command), group=0)
    application.add_handler(CommandHandler("role", role_command), group=0)

    # Секретный обработчик "Кря" (group=0)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, secret_duck_handler),
        group=0
    )

    # Callback-обработчик (group=0)
    application.add_handler(
        CallbackQueryHandler(callback_query_handler),
        group=0
    )

    # Основной AI-обработчик (group=1, вызывается только если не обработано в group=0)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, ai_message_handler),
        group=1
    )

    logger.info("✅ AI handlers registered with ultra-live Anna, improved triggers")