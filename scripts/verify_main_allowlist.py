"""Verify that a proposed main worktree contains only runtime release assets."""

from __future__ import annotations

import argparse
from pathlib import Path


REQUIRED_FILES = (
    ".gitignore",
    "LICENSE",
    "README.md",
    "SOUL.md",
    "pyproject.toml",
    "uv.lock",
    "src/hermes_cgm_agent/knowledge/authoritative_kb.json",
)

REQUIRED_DIRECTORIES = (
    "integrations/hermes",
    "src/hermes_cgm_agent",
)

FORBIDDEN_TOP_LEVEL = {
    ".agents",
    ".claude",
    ".codex",
    ".github",
    ".specify",
    ".trae",
    "audit-report",
    "cgm-agent-competitive-analysis",
    "docs",
    "eval",
    "examples",
    "prompts",
    "schemas",
    "scripts",
    "skills",
    "specs",
    "tests",
}

FORBIDDEN_RUNTIME_PATHS = (
    "src/hermes_cgm_agent/knowledge/ingest",
    "src/hermes_cgm_agent/knowledge/pdfs",
    "src/hermes_cgm_agent/knowledge/review_queue",
    "src/hermes_cgm_agent/services/rag/eval_hit3.py",
    "src/hermes_cgm_agent/services/simulation",
)


def verify(root: Path) -> list[str]:
    errors: list[str] = []
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            errors.append(f"missing required file: {relative}")
    for relative in REQUIRED_DIRECTORIES:
        if not (root / relative).is_dir():
            errors.append(f"missing required directory: {relative}")
    for name in sorted(FORBIDDEN_TOP_LEVEL):
        if (root / name).exists():
            errors.append(f"forbidden top-level path: {name}")
    for relative in FORBIDDEN_RUNTIME_PATHS:
        if (root / relative).exists():
            errors.append(f"forbidden runtime path: {relative}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="release worktree to inspect")
    args = parser.parse_args()
    root = args.root.resolve()
    errors = verify(root)
    if errors:
        print("main runtime allowlist check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"main runtime allowlist check passed: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
