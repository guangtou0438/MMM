from contextlib import asynccontextmanager
from pathlib import Path
import threading
import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api.routes import router
from .config import Settings
from .database import create_database

STATIC = Path(__file__).with_name("static")


def create_app(database_path=None, model_client=None, clock=None, legacy_source=None, scheduler_enabled=None):
    @asynccontextmanager
    async def lifespan(app):
        engine, factory = create_database(Path(database_path) if database_path else Settings.from_env().database_path)
        app.state.sessions = factory
        app.state.engine = engine
        app.state.model_client = model_client
        app.state.clock = clock
        app.state.planning_lock = threading.Lock()
        app.state.import_warning = ""
        from .legacy import import_legacy
        from .config import ROOT
        # Tests with an explicit DB never inspect a user's real history.
        source = Path(legacy_source) if legacy_source else (Path(os.getenv("MMM_DB_PATH", "data/mmm.db")) if database_path is None else None)
        if source and not source.is_absolute(): source = ROOT / source
        if source:
            try:
                count = import_legacy(factory, source)
                logging.getLogger("mmm.migration").info("Imported %d legacy tasks", count)
            except Exception:
                app.state.import_warning = "旧任务导入未完成，原数据库未修改，请检查服务日志。"
                logging.getLogger("mmm.migration").exception("Legacy import failed; source database unchanged")
        from .session_migration import migrate_dates, repair_message_origins
        from .models import utc_now
        migrate_dates(factory, clock or utc_now)
        repair_message_origins(factory, clock or utc_now)
        from .core.date_tasks import classify_existing
        classify_existing(factory)
        from .agent.store import Store
        from .agent.runtime import Runtime
        from .memory.service import MemoryService
        from .providers.embedding import LocalEmbedding
        from .providers.weather import WeatherProvider
        from .tools.weather import WeatherService
        from .tools.executor import Executor
        app.state.agent_store = Store(engine)
        from .skills.registry import load_extensions
        extensions = os.getenv('CALENDAR_SKILLS_DIR')
        if extensions:
            load_extensions(Path(extensions), app.state.agent_store)
        app.state.memory_service = MemoryService(app.state.agent_store, LocalEmbedding())
        app.state.weather_service = WeatherService(app.state.agent_store, factory, WeatherProvider(app.state.agent_store), clock or utc_now, app.state.memory_service)
        app.state.agent = Runtime(app.state.agent_store, app.state.memory_service,
                                  Executor(factory, clock or utc_now, app.state.memory_service, app.state.weather_service), model_client)
        app.state.agent.start()
        from apscheduler.schedulers.background import BackgroundScheduler
        app.state.scheduler = BackgroundScheduler(timezone="Australia/Melbourne")
        from .memory.jobs import process_one, schedule_idle
        def consolidate():
            from .memory.retrieval import process_index_job
            process_index_job(app.state.memory_service)
            schedule_idle(app.state.memory_service)
            result = process_one(app.state.memory_service, model_client)
            if result.get('memory_ids'):
                from .memory.retrieval import rebuild
                try:
                    rebuild(app.state.memory_service)
                except (ImportError, OSError, RuntimeError):
                    logging.getLogger('mmm.memory').warning('Embedding unavailable; keyword recall remains active')
        app.state.scheduler.add_job(consolidate, 'interval', seconds=30,
                                    id='agent-memory', max_instances=1, coalesce=True)
        app.state.scheduler.add_job(app.state.memory_service.retain, 'interval', hours=1, id='agent-retention', max_instances=1)
        def check_weather():
            from .skills.registry import available
            if 'weather_planning' in {s['id'] for s in available(app.state.agent_store)}:
                app.state.weather_service.check()
        app.state.scheduler.add_job(check_weather, 'cron', hour=8, minute=0, id='weather-planning', max_instances=1)
        enabled = database_path is None if scheduler_enabled is None else scheduler_enabled
        if enabled: app.state.scheduler.start()
        try:
            yield
        finally:
            if app.state.scheduler.running: app.state.scheduler.shutdown(wait=True)
            app.state.agent.close()
            engine.dispose()

    app = FastAPI(title="MMM · 个人记忆与日期待办", version="P4", lifespan=lifespan)
    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        if request.url.path == "/sessions/complete":
            return JSONResponse({"detail": "必须填写有效的 actual_minutes（非负整数）、session_id 和 outcome。"}, status_code=400)
        return await request_validation_exception_handler(request, error)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (request.url.path == '/chat' or request.url.path == '/tasks' or request.url.path.startswith('/tasks/') or request.url.path.startswith('/sessions/')):
            return JSONResponse({"detail": "旧分钟排期写接口已停用，请使用 /api 日期待办接口。历史仍可读取。"}, status_code=410)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
                return JSONResponse({"detail": "只允许同源操作。"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "不允许跨站操作。"}, status_code=403)
            if request.method == "POST" and request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "需要 application/json 请求。"}, status_code=415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    for route in router.routes:
        if getattr(route, 'methods', set()) & {'POST', 'PUT', 'PATCH', 'DELETE'} and route.path != '/time/resolve':
            route.include_in_schema = False
    app.include_router(router)
    from .api.date_sessions import router as date_router
    app.include_router(date_router)
    from .api.agent import router as agent_router
    app.include_router(agent_router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app
