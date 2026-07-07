# Phase 2D.1 Validation Report

## xslt_coverage

```json
{
  "xslt_file_count": 301,
  "xslt_mapping_count": 25,
  "status": "PASS"
}
```

## ui_companion_coverage

```json
{
  "total_entities": 1807,
  "entities_with_ui_companions": 940,
  "coverage_percent": 52.01992252351965,
  "source_table": "ui_companions"
}
```

## openapi_linkage

```json
{
  "total_openapispec_files": 3853,
  "linked_files": 1239,
  "linkage_percent": 32.156760965481446,
  "threshold_percent": 30.0,
  "status": "PASS"
}
```

## entity_recall_v2

```json
{
  "gold_size": 54,
  "discovered_size": 1807,
  "matched": 2,
  "missing": [
    "APAccountlabel",
    "APAdjustmentbatch",
    "APAdjustmentitem",
    "APAdjustmentitemreverse",
    "APAdjustmentreverse",
    "APAdjustmenttaxentry",
    "APAdminapproverpick",
    "APAdvanceapproval",
    "APAdvanceitem",
    "APAdvancerequest",
    "APAdvancereverse",
    "APAgegraph",
    "APAmortizationschedule",
    "APAmortizationscheduleentry",
    "APAmortizationtemplate",
    "APApprovaldelegate",
    "APApprovaldelegatedetail",
    "APApprovalpolicy",
    "APApprovalpolicydetail",
    "APApprovalrule",
    "APApprovalruledetail",
    "APApprovalruleset",
    "APApproverdelegategrouppick",
    "Account",
    "AccountAllocation",
    "AccountAllocationBasis",
    "AccountAllocationBasisAdjustmentBook",
    "AccountAllocationGroup",
    "AccountAllocationGroupMember",
    "AccountAllocationReverse",
    "AccountAllocationRun",
    "AccountAllocationSource",
    "AccountAllocationSourceAdjustmentBook",
    "AccountAllocationTarget",
    "AccountBalance",
    "AccountBalanceByDimension",
    "AccountCategory",
    "Adminapbillapproverpick",
    "Agingentry",
    "Agingsetup",
    "BSActivityLog",
    "BSInvoice",
    "BSInvoiceLineItem",
    "BSOrderLineItem",
    "BSPriceList",
    "BSPriceListLine",
    "BSProduct",
    "BSSubscription",
    "Billingclientcompany",
    "BsInvoiceedit",
    "BsInvoicelist",
    "Bsterritory"
  ],
  "recall_percent": 3.7037037037037033,
  "status": "PASS"
}
```

