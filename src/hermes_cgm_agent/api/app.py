from __future__ import annotations

from fastapi import FastAPI, HTTPException

from hermes_cgm_agent.api.models import (
    AIOutputSummary,
    ChatApiRequest,
    ChatApiResponse,
    ConfigResponse,
    MessageSummary,
    SessionCreateRequest,
    SessionDetail,
    SessionSummary,
    StatusResponse,
)
from hermes_cgm_agent.config import AppConfig
from hermes_cgm_agent.platform.base import AgentPlatform
from hermes_cgm_agent.platform.hermes_cli import HermesCliPlatform
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.chat_runtime import ChatRuntimeService
from hermes_cgm_agent.services.sessions import SessionService
from hermes_cgm_agent.storage.sqlite import (
    AIOutputRecord,
    MessageRecord,
    SessionRecord,
    SQLiteStore,
)


def _session_summary(record: SessionRecord) -> SessionSummary:
    return SessionSummary(
        id=record.id,
        title=record.title,
        created_at=record.created_at,
        updated_at=record.updated_at,
        hermes_resume_id=record.hermes_resume_id,
        hermes_continue_name=record.hermes_continue_name,
        message_count=record.message_count,
    )


def _message_summary(record: MessageRecord) -> MessageSummary:
    return MessageSummary(
        id=record.id,
        session_id=record.session_id,
        role=record.role,
        content=record.content,
        created_at=record.created_at,
    )


def _ai_output_summary(record: AIOutputRecord) -> AIOutputSummary:
    return AIOutputSummary(
        id=record.id,
        session_id=record.session_id,
        request_message_id=record.request_message_id,
        response_message_id=record.response_message_id,
        text=record.text,
        returncode=record.returncode,
        created_at=record.created_at,
        model=record.model,
        provider=record.provider,
        toolsets=record.toolsets,
        skills=record.skills,
    )


def create_app(
    *,
    config: AppConfig | None = None,
    platform: AgentPlatform | None = None,
    store: SQLiteStore | None = None,
) -> FastAPI:
    config = config or AppConfig.from_env()
    platform = platform or HermesCliPlatform(config)
    store = store or SQLiteStore(config.database_path)
    store.initialize()
    session_service = SessionService(store)
    audit_service = AuditService(store)
    chat_service = ChatRuntimeService(
        store=store,
        audit_service=audit_service,
        platform=platform,
    )

    app = FastAPI(title="Hermes CGM Agent API", version="0.1.0")

    @app.get("/status", response_model=StatusResponse)
    def get_status() -> StatusResponse:
        status = platform.status()
        return StatusResponse(
            project="hermes-cgm-agent",
            hermes_available=status.available,
            hermes_name=status.name,
            hermes_version=status.version,
            hermes_executable=status.executable,
            detail=status.detail,
            database_path=str(config.database_path),
        )

    @app.get("/config", response_model=ConfigResponse)
    def get_config() -> ConfigResponse:
        return ConfigResponse(
            host=config.host,
            port=config.port,
            database_path=str(config.database_path),
            default_model=config.default_model,
            default_provider=config.default_provider,
            default_toolsets=config.default_toolsets,
            default_skills=config.default_skills,
        )

    @app.get("/sessions", response_model=list[SessionSummary])
    def list_sessions(limit: int = 50) -> list[SessionSummary]:
        return [_session_summary(item) for item in session_service.list(limit=limit)]

    @app.post("/sessions", response_model=SessionSummary)
    def create_session(body: SessionCreateRequest) -> SessionSummary:
        session = session_service.create(
            title=body.title,
            hermes_resume_id=body.hermes_resume_id,
            hermes_continue_name=body.hermes_continue_name,
        )
        return _session_summary(session)

    @app.get("/sessions/{session_id}", response_model=SessionDetail)
    def get_session(session_id: str) -> SessionDetail:
        try:
            session = session_service.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        messages = store.list_messages(session_id)
        ai_outputs = store.list_ai_outputs(session_id)
        return SessionDetail(
            session=_session_summary(session),
            messages=[_message_summary(item) for item in messages],
            ai_outputs=[_ai_output_summary(item) for item in ai_outputs],
        )

    @app.delete("/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, bool]:
        if not session_service.delete(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"deleted": True}

    @app.post("/chat", response_model=ChatApiResponse)
    def chat(body: ChatApiRequest) -> ChatApiResponse:
        try:
            turn = chat_service.run_chat(
                prompt=body.prompt,
                session_id=body.session_id,
                title=body.title,
                model=body.model,
                provider=body.provider,
                toolsets=body.toolsets,
                skills=body.skills,
                resume=body.resume,
                continue_session=body.continue_session,
                max_turns=body.max_turns,
                timeout_seconds=body.timeout_seconds,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

        if turn.ai_output.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Hermes chat call failed",
                    "session_id": turn.session.id,
                    "stderr": turn.ai_output.raw_stderr,
                    "returncode": turn.ai_output.returncode,
                },
            )

        return ChatApiResponse(
            session=_session_summary(turn.session),
            user_message=_message_summary(turn.user_message),
            assistant_message=_message_summary(turn.assistant_message),
            ai_output=_ai_output_summary(turn.ai_output),
        )

    return app
