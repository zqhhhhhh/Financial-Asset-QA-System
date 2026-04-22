import logging
import logging.config
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.schemas import ChatRequest, ChatResponse, NewSessionResponse
from app.services.chat_service import ChatOrchestrator

# ── Logging setup ─────────────────────────────────────────────────────────────
_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_LOG_DIR.mkdir(exist_ok=True)

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "trace": {
            # LLM trace 日志只需要消息本身，不需要前缀
            "format": "%(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": "INFO",
        },
        "llm_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(_LOG_DIR / "llm.log"),
            "maxBytes": 10 * 1024 * 1024,  # 10 MB
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "trace",
            "level": "DEBUG",
        },
        "app_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(_LOG_DIR / "app.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "encoding": "utf-8",
            "formatter": "standard",
            "level": "INFO",
        },
    },
    "loggers": {
        # LLM trace：写 llm.log，不往上传播（避免刷控制台）
        "llm.trace": {
            "handlers": ["llm_file"],
            "level": "DEBUG",
            "propagate": False,
        },
        # 应用日志：控制台 + app.log
        "app": {
            "handlers": ["console", "app_file"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console", "app_file"],
        "level": "INFO",
    },
})

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Financial Asset QA System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

orchestrator = ChatOrchestrator()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/new_session", response_model=NewSessionResponse)
def new_session() -> NewSessionResponse:
    session_id = str(uuid.uuid4())
    orchestrator.new_session(session_id)
    return NewSessionResponse(session_id=session_id, message="New session created")


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    intent, response, data = orchestrator.chat(request.session_id, request.message)
    return ChatResponse(
        session_id=request.session_id,
        intent=intent,
        response=response,
        data=data,
    )
