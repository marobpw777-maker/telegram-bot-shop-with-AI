# core/ai_product_helper.py
"""
Helper for extracting product info from local JSON catalog and building
a compact textual context for the AI prompt.

Usage:
  helper = AIProductHelper(path_to_json_or_none)
  products = helper.find_products_by_query("роллер Луч")
  ctx = helper.create_product_context(products)
"""

import os, json, re, logging
from typing import List, Dict, Optional

logger = logging.getLogger("core.ai_product_helper")

DEFAULT_JSON_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "shop_data.json")


class AIProductHelper:
    def __init__(self, json_path: Optional[str] = None):
        self.json_path = json_path or DEFAULT_JSON_PATH
        self.data = {"Products": []}
        try:
            with open(self.json_path, "r", encoding="utf-8") as fh:
                self.data = json.load(fh)
        except Exception as e:
            logger.warning("AIProductHelper: failed to load JSON %s: %s", self.json_path, e)
            self.data = {"Products": []}

    def _normalize(self, s: str) -> str:
        return (s or "").lower()

    def find_products_by_query(self, query: str, max_results: int = 10) -> List[Dict]:
        """Find relevant products by matching name, id, shortdesc, description."""
        q = self._normalize(query)
        results = []
        for p in self.data.get("Products", []):
            # join searchable fields
            fields = []
            for key in ("ProductTitle", "ProductID", "ShortDesc", "description", "short_info", "text"):
                if key in p and p.get(key):
                    fields.append(str(p.get(key)))
            hay = " ".join(fields).lower()
            if q in hay:
                results.append(p)
            else:
                # try token-level match
                tokens = q.split()
                if any(t for t in tokens if t and t in hay):
                    results.append(p)
            if len(results) >= max_results:
                break
        return results

    def create_product_context(self, products: List[Dict]) -> str:
        """Create compact textual block describing found products for the prompt."""
        parts = []
        for p in products:
            pid = p.get("ProductID", "")
            title = p.get("ProductTitle", "")
            price = p.get("Price", "")
            vol = p.get("Volume", "") or ""
            desc = p.get("description", "") or p.get("ShortDesc", "") or ""
            # try to extract the 'СОСТАВ' block
            composition = ""
            if "🔬 СОСТАВ:" in desc:
                try:
                    composition = desc.split("🔬 СОСТАВ:")[1].split("🔬 НАУЧНЫЙ ФАКТ:")[0].strip()
                except Exception:
                    composition = desc
            else:
                # fallback: take first 2 lines
                composition = "\n".join([ln.strip() for ln in desc.splitlines()[:4] if ln.strip()])[:400]

            part = f"ТОВАР: {title}\nID: {pid}\nЦена: {price} руб\nОбъём: {vol}\nСостав: {composition}\n---"
            parts.append(part)
        return "\n".join(parts) if parts else ""

    def all_product_ids(self) -> List[str]:
        return [p.get("ProductID") for p in self.data.get("Products", []) if p.get("ProductID")]
