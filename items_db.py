"""База предметов: название → item_id (из официального listing.json)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import aiofiles
import aiohttp

logger = logging.getLogger("stalcraft-bot.items")

LISTING_URL = (
    "https://raw.githubusercontent.com/EXBO-Studio/stalcraft-database/main/ru/listing.json"
)
CACHE_FILE = Path("data/listing_cache.json")

# Из "/items/artefact/electrophysical/wg53.json" → "wg53"
_ID_FROM_PATH = re.compile(r"/([^/]+)\.json$", re.IGNORECASE)


class ItemsDB:
    def __init__(self) -> None:
        self._items: list[dict] = []
        self._by_id: dict[str, dict] = {}

    @property
    def loaded(self) -> bool:
        return bool(self._items)

    @property
    def count(self) -> int:
        return len(self._items)

    async def load(self, session: aiohttp.ClientSession | None = None) -> None:
        if CACHE_FILE.exists():
            try:
                async with aiofiles.open(CACHE_FILE, "r", encoding="utf-8") as f:
                    raw = json.loads(await f.read())
                self._build_index(raw)
                if self.count > 0:
                    logger.info("Items DB loaded from cache: %s items", self.count)
                    return
                logger.warning("Cache empty, re-downloading…")
            except Exception as e:
                logger.warning("Cache read failed: %s", e)

        await self.refresh(session)

    async def refresh(self, session: aiohttp.ClientSession | None = None) -> None:
        close = False
        if session is None:
            session = aiohttp.ClientSession()
            close = True
        try:
            logger.info("Downloading items listing…")
            async with session.get(LISTING_URL, timeout=aiohttp.ClientTimeout(total=180)) as resp:
                resp.raise_for_status()
                raw = await resp.json(content_type=None)
            self._build_index(raw)
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(CACHE_FILE, "w", encoding="utf-8") as f:
                await f.write(json.dumps(raw, ensure_ascii=False))
            logger.info("Items DB downloaded: %s items", self.count)
        finally:
            if close:
                await session.close()

    def _extract_id(self, entry: dict) -> str | None:
        # Явное поле id
        for key in ("id", "item_id", "itemId"):
            if entry.get(key):
                return str(entry[key])

        # Из пути data: "/items/artefact/.../wg53.json" → "wg53"
        data_path = entry.get("data") or entry.get("path") or ""
        if data_path:
            m = _ID_FROM_PATH.search(str(data_path).replace("\\", "/"))
            if m:
                return m.group(1)

        # Из icon: "/icons/.../wg53.png"
        icon = entry.get("icon") or ""
        if icon:
            m = re.search(r"/([^/]+)\.(png|jpg|webp)$", str(icon), re.I)
            if m:
                return m.group(1)

        return None

    def _extract_name(self, entry: dict) -> str:
        name_obj = entry.get("name")
        if isinstance(name_obj, dict):
            # {"lines": {"ru": "Атом"}} или {"ru": "Атом"}
            lines = name_obj.get("lines")
            if isinstance(lines, dict):
                return (
                    lines.get("ru")
                    or lines.get("en")
                    or next(iter(lines.values()), "")
                    or ""
                )
            return name_obj.get("ru") or name_obj.get("en") or ""
        if isinstance(name_obj, str):
            return name_obj
        return ""

    def _build_index(self, raw) -> None:
        items = []
        by_id: dict[str, dict] = {}

        entries = raw if isinstance(raw, list) else (
            raw.get("data") or raw.get("items") or raw.get("listing") or []
        )

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            item_id = self._extract_id(entry)
            if not item_id:
                continue

            name = self._extract_name(entry)
            if not name:
                continue

            name_clean = (
                name.replace("«", "")
                .replace("»", "")
                .replace('"', "")
                .replace("'", "")
                .strip()
            )
            if not name_clean:
                continue

            rec = {
                "id": item_id,
                "name": name_clean,
                "name_lower": name_clean.lower(),
                "category": entry.get("category") or "",
            }
            items.append(rec)
            by_id[item_id.lower()] = rec

        self._items = items
        self._by_id = by_id

    def search(self, query: str, limit: int = 15) -> list[dict]:
        q = (
            query.strip()
            .lower()
            .replace("«", "")
            .replace("»", "")
            .replace('"', "")
            .replace("'", "")
        )
        if not q:
            return []

        if q in self._by_id:
            return [self._by_id[q]]

        exact = []
        starts = []
        contains = []

        for item in self._items:
            nl = item["name_lower"]
            if nl == q:
                exact.append(item)
            elif nl.startswith(q):
                starts.append(item)
            elif q in nl:
                contains.append(item)

        return (exact + starts + contains)[:limit]

    def get_name(self, item_id: str) -> str:
        rec = self._by_id.get(item_id.lower())
        return rec["name"] if rec else item_id


items_db = ItemsDB()
