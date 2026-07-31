# STALCRAFT Auction Monitor Bot

Telegram-бот для мониторинга аукциона игры STALCRAFT через официальный Stalzone API.

## Возможности

- Отслеживание конкретных предметов по `item_id`
- Уведомление в Telegram, когда появляется **новый** лот
- Разовый просмотр текущих лотов (`/check`)
- Хранение подписок в JSON (переживает перезапуск)

## Быстрый старт

### 1. Получить токен бота

Напиши [@BotFather](https://t.me/BotFather) → `/newbot` → скопируй токен.

### 2. Установить зависимости

```bash
cd stalcraft_auction_bot
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Настроить `.env`

```bash
cp .env.example .env
```

Заполни:

```
BOT_TOKEN=123456:ABC-DEF...
CLIENT_ID=твой_client_id
CLIENT_SECRET=твой_client_secret
REGION=ru
CHECK_INTERVAL=45
```

### 4. Запустить

```bash
python bot.py
```

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие |
| `/add zyv9` | Добавить предмет в отслеживание |
| `/remove zyv9` | Убрать из отслеживания |
| `/list` | Список отслеживаемых |
| `/check zyv9` | Посмотреть текущие лоты |
| `/region` | Текущий регион |
| `/help` | Справка |

## Где взять item_id

Официальная база предметов:
https://github.com/EXBO-Studio/stalcraft-database

Примеры:
- `zyv9`
- `0n9q`
- `y1q9`

## Важные замечания

- API в бете — endpoints могут меняться.
- Соблюдай rate-limit. Интервал 30–60 секунд обычно безопасен.
- При первом запуске бот запоминает уже существующие лоты и **не** шлёт по ним уведомления.
- Данные пользователей хранятся в `data/watches.json`.

## Структура

```
stalcraft_auction_bot/
├── bot.py            # основной код
├── config.py         # настройки из .env
├── storage.py        # хранилище подписок
├── requirements.txt
├── .env.example
└── README.md
```
