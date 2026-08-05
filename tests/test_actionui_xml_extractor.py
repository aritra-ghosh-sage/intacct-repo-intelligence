from parser.actionui.xml_extractor import extract_actionui_xml_facts


def test_extracts_glbatch_style_fields_events_and_direct_calls() -> None:
    source = b"""<?xml version='1.0'?>
<ROOT>
  <view>
    <events><load>onLoadFunctionCalls();</load></events>
    <field>BATCHNO</field>
    <field fullname="IA.BOOK"><path>BOOKTYPE</path><events><change>reloadJournals(this);</change></events></field>
    <field path="JOURNAL"><events><change>toggleBillable(this.meta); showHideEReportingSection();</change></events></field>
    <field><path>BATCH_DATE</path><events><change>handleBatchDateChange(this.meta);</change></events></field>
  </view>
</ROOT>
"""

    result = extract_actionui_xml_facts(source, "app/source/gl/glbatch_form.xml")

    assert [(field.field_name, field.field_path) for field in result.fields] == [
        ("BATCHNO", "BATCHNO"),
        ("IA.BOOK", "BOOKTYPE"),
        ("JOURNAL", "JOURNAL"),
        ("BATCH_DATE", "BATCH_DATE"),
    ]
    assert [(event.event_name, event.evidence) for event in result.events] == [
        ("load", "onLoadFunctionCalls();"),
        ("change", "reloadJournals(this);"),
        ("change", "toggleBillable(this.meta); showHideEReportingSection();"),
        ("change", "handleBatchDateChange(this.meta);"),
    ]
    assert [call.callable_name for call in result.event_calls] == [
        "onLoadFunctionCalls",
        "reloadJournals",
        "toggleBillable",
        "showHideEReportingSection",
        "handleBatchDateChange",
    ]
    assert len(result.artifacts) == 1
    assert result.diagnostics == ()


def test_extracts_ap_print_checks_style_event_calls_and_path_precedence() -> None:
    source = b"""<?xml version='1.0'?>
<ROOT>
  <view>
    <events><load>search();printChecks();</load></events>
    <field path="FILTERS"><path>IGNORED_CHILD_PATH</path></field>
    <field><path>CHECKLIST</path><events><change>refreshScreen();</change></events></field>
    <field>CHECKINGACCOUNT</field>
    <field><path>SELECTED</path><events><change>gridCheckBoxHandler(\"CHECKS\", this, \"SELECTED\");</change></events></field>
  </view>
</ROOT>
"""

    result = extract_actionui_xml_facts(source, "app/source/apar/apprintchecks_form.xml")

    assert [(field.field_name, field.field_path) for field in result.fields] == [
        ("FILTERS", "FILTERS"),
        ("CHECKLIST", "CHECKLIST"),
        ("CHECKINGACCOUNT", "CHECKINGACCOUNT"),
        ("SELECTED", "SELECTED"),
    ]
    assert [call.callable_name for call in result.event_calls] == [
        "search",
        "printChecks",
        "refreshScreen",
        "gridCheckBoxHandler",
    ]


def test_extracts_xinclude_without_expanding_it() -> None:
    source = b"""<?xml version='1.0'?>
<ROOT xmlns:xi="http://www.w3.org/2003/XInclude">
  <xi:include href="glposting_grid.xml" />
</ROOT>
"""

    result = extract_actionui_xml_facts(source, "app/source/apar/apbill_form.xml")

    assert [(include.included_path, include.start_line) for include in result.includes] == [
        ("glposting_grid.xml", 3)
    ]
    assert result.fields == ()


def test_ignores_non_direct_events_and_member_calls() -> None:
    source = b"""<?xml version='1.0'?>
<ROOT>
  <events><change>save(); this.meta.refresh(); "notACall()";</change></events>
  <wrapper><events><change><nested>ignored();</nested></change></events></wrapper>
</ROOT>
"""

    result = extract_actionui_xml_facts(source, "app/source/gl/example_form.xml")

    assert [(event.event_name, event.evidence) for event in result.events] == [
        ("change", 'save(); this.meta.refresh(); "notACall()";'),
        ("change", "<change>"),
    ]
    assert [call.callable_name for call in result.event_calls] == ["save"]


def test_malformed_xml_returns_no_partial_facts() -> None:
    source = b"""<?xml version='1.0'?>
<ROOT><field>BATCHNO</field><events><load>onLoad();</load></ROOT>
"""

    result = extract_actionui_xml_facts(source, "app/source/gl/broken_form.xml")

    assert result.artifacts == ()
    assert result.fields == ()
    assert result.includes == ()
    assert result.events == ()
    assert result.event_calls == ()
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["actionui.xml.parse_error"]
    assert result.diagnostics[0].severity == "warning"


def test_document_type_is_rejected_without_partial_facts() -> None:
    source = b"""<?xml version='1.0'?>
<!DOCTYPE ROOT [<!ENTITY unsafe "x">]>
<ROOT><field>&unsafe;</field></ROOT>
"""

    result = extract_actionui_xml_facts(source, "app/source/gl/doctype_form.xml")

    assert result.fields == ()
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["actionui.xml.parse_error"]
