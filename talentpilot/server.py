from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import db
from .api import router
from .config import settings
from .platform_api import router as platform_router
from .runtime import Runtime
from urllib.parse import urlsplit
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(application):
    db.init_db()
    application.state.platform = Runtime(settings.data_dir / "platform")
    application.state.platform.start()
    try:
        yield
    finally:
        application.state.platform.close()


app = FastAPI(title="iTalentPilot Agent", version="0.6.0", docs_url=None, redoc_url=None,
              openapi_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
app.include_router(router)
app.include_router(platform_router)
@app.middleware("http")
async def secure_local_api(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        overseas_extension = (
            ((request.method == "GET" and request.url.path == "/api/platform/connectors/overseas/jobs")
             or (request.method == "POST" and request.url.path == "/api/platform/connectors/overseas/profiles"))
            and origin is not None
            and origin.startswith("chrome-extension://")
            and request.headers.get("x-talentpilot-extension") == "overseas-v1"
        )
        if origin and urlsplit(origin).netloc != request.headers.get("host") and not overseas_extension:
            return JSONResponse({"detail": "Cross-origin API access denied"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site" and not overseas_extension:
            return JSONResponse({"detail": "Cross-site API access denied"}, status_code=403)
        if int(request.headers.get("content-length", "0") or 0) > 20_000_000:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        if request.url.path != "/api/health":
            request.state.actor = {"id": "local-admin", "role": "admin", "departments": ["*"]}
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(ValueError)
async def invalid_input(request, error):
    return JSONResponse({"detail": str(error)}, status_code=409)


@app.get("/")
def index():
    return FileResponse(Path(__file__).resolve().parent.parent / "web" / "console.html")


@app.get("/legacy")
def legacy():
    return FileResponse(Path(__file__).resolve().parent.parent / "web" / "index.html")


@app.get("/console")
def console():
    return FileResponse(Path(__file__).resolve().parent.parent / "web" / "console.html")


@app.get("/assets/{filename}")
def lock_monitor_asset(filename: str):
    if filename not in {"lock-monitor.js", "lock-monitor.css", "recruitment-report.js", "recruitment-report.css"}:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(Path(__file__).resolve().parent.parent / "web" / filename)
