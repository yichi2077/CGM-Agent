from __future__ import annotations

import json
from pathlib import Path

from hermes_cgm_agent.domain.cgm import utc_now
from hermes_cgm_agent.services.simulation import CsvReplaySource, HermesStage, SimulationRunner


def _simulate(
    *,
    csv_path: Path,
    user_id: str,
    source_label: str,
    timezone_name: str,
    db_path: Path | None,
    acceleration: float,
    max_speed: bool,
    time_base: str,
    days: int | None,
    expected_interval_minutes: int | None,
    out_dir: Path | None,
    hermes: bool,
    fail_fast: bool,
) -> int:
    if not csv_path.exists():
        print(
            json.dumps(
                {"status": "error", "message": f"CSV not found: {csv_path}"},
                ensure_ascii=False,
            )
        )
        return 1
    if out_dir is None:
        run_ts = utc_now().strftime("%Y%m%d-%H%M%S")
        out_dir = Path(".runtime") / "simulation" / run_ts
    out_dir.mkdir(parents=True, exist_ok=True)
    if db_path is None:
        db_path = out_dir / "app.db"

    source = CsvReplaySource(
        csv_path,
        source_name=csv_path.name,
        time_base=time_base,
        days=days,
        default_timezone=timezone_name,
    )
    runner = SimulationRunner(
        db_path=db_path,
        out_dir=out_dir,
        user_id=user_id,
        source_label=source_label,
        timezone_name=timezone_name,
        acceleration=acceleration,
        max_speed=max_speed,
        expected_interval_minutes=expected_interval_minutes,
    )
    result = runner.run(source, fail_fast=fail_fast)
    body = result.to_dict()

    if hermes:
        stage = HermesStage(db_path=db_path, time_base=time_base)
        stage_result = stage.preflight()
        stage_path = stage.write_result(out_dir, stage_result)
        body["hermes_stage"] = stage_result.to_dict()
        body["hermes_stage_path"] = str(stage_path)
        print(json.dumps(body, ensure_ascii=False, sort_keys=True))
        if stage_result.exit_code != 0:
            return stage_result.exit_code
    else:
        print(json.dumps(body, ensure_ascii=False, sort_keys=True))
    return result.exit_code
