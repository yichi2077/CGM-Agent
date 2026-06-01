from __future__ import annotations

from pydantic import BaseModel, Field


class StatusResponse(BaseModel):
    project: str
    hermes_available: bool
    hermes_name: str
    hermes_version: str | None = None
    hermes_executable: str | None = None
    detail: str | None = None
    database_path: str


class SessionCreateRequest(BaseModel):
    title: str | None = None
    hermes_resume_id: str | None = None
    hermes_continue_name: str | None = None


class SessionSummary(BaseModel):
    id: str
    title: str | None = None
    created_at: str
    updated_at: str
    hermes_resume_id: str | None = None
    hermes_continue_name: str | None = None
    message_count: int = 0


class MessageSummary(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    created_at: str


class AIOutputSummary(BaseModel):
    id: str
    session_id: str
    request_message_id: str
    response_message_id: str
    text: str
    returncode: int
    created_at: str
    model: str | None = None
    provider: str | None = None
    toolsets: str | None = None
    skills: str | None = None


class SessionDetail(BaseModel):
    session: SessionSummary
    messages: list[MessageSummary]
    ai_outputs: list[AIOutputSummary]


class ChatApiRequest(BaseModel):
    prompt: str = Field(min_length=1)
    session_id: str | None = None
    title: str | None = None
    model: str | None = None
    provider: str | None = None
    toolsets: str | None = None
    skills: str | None = None
    resume: str | None = None
    continue_session: str | None = None
    max_turns: int | None = Field(default=None, ge=1)
    timeout_seconds: int | None = Field(default=None, ge=1)


class ChatApiResponse(BaseModel):
    session: SessionSummary
    user_message: MessageSummary
    assistant_message: MessageSummary
    ai_output: AIOutputSummary


class ConfigResponse(BaseModel):
    host: str
    port: int
    database_path: str
    default_model: str | None = None
    default_provider: str | None = None
    default_toolsets: str | None = None
    default_skills: str | None = None
