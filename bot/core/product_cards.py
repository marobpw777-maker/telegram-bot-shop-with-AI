# core/product_cards.py

import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from core.json_config import json_config
from core.photo_manager import get_product_photo_by_id
from core.hybrid_cart import hybrid_cart as cart_manager
from pathlib import Path

logger = logging.getLogger(__name__)

class ProductCardSystem:
    def __init__(self):
        pass

    async def show_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str, detail_mode: bool = False):
        """Показать карточку товара в кратком или подробном режиме"""
        try:
            product = json_config.get_product_by_id(product_id)
            if not product:
                await self._send_error(update, f"Товар {product_id} не найден")
                return

            # Получаем количество товара в корзине
            user_id = update.effective_user.id
            quantity_in_cart = cart_manager.get_product_quantity(user_id, product_id)

            # Формируем текст в зависимости от режима
            if detail_mode:
                text = self._get_detail_text(product, quantity_in_cart)
            else:
                text = self._get_brief_text(product, quantity_in_cart)

            # Получаем фото товара
            photo_path = get_product_photo_by_id(product_id)

            # Создаем клавиатуру с учетом текущего количества в корзине
            if detail_mode:
                keyboard = self._create_detail_keyboard(product_id, quantity_in_cart)
            else:
                keyboard = self._create_brief_keyboard(product_id, quantity_in_cart)
            reply_markup = keyboard

            # Проверяем размер файла перед отправкой
            if photo_path and photo_path.exists():
                file_size = photo_path.stat().st_size
                max_size = 10 * 1024 * 1024  # 10MB в байтах
                
                if file_size > max_size:
                    logger.warning(f"Файл слишком большой: {file_size} байт. Отправляем без фото.")
                    await self._send_text_message(update, context, text, reply_markup)
                else:
                    # Пытаемся отправить с фото
                    try:
                        with open(photo_path, 'rb') as photo:
                            if update.callback_query:
                                try:
                                    await update.callback_query.message.delete()
                                except:
                                    pass
                            await context.bot.send_photo(
                                chat_id=update.effective_chat.id,
                                photo=photo,
                                caption=text,
                                parse_mode='Markdown',
                                reply_markup=reply_markup
                            )
                    except Exception as photo_error:
                        logger.error(f"Ошибка отправки фото: {photo_error}. Отправляем без фото.")
                        await self._send_text_message(update, context, text, reply_markup)
            else:
                # Если фото нет или не найдено
                await self._send_text_message(update, context, text, reply_markup)

            logger.info(f"Показан товар: {product_id} для пользователя {user_id}. Режим: {'детальный' if detail_mode else 'краткий'}. В корзине: {quantity_in_cart} шт.")

        except Exception as e:
            logger.error(f"Ошибка показа товара {product_id}: {e}")
            await self._send_error(update, "Ошибка загрузки товара")

    def _get_brief_text(self, product: dict, quantity_in_cart: int) -> str:
        """Формирует краткое описание товара со статусом корзины"""
        title = product.get('ProductTitle', 'Товар')
        short_desc = product.get('ShortDesc', '')
        price = product.get('Price', '0')
        volume = product.get('Volume', '')
        
        short_legal = product.get('ShortLegalLine', '')
        allergy_info = product.get('AllergyShort', '')
        storage_info = product.get('StorageShort', '')

        text = f"*{title}* • {price}₽\n\n"
        text += f"{short_desc}\n\n"
        text += f"📦 *Объем:* {volume}\n\n"

        # 🔥 НОВОЕ: Статус добавления в корзину
        if quantity_in_cart > 0:
            text += f"✅ *Товар добавлен в корзину ({quantity_in_cart} шт.)*\n\n"
        
        # Добавляем обязательные поля для краткого режима
        if short_legal:
            text += f"📋 {short_legal}\n"
        if allergy_info:
            text += f"⚠️ {allergy_info}\n"
        if storage_info:
            text += f"🌡️ {storage_info}\n"

        text += "\n👇 *Выберите действие:*"
        return text

    def _get_detail_text(self, product: dict, quantity_in_cart: int) -> str:
        """Формирует полное описание товара со статусом корзины"""
        title = product.get('ProductTitle', 'Товар')
        short_desc = product.get('ShortDesc', '')
        full_desc = product.get('description', product.get('FullDesc', ''))
        price = product.get('Price', '0')
        volume = product.get('Volume', '')

        # Все поля для детального режима
        short_legal = product.get('ShortLegalLine', '')
        allergy_info = product.get('AllergyShort', '')
        storage_info = product.get('StorageShort', '')
        contact_info = product.get('ContactShort', '')
        safety_info = product.get('SafetyShort', '')
        tech_details = product.get('TechnicalDetails', '')

        text = f"*{title}* • {price}₽\n\n"
        
        # Основное описание
        if full_desc:
            text += f"{full_desc}\n\n"
        else:
            text += f"{short_desc}\n\n"

        text += f"📦 *Объем:* {volume}\n\n"

        # Технические детали
        if tech_details:
            text += "📊 *ТЕХНИЧЕСКИЕ ДЕТАЛИ:*\n"
            details = tech_details.split('|')
            for detail in details:
                text += f"• {detail.strip()}\n"
            text += "\n"

        # 🔥 НОВОЕ: Статус добавления в корзину
        if quantity_in_cart > 0:
            text += f"✅ *Товар добавлен в корзину ({quantity_in_cart} шт.)*\n\n"
        
        # Полный блок с важной информацией
        text += "⚠️ *ВАЖНАЯ ИНФОРМАЦИЯ:*\n"
        
        if short_legal:
            text += f"• {short_legal}\n"
        if allergy_info:
            text += f"• {allergy_info}\n"
        if storage_info:
            text += f"• {storage_info}\n"
        if safety_info:
            text += f"• {safety_info}\n"
        if contact_info:
            text += f"• {contact_info}\n"

        text += "\n👇 *Выберите действие:*"
        return text

    def _create_brief_keyboard(self, product_id: str, current_quantity: int = 0) -> InlineKeyboardMarkup:
        """Создает клавиатуру для краткого режима с управлением количеством"""
        if current_quantity > 0:
            # Товар уже в корзине - показываем управление количеством
            cart_row = [
                InlineKeyboardButton("➖", callback_data=f"cart:dec:{product_id}"),
                InlineKeyboardButton(f"🛒 В корзине ({current_quantity})", callback_data="cart:view"),
                InlineKeyboardButton("➕", callback_data=f"cart:inc:{product_id}")
            ]
        else:
            # Товара нет в корзине
            cart_row = [InlineKeyboardButton("🛒 Добавить в корзину", callback_data=f"cart:silent_add:{product_id}")]
        
        keyboard = [
            cart_row,
            [InlineKeyboardButton("📖 Подробнее", callback_data=f"product:detail:{product_id}")],
            [InlineKeyboardButton("⬅️ Назад к категории", callback_data="nav:back_to_category"),
             InlineKeyboardButton("🏠 Главная", callback_data="nav:home")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def _create_detail_keyboard(self, product_id: str, current_quantity: int = 0) -> InlineKeyboardMarkup:
        """Создает клавиатуру для детального режима с управлением количеством"""
        if current_quantity > 0:
            # Товар уже в корзине - показываем управление количеством
            cart_row = [
                InlineKeyboardButton("➖", callback_data=f"cart:dec:{product_id}"),
                InlineKeyboardButton(f"🛒 В корзине ({current_quantity})", callback_data="cart:view"),
                InlineKeyboardButton("➕", callback_data=f"cart:inc:{product_id}")
            ]
        else:
            # Товара нет в корзине
            cart_row = [InlineKeyboardButton("🛒 Добавить в корзину", callback_data=f"cart:silent_add:{product_id}")]
        
        keyboard = [
            cart_row,
            [InlineKeyboardButton("📝 Свернуть", callback_data=f"product:brief:{product_id}")],
            [InlineKeyboardButton("⬅️ Назад к категории", callback_data="nav:back_to_category"),
             InlineKeyboardButton("🏠 Главная", callback_data="nav:home")]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    async def _send_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup: InlineKeyboardMarkup):
        """Универсальный метод отправки текстового сообщения"""
        try:
            if update.callback_query:
                # Пытаемся отредактировать существующее сообщение
                try:
                    await update.callback_query.edit_message_text(
                        text=text,
                        parse_mode='Markdown',
                        reply_markup=reply_markup
                    )
                except:
                    # Если не получается отредактировать, отправляем новое
                    await update.callback_query.message.reply_text(
                        text=text,
                        parse_mode='Markdown',
                        reply_markup=reply_markup
                    )
            else:
                await update.message.reply_text(
                    text=text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
        except Exception as e:
            logger.error(f"Ошибка отправки текстового сообщения: {e}")
            # Последняя попытка - просто текст без разметки
            if update.callback_query:
                await update.callback_query.message.reply_text(
                    text=text.replace('*', ''),
                    reply_markup=reply_markup
                )
            else:
                await update.message.reply_text(
                    text=text.replace('*', ''),
                    reply_markup=reply_markup
                )

    async def _send_error(self, update: Update, message: str):
        """Отправка сообщения об ошибке"""
        try:
            if update.callback_query:
                await update.callback_query.answer(message, show_alert=True)
            else:
                await update.message.reply_text(f"❌ {message}")
        except Exception as e:
            logger.error(f"Ошибка отправки ошибки: {e}")

# Глобальный экземпляр
product_system = ProductCardSystem()