from pathlib import Path

from parser.actionui.loader_resolution import (
    InheritanceEdge,
    build_script_dependencies,
    extract_common_script_dependencies,
    resolve_inherited_loader_facts,
)
from parser.actionui.model import LoaderFact
from parser.actionui.php_loader_extractor import extract_php_loader_facts


def test_glbatch_script_loader_links_to_local_script() -> None:
    repo_root = Path("/Users/aritra.ghosh/projects/main")
    source_file = "app/source/gl/GLBatchEditor.cls"
    result = extract_php_loader_facts((repo_root / source_file).read_bytes(), source_file)

    scripts = build_script_dependencies(result.loaders, repo_root=repo_root)

    assert "app/resources/js/glbatch.js" in {fact.script_path for fact in scripts.dependencies}
    assert {fact.activation_state for fact in scripts.dependencies} == {"active"}


def test_parent_loader_uses_explicit_inherits_evidence_for_formeditor_convention() -> None:
    inherited = LoaderFact(
        source_file="app/source/gl/GLBatchEditor.cls",
        class_name="GLBatchEditor",
        method_name="getMetadataKeyName",
        loader_kind="form",
        value_kind="direct_call",
        value="parent::getMetadataKeyName($params)",
        start_line=1147,
        end_line=1147,
        evidence="parent::getMetadataKeyName($params)",
    )
    result = resolve_inherited_loader_facts(
        (inherited,),
        (InheritanceEdge("GLBatchEditor", "FormEditor", "app/source/gl/GLBatchEditor.cls", "class GLBatchEditor extends FormEditor"),),
        form_editor_source_file="app/source/core/FormEditor.cls",
    )

    assert [(fact.value_kind, fact.value, fact.source_file) for fact in result.loaders] == [
        ("form_editor_convention", "{entity}_form.pxml", "app/source/core/FormEditor.cls")
    ]
    assert result.diagnostics == ()


def test_common_includes_keep_branch_specific_scripts_conditional(tmp_path: Path) -> None:
    repo_root = tmp_path
    (repo_root / "app/resources/js").mkdir(parents=True)
    (repo_root / "app/resources/js/active.js").write_text("")
    (repo_root / "app/resources/js/legacy.js").write_text("")
    (repo_root / "app/resources/js/commented.js").write_text("")
    source = b'''<?php
function jsCommonIncludes() {
  // echo '<script src="../resources/js/commented.js"></script>';
  echo '<script src="../resources/js/active.js"></script>';
  if ($legacy) { jsCommon2012Includes(); }
}
function jsCommon2012Includes() {
  echo '<script src="../resources/js/legacy.js"></script>';
}
?>'''

    result = extract_common_script_dependencies(source, "app/source/common/html_header.inc", repo_root)

    assert {(fact.script_path, fact.activation_state) for fact in result.dependencies} == {
        ("app/resources/js/active.js", "active"),
        ("app/resources/js/legacy.js", "conditional"),
    }


def test_common_includes_accept_ast_proven_script_tag_interpolation(tmp_path: Path) -> None:
    repo_root = tmp_path
    (repo_root / "app/resources/qx/js").mkdir(parents=True)
    (repo_root / "app/resources/qx/js/i18n.js").write_text("")
    source = b'''<?php
function jsCommonIncludes() {
  ?>
  <script src="<? echo URLReplace::replaceRelativeURL('../resources/qx/js/i18n.js'); ?>"></script>
  <?php
}
?>'''

    result = extract_common_script_dependencies(source, "app/source/common/html_header.inc", repo_root)

    assert [(fact.script_path, fact.activation_state) for fact in result.dependencies] == [
        ("app/resources/qx/js/i18n.js", "active")
    ]


def test_unusable_script_paths_are_diagnostics(tmp_path: Path) -> None:
    loader = LoaderFact(
        source_file="app/source/gl/GLBatchEditor.cls",
        class_name="GLBatchEditor",
        method_name="getJavaScriptFileNames",
        loader_kind="script",
        value_kind="literal",
        value="https://example.test/script.js",
        start_line=1,
        end_line=1,
        evidence="return 'https://example.test/script.js';",
    )

    result = build_script_dependencies((loader,), repo_root=tmp_path)

    assert result.dependencies == ()
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["actionui.script.external_path"]
