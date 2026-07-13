from __future__ import annotations

import json
from pathlib import Path

from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


def _dexcom_auth(
    *,
    db_path: Path,
    user_id: str,
    state: str | None,
    code: str | None,
) -> int:
    from hermes_cgm_agent.services.dexcom import (
        DexcomAuthError,
        DexcomAuthService,
        DexcomClient,
        DexcomConfig,
        DexcomTokenStore,
    )

    try:
        dexcom_config = DexcomConfig.from_env()
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1

    store = SQLiteStore(db_path)
    store.initialize()
    client = DexcomClient(dexcom_config)
    auth = DexcomAuthService(
        config=dexcom_config,
        client=client,
        token_store=DexcomTokenStore(store),
    )

    authorize_url = auth.authorization_url(state=state)
    print(f"environment: {dexcom_config.environment}")
    print(f"redirect_uri: {dexcom_config.redirect_uri}")
    print("Open this URL in a browser, authorize, then copy the redirect URL you land on:")
    print(authorize_url)

    code_or_url = code
    if not code_or_url:
        try:
            code_or_url = input("Paste the redirect URL or authorization code: ").strip()
        except EOFError:
            code_or_url = ""

    try:
        token = auth.complete_authorization(user_id, code_or_url)
    except (DexcomAuthError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1

    payload = {
        "status": "ok",
        "user_id": token.user_id,
        "environment": token.environment,
        "scope": token.scope,
        "token_type": token.token_type,
        "expires_at": token.expires_at.isoformat(),
        "database_path": str(db_path),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _dexcom_sync(
    *,
    db_path: Path,
    user_id: str,
    days: int,
    force: bool,
    session_id: str,
) -> int:
    store = SQLiteStore(db_path)
    store.initialize()
    executor = ToolExecutor(
        repository=SQLiteCGMRepository(store),
        audit_service=AuditService(store),
    )
    response = executor.execute(
        tool_name="data.dexcom_sync",
        arguments={"user_id": user_id, "days": days, "force": force},
        session_id=session_id,
    )
    body = response.to_dict()
    body["database_path"] = str(db_path)
    print(json.dumps(body, ensure_ascii=False, sort_keys=True))
    from hermes_cgm_agent.services.tools.handlers.base import FAILURE_STATUSES

    return 1 if response.status in FAILURE_STATUSES else 0
