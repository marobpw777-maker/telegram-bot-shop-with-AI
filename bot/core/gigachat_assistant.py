# core/gigachat_assistant.py
import os
import json
import uuid
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import httpx

logger = logging.getLogger("core.gigachat_assistant")

# --- Config from env ---
GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY")
GIGACHAT_CLIENT_ID = os.getenv("GIGACHAT_CLIENT_ID")
GIGACHAT_CLIENT_SECRET = os.getenv("GIGACHAT_CLIENT_SECRET")
GIGACHAT_AUTH_URL = os.getenv("GIGACHAT_AUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
GIGACHAT_API_URL = os.getenv("GIGACHAT_API_URL", "https://gigachat.devices.sberbank.ru/api/v1/chat/completions")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_MODEL_LIST = os.getenv("GIGACHAT_MODEL_LIST", "GigaChat").split(",")
HTTP_TIMEOUT = float(os.getenv("GIGACHAT_HTTP_TIMEOUT", "25"))
SSL_VERIFY = os.getenv("GIGACHAT_SSL_VERIFY", "true").lower() not in ("0", "false", "no")
_token_state: Dict[str, Any] = {}
_token_lock = asyncio.Lock()


def _build_auth_header():
    if GIGACHAT_AUTH_KEY:
        return f"Basic {GIGACHAT_AUTH_KEY}"
    if GIGACHAT_CLIENT_ID and GIGACHAT_CLIENT_SECRET:
        import base64
        pair = f"{GIGACHAT_CLIENT_ID}:{GIGACHAT_CLIENT_SECRET}"
        return f"Basic {base64.b64encode(pair.encode()).decode()}"
    return None


async def _fetch_token(timeout: float = HTTP_TIMEOUT) -> Optional[str]:
    auth_header = _build_auth_header()
    if not auth_header:
        logger.error("GigaChat credentials are not configured.")
        return None

    async with _token_lock:
        now = datetime.utcnow()
        token = _token_state.get("access_token")
        expires_at = _token_state.get("expires_at")
        if token and expires_at and now < expires_at - timedelta(seconds=30):
            return token

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "Authorization": auth_header,
            "RqUID": str(uuid.uuid4()),
        }
        data = {"scope": GIGACHAT_SCOPE}
        try:
            async with httpx.AsyncClient(timeout=timeout, verify=SSL_VERIFY) as client:
                resp = await client.post(GIGACHAT_AUTH_URL, data=data, headers=headers)
        except Exception as e:
            logger.exception("GigaChat auth request failed: %s", e)
            return None

        if resp.status_code != 200:
            logger.error("GigaChat auth failed %s: %s", resp.status_code, resp.text[:1000])
            return None

        try:
            j = resp.json()
        except Exception:
            logger.exception("Failed to parse auth JSON: %s", resp.text[:1000])
            return None

        token = j.get("access_token") or j.get("accessToken") or j.get("token")
        expires_in = j.get("expires_in") or j.get("expires") or j.get("expires_at")

        if token:
            if isinstance(expires_in, (int, float)):
                if expires_in > 1e10:
                    expires_dt = datetime.utcfromtimestamp(int(expires_in) / 1000)
                else:
                    expires_dt = datetime.utcnow() + timedelta(seconds=int(expires_in))
            else:
                expires_dt = datetime.utcnow() + timedelta(minutes=30)
            _token_state["access_token"] = token
            _token_state["expires_at"] = expires_dt
            logger.info("GigaChat token obtained, expires at %s", expires_dt.isoformat())
            return token

        logger.error("Auth returned no token: %s", j)
        return None


async def _post_chat_completion(payload: dict, candidate_models: Optional[List[str]] = None, timeout: float = HTTP_TIMEOUT) -> Dict[str, Any]:
    token = await _fetch_token(timeout=timeout)
    if not token:
        raise RuntimeError("No access token for GigaChat")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
    }

    candidate_models = candidate_models or GIGACHAT_MODEL_LIST or []
    tried = []
    last_exc = None

    async with httpx.AsyncClient(timeout=timeout, verify=SSL_VERIFY) as client:
        for m in candidate_models:
            tried.append(m)
            p = dict(payload)
            if m:
                p["model"] = m
            try:
                resp = await client.post(GIGACHAT_API_URL, json=p, headers=headers)
            except Exception as e:
                last_exc = e
                logger.warning("GigaChat request exception for model %s: %s", m, e)
                continue
            if resp.status_code == 200:
                try:
                    return {"status": 200, "body": resp.json(), "tried": tried}
                except Exception:
                    return {"status": resp.status_code, "body_raw": resp.text, "tried": tried}
            else:
                logger.warning("GigaChat returned %s for model %s: %s", resp.status_code, m, resp.text[:1000])
        tried.append("(no model)")
        try:
            resp = await client.post(GIGACHAT_API_URL, json=payload, headers=headers)
            if resp.status_code == 200:
                return {"status": 200, "body": resp.json(), "tried": tried}
            else:
                logger.error("GigaChat final fallback returned %s: %s", resp.status_code, resp.text[:1000])
                return {"status": resp.status_code, "body": resp.text, "tried": tried}
        except Exception as e:
            logger.exception("GigaChat final fallback exception: %s", e)
            last_exc = e

    raise last_exc or RuntimeError("GigaChat request failed with unknown error")


def _summarize_shop_data(products: List[Dict[str, Any]], max_items: int = 50) -> str:
    if not products:
        return "Каталог товаров пуст."

    lines = []
    for p in products[:max_items]:
        pid = p.get("id", "?")
        title = p.get("title", "").replace("\n", " ").strip()
        price = p.get("price", "?")
        product_type = p.get("product_type", "")
        scent = ", ".join(p.get("scent_profile", [])) if p.get("scent_profile") else ""
        allergens = ", ".join(p.get("allergens", [])) if p.get("allergens") else ""
        desc = p.get("description", "")
        # берём первые 200 символов описания, убираем лишние пробелы
        short_desc = (desc[:200] + "...") if len(desc) > 200 else desc
        short_desc = short_desc.replace("\n", " ").strip()

        # собираем дополнительную информацию
        extra_parts = []
        if product_type:
            extra_parts.append(f"тип: {product_type}")
        if scent:
            extra_parts.append(f"ноты: {scent}")
        if allergens:
            extra_parts.append(f"аллергены: {allergens}")
        extra_str = f" ({', '.join(extra_parts)})" if extra_parts else ""

        lines.append(f"- {pid} | {title} | {price}₽{extra_str} | {short_desc}")

    summary = "\n".join(lines)
    if len(products) > max_items:
        summary += f"\n\n... и ещё {len(products) - max_items} товаров."
    return summary


def _build_user_context_prompt(user_profile: Dict[str, Any], history: List[Dict[str, str]]) -> str:
    """Формирует текстовое описание профиля пользователя и истории для передачи AI."""
    parts = []

    if user_profile.get("preferred_scents"):
        parts.append(f"Любимые ароматы: {', '.join(user_profile['preferred_scents'])}.")
    if user_profile.get("disliked_scents"):
        parts.append(f"Не любит: {', '.join(user_profile['disliked_scents'])}.")
    if user_profile.get("allergies"):
        parts.append(f"Аллергии: {', '.join(user_profile['allergies'])}.")
    if user_profile.get("tone_preference") != "auto":
        parts.append(f"Предпочитает стиль общения: {user_profile['tone_preference']}.")

    if history:
        # Последние 3 сообщения для понимания контекста
        last_msgs = history[-3:]
        history_str = "\n".join([f"{m['role']}: {m['content']}" for m in last_msgs])
        parts.append(f"Последние сообщения:\n{history_str}")

    if not parts:
        return "Нет сохранённой информации о пользователе."
    return "\n".join(parts)


async def get_ai_response(
    prompt: str,
    products: Optional[List[Dict[str, Any]]] = None,
    user_profile: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict[str, str]]] = None,
    role_name: str = "дружелюбный",
    role_description: str = "",
    personality: str = "neutral",
    mood: str = "neutral",
    timeout: float = HTTP_TIMEOUT
) -> str:
    """
    Отправляет запрос в GigaChat с учётом профиля пользователя и истории.
    """

    # Формируем текст последних сообщений
    if history:
        last_msgs = history[-3:]
        history_text = "\n".join([f"{m['role']}: {m['content']}" for m in last_msgs])
    else:
        history_text = "Нет недавних сообщений."

    # Создаём сводку каталога
    catalog_summary = _summarize_shop_data(products, max_items=50) if products else "Каталог временно недоступен."
    system = (
     f"Ты — {role_name}, {role_description} консультант магазина натуральной косметики и ароматерапии «SolidSimple».\n\n"
    
    "### О БРЕНДЕ\n"
    "SolidSimple — это место, где рождаются тёплые воспоминания и честные эмоции. Мы создаём продукты, которые становятся частью тебя: ароматы, текстуры, ритуалы. Наша философия: забота о себе и о мире, экологичность, ручная работа, любовь к деталям.\n\n"
    
    "### ТВОЯ ЭКСПЕРТИЗА\n"
    "Ты глубоко разбираешься в эфирных маслах, их свойствах и сочетаниях. Ты знаешь:\n"
    "- Лаванда, сандал, роза, ладан → успокаивают, расслабляют, помогают при стрессе.\n"
    "- Цитрусы (апельсин, лимон, грейпфрут), мята, эвкалипт → бодрят, освежают, повышают настроение.\n"
    "- Жасмин, иланг-иланг, роза → романтика, чувственность, раскрытие.\n"
    "- Пачули, кедр, ветивер → заземление, уверенность, концентрация.\n"
    "- Ваниль, корица, кардамон → уют, тепло, сладость.\n\n"
    "Ты также понимаешь, что у каждого человека могут быть индивидуальные реакции на компоненты, поэтому всегда обращаешь внимание на возможные аллергены.\n\n"
    
    "### О ПОЛЬЗОВАТЕЛЕ (используй для персонализации)\n"
    f"Имя: {user_profile.get('name', 'не указано')}\n"
    f"Любимые ноты/ароматы: {', '.join(user_profile.get('preferred_scents', [])) or 'не указаны'}\n"
    f"Нелюбимые ноты: {', '.join(user_profile.get('disliked_scents', [])) or 'не указаны'}\n"
    f"Аллергии: {', '.join(user_profile.get('allergies', [])) or 'не указаны'}\n"
    f"Психологический тип: {personality} (это подсказка о том, как лучше общаться: аналитический – любит факты, эмоциональный – важны чувства, романтичный – нужны образы, тревожный – нужна поддержка, практичный – ценит конкретику)\n"
    f"Текущее настроение (из последних сообщений): {mood}\n"
    f"Последние сообщения:\n{history_text}\n\n"
    
    "### ТВОИ ЗАДАЧИ\n"
    "1. Помогать выбирать товары, рассказывать об их свойствах, давать рекомендации.\n"
    "2. Если пользователь спрашивает про подарок, предложи подходящие варианты, учитывая пол, возраст, предпочтения, повод. Старайся комбинировать товары из разных категорий (например, роллер + свеча + крем).\n"
    "3. Анализируй состав и ноты из описания товара. Используй свои знания об ароматах, чтобы объяснить, почему товар подходит.\n"
    "4. Учитывай аллергии и предпочтения пользователя. Если видишь в составе потенциальный аллерген (даже если пользователь его не упоминал), деликатно предупреди: «В составе есть ... – если у вас аллергия, обратите внимание».\n"
    "5. Если пользователь готов купить (пишет «покупаю», «беру», «добавь в корзину»), перечисли выбранные товары и скажи: «Я подготовил для тебя кнопки для добавления в корзину». (Кнопки появятся автоматически.)\n"
    "6. Адаптируй стиль общения под психотип и настроение пользователя. Например, с аналитиком используй больше фактов, с романтиком – поэтические образы.\n\n"
    
    "### ПРАВИЛА ОБЩЕНИЯ\n"
    "- Отвечай естественно, как живой человек, с теплотой и уважением.\n"
    "- Используй эмодзи к месту 🌿✨💫, но не перебарщивай.\n"
    "- Обращайся к пользователю на «ты», если он не просит иначе.\n"
    "- Не выдумывай товары — используй только те, что есть в каталоге ниже.\n"
    "- Если товара нет, предложи аналоги: «Такого у нас пока нет, но могу предложить...».\n"
    "- Не перечисляй товары сухим списком ID — опиши их кратко, добавь эмоций.\n"
    "- Всегда проверяй наличие товара в каталоге перед ответом.\n\n"
    
    "### ЭТИЧЕСКИЕ ПРИНЦИПЫ\n"
    "1. Будь прозрачен: если пользователь спрашивает, прямо скажи, что ты ИИ.\n"
    "2. Не обещай того, чего нет.\n"
    "3. Не навязывай, предлагай варианты, уважай выбор.\n"
    "4. Объясняй свои рекомендации: «Я предлагаю это, потому что...».\n"
    "5. Заботься о безопасности: предупреждай об аллергенах, не рекомендуй то, что может навредить.\n\n"
    
    "### КАТАЛОГ (используй ТОЛЬКО эти товары)\n"
    f"{catalog_summary}\n\n"
    
    "Помни: **функциональность важнее личности**. Сначала убедись, что товар есть и он подходит, потом добавляй тепла и эмоций."
    )

    # Добавляем информацию о пользователе (каталог уже включен в system prompt выше)
    if user_profile or history:
        user_context = _build_user_context_prompt(user_profile or {}, history or [])
        system += f"\n\nИнформация о пользователе (используй для персонализации):\n{user_context}\n"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt}
    ]

    payload = {
        "messages": messages,
        "max_tokens": 800,
        "temperature": 0.7,
    }

    try:
        resp = await _post_chat_completion(payload, timeout=timeout)
    except Exception as e:
        logger.exception("GigaChat chat request exception: %s", e)
        raise

    if isinstance(resp, dict) and resp.get("status") == 200:
        body = resp.get("body")
        try:
            if isinstance(body, dict):
                choices = body.get("choices") or []
                if choices and isinstance(choices, list):
                    text_parts = []
                    for ch in choices:
                        if isinstance(ch, dict):
                            msg = ch.get("message") or ch.get("text") or {}
                            if isinstance(msg, dict):
                                content = msg.get("content") or msg.get("parts") or msg.get("text")
                                if isinstance(content, list):
                                    text_parts.extend([str(p) for p in content])
                                elif content:
                                    text_parts.append(str(content))
                            elif isinstance(msg, str):
                                text_parts.append(msg)
                    if text_parts:
                        return "\n".join(text_parts).strip()
                if "result" in body and isinstance(body["result"], dict):
                    r = body["result"]
                    if "content" in r:
                        return str(r["content"])
                if isinstance(body.get("text"), str):
                    return body["text"]
                return json.dumps(body, ensure_ascii=False)[:4000]
        except Exception:
            logger.exception("Failed to parse GigaChat response body")
            return str(body)[:4000]
    else:
        logger.error("GigaChat returned non-200: %s", resp)
        raise RuntimeError(f"GigaChat error: {resp}")


async def warmup_gigachat():
    """Прогрев модели: отправляем простой запрос, чтобы уменьшить задержку первого ответа."""
    try:
        logger.info("Прогрев GigaChat...")
        await get_ai_response("Привет", products=[], user_profile={}, history=[], timeout=10)
        logger.info("GigaChat прогрет успешно")
    except Exception as e:
        logger.warning(f"Ошибка прогрева GigaChat: {e}")