"""Run: uvicorn bankwise.api.main:create_app --factory --workers 1 --no-proxy-headers."""
from contextlib import asynccontextmanager
import logging
import asyncio
import os

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from bankwise.api.config import load_ocr_settings
from bankwise.api.errors import install_error_handlers
from bankwise.api.routers import auth, health, identity, rag, assistant
from bankwise.api.schemas import ErrorResponse
from bankwise.api.transport import TransportMiddleware
from bankwise.core.bootstrap import build_security
from bankwise.core.config import load_app_config
from bankwise.core.errors import ConfigError
from bankwise.core.sms import build_sms_provider
from bankwise.database.accounts import Accounts
from bankwise.database.config import load_database_settings
from bankwise.database.session import Database
from bankwise.integration.id_ocr_adapter import create_id_ocr_adapter
from bankwise.integration.identity_processing import IdentityProcessingService, PROCESS_PERMISSION


def create_app(*, directory=None, environ=None, root=None, processor_factory=None):
    """processor_factory is a test seam; production always constructs the local adapter."""
    source = dict(os.environ if environ is None else environ)
    banking_mode = source.get('BANKWISE_BANKING_MODE', 'disabled')
    if banking_mode not in {'disabled', 'demo'}:
        raise ConfigError('BANKWISE_BANKING_MODE must be disabled or demo')
    config = load_app_config(directory, source)
    if config.identity_processing is None:
        raise ConfigError("app.yaml must define identity_processing")
    database_settings = load_database_settings(source)
    ocr_settings = load_ocr_settings(source)
    rag_mode = source.get("BANKWISE_RAG_MODE", "lexical")
    if rag_mode not in {"lexical", "semantic"}:
        raise ConfigError("BANKWISE_RAG_MODE must be lexical or semantic")
    try:
        rag_threshold = float(source.get("BANKWISE_RAG_THRESHOLD", "0.74"))
    except (TypeError, ValueError):
        raise ConfigError("BANKWISE_RAG_THRESHOLD must be a number between 0 and 1") from None
    if not 0.0 < rag_threshold <= 1.0:
        raise ConfigError("BANKWISE_RAG_THRESHOLD must be a number between 0 and 1")

    @asynccontextmanager
    async def lifespan(app):
        database = Database(database_settings)
        processor = None
        semantic = None
        retention_task = None
        app.state.initialized = False
        try:
            # Fail closed: never serve requests on a missing or stale schema.
            database.ready()
            accounts = Accounts(database)
            runtime = build_security(directory, source, root, principal_validator=accounts.validate_principal)
            settings = runtime.config.identity_processing
            if settings.rate_policy not in runtime.config.rate_limits or "identity_submit" not in runtime.config.rate_limits:
                raise ConfigError("identity processing/read rate policies are required")
            if not {PROCESS_PERMISSION, "identity:read"} <= runtime.authorizer.policy.permissions:
                raise ConfigError("identity processing/read permissions are required")
            if not {"rag_read", "rag_write"} <= runtime.config.rate_limits.keys():
                raise ConfigError("RAG rate policies are required")
            if not {"rag:read", "rag:write"} <= runtime.authorizer.policy.permissions:
                raise ConfigError("RAG permissions are required")
            for policy in ("otp_generation", "otp_resend", "otp_verify"):
                if policy not in runtime.config.rate_limits:
                    raise ConfigError(f"2FA rate policy '{policy}' is required in app.yaml")
            sms, dev_sms = build_sms_provider(source, config.is_production)
            processor = await run_in_threadpool(processor_factory or (lambda: create_id_ocr_adapter(
                model_dir=ocr_settings.model_dir, use_gpu=ocr_settings.use_gpu,
                allow_cpu_fallback=ocr_settings.allow_cpu_fallback, backend=ocr_settings.backend,
                paddle_model_dir=ocr_settings.paddle_model_dir, cpu_threads=ocr_settings.cpu_threads, yolo_weights=ocr_settings.yolo_weights)))
            app.state.database, app.state.accounts, app.state.runtime = database, accounts, runtime
            app.state.sms, app.state.dev_sms = sms, dev_sms
            app.state.identity = IdentityProcessingService(runtime.service, runtime.audit_sink, settings, processor)
            if rag_mode == "semantic":
                from bankwise.rag.semantic import SemanticRuntime
                semantic = await run_in_threadpool(SemanticRuntime, source.get("BANKWISE_RAG_MODEL_DIR", "/rag-models"), rag_threshold, source.get("BANKWISE_LLAMA_URL"))
            app.state.semantic = semantic
            app.state.banking_demo = banking_mode == 'demo'
            app.state.review_key = source.get('BANKWISE_REVIEW_KEY')
            app.state.demo_inbox = assistant.DemoInbox()
            from bankwise.api.routers.onboarding import purge_expired
            await run_in_threadpool(purge_expired, database)
            async def maintain_retention():
                while True:
                    await asyncio.sleep(60)
                    try:
                        await run_in_threadpool(purge_expired, database)
                    except Exception:
                        logging.getLogger("bankwise.api").error("onboarding retention cleanup failed")
            retention_task = asyncio.create_task(maintain_retention())
            app.state.initialized = True
            yield
        except ConfigError:
            raise
        except Exception:
            logging.getLogger("bankwise.api").error("application lifecycle failed")
            raise ConfigError("application resources could not be initialized or closed") from None
        finally:
            if retention_task is not None:
                retention_task.cancel()
                try:
                    await retention_task
                except asyncio.CancelledError:
                    pass
            app.state.initialized = False
            try:
                if processor is not None and callable(getattr(processor, "close", None)):
                    await run_in_threadpool(processor.close)
            finally:
                if semantic is not None:
                    semantic.close()
                database.close()

    errors = {status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 413, 422, 429, 500, 503)}
    app = FastAPI(title="Bankwise AI", version="0.3.0", lifespan=lifespan, debug=False, responses=errors)
    app.add_middleware(TransportMiddleware, max_body_bytes=2 * config.identity_processing.max_image_bytes + 16 * 1024)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(identity.router)
    from bankwise.api.routers import identity_review
    app.include_router(identity_review.router)
    from bankwise.api.routers import onboarding
    app.include_router(onboarding.router)
    app.include_router(rag.router)
    app.include_router(assistant.router)
    return app
