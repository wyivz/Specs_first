from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from collectors.env_schema import all_schema_keys, env_example_path, parse_env_example

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DOTENV_PATH = _PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class CommentLine:
    text: str


@dataclass(frozen=True)
class BlankLine:
    pass


@dataclass(frozen=True)
class AssignmentLine:
    key: str
    value: str


EnvLine = CommentLine | BlankLine | AssignmentLine


def dotenv_path() -> Path:
    if _DOTENV_PATH.exists():
        return _DOTENV_PATH
    fallback = Path.cwd() / ".env"
    return fallback


def _parse_lines(text: str) -> list[EnvLine]:
    document: list[EnvLine] = []
    for raw in text.splitlines():
        if not raw.strip():
            document.append(BlankLine())
            continue
        if raw.lstrip().startswith("#"):
            document.append(CommentLine(raw))
            continue
        if "=" in raw:
            key, _, value = raw.partition("=")
            document.append(AssignmentLine(key=key.strip(), value=value))
            continue
        document.append(CommentLine(raw))
    return document


def _assignment_map(document: list[EnvLine]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in document:
        if isinstance(line, AssignmentLine):
            values[line.key] = line.value
    return values


def read_env_document(path: Path | None = None) -> list[EnvLine]:
    env_path = path or dotenv_path()
    if not env_path.is_file():
        return _document_from_example()
    return _parse_lines(env_path.read_text(encoding="utf-8"))


def read_env_file(path: Path | None = None) -> dict[str, str]:
    return _assignment_map(read_env_document(path))


def _document_from_example() -> list[EnvLine]:
    example = env_example_path()
    if not example.is_file():
        return []
    return _parse_lines(example.read_text(encoding="utf-8"))


def _ensure_all_schema_keys(document: list[EnvLine], values: dict[str, str]) -> list[EnvLine]:
    """Merge example template with current values; append missing keys from example."""
    existing_keys = {line.key for line in document if isinstance(line, AssignmentLine)}
    schema_keys = all_schema_keys()
    if not schema_keys:
        return document

    merged = list(document)
    example_doc = _document_from_example()
    example_values = _assignment_map(example_doc)

    for key in schema_keys:
        if key in existing_keys:
            continue
        value = values.get(key, example_values.get(key, ""))
        if merged and not isinstance(merged[-1], BlankLine):
            merged.append(BlankLine())
        merged.append(AssignmentLine(key=key, value=value))

    return merged


def apply_updates(
    updates: dict[str, str],
    *,
    path: Path | None = None,
    skip_empty_secrets: bool = False,
    secret_keys: frozenset[str] | None = None,
) -> Path:
    """Update .env values while preserving comments and line order."""
    env_path = path or dotenv_path()
    document = read_env_document(env_path) if env_path.is_file() else _document_from_example()
    current = _assignment_map(document)

    secrets = secret_keys or frozenset()
    for key, value in updates.items():
        if skip_empty_secrets and key in secrets and not value.strip():
            continue
        current[key] = value

    document = _ensure_all_schema_keys(document, current)
    new_document: list[EnvLine] = []
    for line in document:
        if isinstance(line, AssignmentLine):
            new_document.append(AssignmentLine(key=line.key, value=current.get(line.key, line.value)))
        else:
            new_document.append(line)

    write_env_file(new_document, path=env_path)
    return env_path


def write_env_file(document: list[EnvLine], *, path: Path | None = None) -> Path:
    env_path = path or dotenv_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for line in document:
        if isinstance(line, BlankLine):
            lines.append("")
        elif isinstance(line, CommentLine):
            lines.append(line.text)
        elif isinstance(line, AssignmentLine):
            lines.append(f"{line.key}={line.value}")

    payload = "\n".join(lines)
    if payload and not payload.endswith("\n"):
        payload += "\n"

    tmp_path = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    shutil.move(str(tmp_path), str(env_path))
    return env_path


def apply_updates_to_environ(updates: dict[str, str], *, schema_keys: list[str] | None = None) -> None:
    keys = schema_keys or all_schema_keys()
    for key in keys:
        if key in updates:
            os.environ[key] = updates[key]
        elif key not in os.environ:
            os.environ[key] = ""


def bootstrap_env_from_example(*, path: Path | None = None) -> Path:
    """Create .env from .env.example when missing."""
    env_path = path or dotenv_path()
    if env_path.is_file():
        return env_path
    document = _document_from_example()
    return write_env_file(document, path=env_path)
