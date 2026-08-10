from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.9/3.10
    import tomli as tomllib  # type: ignore[no-redef]


SUPPORTED_REASONING_EFFORTS = {
    "none",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
}
ROLE_NAMES = ("coordinator", "executor", "verifier")

DEFAULT_CONFIG = """# LoopAI per-role Codex settings.
# CLI role options override global CLI options, which override this file.

[coordinator]
model = "gpt-5.6-sol"
reasoning_effort = "high"
# startup_prompt = \"\"\"请使用中文与用户交互。\"\"\"

[executor]
model = "gpt-5.6-terra"
reasoning_effort = "medium"

[verifier]
model = "gpt-5.6-sol"
reasoning_effort = "high"
"""


@dataclass(frozen=True)
class RoleSettings:
    model: str
    reasoning_effort: str
    startup_prompt: str | None = None


def load_workspace_config(workspace: Path) -> dict[str, RoleSettings]:
    resolved = workspace.expanduser().resolve()
    config_directory = resolved / ".loopai"
    config_directory.mkdir(parents=True, exist_ok=True)
    config_path = config_directory / "config.toml"
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    _exclude_loopai_from_git(resolved)

    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"Invalid LoopAI TOML configuration {config_path}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"LoopAI configuration must be a TOML table: {config_path}")

    unknown_sections = sorted(set(payload) - set(ROLE_NAMES))
    if unknown_sections:
        raise ValueError(
            f"Unknown LoopAI configuration sections in {config_path}: "
            f"{', '.join(unknown_sections)}"
        )

    settings: dict[str, RoleSettings] = {}
    for role in ROLE_NAMES:
        raw = payload.get(role)
        if not isinstance(raw, dict):
            raise ValueError(f"Missing [{role}] section in {config_path}")
        allowed_keys = {"model", "reasoning_effort"}
        if role == "coordinator":
            allowed_keys.add("startup_prompt")
        unknown_keys = sorted(set(raw) - allowed_keys)
        if unknown_keys:
            raise ValueError(
                f"Unknown keys in [{role}] in {config_path}: {', '.join(unknown_keys)}"
            )
        model = _required_string(raw, "model", role, config_path)
        effort = _required_string(raw, "reasoning_effort", role, config_path)
        if effort not in SUPPORTED_REASONING_EFFORTS:
            choices = ", ".join(sorted(SUPPORTED_REASONING_EFFORTS))
            raise ValueError(
                f"Unsupported reasoning_effort {effort!r} in [{role}] in {config_path}; "
                f"choose one of: {choices}"
            )
        startup_prompt = (
            _optional_string(raw, "startup_prompt", role, config_path)
            if role == "coordinator"
            else None
        )
        settings[role] = RoleSettings(
            model=model,
            reasoning_effort=effort,
            startup_prompt=startup_prompt,
        )
    return settings


def _required_string(
    table: dict[str, Any], key: str, role: str, config_path: Path
) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"[{role}].{key} must be a non-empty string in {config_path}"
        )
    return value.strip()


def _optional_string(
    table: dict[str, Any], key: str, role: str, config_path: Path
) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"[{role}].{key} must be a string in {config_path}")
    stripped = value.strip()
    return stripped or None


def _exclude_loopai_from_git(workspace: Path) -> None:
    git_dir = workspace / ".git"
    exclude = git_dir / "info" / "exclude"
    if not git_dir.is_dir() or not exclude.parent.is_dir():
        return
    current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    rule = ".loopai/"
    if rule in {line.strip() for line in current.splitlines()}:
        return
    separator = "" if not current or current.endswith("\n") else "\n"
    exclude.write_text(f"{current}{separator}{rule}\n", encoding="utf-8")
