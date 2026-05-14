# core/animations.py
import asyncio
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

async def send_typing(bot, chat_id: int, action: str = "typing", delay: float = 0.8):
    """Простой индикатор загрузки"""
    try:
        await bot.send_chat_action(chat_id=chat_id, action=action)
        await asyncio.sleep(delay)
    except Exception as e:
        logger.debug(f"send_typing failed: {e}")

async def simple_button_animation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Упрощенная анимация кнопки - гарантированно работает"""
    try:
        query = update.callback_query
        if not query:
            return
            
        original_keyboard = query.message.reply_markup
        
        # Простая анимация - меняем текст на 0.3 секунды
        try:
            new_rows = []
            for row in original_keyboard.inline_keyboard:
                new_row = []
                for btn in row:
                    if hasattr(btn, 'callback_data') and btn.callback_data and 'cart:' in btn.callback_data:
                        new_row.append(InlineKeyboardButton("🔄...", callback_data=btn.callback_data))
                    else:
                        new_row.append(btn)
                new_rows.append(new_row)
            
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_rows))
            await asyncio.sleep(0.3)
            
            # Возвращаем обратно
            await query.edit_message_reply_markup(reply_markup=original_keyboard)
            
        except Exception as e:
            logger.debug(f"Простая анимация кнопки: {e}")
            
    except Exception as e:
        logger.error(f"Анимация кнопки ошибка: {e}")

async def animated_progress_bar(current_step: int, total_steps: int = 3):
    """
    Улучшенный прогресс-бар с анимированными эмодзи
    """
    try:
        step_names = ["📧 Контакты", "🚚 Доставка", "💳 Оплата"]
        
        # Анимированные эмодзи для текущего шага
        import time
        current_emojis = ["🔸", "🎯", "✨", "🔥"]
        animation_index = int(time.time() * 2) % len(current_emojis)
        
        progress_emojis = []
        for i in range(total_steps):
            if i < current_step - 1:
                progress_emojis.append("✅")  # Пройденные
            elif i == current_step - 1:
                progress_emojis.append(current_emojis[animation_index])  # Анимированный текущий
            else:
                progress_emojis.append("⚪")  # Будущие
        
        progress_line = " 🠖 ".join(progress_emojis)
        
        # Динамические описания
        step_descriptions = {
            1: "📝 *Заполните контактные данные для связи*",
            2: "🚀 *Выберите удобный способ получения заказа*", 
            3: "💫 *Завершите оформление и оплатите заказ*"
        }
        
        progress_text = f"""
{progress_line}

*Шаг {current_step} из {total_steps}: {step_names[current_step-1]}*

{step_descriptions.get(current_step, "")}
        """
        
        return progress_text
        
    except Exception as e:
        logger.error(f"Ошибка анимированного прогресс-бара: {e}")
        return ""
        
async def confetti_celebration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Упрощенная, но эффектная анимация конфетти при успешной оплате
    """
    try:
        if update.message:
            msg = update.message
        elif update.callback_query:
            msg = update.callback_query.message
        else:
            return

        # Последовательность праздничных фреймов
        celebration_frames = [
            "🎉✨🎊🌟⭐",
            "✨🎉⭐🎊🌟", 
            "🌟🎊🎉✨⭐",
            "🎊⭐✨🌟🎉",
            "⭐🎉🌟✨🎊"
        ]
        
        # Создаем базовое сообщение
        base_message = await msg.reply_text("🎉")
        
        # Анимация конфетти (3 цикла)
        for frame in celebration_frames * 3:
            try:
                await base_message.edit_text(frame)
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.debug(f"Конфетти анимация: {e}")
                break
        
        # Финальный кадр с текстом
        final_text = """
🎉 *УРА! ЗАКАЗ ОПЛАЧЕН!* 🎉

✨ *Ваш заказ уже готовится!* ✨

🌟 *Что происходит прямо сейчас:*
• 📦 Товары аккуратно упаковываются
• 🎁 Добавляем подарочные сюрпризы  
• 💫 Заряжаем позитивной энергией
• 🚀 Готовим к скорейшей отправке

💝 *Спасибо, что выбираете нас!*
        """
        
        await base_message.edit_text(final_text, parse_mode='Markdown')
        
        return base_message
        
    except Exception as e:
        logger.error(f"Ошибка конфетти-анимации: {e}")
        return None    

async def flying_to_cart_animation(update: Update, context: ContextTypes.DEFAULT_TYPE, product_name: str):
    """
    Анимация полета товара в корзину - упрощенная гарантированная версия
    """
    try:
        query = update.callback_query
        if not query:
            return

        # Создаем анимационное сообщение
        animation_message = await query.message.reply_text("🛒")
        
        # Простая анимация полета (5 кадров)
        flight_frames = [
            f"📦 ➡️ 🛒\n*{product_name}*",
            f"📦 → 🛒\n*{product_name}*", 
            f"📦 ⇒ 🛒\n*{product_name}*",
            f"📦 🛒\n*{product_name}*",
            f"✅ *{product_name} в корзине!*"
        ]
        
        for frame in flight_frames:
            try:
                await animation_message.edit_text(frame, parse_mode='Markdown')
                await asyncio.sleep(0.2)
            except Exception as e:
                logger.debug(f"Анимация полета: {e}")
                break
        
        # Удаляем анимационное сообщение через 1 секунду
        await asyncio.sleep(1)
        await animation_message.delete()
        
    except Exception as e:
        logger.error(f"Ошибка анимации полета: {e}")        