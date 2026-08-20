from __future__ import annotations

import json
import subprocess

from greenfield.semantic_contract import validate_index
from greenfield.semantic_index import (
    build_semantic_index,
    build_semantic_index_from_files,
)

REVISION = "a" * 40


def fixture_files() -> dict[str, str]:
    return {
        "app/source/gl/glaccount.ent": """<?php
require 'base.ent';
$kSchemas['glaccount'] = array(
    'module' => 'gl',
    'table' => 'glaccount',
    'schema' => array('ACCOUNTNO' => 'acct_no'),
    'children' => array(
        'category' => array('entity' => 'reportingcategory')
    ),
    'ownedobjects' => array(
        array('entity' => 'glaccttoreportingacmap')
    )
);
""",
        "app/source/gl/base.ent": "<?php\n// shared entity definitions\n",
        "app/source/openapispec/gl/models/objects.general-ledger.account.s1.schema.yaml": """title: general-ledger-account
x-mappedTo: glaccount
type: object
""",
        "app/source/openapispec/gl/uimeta/objects.general-ledger.account.s1.uimeta.yaml": """fields:
  - name: ACCOUNTNO
""",
        "app/source/openapispec/gl/views/objects.general-ledger.account.systemfw1.s1.view.yaml": """object: general-ledger/account
key: general-ledger/account::systemFW1
""",
        "app/source/openapispec/gl/paths/workflows.general-ledger.account.create.s1.api.yaml": """/workflows/general-ledger/account/create:
  post:
    operationId: createAccount
""",
        "app/source/gl/glaccount_form.xml": """<?xml version="1.0"?>
<ROOT><entity>glaccount</entity><field><name>ACCOUNTNO</name><path>ACCOUNTNO</path></field></ROOT>
""",
        "app/source/gl/GlAccountManager.cls": """class GlAccountManager {
    function create() {
        parent::__construct('glaccount');
    }
}
""",
        "app/source/import/csvimport_glaccount.cls": """class CsvImportGlAccount {
    function load() {
        include 'csv_metadata_glaccount.inc';
        $manager = getManager('glaccount');
    }
}
""",
        "app/source/import/csv_metadata_glaccount.inc": "$imeta = array('ACCOUNTNO' => 'ACCOUNTNO');\n",
        "app/source/dm/import/FlatFileGlaccount.cls": """class FlatFileGlaccount {
    function validate() {
        return Validator::createFlatfileRule('required');
    }
}
""",
        "app/source/legacy/unknown.xml": "<legacy><entity>glaccount</entity></legacy>\n",
        "app/source/gl/DynamicManager.cls": """class DynamicManager {
    function load($name) {
        return getManager($name);
    }
}
""",
    }


def edges(index: dict, kind: str) -> list[dict]:
    return [edge for edge in index["edges"] if edge["kind"] == kind]


def test_business_spine_resolves_exact_cross_layer_bridges() -> None:
    index = build_semantic_index_from_files(
        fixture_files(), repository="ia-main", revision=REVISION
    )

    assert validate_index(index) == []
    assert any(
        node["kind"] == "entity" and node["identity"] == "glaccount"
        for node in index["nodes"]
    )
    assert any(
        edge["resolution"] == "resolved_exact"
        for edge in edges(index, "api_object_entity")
    )
    assert any(
        edge["resolution"] == "resolved_exact"
        for edge in edges(index, "actionui_entity")
    )
    assert any(
        edge["resolution"] == "resolved_exact"
        for edge in edges(index, "nextgen_api_object")
    )
    assert any(
        edge["resolution"] == "resolved_exact"
        for edge in edges(index, "workflow_object")
    )
    assert any(
        edge["resolution"] == "resolved_exact" for edge in edges(index, "import_entity")
    )
    assert any(edge["kind"] == "entity_field" for edge in index["edges"])
    entity = next(
        node
        for node in index["nodes"]
        if node["kind"] == "entity" and node["identity"] == "glaccount"
    )
    assert any(
        fact["key"] == "category" for fact in entity["ent_facts"]["array_facts"]
    )
    assert any(edge["kind"] == "symbol_entity" for edge in index["edges"])


def test_ent_relationships_and_import_includes_remain_typed() -> None:
    index = build_semantic_index_from_files(
        fixture_files(), repository="ia-main", revision=REVISION
    )

    relation_targets = {
        edge["target_ref"]
        for edge in edges(index, "children_entity")
        + edges(index, "ownedobjects_entity")
    }
    assert relation_targets == {"reportingcategory", "glaccttoreportingacmap"}
    assert any(
        edge["kind"] == "import_include"
        and edge["resolution"] == "resolved_exact"
        and edge["target"] is not None
        for edge in index["edges"]
    )
    assert any(
        edge["kind"] == "ent_include" and edge["resolution"] == "resolved_exact"
        for edge in index["edges"]
    )
    assert any(
        diagnostic["code"] == "unsupported_xml_semantics"
        for diagnostic in index["diagnostics"]
    )


def test_openapi_documents_keep_schema_path_and_ui_types_distinct() -> None:
    index = build_semantic_index_from_files(
        fixture_files(), repository="ia-main", revision=REVISION
    )

    assert any(
        node["kind"] == "openapi_schema"
        and node["identity"].endswith(".schema.yaml")
        for node in index["nodes"]
    )
    assert any(node["kind"] == "openapi_path" for node in index["nodes"])
    assert not any(
        node["kind"] == "openapi_schema"
        and ("/uimeta/" in node["identity"] or "/paths/" in node["identity"])
        for node in index["nodes"]
    )
    assert any(edge["kind"] == "api_path_object" for edge in index["edges"])


def test_nextgen_keeps_entity_mapping_gap_when_api_object_has_no_mapping() -> None:
    index = build_semantic_index_from_files(
        {
            "app/source/openapispec/gl/paths/objects.general-ledger.account.s1.api.yaml": "{}\n",
            "app/source/openapispec/gl/uimeta/objects.general-ledger.account.s1.uimeta.yaml": "fields: []\n",
        },
        repository="ia-main",
        revision=REVISION,
    )

    assert any(
        edge["kind"] == "nextgen_api_object"
        and edge["resolution"] == "resolved_exact"
        for edge in index["edges"]
    )
    assert any(
        diagnostic["code"] == "nextgen.entity_mapping.unresolved"
        for diagnostic in index["diagnostics"]
    )


def test_dynamic_entity_and_flatfile_owner_are_not_inferred() -> None:
    index = build_semantic_index_from_files(
        fixture_files(), repository="ia-main", revision=REVISION
    )

    diagnostic_codes = {diagnostic["code"] for diagnostic in index["diagnostics"]}
    assert "symbol_entity_dynamic" in diagnostic_codes
    assert "flatfile_rule_owner_unresolved" in diagnostic_codes
    assert not any(
        edge["target_ref"] == "name" for edge in edges(index, "symbol_entity")
    )


def test_php_literal_propagation_does_not_cross_function_scopes() -> None:
    index = build_semantic_index_from_files(
        {
            "app/source/gl/glaccount.ent": "$kSchemas['glaccount'] = array();",
            "app/source/gl/Scoped.cls": """class Scoped {
    function assign() {
        $entity = 'glaccount';
    }
    function use() {
        return getManager($entity);
    }
}
""",
        },
        repository="ia-main",
        revision=REVISION,
    )

    assert not any(
        edge["kind"] == "symbol_entity" and edge["target_ref"] == "glaccount"
        for edge in index["edges"]
    )
    assert any(
        diagnostic["code"] == "symbol_entity_dynamic"
        for diagnostic in index["diagnostics"]
    )


def test_repeated_build_is_byte_deterministic() -> None:
    first = build_semantic_index_from_files(
        fixture_files(), repository="ia-main", revision=REVISION
    )
    second = build_semantic_index_from_files(
        fixture_files(), repository="ia-main", revision=REVISION
    )

    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second, sort_keys=True, separators=(",", ":")
    )


def test_git_builder_reads_the_pinned_commit_not_mutable_checkout(tmp_path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Greenfield Test"],
        check=True,
    )
    source = tmp_path / "app/source/gl/glaccount.ent"
    source.parent.mkdir(parents=True)
    source.write_text("$kSchemas['glaccount'] = array('module' => 'gl');\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "fixture"], check=True)
    revision = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
    ).strip()
    source.write_text("$kSchemas['wrong'] = array('module' => 'bad');\n")

    index = build_semantic_index(tmp_path, repository="ia-main", revision=revision)

    assert any(node["identity"] == "glaccount" for node in index["nodes"])
    assert not any(node["identity"] == "wrong" for node in index["nodes"])
