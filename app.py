API_VERSION = "6.0.0"
API_NAME = "TANMAY NEWS API"

ADMIN_USER = "tanmay"
ADMIN_PASSWORD = "2015"
ADMIN_KEY = "2015"

NEWSDATA_API_KEY = "pub_e66626daeea34446b167db101e31b19e"
NEWS_URL = "https://newsdata.io/api/1/latest"

import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

BASE = Path(__file__).resolve().parent
TMP = Path("/tmp")
DB = (TMP / "tanmay_news_api.db") if TMP.exists() else (BASE / "tanmay_news_api.db")

app = FastAPI(
    title=API_NAME,
    version=API_VERSION,
    description="Real-time TANMAY NEWS API with Telegram bridge.",
)

STARTED_AT = time.time()
REQUESTS_SEEN = 0


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def db():
    c = sqlite3.connect(str(DB), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    try:
        c = db()
        c.execute(
            """CREATE TABLE IF NOT EXISTS requests(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT,
                country TEXT,
                created_at TEXT
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS bot_outbox(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                sent_at TEXT
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS bot_inbox(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                user_id TEXT,
                username TEXT,
                message TEXT NOT NULL,
                payload TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        c.commit()
        c.close()
        return True
    except sqlite3.Error:
        return False


DB_READY = init_db()


def inc(endpoint: str = "", country: Optional[str] = None):
    global REQUESTS_SEEN
    REQUESTS_SEEN += 1
    try:
        c = db()
        c.execute(
            "INSERT INTO requests(endpoint,country,created_at) VALUES(?,?,?)",
            (endpoint, country, now_iso()),
        )
        c.commit()
        c.close()
    except sqlite3.Error:
        pass


def admin_auth(user: Optional[str], password: Optional[str]) -> bool:
    return (
        bool(user)
        and bool(password)
        and secrets.compare_digest(str(user), ADMIN_USER)
        and secrets.compare_digest(str(password), ADMIN_PASSWORD)
    )


def key_auth(key: Optional[str]) -> bool:
    return bool(key) and secrets.compare_digest(str(key), ADMIN_KEY)


def provider_message(data):
    if isinstance(data, dict):
        if data.get("message"):
            return str(data["message"])
        result = data.get("results")
        if isinstance(result, dict) and result.get("message"):
            return str(result["message"])
    return "News provider returned an error."


async def fetch_news(
    q=None,
    country=None,
    language=None,
    category=None,
    region=None,
    page=None,
    size=5,
    image=None,
    video=None,
    removeduplicate=None,
    sort=None,
    excludefield=None,
):
    """Fetch a page from the upstream provider.

    `page` is an opaque pagination token. It must be reused exactly as returned
    by the previous response. If a stale/invalid token is supplied, retry the
    request once without it so the public TANMAY endpoint still works.
    """
    if not NEWSDATA_API_KEY or NEWSDATA_API_KEY.startswith("PASTE_"):
        return JSONResponse(
            status_code=503,
            content={"status": "error", "code": "PROVIDER_NOT_CONFIGURED",
                     "message": "News provider API key is not configured."},
        )

    params = {
        "apikey": NEWSDATA_API_KEY.strip(),
        "size": max(1, min(int(size), 50)),
        # TANMAY NEWS API defaults mirror the requested NewsData configuration.
        "country": "in,dz",
        "language": "hi",
        "category": "breaking,crime,technology,sports,science",
        "image": "1",
        "video": "1",
        "removeduplicate": "1",
        "sort": "source",
        "excludefield": "title,link,source_id,source_name,source_icon,source_url",
    }
    for name, value in (
        ("q", q), ("country", country), ("language", language),
        ("category", category), ("region", region),
        ("image", image), ("video", video),
        ("removeduplicate", removeduplicate), ("sort", sort),
        ("excludefield", excludefield),
    ):
        if value not in (None, ""):
            params[name] = value
    if page:
        params["page"] = page.strip()

    async def request_provider(request_params):
        timeout = httpx.Timeout(20.0, connect=8.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            return await client.get(
                NEWS_URL,
                params=request_params,
                headers={
                    "Accept": "application/json",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "User-Agent": "TANMAY-NEWS-API/6.0",
                },
            )

    try:
        r = await request_provider(params)
    except httpx.TimeoutException:
        return JSONResponse(status_code=504, content={
            "status": "error", "code": "NEWS_PROVIDER_TIMEOUT",
            "message": "News provider timed out.",
            "provider": "TANMAY NEWS API", "fetched_at": now_iso(),
        })
    except httpx.HTTPError as exc:
        return JSONResponse(status_code=502, content={
            "status": "error", "code": "NEWS_PROVIDER_REQUEST_FAILED",
            "message": "News provider request failed.",
            "detail": exc.__class__.__name__,
            "provider": "TANMAY NEWS API", "fetched_at": now_iso(),
        })

    try:
        data = r.json()
    except ValueError:
        return JSONResponse(status_code=502, content={
            "status": "error", "code": "NEWS_PROVIDER_INVALID_JSON",
            "message": "News provider returned invalid JSON.",
            "provider": "TANMAY NEWS API", "http_status": r.status_code,
            "fetched_at": now_iso(),
        })

    # NewsData uses an opaque `nextPage` token. A stale/modified token returns
    # HTTP 422. Retry the first page once instead of exposing that provider error.
    if r.status_code == 422 and page:
        retry_params = dict(params)
        retry_params.pop("page", None)
        try:
            r = await request_provider(retry_params)
            data = r.json()
        except (httpx.HTTPError, ValueError):
            return JSONResponse(status_code=502, content={
                "status": "error", "code": "NEWS_PROVIDER_PAGINATION_ERROR",
                "message": "The supplied pagination token was invalid and the provider retry failed.",
                "provider": "TANMAY NEWS API", "fetched_at": now_iso(),
            })

    if r.status_code >= 400 or data.get("status") == "error":
        code = "NEWS_PROVIDER_ERROR"
        if r.status_code in (401, 403):
            code = "NEWS_PROVIDER_UNAUTHORIZED"
        elif r.status_code == 422:
            code = "NEWS_PROVIDER_PAGINATION_ERROR"
        return JSONResponse(
            status_code=502 if r.status_code >= 500 or r.status_code in (401, 403) else r.status_code,
            content={
                "status": "error", "code": code,
                "message": provider_message(data),
                "provider": "TANMAY NEWS API",
                "http_status": r.status_code,
                "fetched_at": now_iso(),
            },
            headers={"Cache-Control": "no-store"},
        )

    articles = data.get("results", [])
    if not isinstance(articles, list):
        articles = []
    return JSONResponse(
        content={
            "status": data.get("status", "success"),
            "name": API_NAME,
            "totalResults": data.get("totalResults", len(articles)),
            "nextPage": data.get("nextPage"),
            "articles": articles,
            "results": articles,
            "provider": "TANMAY NEWS API",
            "mode": "real-time",
            "pagination": {
                "nextPage": data.get("nextPage"),
                "note": "Use the returned nextPage value exactly as the page parameter for the next request."
            },
            "fetched_at": now_iso(),
        },
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache", "Expires": "0",
        },
    )


USER_HTML = """<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TANMAY NEWS API</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:system-ui;background:linear-gradient(135deg,#0f172a,#312e81,#0e7490);color:#fff;min-height:100vh}
main{max-width:1050px;margin:auto;padding:24px}.hero,.box{background:rgba(255,255,255,.12);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.2);border-radius:22px;padding:20px;margin:14px 0;box-shadow:0 10px 35px rgba(0,0,0,.18)}
h1{margin:0 0 6px;font-size:34px}.muted{opacity:.8}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
input,select,button{width:100%;padding:13px;border:0;border-radius:12px;font:inherit}input,select{background:#fff;color:#111}button{background:linear-gradient(90deg,#f59e0b,#ec4899);color:#fff;font-weight:800;cursor:pointer}
article{background:rgba(255,255,255,.96);color:#111;border-radius:16px;padding:15px;margin:12px 0}article h3{margin:0 0 8px}article a{color:#4f46e5;font-weight:700}
</style></head><body><main>
<section class="hero"><h1>🚀 TANMAY NEWS API</h1><div class="muted">Real-time worldwide news • Search by country, category, language or PIN</div></section>
<section class="box"><div class="grid">
<input id="q" placeholder="🔎 Search news">
<input id="pin" placeholder="📍 PIN / Pincode">
<select id="c"><option value="">🌍 Worldwide</option><option value="in">🇮🇳 India</option><option value="us">🇺🇸 USA</option><option value="gb">🇬🇧 UK</option><option value="jp">🇯🇵 Japan</option><option value="au">🇦🇺 Australia</option><option value="ca">🇨🇦 Canada</option></select>
<select id="cat"><option value="">📰 All categories</option><option>technology</option><option>business</option><option>sports</option><option>science</option><option>health</option><option>entertainment</option></select>
<select id="lang"><option value="">All languages</option><option value="en">English</option><option value="hi">Hindi</option></select>
<button onclick="load()">✨ Get News</button></div></section>
<section id="out" class="box">Loading...</section></main>
<script>
const e=s=>String(s??'').replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));
async function load(){
 const p=new URLSearchParams(), vals={q:q.value.trim(),pin:pin.value.trim(),country:c.value,category:cat.value,language:lang.value};
 for(const [k,v] of Object.entries(vals))if(v)p.set(k,v);
 out.innerHTML='⏳ Loading real-time news…';
 try{const r=await fetch('/news?'+p,{cache:'no-store'}),d=await r.json();
 if(!r.ok||d.status==='error'){out.innerHTML='<pre>'+e(JSON.stringify(d,null,2))+'</pre>';return}
 out.innerHTML=(d.articles||[]).map(a=>`<article><h3>${e(a.title||'Untitled')}</h3><p>${e(a.description||'')}</p><small>${e(a.pubDate||'')}</small>${a.link?`<br><a target="_blank" rel="noopener" href="${e(a.link)}">Read source →</a>`:''}</article>`).join('')||'<h3>No news found.</h3>';
 }catch(err){out.textContent='❌ Request failed: '+err.message}
}
load();
</script></body></html>"""


ADMIN_HTML = """<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TANMAY NEWS API Admin</title>
<style>body{font-family:system-ui;max-width:700px;margin:auto;padding:20px}input,button{padding:11px;margin:4px;border:1px solid #ccc;border-radius:8px}pre{background:#f5f5f5;padding:15px;border-radius:10px;overflow:auto}</style>
</head><body><h1>TANMAY NEWS API</h1><h2>Admin Panel</h2>
<input id="u" placeholder="Username"><input id="p" type="password" placeholder="Password"><button onclick="login()">Login</button>
<pre id="out">Enter credentials.</pre>
<script>
async function login(){
 const r=await fetch('/api/admin/stats',{headers:{'X-Admin-User':u.value,'X-Admin-Password':p.value}});
 out.textContent=JSON.stringify(await r.json(),null,2);
}
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(USER_HTML)


@app.get("/user", response_class=HTMLResponse)
async def user():
    return HTMLResponse(USER_HTML)


@app.get("/admin", response_class=HTMLResponse)
async def admin():
    return HTMLResponse(ADMIN_HTML)


@app.get("/health")
@app.get("/api/health")
async def health():
    inc("/health")
    return {
        "status": "ok",
        "ok": True,
        "service": API_NAME,
        "version": API_VERSION,
        "provider": "TANMAY NEWS API",
        "database_ready": DB_READY,
        "uptime_seconds": round(time.time() - STARTED_AT, 2),
        "requests_seen": REQUESTS_SEEN,
        "time": now_iso(),
    }


@app.get("/api")
async def api_info():
    inc("/api")
    return {
        "name": API_NAME,
        "version": API_VERSION,
        "status": "online",
        "provider": "TANMAY NEWS API",
        "endpoints": [
            "/api/news",
            "/api/search",
            "/api/health",
            "/api/admin/stats",
            "/api/bot/send",
            "/api/bot/outbox",
            "/api/bot/ack",
            "/api/bot/inbox",
        ],
    }


@app.get("/news")
@app.get("/api/news")
async def news(
    q: Optional[str] = Query(None, max_length=200),
    pin: Optional[str] = Query(None, max_length=12, pattern=r"^[0-9A-Za-z -]{3,12}$"),
    country: Optional[str] = Query(None, max_length=20),
    language: Optional[str] = Query(None, max_length=20),
    category: Optional[str] = Query(None, max_length=50),
    region: Optional[str] = Query(None, max_length=100),
    page: Optional[str] = Query(None, max_length=1000),
    size: int = Query(5, ge=1, le=50),
    image: Optional[str] = Query(None, pattern=r"^(0|1)$"),
    video: Optional[str] = Query(None, pattern=r"^(0|1)$"),
    removeduplicate: Optional[str] = Query(None, pattern=r"^(0|1)$"),
    sort: Optional[str] = Query(None, max_length=30),
    excludefield: Optional[str] = Query(None, max_length=500),
):
    # `/news?pin=244231` is a convenience route. The pincode is searched
    # together with the requested query and defaults to India.
    if pin:
        pin_text = pin.strip()
        pin_query = f'"{pin_text}"'
        q = f"({q.strip()}) AND {pin_query}" if q and q.strip() else pin_query
        country = country or "in"
    inc("/api/news", country)
    return await fetch_news(
        q, country, language, category, region, page, size,
        image, video, removeduplicate, sort, excludefield
    )


@app.get("/search")
@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=1, max_length=200),
    pin: Optional[str] = Query(None, max_length=12, pattern=r"^[0-9A-Za-z -]{3,12}$"),
    country: Optional[str] = Query(None, max_length=20),
    language: Optional[str] = Query(None, max_length=20),
    category: Optional[str] = Query(None, max_length=50),
    page: Optional[str] = Query(None, max_length=1000),
    size: int = Query(10, ge=1, le=50),
):
    if pin:
        q = f"({q.strip()}) AND \"{pin.strip()}\""
        country = country or "in"
    inc("/api/search", country)
    return await fetch_news(q, country, language, category, None, page, size)


@app.get("/local-news")
@app.get("/api/local-news")
async def local_news(
    location: str = Query("Mandi Dhanaura", min_length=2, max_length=100),
    pincode: Optional[str] = Query(None, min_length=3, max_length=12),
    language: str = Query("en", max_length=10),
    size: int = Query(10, ge=1, le=50),
    page: Optional[str] = Query(None, max_length=1000),
):
    parts = [f'"{location.strip()}"']
    if pincode:
        parts.append(f'"{pincode.strip()}"')
    q = " OR ".join(parts)
    inc("/api/local-news", "in")
    return await fetch_news(
        q=q, country="in", language=language, region="Uttar Pradesh",
        page=page, size=size
    )


@app.get("/api/local-news/{pincode}")
async def local_news_by_pin(
    pincode: str, size: int = Query(10, ge=1, le=50),
    page: Optional[str] = Query(None, max_length=1000),
):
    return await local_news(
        location="Mandi Dhanaura", pincode=pincode, language="en",
        size=size, page=page
    )


@app.get("/admin/stats")
async def admin_stats(key: str = Query(..., min_length=1)):
    inc("/admin/stats")
    if not key_auth(key):
        raise HTTPException(status_code=401, detail="Invalid admin key")
    total = 0
    try:
        c = db()
        total = c.execute("SELECT COUNT(*) AS n FROM requests").fetchone()["n"]
        c.close()
    except sqlite3.Error:
        pass
    return {
        "ok": True,
        "name": API_NAME,
        "version": API_VERSION,
        "requests_seen": REQUESTS_SEEN,
        "database_requests": total,
    }


@app.get("/api/admin/stats")
async def api_admin_stats(
    x_admin_user: Optional[str] = Header(None),
    x_admin_password: Optional[str] = Header(None),
):
    inc("/api/admin/stats")
    if not admin_auth(x_admin_user, x_admin_password):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Invalid admin credentials"})
    return {
        "ok": True,
        "name": API_NAME,
        "version": API_VERSION,
        "requests_seen": REQUESTS_SEEN,
        "uptime_seconds": round(time.time() - STARTED_AT, 2),
        "database_ready": DB_READY,
    }


def bot_auth(key: Optional[str]) -> bool:
    return key_auth(key)


@app.post("/api/bot/send")
async def bot_send(request: Request, x_bot_key: Optional[str] = Header(None)):
    if not bot_auth(x_bot_key):
        raise HTTPException(401, "Invalid bot API key")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Request body must be valid JSON")
    chat_id = str(body.get("chat_id", "")).strip()
    if not chat_id:
        raise HTTPException(400, "chat_id is required")
    payload = body.get("payload")
    if payload is None:
        payload = {"text": str(body.get("text", ""))}
    now = now_iso()
    try:
        c = db()
        cur = c.execute(
            "INSERT INTO bot_outbox(chat_id,payload,status,created_at) VALUES(?,?,?,?)",
            (chat_id, json.dumps(payload, ensure_ascii=False), "pending", now),
        )
        c.commit()
        item_id = cur.lastrowid
        c.close()
    except sqlite3.Error as exc:
        raise HTTPException(503, f"Bot queue unavailable: {exc.__class__.__name__}")
    return {"ok": True, "id": item_id, "status": "pending"}


@app.get("/api/bot/outbox")
async def bot_outbox(
    limit: int = Query(20, ge=1, le=100),
    x_bot_key: Optional[str] = Header(None),
):
    if not bot_auth(x_bot_key):
        raise HTTPException(401, "Invalid bot API key")
    try:
        c = db()
        rows = c.execute(
            "SELECT id,chat_id,payload,created_at FROM bot_outbox WHERE status='pending' ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        c.close()
    except sqlite3.Error as exc:
        raise HTTPException(503, f"Bot queue unavailable: {exc.__class__.__name__}")
    items = []
    for r in rows:
        try:
            payload = json.loads(r["payload"])
        except Exception:
            payload = {"text": r["payload"]}
        items.append({"id": r["id"], "chat_id": r["chat_id"], "payload": payload, "created_at": r["created_at"]})
    return {"ok": True, "items": items}


@app.post("/api/bot/ack")
async def bot_ack(request: Request, x_bot_key: Optional[str] = Header(None)):
    if not bot_auth(x_bot_key):
        raise HTTPException(401, "Invalid bot API key")
    try:
        body = await request.json()
        item_id = int(body.get("id", 0))
    except Exception:
        raise HTTPException(400, "Invalid JSON or id")
    if item_id <= 0:
        raise HTTPException(400, "id is required")
    try:
        c = db()
        c.execute(
            "UPDATE bot_outbox SET status='sent', sent_at=? WHERE id=?",
            (now_iso(), item_id),
        )
        c.commit()
        c.close()
    except sqlite3.Error as exc:
        raise HTTPException(503, f"Bot queue unavailable: {exc.__class__.__name__}")
    return {"ok": True, "id": item_id, "status": "sent"}


@app.post("/api/bot/inbox")
async def bot_inbox(request: Request, x_bot_key: Optional[str] = Header(None)):
    if not bot_auth(x_bot_key):
        raise HTTPException(401, "Invalid bot API key")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Request body must be valid JSON")
    chat_id = str(body.get("chat_id", "")).strip()
    message = str(body.get("message", ""))
    if not chat_id or not message:
        raise HTTPException(400, "chat_id and message are required")
    try:
        c = db()
        cur = c.execute(
            """INSERT INTO bot_inbox(chat_id,user_id,username,message,payload,created_at)
               VALUES(?,?,?,?,?,?)""",
            (
                chat_id,
                str(body.get("user_id", "")),
                str(body.get("username", "")),
                message,
                json.dumps(body.get("payload"), ensure_ascii=False),
                now_iso(),
            ),
        )
        c.commit()
        item_id = cur.lastrowid
        c.close()
    except sqlite3.Error as exc:
        raise HTTPException(503, f"Bot inbox unavailable: {exc.__class__.__name__}")
    return {"ok": True, "id": item_id}


@app.get("/api/bot/inbox")
async def bot_inbox_list(
    limit: int = Query(50, ge=1, le=200),
    x_admin_key: Optional[str] = Header(None),
):
    if not key_auth(x_admin_key):
        raise HTTPException(401, "Invalid admin key")
    try:
        c = db()
        rows = c.execute(
            """SELECT id,chat_id,user_id,username,message,payload,created_at
               FROM bot_inbox ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        c.close()
    except sqlite3.Error as exc:
        raise HTTPException(503, f"Bot inbox unavailable: {exc.__class__.__name__}")
    return {
        "ok": True,
        "items": [
            {
                "id": r["id"],
                "chat_id": r["chat_id"],
                "user_id": r["user_id"],
                "username": r["username"],
                "message": r["message"],
                "payload": json.loads(r["payload"]) if r["payload"] else None,
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(status_code=204, content=None)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
