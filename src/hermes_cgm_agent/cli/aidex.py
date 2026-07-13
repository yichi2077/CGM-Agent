from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from hermes_cgm_agent.config import default_hermes_home
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore


def _aidex_auth(
    *, db_path: Path, user_id: str, state: str | None, code: str | None
) -> int:
    from hermes_cgm_agent.services.aidex import (
        AidexAuthError,
        AidexAuthService,
        AidexClient,
        AidexConfig,
        AidexTokenStore,
    )

    store = SQLiteStore(db_path)
    store.initialize()
    try:
        config = AidexConfig.from_env()
    except ValueError as exc:
        _write_aidex_audit(
            store,
            user_id=user_id,
            session_kind="cli",
            event_type="aidex_auth_failed",
            payload={"stage": "configuration", "error": str(exc)},
        )
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1
    auth = AidexAuthService(
        config=config,
        client=AidexClient(config),
        token_store=AidexTokenStore(store),
    )
    oauth_state = state or secrets.token_urlsafe(24)
    print(f"environment: {config.environment}")
    print("Open this URL, authorize the app, then paste the full redirect URL:")
    print(auth.authorization_url(state=oauth_state))
    code_or_url = code
    if not code_or_url:
        try:
            code_or_url = input("Paste the redirect URL or authorization code: ").strip()
        except EOFError:
            code_or_url = ""
    try:
        token = auth.complete_authorization(
            user_id,
            code_or_url,
            expected_state=oauth_state if (state is not None or code is None) else None,
        )
    except (AidexAuthError, ValueError) as exc:
        _write_aidex_audit(
            store,
            user_id=user_id,
            session_kind="cli",
            event_type="aidex_auth_failed",
            payload={"stage": "oauth", "error": str(exc)},
        )
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1
    _write_aidex_audit(
        store,
        user_id=user_id,
        session_kind="cli",
        event_type="aidex_auth_completed",
        payload={
            "environment": token.environment,
            "expires_at": token.expires_at.isoformat(),
        },
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "user_id": token.user_id,
                "environment": token.environment,
                "expires_at": token.expires_at.isoformat(),
                "database_path": str(db_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _aidex_sync(
    *,
    db_path: Path,
    user_id: str,
    days: int,
    force: bool,
    incremental: bool,
    overlap_minutes: int,
    bootstrap_hours: int,
) -> int:
    from hermes_cgm_agent.services.aidex import AidexError, build_aidex_sync_service

    store = SQLiteStore(db_path)
    store.initialize()
    try:
        result = build_aidex_sync_service(SQLiteCGMRepository(store)).sync(
            user_id=user_id,
            days=days,
            force=force,
            incremental=incremental,
            overlap_minutes=overlap_minutes,
            bootstrap_hours=bootstrap_hours,
        )
    except (AidexError, ValueError) as exc:
        _write_aidex_audit(
            store,
            user_id=user_id,
            session_kind="cli",
            event_type="aidex_sync_failed",
            payload={"error": str(exc)},
        )
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1
    body = result.to_dict()
    _write_aidex_audit(
        store,
        user_id=user_id,
        session_kind="cli",
        event_type="aidex_sync_completed",
        payload=body,
    )
    body["database_path"] = str(db_path)
    print(json.dumps(body, ensure_ascii=False, sort_keys=True))
    return 0


def _aidex_status(*, db_path: Path, user_id: str, live: bool) -> int:
    from hermes_cgm_agent.services.aidex import (
        AidexAuthService,
        AidexClient,
        AidexConfig,
        AidexError,
        AidexTokenStore,
    )

    store = SQLiteStore(db_path)
    store.initialize()
    reasons: list[str] = []
    config = None
    try:
        config = AidexConfig.from_env()
    except ValueError as exc:
        reasons.append(str(exc))

    token_store = AidexTokenStore(store)
    token = None
    try:
        token = token_store.load(user_id)
    except RuntimeError:
        reasons.append(
            "Stored AiDEX authorization cannot be decrypted with the current storage key."
        )

    if token is None:
        reasons.append(f"No AiDEX authorization found for user '{user_id}'.")

    configured_target = (
        "production"
        if os.getenv("AIDEX_USE_SANDBOX", "true").strip().lower()
        in {"0", "false", "no", "off"}
        else "sandbox"
    )
    environment = (
        config.environment
        if config is not None
        else token.environment
        if token is not None
        else configured_target
    )
    environment_matches = bool(
        config is not None and token is not None and token.environment == config.environment
    )
    if config is not None and token is not None and not environment_matches:
        reasons.append(
            f"Stored authorization targets {token.environment}, but configuration targets {config.environment}."
        )

    source = f"aidex:{environment}"
    with store.connect() as conn:
        glucose_row = conn.execute(
            """
            SELECT COUNT(*) AS count, MAX(timestamp) AS latest
            FROM glucose_points
            WHERE user_id = ? AND source = ?
            """,
            (user_id, source),
        ).fetchone()

    ready_for_sync = bool(config is not None and token is not None and environment_matches)
    live_verified: bool | None = None
    if live:
        live_verified = False
        if ready_for_sync and config is not None:
            client = AidexClient(config)
            auth = AidexAuthService(
                config=config,
                client=client,
                token_store=token_store,
            )
            try:
                client.get_data_range(auth.valid_access_token(user_id))
                live_verified = True
            except AidexError as exc:
                reasons.append(f"Live AiDEX data-range verification failed: {exc}")

    hermes_home = default_hermes_home()
    cron_script = hermes_home / "scripts" / "cgm_aidex_sync.py"
    cron_user_id = (os.getenv("CGM_AGENT_USER_ID") or "").strip()
    automation_reasons: list[str] = []
    if not cron_script.is_file():
        automation_reasons.append("AiDEX cron script is not installed in Hermes Home.")
    if not cron_user_id:
        automation_reasons.append("CGM_AGENT_USER_ID is not configured for cron sync.")
    elif cron_user_id != user_id:
        automation_reasons.append(
            f"CGM_AGENT_USER_ID targets '{cron_user_id}', not requested user '{user_id}'."
        )
    ready_for_automation = ready_for_sync and not automation_reasons
    success = ready_for_sync and (not live or live_verified is True)
    body = {
        "status": "ready" if success else "blocked",
        "user_id": user_id,
        "environment": environment,
        "credentials_configured": config is not None,
        "authorization_present": token is not None,
        "authorization_environment_matches": environment_matches,
        "token_expires_at": token.expires_at.isoformat() if token is not None else None,
        "token_expired": token.is_expired() if token is not None else None,
        "database_path": str(db_path),
        "aidex_glucose_count": int(glucose_row["count"] if glucose_row else 0),
        "latest_aidex_glucose_at": glucose_row["latest"] if glucose_row else None,
        "cron_script_path": str(cron_script),
        "cron_script_installed": cron_script.is_file(),
        "ready_for_sync": ready_for_sync,
        "ready_for_automation": ready_for_automation,
        "automation_blocking_reasons": automation_reasons,
        "live_requested": live,
        "live_verified": live_verified,
        "blocking_reasons": reasons,
    }
    print(json.dumps(body, ensure_ascii=False, sort_keys=True))
    return 0 if success else 1


def _write_aidex_audit(
    store: SQLiteStore,
    *,
    user_id: str,
    session_kind: str,
    event_type: str,
    payload: dict,
) -> None:
    safe_payload = {"user_id": user_id, **payload}
    store.create_audit_log(
        session_id=f"aidex-{session_kind}:{user_id}",
        event_type=event_type,
        payload=safe_payload,
    )
