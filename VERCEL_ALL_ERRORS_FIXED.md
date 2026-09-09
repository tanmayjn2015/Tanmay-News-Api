# TANMAY NEWS API — ALL ERRORS FIXED

Version 5.0.0.

Fixed:
- Removed duplicate FastAPI route definitions.
- Removed the broken `requests` dependency from the API implementation.
- Rebuilt the app as one clean FastAPI application.
- Removed template-file dependency from Vercel runtime.
- Fixed Vercel entrypoint import path.
- Added explicit Python 3.12 runtime hint.
- Pinned compatible dependencies.
- Added safe NewsData.io timeout/error/invalid-JSON handling.
- Preserved `/`, `/user`, `/admin`, `/api`, `/health`, `/api/health`,
  `/news`, `/api/news`, `/search`, `/api/search`, admin stats and bot bridge.
- Preserved direct configuration: no `.env`, no `os.getenv()`, no `os.environ.get()`.
- Removed cache artifacts.

Admin:
username: tanmay
password: 2015

Bot:
Set your real Telegram bot token in `bot.py`; it cannot be invented.
