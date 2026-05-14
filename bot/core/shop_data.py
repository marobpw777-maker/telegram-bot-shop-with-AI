# core/shop_data.py
import os
import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from collections import defaultdict

logger = logging.getLogger("core.shop_data")

# path to data file (project root / data/shop_data.json)
ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = os.getenv("SHOP_DATA_PATH", str(ROOT / "data" / "shop_data.json"))

# in-memory store
_shop_data: Dict[str, Dict[str, Any]] = {}
_products_list: List[Dict[str, Any]] = []

def _normalize_product(raw: dict) -> dict:
    product_id = raw.get("ProductID") or raw.get("id")
    if not product_id:
        return None

    title = raw.get("ProductTitle") or raw.get("title") or "Без названия"
    description = raw.get("description") or raw.get("text") or raw.get("ShortDesc") or ""
    full_text = (title + " " + description).lower()

    # Определяем тип продукта по ключевым словам в ID или названии
    product_type = "other"
    if "роллер" in full_text or str(product_id).lower().startswith("ar"):
        product_type = "roller"
    elif "свеч" in full_text or str(product_id).lower().startswith("be"):
        product_type = "candle"
    elif "диффузор" in full_text or str(product_id).lower().startswith("di"):
        product_type = "diffuser"
    elif "духи" in full_text or str(product_id).lower().startswith("sp"):
        product_type = "solid_perfume"
    elif "крем" in full_text or "баттер" in full_text:
        product_type = "cream"
    elif "соль" in full_text or "бомбочка" in full_text:
        product_type = "bath"

    # Извлекаем ароматические ноты
    scent_keywords = {
        "citrus": ["цитрус", "апельсин", "лимон", "грейпфрут", "бергамот"],
        "floral": ["роза", "жасмин", "лаванда", "фиалка", "пион", "иланг-иланг"],
        "woody": ["сандал", "кедр", "пачули", "ветивер", "сосна"],
        "fresh": ["мята", "эвкалипт", "морской", "озон", "зелень"],
        "spicy": ["корица", "имбирь", "кардамон", "гвоздика", "перец"],
        "sweet": ["ваниль", "шоколад", "карамель", "мед"],
    }
    scent_profile = []
    for scent, keywords in scent_keywords.items():
        for kw in keywords:
            if kw in full_text:
                scent_profile.append(scent)
                break  # достаточно одного совпадения для категории

    # Извлекаем аллергены (ищем в описании, особенно в блоке СОСТАВ)
    allergens = []
    allergen_list = ["иланг-иланг", "орехи", "цитрус", "лаванда", "эфирные масла"]
    for allergen in allergen_list:
        if allergen in full_text:
            allergens.append(allergen)

    return {
        "id": product_id,
        "title": title,
        "description": description,
        "price": raw.get("Price") or raw.get("price"),
        "volume": raw.get("Volume") or "",
        "category_id": raw.get("CategoryID") or "",
        "product_type": product_type,
        "scent_profile": list(set(scent_profile)),   # убираем дубликаты
        "allergens": list(set(allergens)),
        "allergy": raw.get("AllergyShort") or raw.get("allergy_test") or "",
        "storage": raw.get("StorageShort") or "",
        "contact": raw.get("ContactShort") or "",
        "tags": raw.get("tags") or [],
        "enabled": raw.get("Enabled", True),
        "image_path": raw.get("ImagePath") or "",
        "_raw": raw
    }

def _load_data():
    global _shop_data, _products_list
    try:
        p = Path(DATA_PATH)
        if not p.exists():
            logger.warning("shop_data.json not found at %s", DATA_PATH)
            _shop_data = {}
            _products_list = []
            return

        with p.open("r", encoding="utf-8") as fh:
            raw_data = json.load(fh)

        # Извлекаем товары из корневого объекта (ожидается словарь с ключом "Products")
        products_raw = []
        if isinstance(raw_data, dict):
            products_raw = raw_data.get("Products", [])
        elif isinstance(raw_data, list):
            # На случай, если файл — просто список товаров
            products_raw = raw_data
        else:
            logger.error("Unexpected shop_data.json format (not dict or list).")
            products_raw = []

        # Нормализуем и дедуплицируем по ProductID (последний побеждает)
        normalized = {}
        for prod in products_raw:
            norm = _normalize_product(prod)
            if norm is None:
                logger.warning("Skipping product without ID: %s", prod.get("ProductTitle", "?"))
                continue
            pid = norm["id"]
            normalized[pid] = norm   # последний экземпляр перезаписывает предыдущий

        _products_list = list(normalized.values())
        _shop_data = {p["id"]: p for p in _products_list}

        logger.info("Loaded shop data: %d products (after normalization)", len(_products_list))
        if _products_list:
            logger.debug("First product keys: %s", list(_products_list[0].keys()))

    except Exception as e:
        logger.exception("Failed to load shop_data.json: %s", e)
        _shop_data = {}
        _products_list = []

# load on import
_load_data()

# Public API
def reload_shop_data():
    _load_data()

def get_product(product_id: str) -> Optional[Dict[str, Any]]:
    return _shop_data.get(str(product_id))

def all_products() -> List[Dict[str, Any]]:
    return list(_products_list)

# small helper normalizer
def _tokens(text: str) -> List[str]:
    if not text:
        return []
    s = text.lower()
    # simple split, keep words only
    import re
    toks = re.findall(r"[а-яА-Яa-zA-Z0-9\-]+", s)
    return toks

def recommend_products_for_gift(
    user_text: str,
    budget: Optional[int] = None,
    gender: Optional[str] = None,
    top_n: int = 6
) -> List[Dict[str, Any]]:
    """
    Простая эвристика для рекомендаций:
      - сравниваем user_text tokens с product tags, title, description
      - учитываем цену относительно бюджета (предпочитаем вписаться)
      - учитываем поле 'popularity' если есть
    Возвращаем top_n продуктов (каждый — dict, добавлено поле 'score' и 'reason')
    """
    text_tokens = set(_tokens(user_text))
    results = []
    for prod in _products_list:
        score = 0.0
        # tags
        tags = prod.get("tags") or prod.get("categories") or []
        tags_lower = [t.lower() for t in tags] if isinstance(tags, list) else []
        tag_matches = 0
        for tt in tags_lower:
            if tt in text_tokens:
                tag_matches += 1
        score += tag_matches * 2.0

        # title/description match
        title = prod.get("title") or prod.get("name") or ""
        desc = prod.get("description") or prod.get("desc") or ""
        title_tokens = set(_tokens(title))
        desc_tokens = set(_tokens(desc))
        if text_tokens & title_tokens:
            score += 2.0
        if text_tokens & desc_tokens:
            score += 1.0

        # gender preference if product has target (e.g. 'for': 'women' or tag)
        target = (prod.get("target") or "").lower()
        if gender:
            if target and gender.lower() in target:
                score += 1.5
            # tags with 'women'/'men'
            if any(gender.lower() in x for x in tags_lower):
                score += 1.2

        # price influence
        price = None
        try:
            price = float(prod.get("price") or prod.get("cost") or 0)
        except Exception:
            price = None
        if budget and price:
            if price <= budget:
                # closer to budget is slightly worse than significantly lower? we'll prefer near-budget: score += (1 - abs(budget-price)/budget)
                diff = abs(budget - price) / max(1, budget)
                score += max(0.0, 1.0 - diff) * 2.0
            else:
                # if price > budget, penalize
                over = (price - budget) / max(1, budget)
                score -= min(1.5, over * 2.0)

        # popularity if exists
        pop = float(prod.get("popularity") or prod.get("rating") or 1)
        score *= (1.0 + min(1.0, pop / 10.0))

        # small boost if product explicitly has "gift" tag
        if "gift" in tags_lower or "подарок" in tags_lower:
            score += 0.8

        results.append((score, prod))

    # sort desc
    results.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, prod in results[:top_n]:
        p = prod.copy()
        p["score"] = round(float(score), 3)
        # prepare a short reason string
        reasons = []
        if text_tokens & set(_tokens(p.get("title",""))):
            reasons.append("совпадает по названию")
        if text_tokens & set(_tokens(p.get("description",""))):
            reasons.append("подходит по описанию")
        if budget:
            price = p.get("price") or p.get("cost")
            if price and float(price) <= budget:
                reasons.append(f"вписывается в бюджет (цена {price} ₽)")
            else:
                reasons.append(f"цена {price} ₽")
        if p.get("tags"):
            reasons.append("соответствует тегам: " + ", ".join((p.get("tags") or [])[:3]))
        p["reason"] = "; ".join(reasons) if reasons else "подходит по параметрам"
        out.append(p)
    return out

def format_product_short(prod: Dict[str, Any]) -> str:
    """Return short textual card for message"""
    pid = prod.get("id") or prod.get("sku") or "?"
    title = prod.get("title") or prod.get("name") or "Без названия"
    price = prod.get("price") or prod.get("cost") or "—"
    return f"• {title} (ID: {pid}) — {price} ₽ — {prod.get('reason','')}"