# core/ai_assistant.py
"""
DEPRECATED: Этот файл устарел и не используется в production.
Основной AI модуль теперь находится в core/gigachat_assistant.py

Асинхронный модуль для общения с DeepSeek API с простым circuit-breaker'ом.
Функция: async def get_ai_response(user_message: str, shop_data: dict) -> str
"""

import os
import json
import hashlib
import logging
from typing import Dict, Any, Optional, List
import asyncio
from datetime import datetime, timedelta

import httpx  # ensure installed: pip install httpx

logger = logging.getLogger("core.ai_assistant")

# Config from env (sane defaults)
DEESEEK_API_URL = os.getenv("DEESEEK_API_URL", "https://api.deepseek.com/chat/completions")
DEESEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", None)
DEESEEK_MODEL = os.getenv("DEEPSEEK_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))
DEFAULT_TIMEOUT = float(os.getenv("DEEPSEEK_TIMEOUT", "20.0"))

# Feature toggle: quick way to disable AI globally via env
DEESEEK_ENABLED = os.getenv("DEEPSEEK_ENABLED", "true").lower() in ("1", "true", "yes", "on")

# Circuit-breaker settings
_MAX_FAILURES = int(os.getenv("DEEPSEEK_MAX_CONSECUTIVE_FAILURES", "3"))
_COOLDOWN_SECONDS = int(os.getenv("DEEPSEEK_COOLDOWN_SECONDS", "600"))  # 10 minutes default

# In-memory state (process-local)
_SERVICE_STATE: Dict[str, Any] = {
    "available": True,
    "fail_count": 0,
    "disabled_until": None,  # datetime
}
_STATE_LOCK = asyncio.Lock()

# Prompt cache keyed by shop hash
_PROMPT_CACHE: Dict[str, Dict[str, Any]] = {}
_PROMPT_CACHE_TTL = int(os.getenv("DEESEEK_PROMPT_CACHE_TTL_SEC", "3600"))  # 1 hour

# Helpers for shop summarization
def _summarize_shop_data(shop_data: Dict[str, Any], max_products: int = 50) -> str:
    products = shop_data.get("Products", []) if isinstance(shop_data, dict) else []
    enabled = [p for p in products if p.get("Enabled", True)]
    enabled_sorted = sorted(enabled, key=lambda p: (p.get("CategoryID", ""), p.get("ProductID", "")))
    lines: List[str] = []
    for p in enabled_sorted[:max_products]:
        pid = p.get("ProductID", "") or p.get("id", "")
        title = str(p.get("ProductTitle", p.get("name", ""))).replace("\n", " ").strip()
        price = p.get("Price", "")
        cat = p.get("CategoryID", "")
        short = p.get("ShortDesc", "") or ""
        short = str(short).replace("\n", " ").strip()
        lines.append(f"- {pid} | {title} | {price} руб. | {cat} | {short}")
    summary = "\n".join(lines)
    if len(enabled_sorted) > max_products:
        from collections import Counter
        extra = enabled_sorted[max_products:]
        cat_count = Counter([p.get("CategoryID", "unknown") for p in extra])
        cat_lines = [f"{c}: {n} more products" for c, n in cat_count.items()]
        summary += "\n\nSummary of remaining products:\n" + ", ".join(cat_lines)
    return summary

def _get_shop_hash(shop_data: Dict[str, Any]) -> str:
    try:
        dump = json.dumps(shop_data, ensure_ascii=False, sort_keys=True)
    except Exception:
        dump = repr(shop_data)
    return hashlib.sha256(dump.encode("utf-8")).hexdigest()

def _build_system_prompt(shop_data: Dict[str, Any], shop_name: Optional[str] = None) -> str:
    if not shop_name:
        slides = shop_data.get("Slides", []) if isinstance(shop_data, dict) else []
        if slides and isinstance(slides, list) and len(slides) > 0:
            shop_name = slides[0].get("SlideTitle", "наш магазин")
        else:
            shop_name = "наш магазин"
    prompt_lines = [
        f"System: You are a product consultant for '{shop_name}'.",
        "Strict rules (must follow):",
        "1) Answer only about products, price, availability, categories, shipping options and public shop policies.",
        "2) Do NOT invent or hallucinate products that are not in the provided catalog.",
        "3) Do NOT request or use personal data (user ids, names, phone numbers, addresses, order history, cart contents).",
        "4) If the user asks about a particular order or personal data, politely reply: 'Я не могу проверить детали заказов. Для вопросов по конкретному заказу обратитесь в поддержку.'",
        "5) Provide short, actionable, friendly answers (1-3 short paragraphs). If the user asks to add a product to cart, prepare a structured suggestion like: ADD_TO_CART: <ProductID> <Qty=1>. Do not perform any action yourself.",
        "",
        "Catalog summary (only public data follows):",
        ""
    ]
    summary = _summarize_shop_data(shop_data, max_products=40)
    prompt_lines.append(summary)
    prompt_lines.append("\nEnd of catalog summary.")
    prompt_lines.append("\nUser query follows. Answer concisely in Russian (or the user's language).")
    return "\n".join(prompt_lines)

# Circuit-breaker helpers
async def is_service_available() -> bool:
    """Return whether DeepSeek should be considered available now."""
    if not DEESEEK_ENABLED:
        return False
    async with _STATE_LOCK:
        disabled_until = _SERVICE_STATE.get("disabled_until")
        if disabled_until and datetime.utcnow() < disabled_until:
            return False
        return True

async def _record_failure_async():
    """Record a failure; possibly disable the service if failures >= threshold."""
    async with _STATE_LOCK:
        _SERVICE_STATE["fail_count"] = _SERVICE_STATE.get("fail_count", 0) + 1
        fc = _SERVICE_STATE["fail_count"]
        logger.info("DeepSeek fail count -> %d", fc)
        if fc >= _MAX_FAILURES:
            disabled_until = datetime.utcnow() + timedelta(seconds=_COOLDOWN_SECONDS)
            _SERVICE_STATE["disabled_until"] = disabled_until
            _SERVICE_STATE["available"] = False
            logger.warning("DeepSeek disabled until %s after %d failures", disabled_until.isoformat(), fc)

async def _record_success_async():
    async with _STATE_LOCK:
        _SERVICE_STATE["fail_count"] = 0
        _SERVICE_STATE["disabled_until"] = None
        _SERVICE_STATE["available"] = True

# Main function
async def get_ai_response(user_message: str, shop_data: Dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT) -> str:
    """
    Make an async call to DeepSeek and return a user-friendly string in Russian.
    On failures, returns an explanatory message. Implements circuit-breaker.
    """
    # Check feature toggle
    if not DEESEEK_ENABLED:
        return "ИИ-консультант временно отключён (администратор)."

    # Ensure API key present
    if not DEESEEK_API_KEY:
        logger.error("DEEPSEEK_API_KEY not set in environment.")
        return "Консультант временно недоступен (настройки). Попробуйте позже."

    # Check circuit-breaker availability
    if not await is_service_available():
        return "Консультант временно недоступен. Попробуйте позже."

    # Build or reuse system prompt
    shop_hash = _get_shop_hash(shop_data or {})
    cache_entry = _PROMPT_CACHE.get(shop_hash)
    if cache_entry:
        # check ttl
        created = cache_entry.get("created_at")
        if created and (datetime.utcnow() - created).total_seconds() > _PROMPT_CACHE_TTL:
            # expired
            cache_entry = None
    if cache_entry:
        system_prompt = cache_entry["prompt"]
    else:
        system_prompt = _build_system_prompt(shop_data or {})
        _PROMPT_CACHE[shop_hash] = {"prompt": system_prompt, "created_at": datetime.utcnow()}

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ]

    payload = {
        "model": DEESEEK_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 800
    }

    headers = {
        "Authorization": f"Bearer {DEESEEK_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # Make request
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(DEESEEK_API_URL, json=payload, headers=headers)
    except httpx.TimeoutException:
        logger.warning("DeepSeek request timed out.")
        await _record_failure_async()
        return "Консультант временно не отвечает (таймаут). Попробуйте позже."
    except Exception as e:
        logger.exception("Network error while calling DeepSeek: %s", e)
        await _record_failure_async()
        return "Ошибка сети при обращении к консультанту. Попробуйте повторить через минуту."

    # Handle HTTP status codes explicitly
    status = resp.status_code
    if status == 401:
        logger.error("DeepSeek returned 401 Unauthorized. Check DEEPSEEK_API_KEY.")
        await _record_failure_async()
        return "Консультант недоступен (проблема с авторизацией). Свяжитесь с администратором."
    if status == 402:
        # Insufficient balance / payment required
        logger.error("DeepSeek returned 402 Payment Required: %s", resp.text)
        await _record_failure_async()
        # Give a clear user-facing message (don't expose internal details)
        return "Консультант временно недоступен (недостаточно средств на сервисе ИИ). Попробуйте позже."
    if status == 429:
        logger.warning("DeepSeek rate limit (429).")
        await _record_failure_async()
        return "Консультант временно перегружен. Попробуйте снова через минуту."
    if status >= 500:
        logger.error("DeepSeek server error: %s", status)
        await _record_failure_async()
        return "Консультант временно недоступен (ошибка сервера). Попробуйте позже."

    # Parse JSON
    try:
        j = resp.json()
    except Exception:
        logger.exception("Invalid JSON from DeepSeek: %s", resp.text[:200])
        await _record_failure_async()
        return "Ошибка на стороне консультанта. Попробуйте позже."

    # Try to extract answer from common shapes
    # 1) OpenAI-like response
    try:
        if isinstance(j, dict) and "choices" in j and isinstance(j["choices"], list) and len(j["choices"]) > 0:
            choice = j["choices"][0]
            if isinstance(choice.get("message"), dict) and "content" in choice["message"]:
                text = choice["message"]["content"].strip()
                await _record_success_async()
                return text
            if "text" in choice and isinstance(choice["text"], str):
                text = choice["text"].strip()
                await _record_success_async()
                return text
    except Exception:
        pass

    # 2) direct reply/message fields
    if isinstance(j, dict):
        if "reply" in j and isinstance(j["reply"], str):
            await _record_success_async()
            return j["reply"].strip()
        if "message" in j and isinstance(j["message"], str):
            await _record_success_async()
            return j["message"].strip()

    # 3) fallback output/choices stringification
    if "output" in j:
        try:
            out = str(j["output"])
            await _record_success_async()
            return out[:4000]
        except Exception:
            pass
    if "choices" in j:
        try:
            out = str(j["choices"])
            await _record_success_async()
            return out[:4000]
        except Exception:
            pass

    # unknown format - treat as failure
    logger.error("Unexpected DeepSeek response shape: %s", j)
    await _record_failure_async()
    return "Консультант ответил в непривычном формате. Попробуйте переформулировать вопрос."
