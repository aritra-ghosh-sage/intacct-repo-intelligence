"""Application-owned deterministic Step 6 template registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import PurePosixPath
from typing import Any

from greenfield.step6_contract import Step6Error

TEMPLATE_VERSIONS = {
    "gwdata_gl_existing_case_update_v1": "0.1",
    "restapi_existing_case_update_v1": "0.1",
}


def _repository_name(value: str) -> str:
    return value.rsplit("/", 1)[-1]


def _paths(request: Mapping[str, Any]) -> list[str]:
    return [str(item["path"]) for item in request["target"]["files"]]


def _content_by_path(request: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(item["path"]): str(item["content"]) for item in request["target"]["files"]
    }


def _validate_gwdata(request: Mapping[str, Any]) -> None:
    if _repository_name(str(request["target"]["repository"])) != "ia-gwdata-gl":
        raise Step6Error("gwdata template target repository is invalid")
    paths = _paths(request)
    csv_paths = [
        path
        for path in paths
        if path.startswith("testdefinitions/") and path.endswith(".csv")
    ]
    xml_paths = [
        path
        for path in paths
        if path.startswith("testscripts/") and path.endswith(".xml")
    ]
    if len(csv_paths) != 1 or len(xml_paths) != 2 or len(paths) != 3:
        raise Step6Error("gwdata template requires one CSV and two paired XML files")
    request_names = {
        path.rsplit("/", 1)[-1][len("test_") : -len(".xml")]
        for path in xml_paths
        if path.rsplit("/", 1)[-1].startswith("test_")
    }
    response_names = {
        path.rsplit("/", 1)[-1][len("res_test_") : -len(".xml")]
        for path in xml_paths
        if path.rsplit("/", 1)[-1].startswith("res_test_")
    }
    if len(request_names) != 1 or len(response_names) != 1:
        raise Step6Error("gwdata request XML pair is missing")
    if request_names != response_names:
        raise Step6Error("gwdata request and response XML files are not paired")


def _validate_rest(request: Mapping[str, Any]) -> None:
    if (
        _repository_name(str(request["target"]["repository"]))
        != "ia-restapi-automation-tests"
    ):
        raise Step6Error("REST template target repository is invalid")
    paths = _paths(request)
    feature_paths = [
        path
        for path in paths
        if path.startswith("features/") and path.endswith(".feature")
    ]
    if len(feature_paths) != 1:
        raise Step6Error("REST template requires exactly one feature file")
    if any(not path.startswith("features/") for path in paths):
        raise Step6Error("REST template paths must remain under features/")
    if any(not path.endswith((".feature", ".json")) for path in paths):
        raise Step6Error("REST template supports only feature and JSON files")
    contents = _content_by_path(request)
    feature_content = contents[feature_paths[0]]
    for path in paths:
        if path.endswith(".json"):
            name = PurePosixPath(path).name
            if name not in feature_content and path not in feature_content:
                raise Step6Error(
                    f"REST JSON fixture is not referenced by feature: {path}"
                )


TemplateValidator = Callable[[Mapping[str, Any]], None]
TEMPLATE_VALIDATORS: dict[str, TemplateValidator] = {
    "gwdata_gl_existing_case_update_v1": _validate_gwdata,
    "restapi_existing_case_update_v1": _validate_rest,
}


def validate_template(request: Mapping[str, Any]) -> None:
    template_id = str(request["template"]["id"])
    validator = TEMPLATE_VALIDATORS.get(template_id)
    if validator is None:
        raise Step6Error(f"template is not registered: {template_id}")
    expected_version = TEMPLATE_VERSIONS[template_id]
    if request["template"].get("version") != expected_version:
        raise Step6Error(f"template {template_id} must use version {expected_version}")
    validator(request)


__all__ = ["TEMPLATE_VALIDATORS", "TEMPLATE_VERSIONS", "validate_template"]
