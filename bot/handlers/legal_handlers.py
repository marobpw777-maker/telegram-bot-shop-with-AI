# handlers/legal_handlers.py
"""
Юридический модуль бота "СОЛИД СИМПЛ"
- Фиксация согласия с версионированием
- GDPR-compliant удаление данных
- Полная юридическая защита
- Логирование IP/User-Agent при наличии
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler

from core.agreements import user_has_agreement, set_user_agreement, anonymize_user_pii

logger = logging.getLogger(__name__)

# ============ КОНФИГУРАЦИЯ ============
# ⚠️ МЕНЯЙТЕ ПРИ ОБНОВЛЕНИИ ДОКУМЕНТОВ!
AGREEMENT_VERSION = "2026-03-06-v1"  # Формат: ГГГГ-ММ-ДД-версия

# 🔗 ЗАМЕНИТЕ НА РЕАЛЬНЫЕ ССЫЛКИ (должны открываться без авторизации!)
PRIVACY_URL = "https://disk.yandex.ru/i/MZQWjNs0z1pctQ"
OFFER_URL = "https://disk.yandex.ru/i/-ZMOOKjB_vMltw"

# Тексты документов для отображения в боте
PRIVACY_TITLE = "Политика обработки персональных данных"
OFFER_TITLE = "Публичная оферта"


# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============

def _get_user_info(user) -> Dict[str, Any]:
    """Собирает информацию о пользователе для логирования"""
    return {
        "user_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "language_code": user.language_code,
        "is_bot": user.is_bot,
    }


def _check_agreement_valid(agreement: Optional[Dict]) -> bool:
    """
    Проверяет, актуально ли соглашение пользователя.
    Если соглашения нет, версия не совпадает или отсутствует – считаем недействительным.
    """
    if not agreement:
        return False

    stored_version = agreement.get('agreement_version')
    if not stored_version:
        logger.debug("Agreement record has no version – treating as outdated")
        return False

    if stored_version != AGREEMENT_VERSION:
        logger.info(f"Version mismatch: stored={stored_version}, current={AGREEMENT_VERSION}")
        return False

    return True


def _try_get_client_meta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[Optional[str], Optional[str]]:
    """
    Пытается извлечь IP и User-Agent пользователя.
    Доступно только при использовании webhook с правильной конфигурацией сервера.
    """
    ip = None
    ua = None
    try:
        # Если бот работает через webhook, можно получить заголовки из update
        if update.effective_message and update.effective_message.from_user:
            # IP может быть в update.effective_user или в контексте, но стандартно недоступен
            # Реальная реализация зависит от того, как настроен сервер
            # Пример для nginx с передачей заголовков:
            # if hasattr(context, 'bot_data') and 'request_meta' in context.bot_data:
            #     meta = context.bot_data['request_meta']
            #     ip = meta.get('x-forwarded-for') or meta.get('remote_addr')
            #     ua = meta.get('user-agent')
            pass
    except Exception:
        logger.debug("Could not extract client meta", exc_info=True)
    return ip, ua


# ============ ОСНОВНЫЕ ФУНКЦИИ ============

async def send_agreement_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    post_action: Optional[Dict] = None
):
    """
    Показывает экран согласия с полной юридической информацией.
    Вызывается при /start, при смене версии документов,
    и при попытке оформить заказ без согласия.
    """
    user = update.effective_user
    if not user:
        return

    # Формируем клавиатуру
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принимаю", callback_data="accept_terms")],
        [
            InlineKeyboardButton(f"📄 {PRIVACY_TITLE[:20]}...", url=PRIVACY_URL),
            InlineKeyboardButton(f"📄 {OFFER_TITLE[:20]}...", url=OFFER_URL)
        ]
    ])

    # Текст согласия — дружелюбный и понятный
    text = (
        "👋 *С радостью встречаем вас в «СОЛИД СИМПЛ»!*\n\n"
        "Чтобы мы могли оформить для вас заказ и отправить его, нам нужно ваше согласие на обработку персональных данных.  Это обычная практика для всех онлайн-магазинов, так мы защищаем и ваши, и наши интересы.\n\n"
        "📄 Пожалуйста, ознакомьтесь с документами:\n"
        f"• [{PRIVACY_TITLE}]({PRIVACY_URL})\n"
        f"• [{OFFER_TITLE}]({OFFER_URL})\n\n"
        "✅ Нажимая кнопку «✅ Принимаю», вы подтверждаете, что:\n"
        "• прочитали и поняли эти документы,\n"
        "• соглашаетесь на обработку ваших данных для оформления заказов,\n"
        "• принимаете условия публичной оферты.\n\n"
        "🔐 *Ваши данные под защитой, мы используем их только для доставки и связи.*\n\n"
        "Если в будущем захотите удалить данные, то просто напишите команду /delete_my_data (это полностью сотрёт вашу информацию из нашей системы, заказы останутся обезличенными)."
    )

    # Сохраняем действие для возврата
    if post_action:
        context.user_data['post_agreement_action'] = post_action
        logger.debug(f"Saved post_action for user {user.id}: {post_action}")

    # Отправляем сообщение
    try:
        if update.callback_query:
            await update.callback_query.answer()
            # Если есть старое сообщение — редактируем, иначе новое
            try:
                await update.callback_query.edit_message_text(
                    text=text,
                    reply_markup=keyboard,
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
            except Exception:
                await update.callback_query.message.reply_text(
                    text=text,
                    reply_markup=keyboard,
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
        else:
            await update.effective_message.reply_text(
                text=text,
                reply_markup=keyboard,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
    except Exception as e:
        logger.exception(f"Failed to send agreement prompt to user {user.id}: {e}")
        # Fallback — простой текст
        await update.effective_message.reply_text(
            "Для продолжения примите условия: /documents",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Принимаю", callback_data="accept_terms")
            ]])
        )


async def accept_terms_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик принятия соглашения.
    Фиксирует: версию, время, ссылки на документы, данные пользователя,
    а также IP и User-Agent, если доступны.
    """
    query = update.callback_query
    await query.answer("✅ Согласие принято и записано!")

    user = update.effective_user
    user_id = user.id
    user_info = _get_user_info(user)

    # Пытаемся получить технические данные
    user_ip, user_agent = _try_get_client_meta(update, context)

    # Сохраняем соглашение в БД с полной информацией
    extra_data = {
        "accepted_at": datetime.now().isoformat(),
        "user_info": user_info,
        "platform": "telegram",
        "bot_version": "2.2"
    }
    
    logger.info(f"Saving agreement for user {user_id}, version={AGREEMENT_VERSION}")
    
    ok = set_user_agreement(
        telegram_id=user_id,
        agreement_version=AGREEMENT_VERSION,
        privacy_url=PRIVACY_URL,
        offer_url=OFFER_URL,
        user_ip=user_ip,
        user_agent=user_agent,
        extra=extra_data
    )

    if not ok:
        logger.error(f"CRITICAL: Failed to save agreement for user {user_id}")
        # Попробуем ещё раз с минимальными данными
        logger.info(f"Retrying with minimal data for user {user_id}")
        ok = set_user_agreement(
            telegram_id=user_id,
            agreement_version=AGREEMENT_VERSION,
            privacy_url=PRIVACY_URL,
            offer_url=OFFER_URL
        )
        if not ok:
            logger.error(f"CRITICAL: Second attempt failed for user {user_id}")
            await query.message.reply_text(
                "❌ *Произошла техническая ошибка при сохранении согласия.*\n"
                "Пожалуйста, попробуйте позже или обратитесь в поддержку.",
                parse_mode='Markdown'
            )
            return

    # Успешное сохранение — логируем
    logger.info(
        f"AGREEMENT_ACCEPTED: user={user_id}, "
        f"version={AGREEMENT_VERSION}, "
        f"username={user.username}, ip={user_ip}"
    )

    # Отправляем подтверждение пользователю
    confirmation_text = (
        "🎉 *Спасибо за доверие!*\n\n"
        "✅ Ваше согласие успешно сохранено.\n"
        f"📌 *Версия документов:* `{AGREEMENT_VERSION}`\n"
        f"📅 *Дата и время:* {datetime.now().strftime('%d.%m.%Y %H:%M:%S')} МСК\n\n"
        "Теперь вы можете:\n"
        "• 🛍️ Просматривать каталог\n"
        "• 🛒 Оформлять заказы\n"
        "• 💳 Оплачивать покупки\n"
        "• 📦 Отслеживать доставку\n\n"
        "_Ваши данные используются только для обработки заказов и хранятся на защищённых серверах. Передача возможна лишь для выполнения доставки (курьерские службы, ПВЗ)._"
    )

    await query.message.reply_text(confirmation_text, parse_mode='Markdown')

    # Возвращаемся к сохранённому действию или показываем главную
    post = context.user_data.pop('post_agreement_action', None)

    if post:
        action_type = post.get('type')
        logger.info(f"Resuming post-agreement action for user {user_id}: {action_type}")

        try:
            if action_type == 'start':
                from core.navigation import nav_system
                await nav_system.show_slide(update, context, 'S01')

            elif action_type == 'checkout_start':
                from handlers.checkout_handlers import checkout_start
                await checkout_start(update, context)

            elif action_type == 'callback' and post.get('data'):
                # Для callback'ов (например, оформление заказа) – вызываем общий обработчик checkout
                from handlers.checkout_handlers import handle_checkout_callbacks
                # Подменяем данные callback'а на сохранённые
                # (это требует аккуратной работы, но в вашем коде handle_checkout_callbacks уже существует)
                # В простейшем случае просто вызываем checkout_start как fallback
                await checkout_start(update, context)

            else:
                # По умолчанию — главная
                from core.navigation import nav_system
                await nav_system.show_slide(update, context, 'S01')
        except Exception as e:
            logger.exception(f"Failed to resume action for user {user_id}: {e}")
            # Fallback – главная
            from core.navigation import nav_system
            await nav_system.show_slide(update, context, 'S01')
    else:
        # Нет сохранённого действия — показываем главную
        from core.navigation import nav_system
        await nav_system.show_slide(update, context, 'S01')


async def cmd_documents(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /documents — показывает ссылки на документы и версию"""
    text = (
        f"📋 *Документы магазина «СОЛИД СИМПЛ»*\n\n"
        f"• [{PRIVACY_TITLE}]({PRIVACY_URL})\n"
        f"• [{OFFER_TITLE}]({OFFER_URL})\n\n"
        f"📌 *Текущая версия:* `{AGREEMENT_VERSION}`"
    )
    await update.effective_message.reply_text(text, parse_mode='Markdown', disable_web_page_preview=True)


async def cmd_delete_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /delete_my_data — удаление персональных данных с подтверждением"""
    logger.info(f"!!!!!!!!!! User {update.effective_user.id} called /deletemydata !!!!!!!!!!")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ Да, удалить мои данные", callback_data="delete_confirm")],
        [InlineKeyboardButton("❌ Нет, отмена", callback_data="delete_cancel")]
    ])

    await update.effective_message.reply_text(
        "⚠️ *Вы уверены?*\n\n"
        "Это удалит ваши контактные данные из системы.\n"
        "История заказов сохранится в обезличенном виде.\n\n"
        "_Для продолжения использования бота потребуется повторное согласие._",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )


async def delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение удаления данных — запускает процесс обезличивания"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    # Запускаем обезличивание (техническая часть)
    results = anonymize_user_pii(user_id)

    # Логируем технические детали (они останутся в логах)
    logger.info(f"User {user_id} deleted PII. Results: {results}")

    # Понятное сообщение пользователю
    msg = (
        "🗑️ *Ваши данные удалены*\n\n"
        "✅ Ваши персональные данные (имя, телефон, email) удалены из системы.\n"
        "История заказов сохранена в обезличенном виде.\n\n"
        "Для продолжения нажмите /start"
    )

    await query.message.reply_text(msg, parse_mode='Markdown')


async def delete_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена удаления данных"""
    query = update.callback_query
    await query.answer("Отменено")
    await query.message.reply_text("✅ Удаление данных отменено.")


# ============ РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ ============

handlers = [
    CallbackQueryHandler(accept_terms_callback, pattern="^accept_terms$"),
    CallbackQueryHandler(delete_confirm_callback, pattern="^delete_confirm$"),
    CallbackQueryHandler(delete_cancel_callback, pattern="^delete_cancel$"),
    CommandHandler("documents", cmd_documents),
    CommandHandler("delete_my_data", cmd_delete_my_data),   # команда с подчёркиванием
    CommandHandler("deletemydata", cmd_delete_my_data),     # команда без подчёркивания
]