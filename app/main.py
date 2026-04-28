import logging

from fastapi import FastAPI

from app.config import get_settings
from app.middleware.auth import AuthStore
from app.middleware.prompt_security import SecurityEngine
from app.routers import health, messages

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = FastAPI(title="AI Gateway", version="0.1.0")
    app.state.settings = settings
    app.state.auth_store = AuthStore.from_yaml(settings.auth_config_path)
    app.state.security_engine = SecurityEngine.from_yaml(settings.security_config_path)

    app.include_router(health.router)
    app.include_router(messages.router)

    logger.info(
        "ai-gateway ready: backend=%s model=%s clients=%d security_rules=%d",
        settings.backend_api_base,
        settings.backend_model,
        len(app.state.auth_store),
        len(app.state.security_engine),
    )
    return app


app = create_app()
