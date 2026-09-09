API_VERSION = "5.0.0"
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
    description="Real-time news API powered by NewsData.io with Telegram bridge.",
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
    return "NewsData.io returned an error."


async def fetch_news(
    q=None,
    country=None,
    language=None,
    category=None,
    page=None,
    size=10,
):
    if not NEWSDATA_API_KEY or NEWSDATA_API_KEY.startswith("PASTE_"):
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": "NewsData API key is not configured."},
        )

    params = {
        "apikey": NEWSDATA_API_KEY.strip(),
        "size": max(1, min(int(size), 50)),
    }
    for name, value in (
        ("q", q),
        ("country", country),
        ("language", language),
        ("category", category),
        ("page", page),
    ):
        if value not in (None, ""):
            params[name] = value

    try:
        timeout = httpx.Timeout(20.0, connect=8.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(
                NEWS_URL,
                params=params,
                headers={
                    "Accept": "application/json",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "User-Agent": "TANMAY-NEWS-API/5.0",
                },
            )
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={"status": "error", "message": "News provider timed out."},
        )
    except httpx.HTTPError as exc:
        return JSONResponse(
            status_code=502,
            content={
                "status": "error",
                "message": "News provider request failed.",
                "detail": exc.__class__.__name__,
            },
        )

    try:
        data = r.json()
    except ValueError:
        return JSONResponse(
            status_code=502,
            content={"status": "error", "message": "News provider returned invalid JSON."},
        )

    if r.status_code >= 400 or data.get("status") == "error":
        code = "NEWS_PROVIDER_ERROR"
        if r.status_code == 401:
            code = "NEWS_PROVIDER_UNAUTHORIZED"
        return JSONResponse(
            status_code=502 if r.status_code >= 500 or r.status_code == 401 else r.status_code,
            content={
                "status": "error",
                "code": code,
                "message": provider_message(data),
                "provider": "NewsData.io",
                "http_status": r.status_code,
                "fetched_at": now_iso(),
            },
            headers={"Cache-Control": "no-store"},
        )

    articles = data.get("results", [])
    return JSONResponse(
        content={
            "status": data.get("status", "success"),
            "totalResults": data.get("totalResults", len(articles) if isinstance(articles, list) else 0),
            "nextPage": data.get("nextPage"),
            "articles": articles if isinstance(articles, list) else [],
            "results": articles if isinstance(articles, list) else [],
            "provider": "NewsData.io",
            "mode": "real-time",
            "fetched_at": now_iso(),
        },
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


USER_HTML = """<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TANMAY NEWS API</title>
<style>body{font-family:system-ui;max-width:1000px;margin:auto;padding:20px}.row{display:flex;flex-wrap:wrap;gap:8px}input,button{padding:11px;border:1px solid #ccc;border-radius:8px}.card{border:1px solid #ddd;border-radius:12px;padding:14px;margin:10px 0}.muted{color:#666}</style>
</head><body><h1>TANMAY NEWS API</h1>
<p class="muted">Real-time NewsData.io API</p>
<div class="row">
<input id="q" placeholder="Search news">
<input id="country" placeholder="Country e.g. in">
<input id="language" placeholder="Language e.g. en">
<input id="category" placeholder="Category">
<button onclick="loadNews()">Get News</button>
</div><div id="status"></div><div id="out"></div>
<script>
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function loadNews(){
 const p=new URLSearchParams();
 for(const id of ['q','country','language','category']){const v=document.getElementById(id).value.trim();if(v)p.set(id,v);}
 document.getElementById('status').textContent='Loading...';
 try{
  const r=await fetch('/api/news?'+p.toString(),{cache:'no-store'});
  const d=await r.json();
  if(!r.ok || d.status==='error'){document.getElementById('out').innerHTML='<pre>'+esc(JSON.stringify(d,null,2))+'</pre>';return;}
  const a=d.articles||d.results||[];
  document.getElementById('out').innerHTML=a.map(x=>'<div class="card"><b>'+esc(x.title||'Untitled')+'</b><p>'+esc(x.description||'')+'</p></div>').join('')||'<p>No articles found.</p>';
 }catch(e){document.getElementById('out').innerHTML='<pre>'+esc(e.message)+'</pre>';}
 finally{document.getElementById('status').textContent='';}
}
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
        "provider": "NewsData.io",
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
        "provider": "NewsData.io",
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
    country: Optional[str] = Query(None, max_length=20),
    language: Optional[str] = Query(None, max_length=20),
    category: Optional[str] = Query(None, max_length=50),
    page: Optional[str] = Query(None, max_length=200),
    size: int = Query(10, ge=1, le=50),
):
    inc("/api/news", country)
    return await fetch_news(q, country, language, category, page, size)


@app.get("/search")
@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=1, max_length=200),
    country: Optional[str] = Query(None, max_length=20),
    language: Optional[str] = Query(None, max_length=20),
    category: Optional[str] = Query(None, max_length=50),
    page: Optional[str] = Query(None, max_length=200),
    size: int = Query(10, ge=1, le=50),
):
    inc("/api/search", country)
    return await fetch_news(q, country, language, category, page, size)


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
