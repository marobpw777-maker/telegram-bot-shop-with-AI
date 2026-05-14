# core/navigation.py
import logging
import asyncio
from typing import Dict, Any, Optional, List
from core.json_config import json_config
from core.photo_manager import get_product_photo_by_id
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from pathlib import Path

# Импорт базы данных (опционально)
from core.database import db

logger = logging.getLogger(__name__)


class NavigationSystem:
    def __init__(self):
        # user_id -> текущий слайд
        self.user_states: Dict[int, str] = {}
        # user_id -> список слайдов (история переходов)
        self.user_history: Dict[int, List[str]] = {}
        # user_id -> текущая категория (если у пользователя открыт товар, чтобы вернуться в категорию)
        self.current_categories: Dict[int, str] = {}

    async def show_slide(self, update: Update, context: ContextTypes.DEFAULT_TYPE, slide_id: str, is_back_navigation: bool = False):
        """Показать слайд по ID с БЫСТРОЙ логикой переходов"""
        try:
            user = update.effective_user
            if not user:
                logger.warning("show_slide: update has no effective_user")
                return
            user_id = user.id

            # Инициализируем историю для пользователя
            if user_id not in self.user_history:
                self.user_history[user_id] = []

            # Получаем текущий слайд до изменения
            current_slide = self.user_states.get(user_id)

            # 🔥 УЛУЧШЕННАЯ ЛОГИКА: Сохраняем категории при переходе в товар
            if not is_back_navigation and current_slide and current_slide != slide_id:
                # Если переходим из категории в товар - сохраняем категорию
                if slide_id.startswith('product:') and not current_slide.startswith('product:'):
                    if current_slide != 'S01' and not current_slide.startswith('product:'):
                        self.current_categories[user_id] = current_slide
                        logger.info(f"📁 Сохранена категория для {user_id}: {current_slide}")

                # Для обычных переходов сохраняем в историю (только страницы, не продукты)
                if not slide_id.startswith('product:') and slide_id != 'S01':
                    if current_slide and current_slide not in self.user_history[user_id]:
                        self.user_history[user_id].append(current_slide)

                # Ограничиваем историю последними 10 переходами
                if len(self.user_history[user_id]) > 10:
                    self.user_history[user_id] = self.user_history[user_id][-10:]

            # Обновляем текущее состояние
            self.user_states[user_id] = slide_id

            # 🔥 Сохранение в БД делаем асинхронно в фоне, чтобы не задерживать ответ
            asyncio.create_task(self._save_state_background(user_id, slide_id))

            # Если это слайд продукта, обрабатываем отдельно (через product_system)
            if slide_id.startswith('product:'):
                product_id = slide_id.replace('product:', '')
                await self._show_product(update, context, product_id)
                return

            # Обычный слайд из JSON
            slide = json_config.get_slide_by_id(slide_id)
            if not slide:
                await self._send_error(update, f"Слайд {slide_id} не найден")
                return

            # Получаем текст, фото и кнопки
            text = slide.get('SlideText', '')
            image_path = slide.get('ImagePath', '')
            buttons = slide.get('Buttons', [])

            # Создаем клавиатуру
            keyboard = self._create_keyboard(buttons, user_id)

            # Формируем хлебные крошки и добавляем к тексту/подписи
            try:
                crumbs = self._render_breadcrumbs(user_id)
                full_text = f"{crumbs}\n\n{text}" if text else crumbs
            except Exception as e:
                logger.debug(f"Ошибка рендера breadcrumbs: {e}")
                full_text = text

            # 🔥 ОТОБРАЖЕНИЕ: всегда удаляем предыдущее сообщение и отправляем новое
            await self._send_new_message(update, context, full_text, image_path, keyboard)

            logger.info(f"Показан слайд: {slide_id} для пользователя {user_id}")

        except Exception as e:
            logger.error(f"❌ Ошибка показа слайда {slide_id}: {e}")
            await self._send_error(update, "Ошибка загрузки слайда")

    async def _save_state_background(self, user_id: int, slide_id: str):
        """Сохранить состояние в БД в фоне (не блокируя ответ)"""
        try:
            if db:
                db.update_user_state(user_id, slide_id, self.user_history.get(user_id, []))
                logger.debug(f"💾 Состояние сохранено в БД для {user_id}")
        except Exception as e:
            logger.debug(f"❌ Ошибка сохранения состояния в БД (не критично): {e}")

    async def _send_new_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                text: str, image_path: str, keyboard: InlineKeyboardMarkup):
        """Отправляет новое сообщение, предварительно удалив предыдущее (если есть)."""
        # Удаляем предыдущее сообщение бота, если это callback
        if update.callback_query and update.callback_query.message:
            try:
                await update.callback_query.message.delete()
            except Exception as e:
                logger.warning(f"Не удалось удалить предыдущее сообщение: {e}")

        photo_path = Path(image_path) if image_path else None

        # Проверяем, существует ли файл и не пустой ли он
        if photo_path and photo_path.exists() and photo_path.stat().st_size > 0:
            # Опционально: проверка на слишком большой размер (чтобы избежать таймаутов)
            if photo_path.stat().st_size > 10 * 1024 * 1024:  # 10 MB
                logger.warning(f"Файл {photo_path} слишком большой. Отправляем текст.")
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text,
                    reply_markup=keyboard
                )
                return

            # Отправляем фото
            try:
                with open(photo_path, 'rb') as photo:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=photo,
                        caption=text,
                        reply_markup=keyboard,
                        read_timeout=60,
                        write_timeout=60,
                        connect_timeout=60
                    )
            except Exception as e:
                logger.error(f"Ошибка отправки фото: {e}. Отправляем текст.")
                # fallback - текст
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text,
                    reply_markup=keyboard
                )
        else:
            # Если фото нет или оно пустое – отправляем текст
            if photo_path and photo_path.exists() and photo_path.stat().st_size == 0:
                logger.warning(f"Файл {photo_path} пустой. Отправляем текст.")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                reply_markup=keyboard
            )

    # Метод _fast_content_display больше не используется, можно удалить, но оставим для совместимости
    async def _fast_content_display(self, *args, **kwargs):
        """Устаревший метод, используйте _send_new_message."""
        await self._send_new_message(*args, **kwargs)

    async def _send_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                               text: str, keyboard: InlineKeyboardMarkup):
        """Резервный метод для отправки текста (почти не используется)."""
        await self._send_new_message(update, context, text, "", keyboard)

    def set_current_category(self, user_id: int, category_slide: str):
        """Установить текущую категорию для пользователя"""
        self.current_categories[user_id] = category_slide
        logger.info(f"📁 Установлена категория для {user_id}: {category_slide}")

    def get_current_category(self, user_id: int) -> str:
        """Получить текущую категорию пользователя"""
        return self.current_categories.get(user_id, 'S_CATALOG')

    async def _show_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str):
        """Показать карточку товара"""
        try:
            from core.product_cards import product_system
            await product_system.show_product(update, context, product_id, detail_mode=False)
        except Exception as e:
            logger.error(f"Ошибка показа товара {product_id}: {e}")
            await self._send_error(update, "Ошибка загрузки товара")

    def _create_keyboard(self, buttons: list, user_id: int) -> InlineKeyboardMarkup:
        keyboard: List[List[InlineKeyboardButton]] = []
        for btn in buttons:
            if not isinstance(btn, dict):
                continue
            label = btn.get('Label')
            callback = btn.get('Callback')
            style = btn.get('Style')  # получаем стиль из JSON, если есть
            if not label or not callback:
                continue
            if callback == 'nav:back' and not self.user_history.get(user_id):
                continue
            # Создаём кнопку, передавая style, если он задан
            button = InlineKeyboardButton(label, callback_data=callback)
            if style:
                button = InlineKeyboardButton(label, callback_data=callback, style=style)
            keyboard.append([button])
        return InlineKeyboardMarkup(keyboard)

    def _render_breadcrumbs(self, user_id: int, max_items: int = 3) -> str:
        """Построить строку хлебных крошек"""
        try:
            prev_slides: List[str] = []
            try:
                state = db.get_user_state(user_id) if db else None
                if state and isinstance(state, dict):
                    prev_slides = state.get('previous_slides', []) or []
            except Exception as e:
                logger.debug(f"Ошибка чтения состояния из БД: {e}")
                prev_slides = []

            if not prev_slides:
                prev_slides = self.user_history.get(user_id, [])

            slices = prev_slides[-max_items:] if prev_slides else []

            crumbs: List[str] = []
            for sid in slices:
                slide = json_config.get_slide_by_id(sid)
                if slide:
                    title = slide.get('SlideTitle') or sid
                    crumbs.append(title)
                else:
                    crumbs.append(sid)

            if not crumbs:
                return "🏠 Главная"

            return " • ".join(["🏠 Главная"] + crumbs)
        except Exception as e:
            logger.debug(f"Ошибка при построении breadcrumbs: {e}")
            return "🏠 Главная"

    async def go_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """БЫСТРЫЙ переход на предыдущий слайд"""
        user = update.effective_user
        if not user:
            return
        user_id = user.id

        if not self.user_history.get(user_id):
            await self.show_slide(update, context, 'S01')
            return

        previous_slide = self.user_history[user_id].pop()
        await self.show_slide(update, context, previous_slide, is_back_navigation=True)

    async def _send_error(self, update: Update, message: str):
        """Отправка сообщения об ошибке"""
        try:
            if update.callback_query:
                await update.callback_query.answer(message, show_alert=True)
            else:
                await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения об ошибке: {e}")


# Глобальный экземпляр
nav_system = NavigationSystem()