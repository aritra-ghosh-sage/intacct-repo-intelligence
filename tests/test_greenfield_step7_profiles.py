from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from greenfield.step7_prepare import PREPARATION_ANALYSIS_KIND, prepare_step7
from greenfield.step7_profiles import (
    Step7ProfileError,
    load_profile_registry,
    materialize_path_policy,
    normalize_profile_registry,
)
from scripts import prepare_greenfield_step7
from tests.test_greenfield_step7 import _registry, _step6_report, _target_repo

ROOT = Path(__file__).resolve().parents[1]


def _raw_enabled_profile() -> dict:
    registry = _registry()
    registry.pop("registry_sha256")
    for profile in registry["profiles"]:
        profile.pop("profile_sha256")
    return registry


def test_repository_registry_loads_disabled_production_profiles() -> None:
    registry = load_profile_registry(ROOT / "config/greenfield_step7_profiles.yaml")
    assert [row["repository"] for row in registry["profiles"]] == [
        "intacct/ia-gwdata-gl",
        "intacct/ia-restapi-automation-tests",
    ]
    assert all(row["enabled"] is False for row in registry["profiles"])


def test_profile_normalization_is_deterministic() -> None:
    raw = _raw_enabled_profile()
    first = normalize_profile_registry(deepcopy(raw))
    second = normalize_profile_registry(deepcopy(raw))
    assert first == second
    assert first["registry_sha256"] == second["registry_sha256"]


@pytest.mark.parametrize(
    "duplicate_yaml",
    [
        "version: 1\nversion: 1\nprofiles: []\n",
        "version: 1\nprofiles:\n  - profile_id: duplicate\n    profile_id: duplicate\n",
    ],
)
def test_profile_loader_rejects_duplicate_yaml_keys(
    tmp_path: Path, duplicate_yaml: str
) -> None:
    path = tmp_path / "profiles.yaml"
    path.write_text(duplicate_yaml, encoding="utf-8")
    with pytest.raises(Step7ProfileError, match="found duplicate key"):
        load_profile_registry(path)


def test_tampered_normalized_profile_is_blocked(tmp_path: Path) -> None:
    _, revision = _target_repo(tmp_path)
    registry = _registry()
    registry["profiles"][0]["commands"]["targeted"][0]["argv"] = [
        sys.executable,
        "-c",
        "assert False",
    ]
    blocked = prepare_step7(_step6_report(revision), registry)
    assert blocked["status"] == "blocked"
    assert "fingerprint is invalid" in blocked["failures"][0]["detail"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: raw.update({"unknown": True}), "unknown fields"),
        (
            lambda raw: raw["profiles"][0]["commands"].pop("regression"),
            "must contain exactly",
        ),
        (
            lambda raw: raw["profiles"][0]["policy"]["path_classification"].update(
                {"generated_prefixes": ["features/generated"]}
            ),
            "must not overlap",
        ),
        (
            lambda raw: raw["profiles"][0]["commands"]["targeted"][0].update(
                {"cwd": "../outside"}
            ),
            "safe relative",
        ),
    ],
)
def test_profile_contract_rejects_unsafe_or_incomplete_values(
    mutation, message: str
) -> None:
    raw = _raw_enabled_profile()
    mutation(raw)
    with pytest.raises(Step7ProfileError, match=message):
        normalize_profile_registry(raw)


def test_duplicate_repository_and_profile_ids_are_rejected() -> None:
    raw = _raw_enabled_profile()
    duplicate = deepcopy(raw["profiles"][0])
    raw["profiles"].append(duplicate)
    with pytest.raises(Step7ProfileError, match="duplicate repository"):
        normalize_profile_registry(raw)


def test_path_policy_requires_exact_single_classification() -> None:
    profile = _registry()["profiles"][0]
    policy = materialize_path_policy(profile, ["features/example.feature"])
    assert policy["generated_file_policy"]["source_paths"] == [
        "features/example.feature"
    ]
    with pytest.raises(Step7ProfileError, match="unclassified"):
        materialize_path_policy(profile, ["outside/example.feature"])


def test_preparation_is_reproducible_and_disabled_profiles_block(
    tmp_path: Path,
) -> None:
    _, revision = _target_repo(tmp_path)
    step6 = _step6_report(revision)
    registry = _registry()
    first = prepare_step7(step6, registry)
    second = prepare_step7(step6, registry)
    assert first == second
    assert first["schema_version"] == "0.2"
    assert first["profile"]["sha256"] == registry["profiles"][0]["profile_sha256"]

    disabled = normalize_profile_registry(
        {
            "version": 1,
            "profiles": [
                {
                    "profile_id": "rest-step7",
                    "profile_version": "0.1",
                    "repository": step6["target"]["repository"],
                    "enabled": False,
                    "unavailable_reason": "owner approval missing",
                }
            ],
        }
    )
    blocked = prepare_step7(step6, disabled)
    assert blocked["analysis_kind"] == PREPARATION_ANALYSIS_KIND
    assert blocked["status"] == "blocked"
    assert blocked["request"] is None
    assert "owner approval missing" in blocked["failures"][0]["detail"]


def test_preparation_blocks_non_strict_step6() -> None:
    step6 = json.loads(
        (
            ROOT / "examples/greenfield/ia-app-pr-49156/replay/step6.report.json"
        ).read_text(encoding="utf-8")
    )
    blocked = prepare_step7(step6, _registry())
    assert blocked["status"] == "blocked"
    assert "strict Step 6" in blocked["failures"][0]["detail"]


def test_preparation_cli_writes_ready_request_atomically(tmp_path: Path) -> None:
    _, revision = _target_repo(tmp_path)
    step6_path = tmp_path / "step6.json"
    profiles_path = tmp_path / "profiles.yaml"
    output_path = tmp_path / "step7.request.json"
    step6_path.write_text(json.dumps(_step6_report(revision)), encoding="utf-8")
    profiles_path.write_text(
        yaml.safe_dump(_raw_enabled_profile(), sort_keys=False), encoding="utf-8"
    )
    assert (
        prepare_greenfield_step7.main(
            [
                "--step6-report",
                str(step6_path),
                "--profiles",
                str(profiles_path),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    artifact = json.loads(output_path.read_text(encoding="utf-8"))
    assert artifact["analysis_kind"] == "greenfield_pr_impact_step_7_request"
    assert not list(tmp_path.glob(f".{output_path.name}.*.tmp"))


def test_command_ids_are_globally_unique() -> None:
    raw = _raw_enabled_profile()
    raw["profiles"][0]["commands"]["regression"][0]["id"] = "targeted-check"
    with pytest.raises(Step7ProfileError, match="duplicate command id"):
        normalize_profile_registry(raw)


def test_enabled_profile_rejects_shell_commands() -> None:
    raw = _raw_enabled_profile()
    raw["profiles"][0]["commands"]["format"][0]["shell"] = True
    with pytest.raises(Step7ProfileError, match="shell must be false"):
        normalize_profile_registry(raw)


def test_profile_command_argv_remains_structured() -> None:
    registry = _registry(command_code="assert True")
    argv = registry["profiles"][0]["commands"]["targeted"][0]["argv"]
    assert argv[:2] == [sys.executable, "-c"]
