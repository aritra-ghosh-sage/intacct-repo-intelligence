from dataclasses import FrozenInstanceError

import pytest

from parser.actionui.model import Diagnostic, LoaderFact
from parser.actionui.php_loader_extractor import extract_php_loader_facts


def test_extracts_literal_and_assigned_actionui_loader_values() -> None:
    source = b'''<?php
class GLBatchEditor extends FormEditor {
    protected function getMetadataKeyName(&$params) {
        $name = 'glbatch_form.pxml';
        return $name;
    }
    protected function getJavaScriptFileNames() {
        return array('../resources/js/glbatch.js', '../resources/js/helper.js');
    }
}
'''

    result = extract_php_loader_facts(source, "app/source/gl/GLBatchEditor.cls")

    assert [(fact.loader_kind, fact.value_kind, fact.value) for fact in result.loaders] == [
        ("form", "assignment", "glbatch_form.pxml"),
        ("script", "array", "../resources/js/glbatch.js"),
        ("script", "array", "../resources/js/helper.js"),
    ]
    assert result.diagnostics == ()


def test_records_parent_loader_call_without_evaluating_it() -> None:
    source = b'''<?php
class GLBatchEditor extends FormEditor {
    protected function getMetadataKeyName(&$params) {
        return parent::getMetadataKeyName($params);
    }
}
'''

    result = extract_php_loader_facts(source, "app/source/gl/GLBatchEditor.cls")

    assert len(result.loaders) == 1
    fact = result.loaders[0]
    assert fact.loader_kind == "form"
    assert fact.value_kind == "direct_call"
    assert fact.value == "parent::getMetadataKeyName($params)"
    assert result.diagnostics == ()


def test_records_parent_loader_call_assigned_before_a_return() -> None:
    source = b'''<?php
class GLBatchEditor extends FormEditor {
    protected function getMetadataKeyName(&$params) {
        $xmlName = parent::getMetadataKeyName($params);
        return $xmlName;
    }
}
'''

    result = extract_php_loader_facts(source, "app/source/gl/GLBatchEditor.cls")

    assert [(fact.value_kind, fact.value) for fact in result.loaders] == [
        ("direct_call", "parent::getMetadataKeyName($params)"),
    ]
    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        "actionui.php.dynamic_return",
    ]


def test_rejects_dynamic_values_without_guessing() -> None:
    source = b'''<?php
class GLBatchEditor extends FormEditor {
    protected function getMetadataKeyName(&$params) {
        $name = $params['entity'] . '_form.pxml';
        return $name;
    }
    protected function getJavaScriptFileNames() {
        return array('../resources/js/' . $script);
    }
}
'''

    result = extract_php_loader_facts(source, "app/source/gl/GLBatchEditor.cls")

    assert result.loaders == ()
    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        "actionui.php.dynamic_assignment",
        "actionui.php.dynamic_return",
        "actionui.php.dynamic_return",
    ]


def test_reassignment_and_control_flow_do_not_reuse_or_infer_values() -> None:
    source = b'''<?php
class GLBatchEditor extends FormEditor {
    protected function getMetadataKeyName(&$params) {
        $name = 'old_form.pxml';
        if ($params['legacy']) {
            $name = 'legacy_form.pxml';
        }
        $name = $params['entity'];
        return $name;
    }
}
'''

    result = extract_php_loader_facts(source, "app/source/gl/GLBatchEditor.cls")

    assert result.loaders == ()
    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        "actionui.php.unsupported_control_flow",
        "actionui.php.dynamic_assignment",
        "actionui.php.dynamic_return",
    ]


def test_parse_error_prevents_loader_fact_emission() -> None:
    source = b'''<?php
class GLBatchEditor extends FormEditor {
    protected function getJavaScriptFileNames() {
        return array('../resources/js/glbatch.js';
    }
}
'''

    result = extract_php_loader_facts(source, "app/source/gl/GLBatchEditor.cls")

    assert result.loaders == ()
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["actionui.php.parse_error"]


def test_model_facts_are_frozen() -> None:
    fact = LoaderFact(
        source_file="app/source/gl/GLBatchEditor.cls",
        class_name="GLBatchEditor",
        method_name="getMetadataKeyName",
        loader_kind="form",
        value_kind="literal",
        value="glbatch_form.pxml",
        start_line=1,
        end_line=1,
        evidence="return 'glbatch_form.pxml';",
    )
    diagnostic = Diagnostic(
        code="actionui.php.dynamic_return",
        message="dynamic",
        source_file="app/source/gl/GLBatchEditor.cls",
        start_line=1,
        end_line=1,
    )

    with pytest.raises(FrozenInstanceError):
        fact.value = "other_form.pxml"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        diagnostic.code = "other"  # type: ignore[misc]
