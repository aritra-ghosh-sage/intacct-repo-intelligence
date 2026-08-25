"""Central validation-profile contracts for Greenfield Step 7."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from greenfield.step7_contract import CHECK_CATEGORIES, artifact_sha256

REGISTRY_VERSION = 1
REQUIRED_RUNNER = "sandbox"

_ROOT_KEYS = frozenset({"version", "profiles"})
_PROFILE_KEYS = frozenset(
    {
        "profile_id",
        "profile_version",
        "repository",
        "enabled",
        "unavailable_reason",
        "required_runner",
        "commands",
        "policy",
    }
)
_COMMAND_KEYS = frozenset({"id", "argv", "cwd", "timeout_seconds", "shell"})
_POLICY_KEYS = frozenset({"diff_limits", "max_output_bytes", "path_classification"})
_DIFF_LIMIT_KEYS = frozenset(
    {"max_files", "max_added_lines", "max_deleted_lines", "max_bytes"}
)
_CLASSIFICATION_KEYS = frozenset(
    {"source_prefixes", "generated_prefixes", "allowed_generated_prefixes"}
)


class Step7ProfileError(ValueError):
    """Raised when a Step 7 validation profile is unsafe or malformed."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that fails closed on duplicate mapping keys."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> Any:
        if not isinstance(node, MappingNode):
            raise ConstructorError(
                None, None, "expected a mapping node", node.start_mark
            )
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key: {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _reject_unknown(
    value: Mapping[str, Any], allowed: frozenset[str], label: str
) -> None:
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise Step7ProfileError(
            f"{label} contains unknown fields: {', '.join(unknown)}"
        )


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Step7ProfileError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Step7ProfileError(f"{label} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Step7ProfileError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Step7ProfileError(f"{label} must be a non-negative integer")
    return value


def _repository(value: Any, label: str) -> str:
    result = _text(value, label).lower()
    parts = result.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        raise Step7ProfileError(f"{label} must be an exact owner/repository identity")
    return result.removesuffix(".git")


def _safe_relative(value: Any, label: str, *, root_allowed: bool = False) -> str:
    result = _text(value, label)
    if result == "." and root_allowed:
        return result
    path = PurePosixPath(result)
    if (
        result.startswith("/")
        or "\\" in result
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise Step7ProfileError(f"{label} must be a safe relative POSIX path")
    return path.as_posix().rstrip("/")


def _prefixes(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise Step7ProfileError(f"{label} must be a list")
    result = [
        _safe_relative(item, f"{label}[{index}]") for index, item in enumerate(value)
    ]
    if result != sorted(set(result)):
        raise Step7ProfileError(f"{label} must be sorted and unique")
    return result


def _normalize_commands(value: Any, label: str) -> dict[str, list[dict[str, Any]]]:
    commands = _mapping(value, label)
    if set(commands) != set(CHECK_CATEGORIES):
        raise Step7ProfileError(
            f"{label} must contain exactly: {', '.join(CHECK_CATEGORIES)}"
        )
    normalized: dict[str, list[dict[str, Any]]] = {}
    all_ids: set[str] = set()
    for category in CHECK_CATEGORIES:
        rows = commands[category]
        if not isinstance(rows, list) or not rows:
            raise Step7ProfileError(f"{label}.{category} must be a non-empty list")
        category_rows: list[dict[str, Any]] = []
        ids: list[str] = []
        for index, raw in enumerate(rows):
            row = _mapping(raw, f"{label}.{category}[{index}]")
            _reject_unknown(row, _COMMAND_KEYS, f"{label}.{category}[{index}]")
            command_id = _text(row.get("id"), f"{label}.{category}[{index}].id")
            argv = row.get("argv")
            if (
                not isinstance(argv, list)
                or not argv
                or any(not isinstance(item, str) or not item.strip() for item in argv)
            ):
                raise Step7ProfileError(
                    f"{label}.{category}[{index}].argv must be a non-empty list of strings"
                )
            if row.get("shell", False) is not False:
                raise Step7ProfileError(
                    f"{label}.{category}[{index}].shell must be false"
                )
            cwd = _safe_relative(
                row.get("cwd", "."),
                f"{label}.{category}[{index}].cwd",
                root_allowed=True,
            )
            category_rows.append(
                {
                    "id": command_id,
                    "argv": [str(item) for item in argv],
                    "cwd": cwd,
                    "timeout_seconds": _positive_int(
                        row.get("timeout_seconds"),
                        f"{label}.{category}[{index}].timeout_seconds",
                    ),
                    "shell": False,
                }
            )
            ids.append(command_id)
            if command_id in all_ids:
                raise Step7ProfileError(f"duplicate command id: {command_id}")
            all_ids.add(command_id)
        if ids != sorted(ids):
            raise Step7ProfileError(f"{label}.{category} must be sorted by command id")
        normalized[category] = category_rows
    return normalized


def _overlaps(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _normalize_policy(value: Any, label: str) -> dict[str, Any]:
    policy = _mapping(value, label)
    _reject_unknown(policy, _POLICY_KEYS, label)
    limits = _mapping(policy.get("diff_limits"), f"{label}.diff_limits")
    _reject_unknown(limits, _DIFF_LIMIT_KEYS, f"{label}.diff_limits")
    if set(limits) != set(_DIFF_LIMIT_KEYS):
        raise Step7ProfileError(f"{label}.diff_limits must contain every limit")
    output_limit = _positive_int(
        policy.get("max_output_bytes"), f"{label}.max_output_bytes"
    )
    if output_limit > 10_000_000:
        raise Step7ProfileError(f"{label}.max_output_bytes must not exceed 10000000")
    classification = _mapping(
        policy.get("path_classification"), f"{label}.path_classification"
    )
    _reject_unknown(
        classification, _CLASSIFICATION_KEYS, f"{label}.path_classification"
    )
    source = _prefixes(
        classification.get("source_prefixes"),
        f"{label}.path_classification.source_prefixes",
    )
    generated = _prefixes(
        classification.get("generated_prefixes"),
        f"{label}.path_classification.generated_prefixes",
    )
    allowed_generated = _prefixes(
        classification.get("allowed_generated_prefixes"),
        f"{label}.path_classification.allowed_generated_prefixes",
    )
    for source_prefix in source:
        for generated_prefix in generated:
            if _overlaps(source_prefix, generated_prefix):
                raise Step7ProfileError(
                    "source and generated path prefixes must not overlap: "
                    f"{source_prefix}, {generated_prefix}"
                )
    for prefix in allowed_generated:
        if not any(
            _overlaps(prefix, generated_prefix) for generated_prefix in generated
        ):
            raise Step7ProfileError(
                f"allowed generated prefix is outside generated prefixes: {prefix}"
            )
    return {
        "diff_limits": {
            field: _nonnegative_int(limits[field], f"{label}.diff_limits.{field}")
            for field in sorted(_DIFF_LIMIT_KEYS)
        },
        "max_output_bytes": output_limit,
        "path_classification": {
            "source_prefixes": source,
            "generated_prefixes": generated,
            "allowed_generated_prefixes": allowed_generated,
        },
    }


def _normalize_profile(value: Any, index: int) -> dict[str, Any]:
    label = f"profiles[{index}]"
    raw = _mapping(value, label)
    _reject_unknown(raw, _PROFILE_KEYS, label)
    profile: dict[str, Any] = {
        "profile_id": _text(raw.get("profile_id"), f"{label}.profile_id"),
        "profile_version": _text(
            raw.get("profile_version"), f"{label}.profile_version"
        ),
        "repository": _repository(raw.get("repository"), f"{label}.repository"),
        "enabled": raw.get("enabled"),
    }
    if not isinstance(profile["enabled"], bool):
        raise Step7ProfileError(f"{label}.enabled must be a boolean")
    if not profile["enabled"]:
        profile["unavailable_reason"] = _text(
            raw.get("unavailable_reason"), f"{label}.unavailable_reason"
        )
        forbidden = sorted(
            key for key in ("required_runner", "commands", "policy") if key in raw
        )
        if forbidden:
            raise Step7ProfileError(
                f"{label} disabled profile must not define: {', '.join(forbidden)}"
            )
    else:
        if "unavailable_reason" in raw:
            raise Step7ProfileError(
                f"{label} enabled profile must not define unavailable_reason"
            )
        if raw.get("required_runner") != REQUIRED_RUNNER:
            raise Step7ProfileError(f"{label}.required_runner must be sandbox")
        profile["required_runner"] = REQUIRED_RUNNER
        profile["commands"] = _normalize_commands(
            raw.get("commands"), f"{label}.commands"
        )
        profile["policy"] = _normalize_policy(raw.get("policy"), f"{label}.policy")
    profile["profile_sha256"] = profile_fingerprint(profile)
    return profile


def profile_fingerprint(profile: Mapping[str, Any]) -> str:
    payload = dict(profile)
    payload.pop("profile_sha256", None)
    return artifact_sha256(payload)


def normalize_profile_registry(value: Any) -> dict[str, Any]:
    root = _mapping(value, "profile registry")
    _reject_unknown(root, _ROOT_KEYS, "profile registry")
    if root.get("version") != REGISTRY_VERSION:
        raise Step7ProfileError(f"profile registry version must be {REGISTRY_VERSION}")
    rows = root.get("profiles")
    if not isinstance(rows, list) or not rows:
        raise Step7ProfileError("profile registry profiles must be a non-empty list")
    profiles = [_normalize_profile(row, index) for index, row in enumerate(rows)]
    keys = [(row["repository"], row["profile_id"]) for row in profiles]
    if keys != sorted(keys):
        raise Step7ProfileError("profiles must be sorted by repository and profile_id")
    repositories = [row["repository"] for row in profiles]
    ids = [row["profile_id"] for row in profiles]
    if len(repositories) != len(set(repositories)):
        raise Step7ProfileError("profile registry contains a duplicate repository")
    if len(ids) != len(set(ids)):
        raise Step7ProfileError("profile registry contains a duplicate profile_id")
    normalized = {"version": REGISTRY_VERSION, "profiles": profiles}
    normalized["registry_sha256"] = artifact_sha256(normalized)
    return normalized


def load_profile_registry(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        document = yaml.load(
            source.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader
        )
    except (OSError, yaml.YAMLError) as exc:
        raise Step7ProfileError(
            f"unable to load Step 7 profiles: {source}: {exc}"
        ) from exc
    return normalize_profile_registry(document)


def select_profile(
    registry: Mapping[str, Any], repository: str
) -> dict[str, Any] | None:
    identity = _repository(repository, "repository")
    for profile in registry.get("profiles", []):
        if isinstance(profile, Mapping) and profile.get("repository") == identity:
            return dict(profile)
    return None


def _matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def materialize_path_policy(
    profile: Mapping[str, Any], paths: list[str]
) -> dict[str, Any]:
    classification = profile["policy"]["path_classification"]
    source_paths: list[str] = []
    generated_paths: list[str] = []
    allowed_generated_paths: list[str] = []
    for path in sorted(paths):
        safe_path = _safe_relative(path, "patch path")
        source = any(
            _matches(safe_path, prefix) for prefix in classification["source_prefixes"]
        )
        generated = any(
            _matches(safe_path, prefix)
            for prefix in classification["generated_prefixes"]
        )
        if source == generated:
            state = "ambiguous" if source else "unclassified"
            raise Step7ProfileError(f"patch path is {state}: {safe_path}")
        if source:
            source_paths.append(safe_path)
        else:
            generated_paths.append(safe_path)
            if any(
                _matches(safe_path, prefix)
                for prefix in classification["allowed_generated_prefixes"]
            ):
                allowed_generated_paths.append(safe_path)
    return {
        "diff_limits": dict(profile["policy"]["diff_limits"]),
        "max_output_bytes": profile["policy"]["max_output_bytes"],
        "generated_file_policy": {
            "mode": "allowlist" if generated_paths else "reject",
            "generated_paths": generated_paths,
            "source_paths": source_paths,
            "allowed_generated_paths": allowed_generated_paths,
            "unknown_status": "fail",
        },
    }


__all__ = [
    "REGISTRY_VERSION",
    "REQUIRED_RUNNER",
    "Step7ProfileError",
    "load_profile_registry",
    "materialize_path_policy",
    "normalize_profile_registry",
    "profile_fingerprint",
    "select_profile",
]
