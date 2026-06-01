from __future__ import annotations

from dataclasses import dataclass

from hermes_cgm_agent.platform.base import AgentPlatform, ChatRequest
from hermes_cgm_agent.platform.hermes_cli import HermesCliPlatform
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.storage.sqlite import AIOutputRecord, MessageRecord, SQLiteStore


def _derive_title(prompt: str) -> str:
    compact = " ".join(prompt.split())
    return compact[:60] or "New session"


@dataclass(frozen=True)
class ChatTurn:
    session: object
    user_message: MessageRecord
    assistant_message: MessageRecord
    ai_output: AIOutputRecord


class ChatRuntimeService:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        audit_service: AuditService,
        platform: AgentPlatform | None = None,
    ) -> None:
        self.store = store
        self.audit_service = audit_service
        self.platform = platform or HermesCliPlatform()

    def run_chat(
        self,
        *,
        prompt: str,
        session_id: str | None = None,
        title: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        toolsets: str | None = None,
        skills: str | None = None,
        resume: str | None = None,
        continue_session: str | None = None,
        max_turns: int | None = None,
        timeout_seconds: int | None = None,
    ) -> ChatTurn:
        if session_id:
            session = self.store.get_session(session_id)
        else:
            session = self.store.create_session(
                title=title or _derive_title(prompt),
                hermes_resume_id=resume,
                hermes_continue_name=continue_session,
            )

        effective_resume = resume or session.hermes_resume_id
        effective_continue = (
            continue_session
            if continue_session is not None
            else session.hermes_continue_name
        )

        if title or effective_resume or effective_continue:
            session = self.store.update_session(
                session.id,
                title=title if title is not None else session.title,
                hermes_resume_id=effective_resume,
                hermes_continue_name=effective_continue,
            )

        user_message = self.store.create_message(
            session_id=session.id,
            role="user",
            content=prompt,
            metadata={
                "model": model,
                "provider": provider,
                "toolsets": toolsets,
                "skills": skills,
                "resume": effective_resume,
                "continue_session": effective_continue,
            },
        )
        self.audit_service.log(
            session.id,
            "chat.request",
            {
                "message_id": user_message.id,
                "prompt": prompt,
                "model": model,
                "provider": provider,
                "toolsets": toolsets,
                "skills": skills,
                "resume": effective_resume,
                "continue_session": effective_continue,
            },
        )

        result = self.platform.chat(
            ChatRequest(
                prompt=prompt,
                model=model,
                provider=provider,
                toolsets=toolsets,
                skills=skills,
                resume=effective_resume,
                continue_session=effective_continue,
                max_turns=max_turns,
                timeout_seconds=timeout_seconds,
            )
        )
        assistant_message = self.store.create_message(
            session_id=session.id,
            role="assistant",
            content=result.text,
            metadata={
                "returncode": result.returncode,
            },
        )
        ai_output = self.store.create_ai_output(
            session_id=session.id,
            request_message_id=user_message.id,
            response_message_id=assistant_message.id,
            text=result.text,
            raw_stdout=result.raw_stdout,
            raw_stderr=result.raw_stderr,
            returncode=result.returncode,
            model=model,
            provider=provider,
            toolsets=toolsets,
            skills=skills,
        )
        self.audit_service.log(
            session.id,
            "chat.response",
            {
                "request_message_id": user_message.id,
                "response_message_id": assistant_message.id,
                "ai_output_id": ai_output.id,
                "returncode": result.returncode,
                "stderr": result.raw_stderr,
            },
        )
        session = self.store.get_session(session.id)
        return ChatTurn(
            session=session,
            user_message=user_message,
            assistant_message=assistant_message,
            ai_output=ai_output,
        )
