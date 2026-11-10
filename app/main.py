"""FastAPI application entry point."""

import asyncio
import contextlib

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import init_db
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import backup as backup_router
from app.routers import clients as clients_router
from app.routers import inbounds as inbounds_router
from app.routers import monitor as monitor_router
from app.routers import subscription as subscription_router


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Bootstrap on startup; keep the monitor loop until shutdown."""
    init_db()
    _bootstrap_default_admin()

    # Phase 4: optional background traffic sweep.
    task = None
    if settings.MONITOR_INTERVAL_SECONDS > 0:
        task = asyncio.create_task(_monitor_loop(float(settings.MONITOR_INTERVAL_SECONDS)))
    try:
        yield
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _monitor_loop(interval: float):
    """Repeatedly collect stats and enforce limits; errors are logged and
    retried on the next tick so a temporarily down Xray does not kill it."""
    import logging

    import app.monitor as monitor
    from app.database import SessionLocal

    logger = logging.getLogger("xray-panel.monitor")
    while True:
        await asyncio.sleep(interval)
        db = SessionLocal()
        try:
            updated, disabled = monitor.refresh_stats_and_auto_disable(db)
            if updated or disabled:
                logger.info("monitor: %s updated, %s disabled", updated, disabled)
        except Exception as exc:
            logger.warning("monitor: stats collection failed: %s", exc)
        finally:
            db.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Xray Management Panel", version="0.1.0", lifespan=lifespan)

    # Session middleware for CSRF tokens and admin_id storage.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        session_cookie="panel_session",
        https_only=False,  # Set True behind HTTPS in production
        same_site="lax",
    )

    # Static files (CSS, JS)
    app.mount("/static", StaticFiles(directory="static"), name="static")

    # Routers (mounted under PANEL_PATH)
    prefix = settings.PANEL_PATH.rstrip("/")
    app.include_router(auth_router.router, prefix=prefix)
    app.include_router(admin_router.router, prefix=prefix)
    app.include_router(inbounds_router.router, prefix=prefix)
    app.include_router(clients_router.router, prefix=prefix)
    app.include_router(monitor_router.router, prefix=prefix)
    app.include_router(subscription_router.router, prefix=prefix)
    app.include_router(backup_router.router, prefix=prefix)

    @app.get("/")
    def root():
        return RedirectResponse(url=prefix + "/login")

    return app


def _bootstrap_default_admin():
    """Create a default admin account on first run if configured."""
    from app.database import SessionLocal
    import app.crud as crud
    from app.schemas import AdminCreate

    db = SessionLocal()
    try:
        existing = crud.get_admin_by_username(db, settings.DEFAULT_ADMIN_USERNAME)
        if existing:
            return
        if not settings.DEFAULT_ADMIN_PASSWORD:
            # No default password configured: skip bootstrap. Admin must be
            # created via a setup command or directly in the DB.
            return
        crud.create_admin(
            db,
            AdminCreate(
                username=settings.DEFAULT_ADMIN_USERNAME,
                password=settings.DEFAULT_ADMIN_PASSWORD,
            ),
        )
    finally:
        db.close()


app = create_app()
