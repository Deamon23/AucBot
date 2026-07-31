"""
Telegram-бот мониторинга аукциона STALCRAFT.
Меню: /new, /tracks, /check, /help
Поиск по названию предмета + фильтры (редкость, заточка, цена).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Awaitable

import aiohttp
from aiohttp import resolver as aiohttp_resolver
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    BotCommand,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    TelegramObject,
)

from config import (
    ALLOWED_USERS,
    BOT_TOKEN,
    CHECK_INTERVAL,
    CLIENT_ID,
    CLIENT_SECRET,
    DNS_CACHE_TTL,
    REGION,
)
from storage import Storage
from items_db import items_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("stalcraft-bot")

# Настраиваем DNS кеш для aiohttp - кешируем DNS записи на заданное время
# Это предотвращает проблемы с сессиями при временных сбоях DNS
aiohttp_resolver.DefaultResolver._resolve_timeout = 10.0

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
storage = Storage()

API_BASE = "https://eapi.stalzone.net"
OAUTH_URL = "https://exbo.net/oauth/token"

# Глобальная сессия для повторного использования соединений и DNS кеша
_session: aiohttp.ClientSession | None = None
_access_token: str | None = None
_token_expires_at: float = 0


def get_session() -> aiohttp.ClientSession:
    """Возвращает глобальную сессию для API запросов."""
    global _session
    if _session is None or _session.closed:
        # Настраиваем таймауты и лимиты соединений
        connector = aiohttp.TCPConnector(
            ttl_dns_cache=DNS_CACHE_TTL,  # Кешируем DNS записи
            limit=50,  # Максимум открытых соединений
            limit_per_host=10,  # Максимум соединений на хост
            enable_cleanup_closed=True,  # Очищаем закрытые соединения
        )
        timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=45)
        _session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _session


async def get_access_token(session: aiohttp.ClientSession) -> str:
    """Получает и кэширует OAuth токен. Токен действителен 1 час."""
    global _access_token, _token_expires_at
    import time
    
    # Если токен ещё действителен (с запасом 5 минут), возвращаем его
    if _access_token and time.time() < _token_expires_at - 300:
        logger.debug("Используем кэшированный токен (действителен до %s)", _token_expires_at)
        return _access_token
    
    data = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    
    async with session.post(OAUTH_URL, data=data) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"OAuth ошибка {resp.status}: {text[:200]}")
        result = await resp.json()
        _access_token = result["access_token"]
        expires_in = int(result.get("expires_in", 3600))
        _token_expires_at = time.time() + expires_in
        logger.info("Получен новый access_token, действителен %d секунд (до %s)", expires_in, _token_expires_at)
        return _access_token

MODULE_ITEM_ID = "1pyq"

QLT_NAMES = {
    0: "обычный",
    1: "необычный",
    2: "особый",
    3: "редкий",
    4: "исключительный",
    5: "легендарный",
}

# definitionId → человекочитаемое имя
# _pre = Надстройка (позитивные), _suf = Отклонение (негативные), остальные = Концепт
ATTR_NAMES: dict[str, str] = {
    # ── Надстройки (_pre) ─────────────────────────────────────────────
    "aim_switch_time_pre": "Бдительный",
    "hip_spread_pre": "Устойчивый",
    "spread_pre": "Суммирующий",
    "recoil_pre": "Вертикальный",
    "horizontal_recoil_pre": "Горизонтальный",
    "recoil_gain_pre": "Плавный",
    "shoot_factor_decrement_pre": "Стабильный",
    "draw_time_pre": "Мгновенный",
    "aiming_speed_modifier_pre": "Фокусный",
    "equipped_speed_modifier_pre": "Подвижный",
    "ergonomics_pre": "Комфортный",
    "wiggle_pre": "Гармоничный",

    # ── Отклонения (_suf) ─────────────────────────────────────────────
    "aim_switch_time_suf": "Дрожащий",
    "hip_spread_suf": "Неустойчивый",
    "spread_suf": "Отрицающий",
    "recoil_suf": "Уходящий",
    "horizontal_recoil_suf": "Сдвигающий",
    "recoil_gain_suf": "Резкий",
    "shoot_factor_decrement_suf": "Нестабильный",
    "draw_time_suf": "Инертный",
    "aiming_speed_modifier_suf": "Медлительный",
    "equipped_speed_modifier_suf": "Заторможенный",
    "ergonomics_suf": "Дискомфортный",
    "wiggle_suf": "Люфтящий",

    # ── Концепты (_aff + особые) ──────────────────────────────────────
    "aim_switch_time_aff": "Стрелок",
    "hip_spread_aff": "Проектор",
    "spread_aff": "Контролер",
    "recoil_aff": "Держатель",
    "horizontal_recoil_aff": "Фиксатор",
    "recoil_gain_aff": "Седатор",
    "draw_time_aff": "Экстрактор",
    "aiming_speed_modifier_aff": "Оператор",
    "equipped_speed_modifier_aff": "Слайдер",
    "ergonomics_aff": "Биостабилизатор",
    "shoot_factor_decrement_aff": "Гаситель",
    "wiggle_aff": "Вариатор",
    "marksman": "Снайпер",
    "sadist": "Охотник",
    "sledgehammer": "Перфоратор",
    "gyroscope": "Регулятор",
    "acceleration": "Термос",
    "equalizer_op": "Палач",
    "concentration": "Губитель",
    "surge": "Пробойник",
    "inside": "Компрессор",
    "inside2": "Декомпрессор",
    "berserk": "Агрессор",
    "finish": "Завершитель",
}

ATTR_SLOT_NAMES = {
    1: "Надстройка",
    2: "Концепт",
    0: "Отклонение",
}

# Порядок запроса редкости: Надстройка → Отклонение → Концепт
_SLOT_ORDER = (1, 0, 2)


def _attr_slot(definition_id: str) -> int:
    """Слот атрибута по definitionId.
    _pre → Надстройка (1), _suf → Отклонение (0), остальное → Концепт (2).
    """
    if definition_id.endswith("_pre"):
        return 1
    if definition_id.endswith("_suf"):
        return 0
    return 2


# Обратный индекс: "компрессор" → "inside", "компрессоры" тоже сработает через нормализацию
ATTR_BY_NAME: dict[str, str] = {
    name.lower(): did for did, name in ATTR_NAMES.items()
}


def resolve_attr_token(token: str) -> str | None:
    """Превращает ввод пользователя в definitionId.
    Принимает: definitionId, русское название, частичное совпадение.
    """
    t = token.strip().lower()
    if not t:
        return None
    # точный definitionId
    if t in ATTR_NAMES:
        return t
    # точное русское имя
    if t in ATTR_BY_NAME:
        return ATTR_BY_NAME[t]
    # частичное: "компрессор" / "компрессоры" / "вертикальн"
    matches = [
        did for did, name in ATTR_NAMES.items()
        if t in name.lower() or name.lower() in t or t in did
    ]
    if len(matches) == 1:
        return matches[0]
    return None


# ── Доступ ───────────────────────────────────────────────────────────────────

class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        if ALLOWED_USERS and user.id not in ALLOWED_USERS:
            if isinstance(event, Message):
                await event.answer("⛔ Доступ запрещён.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Доступ запрещён.", show_alert=True)
            return None
        return await handler(event, data)


dp.message.middleware(AccessMiddleware())
dp.callback_query.middleware(AccessMiddleware())


# ── FSM ──────────────────────────────────────────────────────────────────────

class NewWatch(StatesGroup):
    item_id = State()
    rarity = State()
    ptn = State()
    attrs = State()          # только для модулей: список definitionId
    attr_rarity = State()    # поочерёдно редкость по слотам (надстройка/отклонение/концепт)
    price = State()
    confirm = State()


class CheckFilter(StatesGroup):
    """Состояния для фильтров команды /check"""
    selecting_item = State()
    rarity = State()
    ptn = State()


# ── API ──────────────────────────────────────────────────────────────────────

async def fetch_lots(
    session: aiohttp.ClientSession,
    item_id: str,
    limit: int = 50,
    offset: int = 0,
    sort: str = "time_created",
    order: str = "desc",
    retries: int = 3,
) -> dict[str, Any]:
    url = f"{API_BASE}/{REGION}/auction/{item_id}/lots"
    params = {
        "limit": min(limit, 200),
        "offset": offset,
        "sort": sort,
        "order": order,
        "additional": "true",
    }
    
    # Получаем кэшированный OAuth токен
    token = await get_access_token(session)
    headers = {
        "Authorization": f"Bearer {token}",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            async with session.get(
                url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=45)
            ) as resp:
                if resp.status == 401:
                    # Токен истёк, пробуем обновить
                    _globals = globals()
                    _globals["_access_token"] = None
                    token = await get_access_token(session)
                    headers["Authorization"] = f"Bearer {token}"
                    continue
                if resp.status == 404:
                    return {"total": 0, "lots": []}
                if resp.status == 429:
                    await asyncio.sleep(2.0 + attempt * 1.5)
                    continue
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"API ошибка {resp.status}: {text[:200]}")
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_err = e
            await asyncio.sleep(1.0 + attempt)
    if last_err:
        raise RuntimeError(f"API недоступен после {retries} попыток: {last_err}")
    return {"total": 0, "lots": []}


def _page_has_module_attrs(lots: list) -> bool:
    """Проверяет, что API реально вернул attributes (не обрезал additional)."""
    if not lots:
        return True
    with_attrs = 0
    for lot in lots:
        add = lot.get("additional") or {}
        if add.get("attributes"):
            with_attrs += 1
    # хотя бы 30% лотов на странице должны иметь attributes
    return with_attrs >= max(1, len(lots) // 3)


async def fetch_all_lots(
    session: aiohttp.ClientSession,
    item_id: str,
    max_lots: int = 500,
    sort: str = "time_created",
    order: str = "asc",
) -> dict[str, Any]:
    """Загружает лоты страницами через offset.

    Пропускает страницы с new=0 (дубли).
    При задержке API — игнорируем и переходим к следующему offset.
    """
    page_size = 200  # Максимальный размер страницы API
    all_lots: list = []
    seen_keys: set[str] = set()
    total = 0
    offset = 0
    page_delay = 0.5  # Быстрая пагинация: 200 запросов в минуту
    is_module = item_id.lower() == MODULE_ITEM_ID
    max_dup_retries = 4

    while len(all_lots) < max_lots:
        data = await fetch_lots(
            session, item_id, limit=page_size, offset=offset, sort=sort, order=order
        )
        total = int(data.get("total") or total or 0)
        lots = data.get("lots") or []

        if not lots:
            logger.info(
                "fetch %s: offset=%s пустая страница, стоп (unique=%s total=%s)",
                item_id, offset, len(all_lots), total,
            )
            break

        if is_module and not _page_has_module_attrs(lots):
            logger.warning("offset=%s: без attributes, повтор через 3s", offset)
            await asyncio.sleep(3.0)
            data = await fetch_lots(
                session, item_id, limit=page_size, offset=offset, sort=sort, order=order
            )
            lots = data.get("lots") or []
            if not lots:
                break

        new_on_page = 0
        first_t = (lots[0].get("startTime") if lots else "") or ""
        for lot in lots:
            k = lot_key(lot)
            if k in seen_keys:
                continue
            seen_keys.add(k)
            all_lots.append(lot)
            new_on_page += 1

        logger.info(
            "fetch %s: offset=%s page=%s new=%s unique=%s total=%s first=%s",
            item_id, offset, len(lots), new_on_page, len(all_lots), total, first_t[:19],
        )

        # УДАЛЕНА вся ветка с dup_retries и ожиданием 9с
        # Теперь при new=0 просто продолжаем пагинацию

        offset += page_size

        if total and offset >= total:
            break
        if len(all_lots) >= max_lots:
            break
        if len(lots) < page_size:
            break

        await asyncio.sleep(page_delay)

    return {"total": total, "lots": all_lots[:max_lots]}
def _attrs_signature(add: dict) -> str:
    """Стабильная строка атрибутов модуля для ключа лота."""
    attrs = add.get("attributes") or []
    parts = []
    for a in sorted(attrs, key=lambda x: (x.get("type") or 0, x.get("definitionId") or "")):
        parts.append(
            f"{a.get('type')}:{a.get('definitionId')}:{a.get('quality')}:{round(float(a.get('statsRandom') or 0), 5)}"
        )
    return "|".join(parts)


def watch_seen_key(item_id: str, watch: dict, uid: int) -> str:
    """Стабильный ключ для хранения увиденных лотов по конкретному слежению."""
    attrs_part = ",".join(sorted(watch.get("attrs") or []))
    aq = watch.get("attr_qlts") or {}
    aq_part = ",".join(f"{k}:{aq[k]}" for k in sorted(aq)) if aq else ""
    return (
        f"{item_id}|{watch.get('qlt')}|{watch.get('ptn')}|"
        f"{watch.get('max_price')}|{attrs_part}|{aq_part}|{uid}"
    )


def lot_key(lot: dict) -> str:
    """Уникальный ключ лота. currentPrice НЕ включаем — он меняется при ставках."""
    start = lot.get("startTime") or ""
    end = lot.get("endTime") or ""
    buyout = lot.get("buyoutPrice") or 0
    start_price = lot.get("startPrice") or 0
    amount = lot.get("amount") or 1
    item = lot.get("itemId") or ""
    add = lot.get("additional") or {}
    base = (
        f"{item}|{start}|{end}|{buyout}|{start_price}|{amount}|"
        f"{add.get('qlt')}|{add.get('ptn')}"
    )
    if add.get("attributes"):
        base += "|" + _attrs_signature(add)
    return base


def format_price(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def unit_price(lot: dict) -> float:
    """Цена выкупа за одну штуку (для сортировки «самые выгодные»)."""
    try:
        amount = max(1, int(lot.get("amount") or 1))
        buyout = lot.get("buyoutPrice")
        if buyout is None:
            return 10**18
        return float(buyout) / amount
    except (TypeError, ValueError):
        return 10**18


def _attr_display_name(definition_id: str) -> str:
    return ATTR_NAMES.get(definition_id, definition_id)


def _format_attr(a: dict) -> str:
    def_id = a.get("definitionId") or "?"
    name = _attr_display_name(def_id)
    q = a.get("quality")
    # Слот определяем по definitionId (_pre/_suf), а не по type из API —
    # type в API перепутан относительно игровых названий.
    slot = ATTR_SLOT_NAMES.get(_attr_slot(def_id), "Атрибут")
    qlt_s = QLT_NAMES.get(q, str(q)) if q is not None else ""
    if qlt_s:
        return f"  • <b>{slot}</b>: {name} · {qlt_s}"
    return f"  • <b>{slot}</b>: {name}"


def format_lot(lot: dict, item_id: str) -> str:
    amount = lot.get("amount") or 1
    try:
        amount = max(1, int(amount))
    except (TypeError, ValueError):
        amount = 1
    buyout = lot.get("buyoutPrice")
    current = lot.get("currentPrice")
    end = lot.get("endTime")
    add = lot.get("additional") or {}
    name = items_db.get_name(item_id)
    lines = [
        f"📦 <b>{name}</b> × {amount}",
        f"<code>{item_id}</code>",
        f"💰 Выкуп: <b>{format_price(buyout)}</b>",
    ]
    if amount > 1 and buyout is not None:
        try:
            unit = int(buyout) / amount
            lines.append(f"🏷 За шт.: <b>{format_price(round(unit))}</b>")
        except (TypeError, ValueError):
            pass
    if current:
        lines.append(f"📈 Ставка: {format_price(current)}")
    if "qlt" in add:
        lines.append(f"🎖 Редкость: {QLT_NAMES.get(add['qlt'], add['qlt'])}")
    if "ptn" in add:
        lines.append(f"⚔ Заточка: +{add['ptn']}")
    attrs = add.get("attributes") or []
    if attrs:
        # Порядок: Надстройка (1) → Отклонение (0) → Концепт (2)
        def _attr_sort_key(a: dict) -> int:
            did = a.get("definitionId") or ""
            slot = _attr_slot(did)
            return {1: 0, 0: 1, 2: 2}.get(slot, 3)

        lines.append("🔧 Атрибуты:")
        for a in sorted(attrs, key=_attr_sort_key):
            lines.append(_format_attr(a))
    if end:
        lines.append(f"⏱ До: {end}")
    return "\n".join(lines)


def lot_matches_filter(lot: dict, watch: dict) -> bool:
    add = lot.get("additional") or {}
    # Лоты без выкупа (только ставка) — не интересны
    buyout = lot.get("buyoutPrice")
    try:
        buyout_f = float(buyout) if buyout is not None else 0.0
    except (TypeError, ValueError):
        buyout_f = 0.0
    if buyout is None or buyout_f <= 0:
        return False
    if watch.get("ptn") is not None and add.get("ptn") != watch["ptn"]:
        return False
    if watch.get("max_price") is not None:
        # Цена за штуку: buyout / amount (пачка 5×10 при лимите 3 → 2 ≤ 3 — ок)
        try:
            amount = max(1, int(lot.get("amount") or 1))
        except (TypeError, ValueError):
            amount = 1
        unit_price = buyout_f / amount
        try:
            max_p = float(watch["max_price"])
        except (TypeError, ValueError):
            max_p = watch["max_price"]
        if unit_price > max_p:
            return False

    required = watch.get("attrs")  # list[str] | None
    want_qlt = watch.get("qlt")  # fallback (старые слежения / «любые» attrs)
    attr_qlts: dict = watch.get("attr_qlts") or {}  # def_id → required quality | None

    if required:
        module_qlt = add.get("qlt")
        by_id: dict[str, list] = {}
        for a in add.get("attributes") or []:
            did = (a.get("definitionId") or "").lower()
            if did:
                by_id.setdefault(did, []).append(a)

        for need in required:
            key = need.lower()
            cands = by_id.get(key) or []
            if not cands:
                return False
            # приоритет: per-attr quality из attr_qlts → общий qlt слежения
            need_qlt = attr_qlts.get(key, attr_qlts.get(need))
            if need_qlt is None and key not in attr_qlts and need not in attr_qlts:
                need_qlt = want_qlt
            if need_qlt is not None:
                try:
                    need_i = int(need_qlt)
                except (TypeError, ValueError):
                    return False
                ok = False
                for a in cands:
                    aq = a.get("quality")
                    if aq is None:
                        continue
                    try:
                        aq_i = int(aq)
                    except (TypeError, ValueError):
                        continue
                    # точное совпадение редкости атрибута
                    if aq_i == need_i:
                        ok = True
                        break
                if not ok:
                    return False
        return True

    # Без фильтра по атрибутам — редкость всего предмета/модуля
    if want_qlt is not None and add.get("qlt") != want_qlt:
        return False
    return True


def format_watch_text(w: dict, num: int | None = None) -> str:
    qlt = QLT_NAMES.get(w.get("qlt"), "любая") if w.get("qlt") is not None else "любая"
    ptn = f"+{w['ptn']}" if w.get("ptn") is not None else "любая"
    price = (
        f"до {format_price(w['max_price'])} / шт."
        if w.get("max_price") is not None
        else "любая"
    )
    head = f"<b>#{num}</b> " if num is not None else ""
    name = w.get("name") or items_db.get_name(w["item_id"])
    lines = [
        f"{head}📦 <b>{name}</b>",
        f"<code>{w['item_id']}</code>",
    ]
    attrs = w.get("attrs")
    attr_qlts = w.get("attr_qlts") or {}
    if attrs:
        parts = []
        for a in attrs:
            an = _attr_display_name(a)
            aq = attr_qlts.get(a) if a in attr_qlts else attr_qlts.get(a.lower())
            if aq is None and a not in attr_qlts and a.lower() not in attr_qlts:
                aq = w.get("qlt")
            if aq is not None:
                parts.append(f"{an} · {QLT_NAMES.get(aq, aq)}")
            else:
                parts.append(f"{an} · любая")
        lines.append("🔧 " + ", ".join(parts))
        lines.append(f"💰 {price}")
    else:
        lines.append(f"🎖 {qlt} · ⚔ {ptn} · 💰 {price}")
    return "\n".join(lines)


# ── Клавиатуры ───────────────────────────────────────────────────────────────

def kb_rarity() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Любая", callback_data="qlt:any")],
        [
            InlineKeyboardButton(text="Обычный", callback_data="qlt:0"),
            InlineKeyboardButton(text="Необычный", callback_data="qlt:1"),
        ],
        [
            InlineKeyboardButton(text="Особый", callback_data="qlt:2"),
            InlineKeyboardButton(text="Редкий", callback_data="qlt:3"),
        ],
        [
            InlineKeyboardButton(text="Исключительный", callback_data="qlt:4"),
            InlineKeyboardButton(text="Легендарный", callback_data="qlt:5"),
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_ptn() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="Любая", callback_data="ptn:any")]]
    row: list = []
    for i in range(0, 16):
        row.append(InlineKeyboardButton(text=f"+{i}", callback_data=f"ptn:{i}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_price() -> InlineKeyboardMarkup:
    presets = [500_000, 1_000_000, 5_000_000, 10_000_000, 50_000_000]
    rows = [[InlineKeyboardButton(text="Любая", callback_data="price:any")]]
    row: list = []
    for p in presets:
        row.append(InlineKeyboardButton(text=format_price(p), callback_data=f"price:{p}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="✏️ Ввести свою", callback_data="price:custom")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Сохранить", callback_data="confirm:yes"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
            ]
        ]
    )


def kb_tracks(watches: list) -> InlineKeyboardMarkup:
    rows = []
    for i, w in enumerate(watches, 1):
        name = w.get("name") or items_db.get_name(w["item_id"])
        qlt = QLT_NAMES.get(w.get("qlt"), "любая") if w.get("qlt") is not None else "любая"
        label = f"#{i} {name} · {qlt}"
        if len(label) > 60:
            label = label[:57] + "…"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"track:{i}")])
    rows.append([InlineKeyboardButton(text="➕ Новое слежение", callback_data="new")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_track_actions(num: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{num}")],
            [InlineKeyboardButton(text="« Назад к списку", callback_data="tracks")],
        ]
    )


# ── Команды ──────────────────────────────────────────────────────────────────

@dp.message(Command("start", "help"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "👋 Бот мониторинга аукциона <b>STALCRAFT</b>\n\n"
        "<b>Команды:</b>\n"
        "/new — добавить слежение\n"
        "/tracks — мои слежения\n"
        "/check <code>название</code> — лоты сейчас\n"
        "/attrs — список definitionId модулей\n"
        "/help — справка\n"
        "/cancel — отменить ввод\n\n"
        f"Регион: <b>{REGION.upper()}</b>\n"
        f"База предметов: <b>{items_db.count}</b>\n\n"
        "Модули: item_id <code>1pyq</code> (Оружейный модуль).\n"
        "При слежении можно фильтровать по атрибутам.",
        parse_mode="HTML",
    )


@dp.message(Command("attrs"))
async def cmd_attrs(message: Message) -> None:
    lines = ["🔧 <b>Известные definitionId модулей</b>\n"]
    by_suffix: dict[str, list] = {"pre": [], "suf": [], "aff": [], "other": []}
    for did, name in sorted(ATTR_NAMES.items()):
        if did.endswith("_pre"):
            by_suffix["pre"].append(f"<code>{did}</code> — {name}")
        elif did.endswith("_suf"):
            by_suffix["suf"].append(f"<code>{did}</code> — {name}")
        elif did.endswith("_aff"):
            by_suffix["aff"].append(f"<code>{did}</code> — {name}")
        else:
            by_suffix["other"].append(f"<code>{did}</code> — {name}")
    if by_suffix["pre"]:
        lines.append("<b>Надстройка (pre):</b>")
        lines.extend(by_suffix["pre"])
        lines.append("")
    if by_suffix["suf"]:
        lines.append("<b>Отклонение (suf):</b>")
        lines.extend(by_suffix["suf"])
        lines.append("")
    if by_suffix["aff"]:
        lines.append("<b>Концепт (aff):</b>")
        lines.extend(by_suffix["aff"])
        lines.append("")
    if by_suffix["other"]:
        lines.append("<b>Концепт (особые):</b>")
        lines.extend(by_suffix["other"])
    lines.append(
        "\nПри создании слежения на модуль:\n"
        "1) указываешь атрибуты (русское имя или <code>definitionId</code>)\n"
        "2) по очереди выбираешь редкость для каждого слота:\n"
        "   Надстройка → Отклонение → Концепт (если они есть)\n"
        "Пример: <code>бдительный снайпер</code> → редкость надстройки, затем концепта\n"
        "Фильтр: лот должен содержать все указанные атрибуты нужной редкости."
    )
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("new"))
async def cmd_new(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(NewWatch.item_id)
    await message.answer(
        "➕ <b>Новое слежение</b>\n\n"
        "Напиши <b>название</b> предмета\n"
        "(например: <code>Атом</code>, <code>Гадюка</code>, "
        "<code>Оружейный модуль</code>)\n\n"
        "Можно и item_id (<code>1pyq</code> для модулей).",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]]
        ),
    )


@dp.message(Command("tracks", "list"))
async def cmd_tracks(message: Message, state: FSMContext) -> None:
    await state.clear()
    watches = storage.get_user_watches(message.from_user.id)
    if not watches:
        await message.answer(
            "Список пуст.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Новое слежение", callback_data="new")]
                ]
            ),
        )
        return
    text = "📋 <b>Твои слежения:</b>\n\n" + "\n\n".join(
        format_watch_text(w, i) for i, w in enumerate(watches, 1)
    )
    await message.answer(text, parse_mode="HTML", reply_markup=kb_tracks(watches))


@dp.message(Command("check"))
async def cmd_check(message: Message, state: FSMContext, command: CommandObject) -> None:
    if not command.args:
        await message.answer(
            "Укажи название или id:\n<code>/check Атом</code>\n\n"
            "Можно добавить фильтры:\n"
            "<code>/check Атом 3</code> — редкость (0-5)\n"
            "<code>/check Атом +3</code> — заточка (+1...+15)",
            parse_mode="HTML",
        )
        return

    args = command.args.strip().split()
    query = args[0]
    
    # Парсинг фильтров
    rarity_filter = None
    ptn_filter = None
    
    for arg in args[1:]:
        # Редкость: число 0-5
        if arg.isdigit() and 0 <= int(arg) <= 5:
            rarity_filter = int(arg)
        # Заточка: +число
        elif arg.startswith('+') and arg[1:].isdigit():
            ptn_val = int(arg[1:])
            if 0 <= ptn_val <= 15:
                ptn_filter = ptn_val
    
    results = items_db.search(query, limit=5)

    if not results:
        item_id = query.split()[0].lower()
        name = item_id
    elif len(results) == 1:
        item_id = results[0]["id"]
        name = results[0]["name"]
    else:
        rows = [
            [
                InlineKeyboardButton(
                    text=r["name"][:60],
                    callback_data=f"checkpick:{r['id']}",
                )
            ]
            for r in results
        ]
        # Сохраняем фильтры для последующего использования
        await state.update_data(rarity=rarity_filter, ptn=ptn_filter, query=query)
        await message.answer(
            f"Найдено несколько по «{query}». Выбери:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return

    await message.answer(f"🔍 Смотрю <b>{name}</b>…", parse_mode="HTML")
    try:
        session = get_session()
        # Пагинация только по time_created — buyout_price даёт дубли в eapi
        if item_id.lower() == MODULE_ITEM_ID:
            data = await fetch_all_lots(
                session, item_id, max_lots=2000, sort="time_created", order="asc"
            )
        else:
            data = await fetch_all_lots(
                session, item_id, max_lots=5000, sort="time_created", order="asc"
            )
        raw_lots = data.get("lots") or []
        total = data.get("total", len(raw_lots))
        scanned = len(raw_lots)
        # Без выкупа (только ставка) — не показываем
        lots = [
            L for L in raw_lots
            if L.get("buyoutPrice") is not None and L.get("buyoutPrice") != 0
        ]
        
        # Применяем фильтры
        if rarity_filter is not None:
            lots = [L for L in lots if (L.get("additional") or {}).get("qlt") == rarity_filter]
        if ptn_filter is not None:
            lots = [L for L in lots if (L.get("additional") or {}).get("ptn") == ptn_filter]
        
        if not lots:
            filter_desc = []
            if rarity_filter is not None:
                filter_desc.append(f"редкость={rarity_filter}")
            if ptn_filter is not None:
                filter_desc.append(f"заточка=+{ptn_filter}")
            filter_str = " (" + ", ".join(filter_desc) + ")" if filter_desc else ""
            await message.answer(
                f"Лотов по <b>{name}</b>{filter_str} с выкупом нет "
                f"(скачано {scanned} / ~{total}).",
                parse_mode="HTML",
            )
            return
        # Сортировка по цене за шт. уже на нашей стороне
        lots = sorted(lots, key=unit_price)
        show_n = 15 if item_id.lower() == MODULE_ITEM_ID else 10
        filter_desc = []
        if rarity_filter is not None:
            filter_desc.append(f"редкость={rarity_filter}")
        if ptn_filter is not None:
            filter_desc.append(f"заточка=+{ptn_filter}")
        filter_str = " (" + ", ".join(filter_desc) + ")" if filter_desc else ""
        parts = [
            f"📦 <b>{name}</b>{filter_str} — на ауке ~{total}, скачано {scanned}, "
            f"с выкупом {len(lots)}, показано {min(show_n, len(lots))} "
            f"(по цене за шт.)\n"
        ]
        for lot in lots[:show_n]:
            parts.append(format_lot(lot, item_id))
            parts.append("────────")
        text = "\n".join(parts)
        # Telegram limit ~4096
        if len(text) > 4000:
            chunk = parts[0] + "\n"
            for p in parts[1:]:
                if len(chunk) + len(p) + 1 > 4000:
                    await message.answer(chunk, parse_mode="HTML")
                    chunk = p + "\n"
                else:
                    chunk += p + "\n"
            if chunk.strip():
                await message.answer(chunk, parse_mode="HTML")
        else:
            await message.answer(text, parse_mode="HTML")
    except Exception as e:
        logger.exception("check failed")
        await message.answer(f"Ошибка: <code>{e}</code>", parse_mode="HTML")


@dp.callback_query(F.data.startswith("checkpick:"))
async def cb_checkpick(call: CallbackQuery, state: FSMContext) -> None:
    try:
        item_id = call.data.split(":", 1)[1]
        name = items_db.get_name(item_id)
        
        # Сохраняем предмет в состоянии и переходим к выбору редкости
        await state.update_data(item_id=item_id, item_name=name)
        await state.set_state(CheckFilter.rarity)
        
        # Формируем клавиатуру с редкостями (0-5 + "Любая")
        rows = [
            [
                InlineKeyboardButton(text=f"{QLT_NAMES[q]} ({q})", callback_data=f"check_rarity:{q}")
                for q in range(6)
            ],
            [InlineKeyboardButton(text="Любая", callback_data="check_rarity:any")],
        ]
        
        await call.message.edit_text(
            f"<b>{name}</b>\nВыберите редкость:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception as e:
        logger.exception("checkpick failed")
        try:
            await call.message.edit_text(f"Ошибка: <code>{e}</code>", parse_mode="HTML")
        except Exception:
            await call.message.answer(f"Ошибка: <code>{e}</code>", parse_mode="HTML")


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


# ── FSM: ввод названия / id ──────────────────────────────────────────────────

@dp.message(StateFilter(NewWatch.item_id))
async def on_item_id(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Напиши название или item_id:")
        return

    results = items_db.search(text, limit=12)

    if not results:
        await message.answer(
            f"Ничего не найдено по «{text}».\n"
            "Попробуй другое название или item_id.\n"
            "Или /cancel"
        )
        return

    if len(results) == 1:
        item = results[0]
        await state.update_data(item_id=item["id"], name=item["name"])
        if item["id"].lower() == MODULE_ITEM_ID:
            await state.set_state(NewWatch.attrs)
            await message.answer(
                f"📦 <b>{item['name']}</b>\n<code>{item['id']}</code>\n\n"
                "🔧 <b>Атрибуты модуля</b>\n"
                "Напиши нужные атрибуты через пробел или запятую.\n"
                "Можно <b>русское название</b> или <code>definitionId</code>:\n"
                "• <code>снайпер</code>\n"
                "• <code>снайпер плавный</code>\n"
                "• <code>marksman recoil_gain_suf</code>\n\n"
                "Фильтр: лот должен содержать <b>все</b> указанные атрибуты.\n"
                "Список: /attrs",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Любые", callback_data="attrs:any")],
                        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
                    ]
                ),
            )
        else:
            await state.set_state(NewWatch.rarity)
            await message.answer(
                f"📦 <b>{item['name']}</b>\n<code>{item['id']}</code>\n\n"
                "Выбери <b>редкость</b>:",
                parse_mode="HTML",
                reply_markup=kb_rarity(),
            )
        return

    rows = []
    for item in results:
        label = item["name"]
        if len(label) > 60:
            label = label[:57] + "…"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"pick:{item['id']}")]
        )
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])

    await message.answer(
        f"Найдено несколько по «{text}».\nВыбери нужный:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ── Callback-и ───────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("Отменено.")
    await call.answer()


@dp.callback_query(F.data == "new")
async def cb_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(NewWatch.item_id)
    await call.message.answer(
        "➕ <b>Новое слежение</b>\n\n"
        "Напиши <b>название</b> предмета\n"
        "(например: <code>Атом</code>, <code>Оружейный модуль</code>):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]]
        ),
    )
    await call.answer()


@dp.callback_query(F.data == "tracks")
async def cb_tracks(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    watches = storage.get_user_watches(call.from_user.id)
    if not watches:
        await call.message.edit_text(
            "Список пуст.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Новое слежение", callback_data="new")]
                ]
            ),
        )
        await call.answer()
        return
    text = "📋 <b>Твои слежения:</b>\n\n" + "\n\n".join(
        format_watch_text(w, i) for i, w in enumerate(watches, 1)
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb_tracks(watches))
    await call.answer()


@dp.callback_query(F.data.startswith("track:"))
async def cb_track(call: CallbackQuery) -> None:
    num = int(call.data.split(":")[1])
    watches = storage.get_user_watches(call.from_user.id)
    if num < 1 or num > len(watches):
        await call.answer("Не найдено", show_alert=True)
        return
    w = watches[num - 1]
    await call.message.edit_text(
        format_watch_text(w, num),
        parse_mode="HTML",
        reply_markup=kb_track_actions(num),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("del:"))
async def cb_del(call: CallbackQuery) -> None:
    num = int(call.data.split(":")[1])
    ok, text = await storage.remove_watch(call.from_user.id, num)
    await call.message.edit_text(text, parse_mode="HTML")
    await call.answer("Удалено" if ok else "Ошибка")


@dp.callback_query(F.data.startswith("pick:"))
async def cb_pick(call: CallbackQuery, state: FSMContext) -> None:
    item_id = call.data.split(":", 1)[1]
    name = items_db.get_name(item_id)
    await state.update_data(item_id=item_id, name=name)
    if item_id.lower() == MODULE_ITEM_ID:
        await state.set_state(NewWatch.attrs)
        await call.message.edit_text(
            f"📦 <b>{name}</b>\n<code>{item_id}</code>\n\n"
            "🔧 <b>Атрибуты модуля</b>\n"
            "Напиши нужные атрибуты через пробел или запятую.\n"
            "Можно <b>русское название</b> или <code>definitionId</code>:\n"
            "• <code>снайпер</code>\n"
            "• <code>снайпер плавный</code>\n"
            "• <code>marksman recoil_gain_suf</code>\n\n"
            "Фильтр: лот должен содержать <b>все</b> указанные атрибуты.\n"
            "Список: /attrs",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Любые", callback_data="attrs:any")],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
                ]
            ),
        )
    else:
        await state.set_state(NewWatch.rarity)
        await call.message.edit_text(
            f"📦 <b>{name}</b>\n<code>{item_id}</code>\n\nВыбери <b>редкость</b>:",
            parse_mode="HTML",
            reply_markup=kb_rarity(),
        )
    await call.answer()


@dp.callback_query(StateFilter(NewWatch.rarity), F.data.startswith("qlt:"))
async def cb_qlt(call: CallbackQuery, state: FSMContext) -> None:
    val = call.data.split(":")[1]
    qlt = None if val == "any" else int(val)
    await state.update_data(qlt=qlt)
    data = await state.get_data()
    qlt_s = "любая" if qlt is None else QLT_NAMES.get(qlt, str(qlt))
    item_id = (data.get("item_id") or "").lower()
    attrs = data.get("attrs")

    if item_id == MODULE_ITEM_ID:
        # Модуль: после атрибутов выбрали редкость → сразу цена
        await state.set_state(NewWatch.price)
        if attrs:
            nice = ", ".join(_attr_display_name(a) for a in attrs)
            await call.message.edit_text(
                f"🔧 Атрибуты: <b>{nice}</b>\n"
                f"🎖 Редкость атрибутов: <b>{qlt_s}</b>\n\n"
                "Выбери <b>макс. цену за штуку</b>\n"
                "(для пачек считается выкуп ÷ количество):",
                parse_mode="HTML",
                reply_markup=kb_price(),
            )
        else:
            await call.message.edit_text(
                f"🔧 Атрибуты: <b>любые</b>\n"
                f"🎖 Редкость модуля: <b>{qlt_s}</b>\n\n"
                "Выбери <b>макс. цену за штуку</b>\n"
                "(для пачек считается выкуп ÷ количество):",
                parse_mode="HTML",
                reply_markup=kb_price(),
            )
    else:
        await state.set_state(NewWatch.ptn)
        await call.message.edit_text(
            f"🎖 Редкость: <b>{qlt_s}</b>\n\nВыбери <b>заточку</b>:",
            parse_mode="HTML",
            reply_markup=kb_ptn(),
        )
    await call.answer()


# Обработчики для команды /check
@dp.callback_query(StateFilter(CheckFilter.rarity), F.data.startswith("check_rarity:"))
async def cb_check_rarity(call: CallbackQuery, state: FSMContext) -> None:
    val = call.data.split(":")[1]
    rarity = None if val == "any" else int(val)
    await state.update_data(rarity=rarity)
    
    data = await state.get_data()
    item_id = data.get("item_id")
    item_name = data.get("item_name")
    rarity_s = "любая" if rarity is None else QLT_NAMES.get(rarity, str(rarity))
    
    # Проверяем, нужен ли выбор заточки (для артефактов и оружия)
    # Простая эвристика: если предмет не модуль и не расходник, предлагаем заточку
    # Для точности можно добавить проверку в items_db
    await state.set_state(CheckFilter.ptn)
    
    rows = [[InlineKeyboardButton(text="Любая", callback_data="check_ptn:any")]]
    row = []
    for i in range(0, 16):
        row.append(InlineKeyboardButton(text=f"+{i}", callback_data=f"check_ptn:{i}"))
        if len(row) == 8:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    
    await call.message.edit_text(
        f"🎖 Редкость: <b>{rarity_s}</b>\n\nВыбери <b>заточку</b>:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await call.answer()


@dp.callback_query(StateFilter(CheckFilter.ptn), F.data.startswith("check_ptn:"))
async def cb_check_ptn(call: CallbackQuery, state: FSMContext) -> None:
    val = call.data.split(":")[1]
    ptn = None if val == "any" else int(val)
    await state.update_data(ptn=ptn)
    
    data = await state.get_data()
    item_id = data.get("item_id")
    item_name = data.get("item_name")
    rarity = data.get("rarity")
    ptn_s = "любая" if ptn is None else f"+{ptn}"
    
    await state.clear()
    
    # Теперь выполняем поиск с фильтрами
    try:
        await call.message.edit_text(f"🔍 Смотрю <b>{item_name}</b>…", parse_mode="HTML")
        session = get_session()
        if item_id.lower() == MODULE_ITEM_ID:
            fetch_data = await fetch_all_lots(
                session, item_id, max_lots=2000, sort="time_created", order="asc"
            )
        else:
            fetch_data = await fetch_all_lots(
                session, item_id, max_lots=5000, sort="time_created", order="asc"
            )
        raw_lots = fetch_data.get("lots") or []
        total = fetch_data.get("total", len(raw_lots))
        scanned = len(raw_lots)
        lots = [
            L for L in raw_lots
            if L.get("buyoutPrice") is not None and L.get("buyoutPrice") != 0
        ]
        
        # Применяем фильтры
        if rarity is not None:
            lots = [L for L in lots if (L.get("additional") or {}).get("qlt") == rarity]
        if ptn is not None:
            lots = [L for L in lots if (L.get("additional") or {}).get("ptn") == ptn]
        
        if not lots:
            filter_desc = []
            if rarity is not None:
                filter_desc.append(f"редкость={rarity}")
            if ptn is not None:
                filter_desc.append(f"заточка=+{ptn}")
            filter_str = " (" + ", ".join(filter_desc) + ")" if filter_desc else ""
            await call.message.edit_text(
                f"Лотов по <b>{item_name}</b>{filter_str} с выкупом нет "
                f"(скачано {scanned} / ~{total}).",
                parse_mode="HTML",
            )
            return
        lots = sorted(lots, key=unit_price)
        show_n = 15 if item_id.lower() == MODULE_ITEM_ID else 10
        filter_desc = []
        if rarity is not None:
            filter_desc.append(f"редкость={rarity}")
        if ptn is not None:
            filter_desc.append(f"заточка=+{ptn}")
        filter_str = " (" + ", ".join(filter_desc) + ")" if filter_desc else ""
        parts = [
            f"📦 <b>{item_name}</b>{filter_str} — на ауке ~{total}, скачано {scanned}, "
            f"с выкупом {len(lots)}, показано {min(show_n, len(lots))} "
            f"(по цене за шт.)\n"
        ]
        for lot in lots[:show_n]:
            parts.append(format_lot(lot, item_id))
            parts.append("────────")
        text = "\n".join(parts)
        if len(text) > 4000:
            await call.message.edit_text(
                f"📦 <b>{item_name}</b>{filter_str} — на ауке ~{total}, скачано {scanned}. "
                f"Результат ниже:",
                parse_mode="HTML",
            )
            chunk = ""
            for p in parts[1:]:
                if len(chunk) + len(p) + 1 > 4000:
                    if chunk.strip():
                        await call.message.answer(chunk, parse_mode="HTML")
                    chunk = p + "\n"
                else:
                    chunk += p + "\n"
            if chunk.strip():
                await call.message.answer(chunk, parse_mode="HTML")
        else:
            await call.message.edit_text(text, parse_mode="HTML")
    except Exception as e:
        logger.exception("check failed")
        await call.message.edit_text(f"Ошибка: <code>{e}</code>", parse_mode="HTML")


@dp.callback_query(StateFilter(NewWatch.ptn), F.data.startswith("ptn:"))
async def cb_ptn(call: CallbackQuery, state: FSMContext) -> None:
    val = call.data.split(":")[1]
    ptn = None if val == "any" else int(val)
    await state.update_data(ptn=ptn, attrs=None)
    await state.set_state(NewWatch.price)
    ptn_s = "любая" if ptn is None else f"+{ptn}"
    await call.message.edit_text(
        f"⚔ Заточка: <b>{ptn_s}</b>\n\n"
        "Выбери <b>макс. цену за штуку</b>\n"
        "(для пачек считается выкуп ÷ количество):",
        parse_mode="HTML",
        reply_markup=kb_price(),
    )
    await call.answer()


@dp.callback_query(StateFilter(NewWatch.attrs), F.data == "attrs:any")
async def cb_attrs_any(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(attrs=None, ptn=None, slot_qlts={}, pending_slots=[])
    await state.set_state(NewWatch.rarity)
    await call.message.edit_text(
        "🔧 Атрибуты: <b>любые</b>\n\n"
        "Выбери <b>редкость модуля</b>:",
        parse_mode="HTML",
        reply_markup=kb_rarity(),
    )
    await call.answer()


@dp.message(StateFilter(NewWatch.attrs))
async def on_attrs(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer(
            "Напиши атрибуты (русское имя или id) через пробел/запятую.\n"
            "Пример: <code>снайпер</code> или <code>снайпер плавный</code>",
            parse_mode="HTML",
        )
        return

    tokens = [p.strip() for p in text.replace(",", " ").split() if p.strip()]
    resolved: list[str] = []
    unknown: list[str] = []
    for tok in tokens:
        did = resolve_attr_token(tok)
        if did:
            if did not in resolved:
                resolved.append(did)
        else:
            unknown.append(tok)

    if unknown:
        await message.answer(
            "Не распознал: <b>" + ", ".join(unknown) + "</b>\n\n"
            "Примеры: <code>компрессор</code>, <code>охотник</code>, "
            "<code>плавный</code>, <code>inside</code>\n"
            "Полный список: /attrs\n"
            "Или /cancel",
            parse_mode="HTML",
        )
        return

    if not resolved:
        await message.answer("Нечего сохранять. Напиши хотя бы один атрибут или «Любые».")
        return

    # Слоты, которые нужно спросить (в порядке Надстройка → Отклонение → Концепт)
    present = sorted(
        {_attr_slot(d) for d in resolved},
        key=lambda t: _SLOT_ORDER.index(t) if t in _SLOT_ORDER else 99,
    )
    await state.update_data(
        attrs=resolved, ptn=None, slot_qlts={}, pending_slots=present
    )
    await state.set_state(NewWatch.attr_rarity)
    await _ask_next_slot_rarity(message, state, edit=False)


async def _ask_next_slot_rarity(
    target, state: FSMContext, *, edit: bool = False
) -> None:
    """Показывает запрос редкости для следующего слота из pending_slots.
    target — Message. edit=True только если это сообщение бота (из callback).
    """
    data = await state.get_data()
    pending: list = list(data.get("pending_slots") or [])
    attrs: list = data.get("attrs") or []

    if not pending:
        # всё собрано → цена
        await state.set_state(NewWatch.price)
        parts = []
        slot_qlts = _normalize_slot_qlts(data.get("slot_qlts"))
        for did in attrs:
            slot = _attr_slot(did)
            q = slot_qlts.get(slot)
            q_s = "любая" if q is None else QLT_NAMES.get(q, str(q))
            parts.append(f"{_attr_display_name(did)} · {q_s}")
        text = (
            "🔧 " + ", ".join(parts) + "\n\n"
            "Выбери <b>макс. цену за штуку</b>\n"
            "(для пачек считается выкуп ÷ количество):"
        )
        kb = kb_price()
        if edit:
            await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await target.answer(text, parse_mode="HTML", reply_markup=kb)
        return

    slot = int(pending[0])
    slot_name = ATTR_SLOT_NAMES.get(slot, "Атрибут")
    names_in_slot = [
        _attr_display_name(d) for d in attrs if _attr_slot(d) == slot
    ]
    names_s = ", ".join(names_in_slot)
    text = (
        f"🎖 Редкость: <b>{slot_name}</b>\n"
        f"({names_s})\n\n"
        "Выбери редкость:"
    )
    kb = kb_rarity()
    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.callback_query(StateFilter(NewWatch.attr_rarity), F.data.startswith("qlt:"))
async def cb_attr_rarity(call: CallbackQuery, state: FSMContext) -> None:
    val = call.data.split(":")[1]
    qlt = None if val == "any" else int(val)
    data = await state.get_data()
    pending: list = list(data.get("pending_slots") or [])
    # нормализуем ключи (FSM мог сохранить "2" вместо 2)
    slot_qlts = _normalize_slot_qlts(data.get("slot_qlts"))
    pending = [int(x) for x in pending]

    if not pending:
        await call.answer()
        return

    slot = int(pending.pop(0))
    slot_qlts[slot] = qlt
    await state.update_data(pending_slots=pending, slot_qlts=slot_qlts)
    await call.answer()
    await _ask_next_slot_rarity(call.message, state, edit=True)


def _normalize_slot_qlts(raw: dict | None) -> dict[int, int | None]:
    """FSM/JSON часто превращает ключи int → str. Приводим обратно к int."""
    out: dict[int, int | None] = {}
    if not raw:
        return out
    for k, v in raw.items():
        try:
            slot = int(k)
        except (TypeError, ValueError):
            continue
        if v is None:
            out[slot] = None
        else:
            try:
                out[slot] = int(v)
            except (TypeError, ValueError):
                out[slot] = v
    return out


def _watch_from_state(data: dict, max_price=None) -> dict:
    w = {
        "item_id": data["item_id"],
        "name": data.get("name") or items_db.get_name(data["item_id"]),
        "qlt": data.get("qlt"),
        "ptn": data.get("ptn"),
        "max_price": max_price if max_price is not None else data.get("max_price"),
    }
    attrs = data.get("attrs")
    if attrs:
        w["attrs"] = list(attrs)
        slot_qlts = _normalize_slot_qlts(data.get("slot_qlts"))
        # разворачиваем slot → qlt в def_id → qlt
        attr_qlts: dict[str, int | None] = {}
        for did in attrs:
            slot = _attr_slot(did)
            if slot in slot_qlts:
                attr_qlts[did] = slot_qlts[slot]
        # Всегда пишем attr_qlts (даже с None = «любая»), чтобы было явно
        w["attr_qlts"] = attr_qlts
    return w


@dp.callback_query(StateFilter(NewWatch.price), F.data.startswith("price:"))
async def cb_price(call: CallbackQuery, state: FSMContext) -> None:
    val = call.data.split(":")[1]
    if val == "custom":
        await call.message.edit_text(
            "Введи максимальную цену выкупа числом\n"
            "(например <code>2500000</code>):",
            parse_mode="HTML",
        )
        await call.answer()
        return

    max_price = None if val == "any" else int(val)
    await state.update_data(max_price=max_price)
    data = await state.get_data()
    await state.set_state(NewWatch.confirm)
    watch = _watch_from_state(data, max_price)
    await call.message.edit_text(
        "Проверь слежение:\n\n" + format_watch_text(watch) + "\n\nСохранить?",
        parse_mode="HTML",
        reply_markup=kb_confirm(),
    )
    await call.answer()


@dp.message(StateFilter(NewWatch.price))
async def on_custom_price(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().replace(" ", "").replace("_", "")
    try:
        max_price = int(text)
        if max_price < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введи целое число (или /cancel):")
        return

    await state.update_data(max_price=max_price)
    data = await state.get_data()
    await state.set_state(NewWatch.confirm)
    watch = _watch_from_state(data, max_price)
    await message.answer(
        "Проверь слежение:\n\n" + format_watch_text(watch) + "\n\nСохранить?",
        parse_mode="HTML",
        reply_markup=kb_confirm(),
    )


@dp.callback_query(StateFilter(NewWatch.confirm), F.data == "confirm:yes")
async def cb_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    watch = _watch_from_state(data)
    ok, text = await storage.add_watch(call.from_user.id, watch)
    await state.clear()
    await call.message.edit_text(text, parse_mode="HTML")
    await call.answer("Сохранено" if ok else "Ошибка")

    if not ok:
        return

    # Разовый снимок: до 5 самых дешёвых подходящих лотов уже на ауке
    uid = call.from_user.id
    item_id = watch["item_id"]
    try:
        await call.message.answer("🔍 Сканирую аукцион…")
        session = get_session()
        # Снимок: 200 самых дешёвых (чтобы сразу показать, что уже есть).
        # Новые лоты дальше ловит monitor по time_created.
        resp = await fetch_lots(
            session, item_id, limit=200, sort="buyout_price", order="asc"
        )
        lots = resp.get("lots") or []
        total_on_ah = resp.get("total", len(lots))

        # Диагностика для модулей: сколько лотов реально с attributes
        with_attrs = sum(
            1 for L in lots if (L.get("additional") or {}).get("attributes")
        )

        matched: list[dict] = []
        seen_keys: set[str] = set()
        # Счётчики по каждому требуемому attr (без учёта rarity)
        required = watch.get("attrs") or []
        attr_hit: dict[str, int] = {a: 0 for a in required}
        both_hit = 0

        for lot in lots:
            add = lot.get("additional") or {}
            by_id = {
                (a.get("definitionId") or "").lower()
                for a in (add.get("attributes") or [])
                if a.get("definitionId")
            }
            if required:
                hits = [a for a in required if a.lower() in by_id]
                for a in hits:
                    attr_hit[a] = attr_hit.get(a, 0) + 1
                if len(hits) == len(required):
                    both_hit += 1

            if not lot_matches_filter(lot, watch):
                continue
            k = lot_key(lot)
            if k in seen_keys:
                continue
            seen_keys.add(k)
            matched.append(lot)
        matched.sort(key=unit_price)

        seen_key = watch_seen_key(item_id, watch, uid)
        all_keys = list(seen_keys)
        if all_keys:
            await storage.mark_lots_seen(seen_key, all_keys)

        diag = ""
        if required:
            parts_d = [f"с attributes: {with_attrs}/{len(lots)}"]
            for a, n in attr_hit.items():
                parts_d.append(f"{_attr_display_name(a)}: {n}")
            if len(required) > 1:
                parts_d.append(f"все вместе: {both_hit}")
            diag = "\n🔎 " + " · ".join(parts_d)

        if not matched:
            await call.message.answer(
                f"Просмотрено лотов: <b>{len(lots)}</b> (всего на ауке ~{total_on_ah})."
                f"{diag}\n"
                "Подходящих сейчас нет.\n"
                "Буду уведомлять о новых.",
                parse_mode="HTML",
            )
            return

        show = matched[:5]
        header = (
            f"📋 Просмотрено: {len(lots)} / ~{total_on_ah}\n"
            f"Подходящих: <b>{len(matched)}</b> (показываю до 5 дешёвых):"
            f"{diag}\n"
        )
        parts = [header]
        for lot in show:
            parts.append(format_lot(lot, item_id))
            parts.append("────────")
        parts.append("Дальше — только <b>новые</b> лоты.")
        text = "\n".join(parts)
        if len(text) <= 4000:
            await call.message.answer(text, parse_mode="HTML")
        else:
            await call.message.answer(header, parse_mode="HTML")
            for lot in show:
                await call.message.answer(format_lot(lot, item_id), parse_mode="HTML")
            await call.message.answer("Дальше — только <b>новые</b> лоты.", parse_mode="HTML")
    except Exception as e:
        logger.warning("snapshot after add failed: %s", e)
        await call.message.answer(
            f"Слежение сохранено, но не удалось загрузить текущие лоты: <code>{e}</code>",
            parse_mode="HTML",
        )


# ── Мониторинг ───────────────────────────────────────────────────────────────

async def monitor_loop() -> None:
    logger.info("Monitor started, interval=%ss, region=%s", CHECK_INTERVAL, REGION)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                all_watches = storage.get_all_watches()
                if not all_watches:
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                by_item: dict = {}
                for uid, w in all_watches:
                    by_item.setdefault(w["item_id"], []).append((uid, w))

                for item_id, entries in by_item.items():
                    try:
                        # Слежение: ОДИН запрос, без глубокой пагинации
                        data = await fetch_lots(
                            session, item_id, limit=200, sort="time_created", order="desc"
                        )
                        logger.debug("[monitor] %s: %s лотов", item_id, len(data.get("lots") or []))
                        lots = data.get("lots") or []
                        if not lots:
                            continue

                        for uid, watch in entries:
                            seen_key = watch_seen_key(item_id, watch, uid)
                            seen = storage.get_seen_lots(seen_key)
                            new_lots: list[dict] = []
                            new_keys: list[str] = []
                            pending: set[str] = set()  # защита от дублей внутри одного прохода

                            for lot in lots:
                                if not lot_matches_filter(lot, watch):
                                    continue
                                key = lot_key(lot)
                                new_keys.append(key)
                                if key not in seen and key not in pending:
                                    pending.add(key)
                                    new_lots.append(lot)

                            if new_keys:
                                await storage.mark_lots_seen(seen_key, new_keys)

                            if new_lots:
                                logger.info(
                                    "monitor %s uid=%s: %s новых лотов",
                                    item_id, uid, len(new_lots),
                                )
                            for lot in new_lots:
                                text = (
                                    "🆕 <b>Новый лот по фильтру!</b>\n\n"
                                    + format_lot(lot, item_id)
                                )
                                try:
                                    await bot.send_message(uid, text, parse_mode="HTML")
                                except Exception as e:
                                    logger.warning("send fail %s: %s", uid, e)

                        await asyncio.sleep(1.5)
                    except Exception as e:
                        logger.error("check %s: %s", item_id, e)
                        await asyncio.sleep(2)
            except Exception as e:
                logger.exception("monitor: %s", e)
            await asyncio.sleep(CHECK_INTERVAL)


async def main() -> None:
    await storage.load()
    logger.info("Storage loaded")

    try:
        await items_db.load()
        logger.info("Items DB: %s items", items_db.count)
    except Exception as e:
        logger.error("Items DB load failed: %s", e)

    await bot.set_my_commands(
        [
            BotCommand(command="new", description="Добавить слежение"),
            BotCommand(command="tracks", description="Мои слежения"),
            BotCommand(command="check", description="Проверить лоты"),
            BotCommand(command="attrs", description="Атрибуты модулей"),
            BotCommand(command="help", description="Справка"),
            BotCommand(command="cancel", description="Отменить ввод"),
        ]
    )

    all_watches = storage.get_all_watches()
    if all_watches:
        async with aiohttp.ClientSession() as session:
            seen_items = set()
            for uid, w in all_watches:
                item_id = w["item_id"]
                if item_id in seen_items:
                    continue
                seen_items.add(item_id)
                try:
                    data = await fetch_lots(
                        session, item_id, limit=200, sort="time_created", order="desc"
                    )
                    lots = data.get("lots") or []
                    for uid2, watch in all_watches:
                        if watch["item_id"] != item_id:
                            continue
                        seen_key = watch_seen_key(item_id, watch, uid2)
                        keys = [
                            lot_key(lot)
                            for lot in lots
                            if lot_matches_filter(lot, watch)
                        ]
                        if keys:
                            await storage.mark_lots_seen(seen_key, keys)
                    await asyncio.sleep(0.8)
                except Exception as e:
                    logger.warning("Initial scan %s: %s", item_id, e)

    monitor_task = asyncio.create_task(monitor_loop())
    try:
        logger.info("Bot starting…")
        await dp.start_polling(bot)
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
