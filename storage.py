"""Хранилище подписок пользователей."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiofiles

from config import DATA_FILE, MAX_WATCH_PER_USER


class Storage:
    def __init__(self, path: str = DATA_FILE) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = {}

    async def load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            async with aiofiles.open(self.path, "r", encoding="utf-8") as f:
                content = await f.read()
                self._data = json.loads(content) if content.strip() else {}
        else:
            self._data = {"users": {}, "seen": {}}

    async def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(self.path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(self._data, ensure_ascii=False, indent=2))

    def get_user_watches(self, user_id: int) -> list[dict]:
        users = self._data.setdefault("users", {})
        return list(users.get(str(user_id), {}).get("watches", []))

    def get_all_watches(self) -> list[tuple[int, dict]]:
        """[(user_id, watch_dict), ...]"""
        result = []
        for uid_str, udata in self._data.get("users", {}).items():
            uid = int(uid_str)
            for w in udata.get("watches", []):
                result.append((uid, w))
        return result

    async def add_watch(self, user_id: int, watch: dict) -> tuple[bool, str]:
        users = self._data.setdefault("users", {})
        udata = users.setdefault(str(user_id), {"watches": []})
        watches: list[dict] = udata["watches"]

        if len(watches) >= MAX_WATCH_PER_USER:
            return False, f"Достигнут лимит ({MAX_WATCH_PER_USER} отслеживаний)."

        # Проверка дубликата (включая атрибуты и их редкости)
        def _norm_attrs(a):
            if not a:
                return None
            return tuple(sorted(str(x).lower() for x in a))

        def _norm_qlts(q):
            if not q:
                return None
            return tuple(sorted((str(k).lower(), v) for k, v in q.items()))

        for w in watches:
            if (
                w.get("item_id") == watch["item_id"]
                and w.get("qlt") == watch.get("qlt")
                and w.get("ptn") == watch.get("ptn")
                and w.get("max_price") == watch.get("max_price")
                and _norm_attrs(w.get("attrs")) == _norm_attrs(watch.get("attrs"))
                and _norm_qlts(w.get("attr_qlts")) == _norm_qlts(watch.get("attr_qlts"))
            ):
                return False, "Такое отслеживание уже есть."

        watches.append(watch)
        await self.save()
        return True, self._format_watch(watch, prefix="✅ Добавлено:\n")

    async def remove_watch(self, user_id: int, index: int) -> tuple[bool, str]:
        users = self._data.setdefault("users", {})
        udata = users.get(str(user_id))
        if not udata:
            return False, "Список пуст."
        watches = udata.get("watches", [])
        if index < 1 or index > len(watches):
            return False, f"Номер должен быть от 1 до {len(watches)}."
        removed = watches.pop(index - 1)
        await self.save()
        return True, self._format_watch(removed, prefix="🗑 Удалено:\n")

    def get_seen_lots(self, key: str) -> set[str]:
        seen = self._data.setdefault("seen", {})
        return set(seen.get(key, []))

    async def mark_lots_seen(self, key: str, lot_keys: list[str]) -> None:
        seen = self._data.setdefault("seen", {})
        current = set(seen.get(key, []))
        current.update(lot_keys)
        seen[key] = list(current)[-500:]
        await self.save()

    @staticmethod
    def _format_watch(w: dict, prefix: str = "") -> str:
        qlt_names = {
            None: "любая",
            0: "обычный",
            1: "необычный",
            2: "особый",
            3: "редкий",
            4: "исключительный",
            5: "легендарный",
        }
        qlt = qlt_names.get(w.get("qlt"), str(w.get("qlt")))
        ptn = w.get("ptn")
        ptn_s = "любая" if ptn is None else f"+{ptn}"
        price = w.get("max_price")
        price_s = "любая" if price is None else f"до {price:,}".replace(",", " ") + " / шт."
        attrs = w.get("attrs")
        if attrs:
            attr_qlts = w.get("attr_qlts") or {}
            parts = []
            for a in attrs:
                aq = attr_qlts.get(a, attr_qlts.get(str(a).lower()))
                if aq is None and a not in attr_qlts and str(a).lower() not in attr_qlts:
                    aq = w.get("qlt")
                aq_s = qlt_names.get(aq, "любая") if aq is not None else "любая"
                parts.append(f"{a} · {aq_s}")
            lines = [
                f"{prefix}📦 <code>{w['item_id']}</code>",
                f"🔧 {', '.join(parts)}",
                f"💰 Цена: {price_s}",
            ]
        else:
            lines = [
                f"{prefix}📦 <code>{w['item_id']}</code>",
                f"🎖 Редкость: {qlt}",
                f"⚔ Заточка: {ptn_s}",
                f"💰 Цена: {price_s}",
            ]
        return "\n".join(lines)

    # Совместимость со старым /add
    def get_user_items(self, user_id: int) -> list[str]:
        return [w["item_id"] for w in self.get_user_watches(user_id)]

    async def add_item(self, user_id: int, item_id: str) -> tuple[bool, str]:
        return await self.add_watch(user_id, {
            "item_id": item_id.strip().lower(),
            "qlt": None,
            "ptn": None,
            "max_price": None,
        })

    async def remove_item(self, user_id: int, item_id: str) -> tuple[bool, str]:
        item_id = item_id.strip().lower()
        users = self._data.setdefault("users", {})
        udata = users.get(str(user_id))
        if not udata:
            return False, "Список пуст."
        watches = udata.get("watches", [])
        for i, w in enumerate(watches):
            if w["item_id"] == item_id:
                removed = watches.pop(i)
                await self.save()
                return True, self._format_watch(removed, prefix="🗑 Удалено:\n")
        return False, "Предмет не найден в списке."