# TANMAY NEWS API — Upgraded

## Direct configuration
NEWSDATA_API_KEY = "pub_e66626daeea34446b167db101e31b19e"
ADMIN_USER = "tanmay"
ADMIN_PASSWORD = "2015"
ADMIN_KEY = "2015"
NEWS_URL = "https://newsdata.io/api/1/latest"

No `.env` and no `os.getenv()` / `os.environ.get()`.

## Panels
- User: `/user`
- Admin: `/admin`

## API
- `/api`
- `/api/health`
- `/api/news`
- `/api/search`
- `/api/admin/stats`

Admin API headers:
- `X-Admin-User: tanmay`
- `X-Admin-Password: 2015`

## Wispbyte
```bash
pip install -r requirements.txt
python app.py
```

Provider quotas/rate limits are controlled by NewsData.io and cannot be bypassed by application code.

## Two-way Bot ↔ API bridge

API → Bot:
`POST /api/bot/send` with header `X-Bot-Key: 2015` and JSON:
`{"chat_id":"123456","text":"Hello from API"}`

Bot reads:
`GET /api/bot/outbox` with `X-Bot-Key: 2015`

Bot confirms delivery:
`POST /api/bot/ack` with `{"id":123}`

Bot → API:
`POST /api/bot/inbox` with `X-Bot-Key: 2015`
The API saves incoming bot data in SQLite.

Admin can read saved bot data:
`GET /api/bot/inbox` with `X-Admin-Key: 2015`

Run the Telegram bridge separately with `python bot.py`.
Set `BOT_TOKEN` directly inside `bot.py` before starting it.
