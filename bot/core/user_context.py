# core/user_context.py
import re
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

USER_PROFILE_KEY = "user_profile"
CONVERSATION_HISTORY_KEY = "conversation_history"
MAX_HISTORY_LENGTH = 20

def get_user_profile(context) -> Dict[str, Any]:
    if USER_PROFILE_KEY not in context.user_data:
        context.user_data[USER_PROFILE_KEY] = {
            "name": None,
            "preferred_scents": [],
            "disliked_scents": [],
            "allergies": [],
            "gift_preferences": {},
            "tone_preference": "auto",
            "personality": "neutral",
            "preferred_role": None,   # <-- добавляем
            "last_interaction": None,
            "mood_history": [],
        }
    return context.user_data[USER_PROFILE_KEY]

def update_user_profile(context, updates: Dict[str, Any]):
    profile = get_user_profile(context)
    profile.update(updates)
    profile["last_interaction"] = datetime.utcnow().isoformat()
    context.user_data[USER_PROFILE_KEY] = profile

def add_to_conversation_history(context, role: str, content: str):
    if CONVERSATION_HISTORY_KEY not in context.user_data:
        context.user_data[CONVERSATION_HISTORY_KEY] = []
    history = context.user_data[CONVERSATION_HISTORY_KEY]
    history.append({
        "role": role,
        "content": content,
        "timestamp": datetime.utcnow().isoformat()
    })
    if len(history) > MAX_HISTORY_LENGTH:
        context.user_data[CONVERSATION_HISTORY_KEY] = history[-MAX_HISTORY_LENGTH:]

def get_conversation_history(context) -> List[Dict[str, str]]:
    return context.user_data.get(CONVERSATION_HISTORY_KEY, [])

def analyze_sentiment(text: str) -> str:
    text_lower = text.lower()
    positive_words = ["отлично", "супер", "класс", "хорошо", "замечательно", "спасибо", "👍", "❤️", "😊", "🙂", "нравится", "люблю"]
    negative_words = ["плохо", "ужасно", "не нравится", "разочарован", "🤬", "😡", "👎", "отвратительно", "проблема", "ошибка"]
    pos_count = sum(1 for w in positive_words if w in text_lower)
    neg_count = sum(1 for w in negative_words if w in text_lower)
    if pos_count > neg_count:
        return "positive"
    elif neg_count > pos_count:
        return "negative"
    elif pos_count == 0 and neg_count == 0:
        return "neutral"
    else:
        return "mixed"

def extract_name(text: str) -> Optional[str]:
    """Извлекает имя из фраз типа 'меня зовут Артур', 'я Артур', 'зовут Артур'."""
    patterns = [
        r"меня зовут (\w+)",
        r"зовут (\w+)",
        r"я (\w+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).capitalize()
    return None

def extract_preferences_from_message(text: str, current_profile: Dict) -> Dict[str, Any]:
    updates = {}
    text_lower = text.lower()

    # Извлечение имени
    name = extract_name(text)
    if name:
        updates["name"] = name

    # Поиск любимых нот
    if "люблю" in text_lower or "нравится" in text_lower:
        scent_keywords = ["лаванда", "цитрус", "апельсин", "лимон", "роза", "жасмин", "сандал", "ваниль", "мята", "эвкалипт"]
        found = [s for s in scent_keywords if s in text_lower]
        if found:
            current = set(current_profile.get("preferred_scents", []))
            current.update(found)
            updates["preferred_scents"] = list(current)

    # Поиск аллергий
    if "аллергия" in text_lower or "нельзя" in text_lower:
        allergy_keywords = ["орехи", "цитрус", "лаванда", "эфирные масла"]
        found = [a for a in allergy_keywords if a in text_lower]
        if found:
            current = set(current_profile.get("allergies", []))
            current.update(found)
            updates["allergies"] = list(current)

    # Определение предпочитаемого тона
    if "😊" in text or "😉" in text or ")))" in text or "привет" in text_lower:
        updates["tone_preference"] = "casual"
    elif "Здравствуйте" in text or "Уважаемый" in text:
        updates["tone_preference"] = "formal"

    return updates
    
def detect_personality(history: List[Dict[str, str]]) -> str:
    """
    Анализирует историю диалога и возвращает предполагаемый психотип:
    - 'analytical' (любит факты, задаёт много вопросов)
    - 'emotional' (использует эмоциональные слова, эмодзи)
    - 'practical' (короткие сообщения, хочет конкретики)
    - 'romantic' (поэтичный, использует метафоры)
    - 'anxious' (выражает сомнения, тревогу)
    - 'neutral' (неопределённый)
    """
    if not history:
        return "neutral"

    # Берём последние 5 сообщений пользователя
    user_msgs = [msg['content'] for msg in history if msg['role'] == 'user'][-5:]
    if not user_msgs:
        return "neutral"

    full_text = " ".join(user_msgs).lower()

    # Эвристики
    question_count = full_text.count('?')
    emotional_words = ["отлично", "супер", "класс", "грустно", "плохо", "😊", "❤️", "😢", "рад", "спасибо", "пожалуйста"]
    emotional_score = sum(1 for w in emotional_words if w in full_text)
    length_avg = sum(len(m) for m in user_msgs) / len(user_msgs)

    if question_count >= 3:
        return "analytical"
    if emotional_score >= 3:
        return "emotional"
    if length_avg < 30:
        return "practical"
    if any(word in full_text for word in ["мечта", "романтика", "нежность", "чувство"]):
        return "romantic"
    if any(word in full_text for word in ["боюсь", "волнуюсь", "не уверен", "сомневаюсь"]):
        return "anxious"
    return "neutral"    