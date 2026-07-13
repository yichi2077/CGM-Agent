from __future__ import annotations

import json

from hermes_cgm_agent.services.acceptance import AcceptanceConfig, AcceptanceRunner


def _hermes_accept(**kwargs) -> int:
    config = AcceptanceConfig(**kwargs)
    result = AcceptanceRunner(config).run()
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return result.exit_code
