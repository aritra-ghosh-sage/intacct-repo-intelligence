from parser.ui.nextgen import (
    ExplicitEntityMapping,
    NextGenSource,
    extract_nextgen_families,
)


def test_journal_entry_uses_explicit_glbatch_mapping_not_name_guess() -> None:
    result = extract_nextgen_families(
        (
            NextGenSource(
                source_file=(
                    "app/source/openapispec/gl/uimeta/"
                    "objects.general-ledger.journal-entry.s1.uimeta.yaml"
                ),
                text="uiLabel: IA.JOURNAL_ENTRY\n",
            ),
            NextGenSource(
                source_file=(
                    "app/source/openapispec/gl/views/"
                    "objects.general-ledger.journal-entry.systemfw1.s1.view.yaml"
                ),
                text="object: general-ledger/journal-entry\nname: IA.ALL\n",
            ),
        ),
        explicit_entity_mappings=(
            ExplicitEntityMapping(
                object_key="general-ledger/journal-entry",
                entity_name="GLBatch",
                source_file=(
                    "app/source/openapispec/gl/models/"
                    "objects.general-ledger.journal-entry.s1.schema.yaml"
                ),
                start_line=2,
                end_line=2,
                evidence="x-mappedTo: glbatch",
            ),
        ),
    )

    assert [family.family_key for family in result.families] == [
        "general-ledger/journal-entry"
    ]
    assert [artifact.artifact_kind for artifact in result.artifacts] == [
        "uimeta",
        "view",
    ]
    assert [(reference.entity_name, reference.resolution) for reference in result.entity_references] == [
        ("GLBatch", "explicit_mapping")
    ]
    assert all(reference.entity_name != "Journal" for reference in result.entity_references)
    assert result.entity_references[0].mapping_source_file.endswith(".schema.yaml")


def test_unknown_entity_mapping_remains_an_issue_without_filename_guessing() -> None:
    result = extract_nextgen_families(
        (
            NextGenSource(
                source_file=(
                    "app/source/openapispec/gl/uimeta/"
                    "objects.general-ledger.journal-entry.s1.uimeta.yaml"
                ),
                text="uiLabel: IA.JOURNAL_ENTRY\n",
            ),
        )
    )

    assert [(reference.entity_name, reference.resolution) for reference in result.entity_references] == [
        (None, "unresolved")
    ]
    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        "nextgen.entity_mapping.unresolved"
    ]


def test_viewmeta_uses_explicit_object_and_invalid_yaml_is_an_issue() -> None:
    result = extract_nextgen_families(
        (
            NextGenSource(
                source_file="app/source/openapispec/gl/viewmeta/example.viewmeta.yaml",
                text="object: general-ledger/journal-entry\n",
            ),
            NextGenSource(
                source_file="app/source/openapispec/gl/uimeta/bad.uimeta.yaml",
                text="fields: [\n",
            ),
        )
    )

    assert [family.family_key for family in result.families] == [
        "general-ledger/journal-entry"
    ]
    assert any(diagnostic.code == "nextgen.yaml.invalid" for diagnostic in result.diagnostics)


def test_duplicate_evidence_for_one_entity_is_not_ambiguous() -> None:
    result = extract_nextgen_families(
        (
            NextGenSource(
                source_file=(
                    "app/source/openapispec/gl/uimeta/"
                    "objects.general-ledger.journal-entry.s1.uimeta.yaml"
                ),
                text="uiLabel: IA.JOURNAL_ENTRY\n",
            ),
        ),
        explicit_entity_mappings=(
            ExplicitEntityMapping(
                "general-ledger/journal-entry",
                "GLBatch",
                "first.schema.yaml",
                2,
                2,
                "x-mappedTo: glbatch",
            ),
            ExplicitEntityMapping(
                "general-ledger/journal-entry",
                "GLBatch",
                "second.schema.yaml",
                2,
                2,
                "x-mappedTo: glbatch",
            ),
        ),
    )

    assert [(reference.entity_name, reference.resolution) for reference in result.entity_references] == [
        ("GLBatch", "explicit_mapping")
    ]
    assert not any("ambiguous" in diagnostic.code for diagnostic in result.diagnostics)
