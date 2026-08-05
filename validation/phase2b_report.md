# Phase 2B.1 Validation Report

## Scan output checks

### entity_definitions companion_classes keys must match expected role set

OK — no issues found.

## Schema contract checks

### entity_nodes must not expose repo-local declaration columns

OK — no issues found.

### entity_occurrences missing expected columns

OK — no issues found.

## JSONL vs DB checks

### entity_nodes missing expected columns

OK — no issues found.

### entities in JSONL but missing in entity_occurrences

OK — no issues found.

### entities in entity_occurrences but missing in JSONL

- `CoCompanyConfigPasskeySuccessResponse`
- `CustomApproval`
- `CustomApprovalHistory`
- `CustomApprovalPolicy`
- `CustomApprovalPolicyLine`
- `PasskeyCredential`

### entity metadata mismatches between JSONL and entity_occurrences

OK — no issues found.

## Mapping/roots checks

### entity_mappings with unknown mapping_type

OK — no issues found.

### entity_mappings missing corresponding entity_roots rows

OK — no issues found.

### entity_roots role/weight/reason mismatches

OK — no issues found.

## Structural checks

### entity_nodes missing expected columns

OK — no issues found.

### entity_occurrences without ent_file

OK — no issues found.

### entity_mappings pointing to missing symbols

OK — no issues found.

### entity_roots not backed by entity_mappings

OK — no issues found.

### domain entities with 0 seed roots at weight >= 0.75

- `Apxbatch`
- `PASwebhookqueue`
- `PASwebhookqueuehistory`
- `Qdepositpayment`
- `Recordgeneratorqueue`
- `Recordgeneratorqueuehistory`
- `Stxexternalqueue`
- `Stxexternalqueuehistory`
- `Stxfileuploadqueue`
- `Stxfileuploadqueuehistory`
- `Stxwebhookqueue`
- `Stxwebhookqueuehistory`
- `Apautomatedtransactionfileupload`
- `AUTOMATEDTRANSACTIONCompany`
- `AUTOMATEDTRANSACTIONSetup`
- `Automationfeedbackext`
- `Poautomatedtransactionfileupload`
- `Attachment`
- `Entityusecode`
- `GLObals`
- `Stdrepeat`
- `IaSuiteUserApp`
- `Schemamap`
- `Girunsummary`
- `GLObalrunobjectsummary`
- `Runobject`
- `Runobjectdetail`
- `Runobjectgroup`
- `Runobjectsummary`
- `Base`
- `DDSHistory`
- `DDSJob`
- `DDSJobconfig`
- `DDSJobsummary`
- `DDSNotice`
- `DDSSchedule`
- `DDSSetup`
- `DDSSubscription`
- `Cashflowdigitalnetworksyncqueue`
- `Cashflowdigitalnetworksyncqueuehistory`
- `Digitalnetworksyncqueue`
- `Digitalnetworksyncqueuehistory`
- `Podigitalnetworksyncqueue`
- `Podigitalnetworksyncqueuehistory`
- `Expenseexternalqueue`
- `Expenseexternalqueuehistory`
- `Expensefileuploadqueue`
- `Expensefileuploadqueuehistory`
- `Expensewebhookqueue`
- `Expensewebhookqueuehistory`
- `Deprschrunsummary`
- `EmployeeAging`
- `Expensefileupload`
- `CONsbookdeletionqueue`
- `CONsbookdeletionqueuehistory`
- `Projectrunsummary`
- `Tsdocentryresolve`
- `Activitylog`
- `Appissue`
- `Apppageproperties`
- `Approvedapplication`
- `Cerpsetup`
- `CSPviolation`
- `PACkageownership`
- `PACkagepushhistory`
- `PACkagepushqueue`
- `PACkagerepository`
- `PACkagesubscriber`
- `PACkagetracking`
- `Ptapplication`
- `Ptnamespace`
- `Transactionmap`
- `Triggerlogs`
- `SFORCEQueue`
- `SFORCEQueuehistory`
- `SFORCESyncqueuejobui`
- `Loanstatementrunsummary`
- `Acctlabeltaxgroup`
- `Customeravataxsyncqueue`
- `Customeravataxsyncqueuehistory`
- `TAXSummary`
- `AppToolsAppToolsSuiteUserListResponse`
- `AppToolsAppToolsUserMyApplicationsResponse`
- `CoCompanyConfigAudiTrailListResponse`
- `CollaborationCollaborationSageIdInitializeUsersRequest`
- `CollaborationCollaborationSageIdInitializeUsersResponse`
- `CollaborationCollaborationSageIdUsersResponse`
- `CommonCommonAuthAllOperationsRequest`
- `CommonCommonAuthAllOperationsResponse`
- `CommonCommonAuthAllowedOperationsRequest`
- `CommonCommonAuthAllowedOperationsResponse`
- `CommonCoreComposite`
- `CommonCoreExport`
- `CommonCoreKeyValueMap`
- `CommonReportSubmittedStatus`
- `CoreAdminProvisioningGrantSlideInPermissionsRequest`
- `CoreAdminProvisioningGrantSlideInPermissionsResponse`
- `CoreCoreAllowedOperationsRequest`
- `CoreCoreAllowedOperationsResponse`
- `CoreCoreAsyncJobStatus`
- `CoreCoreAsyncOperationResponse`
- `CoreCoreRelatedObjectsQuery`
- `CoreIodlProxyFsComponentContentDeploy`
- `CoreIodlProxyFsComponentContent`
- `CoreIodlProxyFsComponentId`
- `CoreReportStatusCanceled`
- `CoreReportsStoredReports`
- `GlGeneralLedgerAccountingBooksCloseBooksResponse`
- `GlGeneralLedgerAccountingBooksLockStatutoryPeriodRequest`
- `GlGeneralLedgerAccountingBooksLockStatutoryPeriodResponse`
- `GlGeneralLedgerAccountingBooksOpenBooksResponse`
- `GlReportsGeneralLedgerAccountBalanceByDimension`
- `GlReportsGeneralLedgerAccountGroupHierarchy`
- `GlReportsGeneralLedgerBasic`
- `GlReportsGeneralLedgerDetails`
- `GlReportsGeneralLedgerReconciliation`
- `GlReportsGeneralLedgerTrialBalance`
- `InvReportsInventoryControlInventoryCosting`
- `InvReportsInventoryControlInventoryRegister`
- `InvReportsInventoryControlInventoryStatus`
- `InvReportsInventoryControlInventoryValuation`
- `InvReportsInventoryControlItemActivity`
- `InvReportsInventoryControlItemList`
- `InvReportsInventoryControlLotTracking`
- `InvReportsInventoryControlPhysicalInventory`
- `InvReportsInventoryControlSerialTracking`
- `PurchasingReportsPurchasingPriceList`
- `SalesReportsOrderEntryPriceList`
- `CoCompanyConfigPasskeySuccessResponse`

### non-domain entities with 0 seed roots at weight >= 0.75

OK — no issues found.

### unclassified entities with 0 seed roots at weight >= 0.75

OK — no issues found.

### non-domain entity counts

OK — no issues found.

### symbols acting as root for multiple entities

- `(659687, 2)`
- `(659914, 2)`
- `(660063, 2)`
- `(660073, 2)`
- `(660611, 2)`
- `(660624, 2)`
- `(660651, 2)`
- `(660755, 2)`
- `(661381, 2)`
- `(661493, 2)`
- `(661809, 2)`
- `(662094, 2)`
- `(662112, 2)`
- `(662183, 2)`
- `(662440, 2)`
- `(662467, 2)`
- `(662469, 2)`
- `(662472, 2)`
- `(662494, 2)`
- `(662689, 2)`
- `(662708, 2)`
- `(662962, 2)`
- `(662967, 2)`
- `(663416, 2)`
- `(663640, 2)`
- `(664007, 2)`
- `(664010, 2)`
- `(664567, 2)`
- `(664571, 2)`
- `(664591, 2)`
- `(664744, 2)`
- `(664819, 2)`
- `(664875, 2)`
- `(664885, 2)`
- `(664891, 2)`
- `(678968, 2)`
- `(679182, 2)`
- `(679498, 2)`
- `(679730, 2)`
- `(679924, 2)`
- `(680239, 2)`
- `(680426, 2)`
- `(680446, 2)`
- `(680641, 2)`
- `(680819, 2)`
- `(680825, 2)`
- `(680843, 2)`
- `(681838, 2)`
- `(681910, 2)`
- `(682294, 2)`
- `(682397, 2)`
- `(682575, 2)`
- `(682955, 2)`
- `(688688, 2)`
- `(689022, 2)`
- `(689023, 2)`
- `(689480, 2)`
- `(689530, 2)`
- `(689538, 2)`
- `(689800, 2)`
- `(689970, 2)`
- `(690021, 2)`
- `(690642, 2)`
- `(690838, 2)`
- `(691146, 2)`
- `(691169, 2)`
- `(698240, 2)`
- `(698290, 2)`
- `(698340, 2)`
- `(698350, 2)`
- `(698830, 2)`
- `(699362, 2)`
- `(699731, 2)`
- `(706144, 2)`
- `(707820, 2)`
- `(708865, 2)`
- `(715335, 2)`
- `(715390, 2)`
- `(715525, 2)`
- `(715755, 2)`
- `(715835, 2)`
- `(716024, 2)`
- `(716667, 2)`
- `(716769, 2)`
- `(718271, 2)`
- `(719291, 2)`
- `(719523, 2)`
- `(719575, 2)`
- `(719967, 2)`
- `(720534, 2)`
- `(720762, 2)`
- `(721124, 2)`
- `(721997, 2)`
- `(723313, 2)`
- `(723390, 2)`
- `(723468, 2)`
- `(723898, 2)`
- `(725075, 2)`
- `(728358, 2)`
- `(728539, 2)`
- `(728549, 2)`
- `(728952, 2)`
- `(729408, 2)`
- `(729641, 2)`
- `(729925, 2)`
- `(730288, 2)`
- `(730432, 2)`
- `(730563, 2)`
- `(730707, 2)`
- `(731148, 2)`
- `(731169, 2)`
- `(731503, 2)`
- `(731558, 2)`
- `(731985, 2)`
- `(731986, 2)`
- `(732089, 2)`
- `(732558, 2)`
- `(733199, 2)`
- `(733332, 2)`
- `(733506, 2)`
- `(733559, 2)`
- `(733624, 2)`
- `(734372, 2)`
- `(734851, 2)`
- `(735467, 2)`
- `(735494, 2)`
- `(735548, 2)`
- `(735592, 2)`
- `(735593, 2)`
- `(735634, 2)`
- `(735762, 2)`
- `(736128, 2)`
- `(737894, 2)`
- `(739252, 2)`
- `(739266, 2)`
- `(739446, 2)`
- `(739450, 2)`
- `(739864, 2)`
- `(739961, 2)`
- `(740550, 2)`
- `(740707, 2)`
- `(741191, 2)`
- `(741246, 2)`
- `(741435, 2)`
- `(741456, 2)`
- `(741792, 2)`
- `(742561, 2)`
- `(742890, 2)`
- `(742892, 2)`
- `(743176, 2)`
- `(743470, 2)`
- `(745316, 2)`
- `(745527, 2)`
- `(745733, 2)`
- `(745849, 2)`
- `(746437, 2)`
- `(746740, 2)`
- `(746744, 2)`
- `(747005, 2)`
- `(747090, 2)`
- `(747238, 2)`
- `(747471, 2)`
- `(747616, 2)`
- `(747670, 2)`
- `(748099, 2)`
- `(748200, 2)`
- `(748259, 2)`
- `(748751, 2)`
- `(748753, 2)`
- `(748794, 2)`
- `(748993, 2)`
- `(749240, 2)`
- `(749288, 2)`
- `(750018, 2)`
- `(750214, 2)`
- `(750528, 2)`
- `(750558, 2)`
- `(750786, 2)`
- `(751376, 2)`
- `(751680, 2)`
- `(751712, 2)`
- `(752267, 2)`
- `(752319, 2)`
- `(752378, 2)`
- `(752627, 2)`
- `(752769, 2)`
- `(753328, 2)`
- `(753637, 2)`
- `(753649, 2)`
- `(753977, 2)`
- `(754151, 2)`
- `(754273, 2)`
- `(754404, 2)`
- `(754829, 2)`
- `(755121, 2)`
- `(755290, 2)`
- `(755312, 2)`
- `(755618, 2)`
- `(755647, 2)`
- `(755657, 2)`

_(truncated — 247 total)_

## Filesystem checks

### entity_nodes missing expected columns

OK — no issues found.

### entity_occurrences with .ent files missing on disk

- `('BS_ActivityLog', 'app/source/Billing/BS_ActivityLog.ent')`
- `('BS_Invoice', 'app/source/Billing/BS_Invoice.ent')`
- `('BS_InvoiceLineItem', 'app/source/Billing/BS_InvoiceLineItem.ent')`
- `('BS_OrderLineItem', 'app/source/Billing/BS_OrderLineItem.ent')`
- `('BS_PriceList', 'app/source/Billing/BS_PriceList.ent')`
- `('BS_PriceListLine', 'app/source/Billing/BS_PriceListLine.ent')`
- `('BS_Product', 'app/source/Billing/BS_Product.ent')`
- `('BS_Subscription', 'app/source/Billing/BS_Subscription.ent')`
- `('BillingClientCompany', 'app/source/Billing/billingclientcompany.ent')`
- `('BS_InvoiceEdit', 'app/source/Billing/bs_invoiceedit.ent')`
- `('BS_InvoiceList', 'app/source/Billing/bs_invoicelist.ent')`
- `('BsTerritory', 'app/source/Billing/bsterritory.ent')`
- `('AdminAPBillApproverPick', 'app/source/apar/adminapbillapproverpick.ent')`
- `('AgingEntry', 'app/source/apar/agingentry.ent')`
- `('AgingSetup', 'app/source/apar/agingsetup.ent')`
- `('APAccountLabel', 'app/source/apar/apaccountlabel.ent')`
- `('APAdjustment', 'app/source/apar/apadjustment.ent')`
- `('APAdjustmentBatch', 'app/source/apar/apadjustmentbatch.ent')`
- `('APAdjustmentItem', 'app/source/apar/apadjustmentitem.ent')`
- `('APAdjustmentItemReverse', 'app/source/apar/apadjustmentitemreverse.ent')`
- `('APAdjustmentReverse', 'app/source/apar/apadjustmentreverse.ent')`
- `('APAdjustmentTaxEntry', 'app/source/apar/apadjustmenttaxentry.ent')`
- `('APAdminApproverPick', 'app/source/apar/apadminapproverpick.ent')`
- `('APAdvance', 'app/source/apar/apadvance.ent')`
- `('APAdvanceApproval', 'app/source/apar/apadvanceapproval.ent')`
- `('APAdvanceItem', 'app/source/apar/apadvanceitem.ent')`
- `('APAdvanceRequest', 'app/source/apar/apadvancerequest.ent')`
- `('APAdvanceReverse', 'app/source/apar/apadvancereverse.ent')`
- `('APAgeGraph', 'app/source/apar/apagegraph.ent')`
- `('APAmortizationSchedule', 'app/source/apar/apamortizationschedule.ent')`
- `('APAmortizationScheduleEntry', 'app/source/apar/apamortizationscheduleentry.ent')`
- `('APAmortizationTemplate', 'app/source/apar/apamortizationtemplate.ent')`
- `('APApprovalDelegate', 'app/source/apar/apapprovaldelegate.ent')`
- `('APApprovalDelegateDetail', 'app/source/apar/apapprovaldelegatedetail.ent')`
- `('APApprovalPolicy', 'app/source/apar/apapprovalpolicy.ent')`
- `('APApprovalPolicyDetail', 'app/source/apar/apapprovalpolicydetail.ent')`
- `('APApprovalRule', 'app/source/apar/apapprovalrule.ent')`
- `('APApprovalRuleDetail', 'app/source/apar/apapprovalruledetail.ent')`
- `('APApprovalRuleSet', 'app/source/apar/apapprovalruleset.ent')`
- `('APApproverDelegateGroupPick', 'app/source/apar/apapproverdelegategrouppick.ent')`
- `('APApproverGroupPick', 'app/source/apar/apapprovergrouppick.ent')`
- `('APApproverPick', 'app/source/apar/apapproverpick.ent')`
- `('APARRecurTaxEntry', 'app/source/apar/aparrecurtaxentry.ent')`
- `('APARTaxEntry', 'app/source/apar/apartaxentry.ent')`
- `('APBatch', 'app/source/apar/apbatch.ent')`
- `('APBill', 'app/source/apar/apbill.ent')`
- `('APBillAdjustment', 'app/source/apar/apbilladjustment.ent')`
- `('APBillApproval', 'app/source/apar/apbillapproval.ent')`
- `('APBillApproverPick', 'app/source/apar/apbillapproverpick.ent')`
- `('APBillBatch', 'app/source/apar/apbillbatch.ent')`
- `('APBillItem', 'app/source/apar/apbillitem.ent')`
- `('APBillItemReverse', 'app/source/apar/apbillitemreverse.ent')`
- `('APBillJointPayee', 'app/source/apar/apbilljointpayee.ent')`
- `('apbillpayment', 'app/source/apar/apbillpayment.ent')`
- `('APBillReverse', 'app/source/apar/apbillreverse.ent')`
- `('APBillTaxEntry', 'app/source/apar/apbilltaxentry.ent')`
- `('APCheckCopy', 'app/source/apar/apcheckcopy.ent')`
- `('APClosedBatch', 'app/source/apar/apclosedbatch.ent')`
- `('APCloseSummary', 'app/source/apar/apclosesummary.ent')`
- `('APDelegateApproverPick', 'app/source/apar/apdelegateapproverpick.ent')`
- `('APDetail', 'app/source/apar/apdetail.ent')`
- `('APDiscount', 'app/source/apar/apdiscount.ent')`
- `('APDiscountPymt', 'app/source/apar/apdiscountpymt.ent')`
- `('APDiscountPymtEntry', 'app/source/apar/apdiscountpymtentry.ent')`
- `('APDiscountPymtReverse', 'app/source/apar/apdiscountpymtreverse.ent')`
- `('APExchangeGainLoss', 'app/source/apar/apexchangegainloss.ent')`
- `('APExchangeGainLossEntry', 'app/source/apar/apexchangegainlossentry.ent')`
- `('APOPayments', 'app/source/apar/apopayments.ent')`
- `('APOpenBatch', 'app/source/apar/apopenbatch.ent')`
- `('APOpenSummary', 'app/source/apar/apopensummary.ent')`
- `('APOutsourcedChecks', 'app/source/apar/apoutsourcedchecks.ent')`
- `('APPayment', 'app/source/apar/appayment.ent')`
- `('appaymentitem', 'app/source/apar/appaymentitem.ent')`
- `('APPaymentRequest', 'app/source/apar/appaymentrequest.ent')`
- `('APPaymentRequestEntry', 'app/source/apar/appaymentrequestentry.ent')`
- `('APPostedAdvance', 'app/source/apar/appostedadvance.ent')`
- `('APPostedAdvanceEntry', 'app/source/apar/appostedadvanceentry.ent')`
- `('APPostedAdvanceReverse', 'app/source/apar/appostedadvancereverse.ent')`
- `('APPostedPayment', 'app/source/apar/appostedpayment.ent')`
- `('APPostedPaymentBatch', 'app/source/apar/appostedpaymentbatch.ent')`
- `('APPostedPaymentEntry', 'app/source/apar/appostedpaymententry.ent')`
- `('APPrintChecks', 'app/source/apar/apprintchecks.ent')`
- `('ApproveAdvances', 'app/source/apar/approveadvances.ent')`
- `('ApproveAPBill', 'app/source/apar/approveapbill.ent')`
- `('ApprovePayments', 'app/source/apar/approvepayments.ent')`
- `('ApproveVendor', 'app/source/apar/approvevendor.ent')`
- `('APPymt', 'app/source/apar/appymt.ent')`
- `('APPymtApproval', 'app/source/apar/appymtapproval.ent')`
- `('APPymtApproverPick', 'app/source/apar/appymtapproverpick.ent')`
- `('APPymtDetail', 'app/source/apar/appymtdetail.ent')`
- `('APPymtEntry', 'app/source/apar/appymtentry.ent')`
- `('APPymtReverse', 'app/source/apar/appymtreverse.ent')`
- `('APQuickCheckBatch', 'app/source/apar/apquickcheckbatch.ent')`
- `('APQuickPay', 'app/source/apar/apquickpay.ent')`
- `('APQuickPayEntry', 'app/source/apar/apquickpayentry.ent')`
- `('APRecord', 'app/source/apar/aprecord.ent')`
- `('APRecurBill', 'app/source/apar/aprecurbill.ent')`
- `('APRecurBillEntry', 'app/source/apar/aprecurbillentry.ent')`
- `('APRecurBillTaxEntry', 'app/source/apar/aprecurbilltaxentry.ent')`
- `('APSetup', 'app/source/apar/apsetup.ent')`
- `('APSTXFileupload', 'app/source/apar/apstxfileupload.ent')`
- `('APTerm', 'app/source/apar/apterm.ent')`
- `('Apxbatch', 'app/source/apar/apxbatch.ent')`
- `('ARAccountLabel', 'app/source/apar/araccountlabel.ent')`
- `('ARAdjustment', 'app/source/apar/aradjustment.ent')`
- `('ARAdjustmentBatch', 'app/source/apar/aradjustmentbatch.ent')`
- `('ARAdjustmentItem', 'app/source/apar/aradjustmentitem.ent')`
- `('ARAdjustmentItemReverse', 'app/source/apar/aradjustmentitemreverse.ent')`
- `('ARAdjustmentReverse', 'app/source/apar/aradjustmentreverse.ent')`
- `('ARAdjustmentTaxEntry', 'app/source/apar/aradjustmenttaxentry.ent')`
- `('ARAdvance', 'app/source/apar/aradvance.ent')`
- `('ARAdvanceItem', 'app/source/apar/aradvanceitem.ent')`
- `('ARAdvanceReverse', 'app/source/apar/aradvancereverse.ent')`
- `('ARAdvanceTaxEntry', 'app/source/apar/aradvancetaxentry.ent')`
- `('ARAgeGraph', 'app/source/apar/aragegraph.ent')`
- `('ARAmortizationSchedule', 'app/source/apar/aramortizationschedule.ent')`
- `('ARAmortizationScheduleEntry', 'app/source/apar/aramortizationscheduleentry.ent')`
- `('ARAmortizationTemplate', 'app/source/apar/aramortizationtemplate.ent')`
- `('ARBatch', 'app/source/apar/arbatch.ent')`
- `('ARClosedBatch', 'app/source/apar/arclosedbatch.ent')`
- `('ARCloseSummary', 'app/source/apar/arclosesummary.ent')`
- `('ARDetail', 'app/source/apar/ardetail.ent')`
- `('ARDiscount', 'app/source/apar/ardiscount.ent')`
- `('ARDiscountPymt', 'app/source/apar/ardiscountpymt.ent')`
- `('ARDiscountPymtEntry', 'app/source/apar/ardiscountpymtentry.ent')`
- `('ARElectronicPayment', 'app/source/apar/arelectronicpayment.ent')`
- `('ARExchangeGainLoss', 'app/source/apar/arexchangegainloss.ent')`
- `('ARExchangeGainLossEntry', 'app/source/apar/arexchangegainlossentry.ent')`
- `('ARInvoice', 'app/source/apar/arinvoice.ent')`
- `('ARInvoiceAdjustment', 'app/source/apar/arinvoiceadjustment.ent')`
- `('ARInvoiceBatch', 'app/source/apar/arinvoicebatch.ent')`
- `('ARInvoiceItem', 'app/source/apar/arinvoiceitem.ent')`
- `('ARInvoiceItemReverse', 'app/source/apar/arinvoiceitemreverse.ent')`
- `('arinvoicepayment', 'app/source/apar/arinvoicepayment.ent')`
- `('ARInvoiceReverse', 'app/source/apar/arinvoicereverse.ent')`
- `('ARInvoiceTaxEntry', 'app/source/apar/arinvoicetaxentry.ent')`
- `('ARIstaxAcctLabelPick', 'app/source/apar/aristaxacctlabelpick.ent')`
- `('ARItemAcctLabelPick', 'app/source/apar/aritemacctlabelpick.ent')`
- `('ARMultiCustomerPymt', 'app/source/apar/armulticustomerpymt.ent')`
- `('ARMultiCustomerPymtReverse', 'app/source/apar/armulticustomerpymtreverse.ent')`
- `('AROpenBatch', 'app/source/apar/aropenbatch.ent')`
- `('AROpenSummary', 'app/source/apar/aropensummary.ent')`
- `('AROtherSubtotalAcctLabelPick', 'app/source/apar/arothersubtotalacctlabelpick.ent')`
- `('ARPayment', 'app/source/apar/arpayment.ent')`
- `('ARPaymentBatch', 'app/source/apar/arpaymentbatch.ent')`
- `('ARPaymentDefault', 'app/source/apar/arpaymentdefault.ent')`
- `('ARPaymentItem', 'app/source/apar/arpaymentitem.ent')`
- `('ARPostedAdvance', 'app/source/apar/arpostedadvance.ent')`
- `('ARPostedAdvanceItem', 'app/source/apar/arpostedadvanceitem.ent')`
- `('ARPostedOverpayment', 'app/source/apar/arpostedoverpayment.ent')`
- `('ARPostedOverpaymentEntry', 'app/source/apar/arpostedoverpaymententry.ent')`
- `('ARPostedPayment', 'app/source/apar/arpostedpayment.ent')`
- `('ARPostedPaymentBatch', 'app/source/apar/arpostedpaymentbatch.ent')`
- `('ARPostedPaymentItem', 'app/source/apar/arpostedpaymentitem.ent')`
- `('ARPrintEmailPopup', 'app/source/apar/arprintemailpopup.ent')`
- `('ARPymt', 'app/source/apar/arpymt.ent')`
- `('ARPymtDetail', 'app/source/apar/arpymtdetail.ent')`
- `('ARPymtEntry', 'app/source/apar/arpymtentry.ent')`
- `('ARQuickDeposit', 'app/source/apar/arquickdeposit.ent')`
- `('ARQuickDepositBatch', 'app/source/apar/arquickdepositbatch.ent')`
- `('ARQuickDepositEntry', 'app/source/apar/arquickdepositentry.ent')`
- `('ARRecord', 'app/source/apar/arrecord.ent')`
- `('ARRecurInvoice', 'app/source/apar/arrecurinvoice.ent')`
- `('ARRecurInvoiceEntry', 'app/source/apar/arrecurinvoiceentry.ent')`
- `('ARRecurInvoiceTaxEntry', 'app/source/apar/arrecurinvoicetaxentry.ent')`
- `('ARRecurPayment', 'app/source/apar/arrecurpayment.ent')`
- `('ARRefundBatch', 'app/source/apar/arrefundbatch.ent')`
- `('ARSetup', 'app/source/apar/arsetup.ent')`
- `('ARSetupAtEntity', 'app/source/apar/arsetupatentity.ent')`
- `('ARSubtotalAcctLabelPick', 'app/source/apar/arsubtotalacctlabelpick.ent')`
- `('ARTerm', 'app/source/apar/arterm.ent')`
- `('Bbtemplateitem', 'app/source/apar/bbtemplateitem.ent')`
- `('bbtemplaterecs', 'app/source/apar/bbtemplaterecs.ent')`
- `('BillbackRecordGenerator', 'app/source/apar/billbackrecordgenerator.ent')`
- `('Billbacktemplate', 'app/source/apar/billbacktemplate.ent')`
- `('CheckRun', 'app/source/apar/checkrun.ent')`
- `('CheckRunDetail', 'app/source/apar/checkrundetail.ent')`
- `('CheckRunFilter', 'app/source/apar/checkrunfilter.ent')`
- `('CheckRunPick', 'app/source/apar/checkrunpick.ent')`
- `('CheckRunStorage', 'app/source/apar/checkrunstorage.ent')`
- `('CustAging', 'app/source/apar/custaging.ent')`
- `('CustAgingDetail', 'app/source/apar/custagingdetail.ent')`
- `('CustAgingHeader', 'app/source/apar/custagingheader.ent')`
- `('CustGLGroup', 'app/source/apar/custglgroup.ent')`
- `('CustMessage', 'app/source/apar/custmessage.ent')`
- `('Customer', 'app/source/apar/customer.ent')`
- `('CustomerAging_Report', 'app/source/apar/customeraging_report.ent')`
- `('CustomerBankAccount', 'app/source/apar/customerbankaccount.ent')`
- `('CustomerBankAccountPick', 'app/source/apar/customerbankaccountpick.ent')`
- `('CustomerEmailTemplate', 'app/source/apar/customeremailtemplate.ent')`
- `('CustomerEntityContacts', 'app/source/apar/customerentitycontacts.ent')`
- `('CustomerGroup', 'app/source/apar/customergroup.ent')`
- `('CustomerGrpMember', 'app/source/apar/customergrpmember.ent')`
- `('CustomerItemCrossRef', 'app/source/apar/customeritemcrossref.ent')`
- `('CustomerLettrage', 'app/source/apar/customerlettrage.ent')`
- `('CustomerNGroupPick', 'app/source/apar/customerngrouppick.ent')`
- `('CustomerPick', 'app/source/apar/customerpick.ent')`
- `('CustomerRefund', 'app/source/apar/customerrefund.ent')`
- `('CustomerRefundDetail', 'app/source/apar/customerrefunddetail.ent')`
- `('CustomerRefundEntry', 'app/source/apar/customerrefundentry.ent')`

_(truncated — 1855 total)_

### companion class files referenced in entity_mappings missing on disk

- `('BS_ActivityLog', 'manager', 'app/source/Billing/BS_ActivityLogManager.cls')`
- `('BS_Invoice', 'manager', 'app/source/Billing/BS_InvoiceManager.cls')`
- `('BS_InvoiceLineItem', 'manager', 'app/source/Billing/BS_InvoiceLineItemManager.cls')`
- `('BS_OrderLineItem', 'manager', 'app/source/Billing/BS_OrderLineItemManager.cls')`
- `('BS_PriceList', 'manager', 'app/source/Billing/BS_PriceListManager.cls')`
- `('BS_PriceList', 'editor', 'app/source/Billing/BS_PriceListEditor.cls')`
- `('BS_PriceList', 'lister', 'app/source/Billing/BS_PriceListLister.cls')`
- `('BS_PriceListLine', 'manager', 'app/source/Billing/BS_PriceListLineManager.cls')`
- `('BS_Product', 'manager', 'app/source/Billing/BS_ProductManager.cls')`
- `('BS_Product', 'lister', 'app/source/Billing/BS_ProductLister.cls')`
- `('BS_Product', 'picker', 'app/source/Billing/BS_ProductPicker.cls')`
- `('BS_Subscription', 'manager', 'app/source/Billing/BS_SubscriptionManager.cls')`
- `('BillingClientCompany', 'manager', 'app/source/Billing/BillingClientCompanyManager.cls')`
- `('BillingClientCompany', 'lister', 'app/source/Billing/BillingClientCompanyLister.cls')`
- `('BillingClientCompany', 'picker', 'app/source/Billing/BillingClientCompanyPicker.cls')`
- `('BS_InvoiceEdit', 'manager', 'app/source/Billing/BS_InvoiceEditManager.cls')`
- `('BS_InvoiceEdit', 'editor', 'app/source/Billing/BS_InvoiceEditEditor.cls')`
- `('BS_InvoiceList', 'manager', 'app/source/Billing/BS_InvoiceListManager.cls')`
- `('BS_InvoiceList', 'lister', 'app/source/Billing/BS_InvoiceListLister.cls')`
- `('BsTerritory', 'lister', 'app/source/Billing/BsTerritoryLister.cls')`
- `('AdminAPBillApproverPick', 'manager', 'app/source/apar/AdminAPBillApproverPickManager.cls')`
- `('AgingEntry', 'manager', 'app/source/apar/AgingEntryManager.cls')`
- `('AgingSetup', 'manager', 'app/source/apar/AgingSetupManager.cls')`
- `('AgingSetup', 'editor', 'app/source/apar/AgingSetupEditor.cls')`
- `('APAccountLabel', 'manager', 'app/source/apar/APAccountLabelManager.cls')`
- `('APAccountLabel', 'editor', 'app/source/apar/APAccountLabelEditor.cls')`
- `('APAccountLabel', 'lister', 'app/source/apar/APAccountLabelLister.cls')`
- `('APAccountLabel', 'picker', 'app/source/apar/APAccountLabelPicker.cls')`
- `('APAdjustment', 'manager', 'app/source/apar/APAdjustmentManager.cls')`
- `('APAdjustment', 'editor', 'app/source/apar/APAdjustmentEditor.cls')`
- `('APAdjustment', 'lister', 'app/source/apar/APAdjustmentLister.cls')`
- `('APAdjustment', 'allowed_operations_handler', 'app/source/apar/APAdjustmentAllowedOperationsHandler.cls')`
- `('APAdjustment', 'reverse_manager', 'app/source/apar/APAdjustmentReverseManager.cls')`
- `('APAdjustment', 'item_manager', 'app/source/apar/APAdjustmentItemManager.cls')`
- `('APAdjustment', 'batch_manager', 'app/source/apar/APAdjustmentBatchManager.cls')`
- `('APAdjustment', 'batch_picker', 'app/source/apar/APAdjustmentBatchPicker.cls')`
- `('APAdjustmentBatch', 'manager', 'app/source/apar/APAdjustmentBatchManager.cls')`
- `('APAdjustmentBatch', 'picker', 'app/source/apar/APAdjustmentBatchPicker.cls')`
- `('APAdjustmentItem', 'manager', 'app/source/apar/APAdjustmentItemManager.cls')`
- `('APAdjustmentItem', 'reverse_manager', 'app/source/apar/APAdjustmentItemReverseManager.cls')`
- `('APAdjustmentItemReverse', 'manager', 'app/source/apar/APAdjustmentItemReverseManager.cls')`
- `('APAdjustmentReverse', 'manager', 'app/source/apar/APAdjustmentReverseManager.cls')`
- `('APAdjustmentTaxEntry', 'manager', 'app/source/apar/APAdjustmentTaxEntryManager.cls')`
- `('APAdminApproverPick', 'manager', 'app/source/apar/APAdminApproverPickManager.cls')`
- `('APAdminApproverPick', 'picker', 'app/source/apar/APAdminApproverpickPicker.cls')`
- `('APAdvance', 'manager', 'app/source/apar/APAdvanceManager.cls')`
- `('APAdvance', 'editor', 'app/source/apar/APAdvanceEditor.cls')`
- `('APAdvance', 'lister', 'app/source/apar/APAdvanceLister.cls')`
- `('APAdvance', 'allowed_operations_handler', 'app/source/apar/APAdvanceAllowedOperationsHandler.cls')`
- `('APAdvance', 'approval_manager', 'app/source/apar/APAdvanceApprovalManager.cls')`
- `('APAdvance', 'reverse_manager', 'app/source/apar/APAdvanceReverseManager.cls')`
- `('APAdvance', 'item_manager', 'app/source/apar/APAdvanceItemManager.cls')`
- `('APAdvanceApproval', 'manager', 'app/source/apar/APAdvanceApprovalManager.cls')`
- `('APAdvanceItem', 'manager', 'app/source/apar/APAdvanceItemManager.cls')`
- `('APAdvanceRequest', 'manager', 'app/source/apar/APAdvanceRequestManager.cls')`
- `('APAdvanceReverse', 'manager', 'app/source/apar/APAdvanceReverseManager.cls')`
- `('APAgeGraph', 'manager', 'app/source/apar/APAgeGraphManager.cls')`
- `('APAgeGraph', 'lister', 'app/source/apar/APAgeGraphLister.cls')`
- `('APAmortizationSchedule', 'manager', 'app/source/apar/APAmortizationScheduleManager.cls')`
- `('APAmortizationSchedule', 'editor', 'app/source/apar/APAmortizationScheduleEditor.cls')`
- `('APAmortizationSchedule', 'entry_manager', 'app/source/apar/APAmortizationScheduleEntryManager.cls')`
- `('APAmortizationScheduleEntry', 'manager', 'app/source/apar/APAmortizationScheduleEntryManager.cls')`
- `('APAmortizationTemplate', 'manager', 'app/source/apar/APAmortizationTemplateManager.cls')`
- `('APAmortizationTemplate', 'editor', 'app/source/apar/APAmortizationTemplateEditor.cls')`
- `('APAmortizationTemplate', 'lister', 'app/source/apar/APAmortizationTemplateLister.cls')`
- `('APAmortizationTemplate', 'picker', 'app/source/apar/APAmortizationTemplatePicker.cls')`
- `('APApprovalDelegate', 'manager', 'app/source/apar/APApprovalDelegateManager.cls')`
- `('APApprovalDelegate', 'form_editor', 'app/source/apar/APApprovalDelegateFormEditor.cls')`
- `('APApprovalDelegateDetail', 'manager', 'app/source/apar/APApprovalDelegateDetailManager.cls')`
- `('APApprovalPolicy', 'manager', 'app/source/apar/APApprovalPolicyManager.cls')`
- `('APApprovalPolicy', 'form_editor', 'app/source/apar/APApprovalPolicyFormEditor.cls')`
- `('APApprovalPolicyDetail', 'manager', 'app/source/apar/APApprovalPolicyDetailManager.cls')`
- `('APApprovalRule', 'manager', 'app/source/apar/APApprovalRuleManager.cls')`
- `('APApprovalRule', 'form_editor', 'app/source/apar/APApprovalRuleFormEditor.cls')`
- `('APApprovalRuleDetail', 'manager', 'app/source/apar/APApprovalRuleDetailManager.cls')`
- `('APApprovalRuleSet', 'manager', 'app/source/apar/APApprovalRuleSetManager.cls')`
- `('APApprovalRuleSet', 'form_editor', 'app/source/apar/APApprovalRuleSetFormEditor.cls')`
- `('APApproverDelegateGroupPick', 'manager', 'app/source/apar/APApproverDelegateGroupPickManager.cls')`
- `('APApproverDelegateGroupPick', 'picker', 'app/source/apar/APApproverDelegateGrouppickPicker.cls')`
- `('APApproverGroupPick', 'manager', 'app/source/apar/APApproverGroupPickManager.cls')`
- `('APApproverGroupPick', 'picker', 'app/source/apar/APApproverGrouppickPicker.cls')`
- `('APApproverPick', 'manager', 'app/source/apar/APApproverPickManager.cls')`
- `('APApproverPick', 'picker', 'app/source/apar/APApproverpickPicker.cls')`
- `('APARRecurTaxEntry', 'manager', 'app/source/apar/APARRecurTaxEntryManager.cls')`
- `('APARTaxEntry', 'manager', 'app/source/apar/APARTaxEntryManager.cls')`
- `('APBatch', 'manager', 'app/source/apar/APBatchManager.cls')`
- `('APBatch', 'editor', 'app/source/apar/APBatchEditor.cls')`
- `('APBatch', 'lister', 'app/source/apar/APBatchLister.cls')`
- `('APBatch', 'picker', 'app/source/apar/APBatchPicker.cls')`
- `('APBatch', 'allowed_operations_handler', 'app/source/apar/ApbatchAllowedOperationsHandler.cls')`
- `('APBill', 'manager', 'app/source/apar/APBillManager.cls')`
- `('APBill', 'editor', 'app/source/apar/APBillEditor.cls')`
- `('APBill', 'lister', 'app/source/apar/APBillLister.cls')`
- `('APBill', 'picker', 'app/source/apar/APBillPicker.cls')`
- `('APBill', 'allowed_operations_handler', 'app/source/apar/APBillAllowedOperationsHandler.cls')`
- `('APBill', 'approval_manager', 'app/source/apar/APBillApprovalManager.cls')`
- `('APBill', 'reverse_manager', 'app/source/apar/APBillReverseManager.cls')`
- `('APBill', 'item_manager', 'app/source/apar/APBillItemManager.cls')`
- `('APBill', 'batch_manager', 'app/source/apar/APBillBatchManager.cls')`
- `('APBill', 'batch_picker', 'app/source/apar/APBillBatchPicker.cls')`
- `('APBillAdjustment', 'manager', 'app/source/apar/APBillAdjustmentManager.cls')`
- `('APBillAdjustment', 'picker', 'app/source/apar/APBillAdjustmentPicker.cls')`
- `('APBillApproval', 'manager', 'app/source/apar/APBillApprovalManager.cls')`
- `('APBillApproval', 'lister', 'app/source/apar/APBillApprovalLister.cls')`
- `('APBillApproverPick', 'manager', 'app/source/apar/APBillApproverPickManager.cls')`
- `('APBillBatch', 'manager', 'app/source/apar/APBillBatchManager.cls')`
- `('APBillBatch', 'picker', 'app/source/apar/APBillBatchPicker.cls')`
- `('APBillItem', 'manager', 'app/source/apar/APBillItemManager.cls')`
- `('APBillItem', 'allowed_operations_handler', 'app/source/apar/APBillItemAllowedOperationsHandler.cls')`
- `('APBillItem', 'reverse_manager', 'app/source/apar/APBillItemReverseManager.cls')`
- `('APBillItemReverse', 'manager', 'app/source/apar/APBillItemReverseManager.cls')`
- `('APBillJointPayee', 'manager', 'app/source/apar/APBillJointPayeeManager.cls')`
- `('apbillpayment', 'manager', 'app/source/apar/apbillpaymentManager.cls')`
- `('APBillReverse', 'manager', 'app/source/apar/APBillReverseManager.cls')`
- `('APBillTaxEntry', 'manager', 'app/source/apar/APBillTaxEntryManager.cls')`
- `('APCheckCopy', 'manager', 'app/source/apar/APCheckCopyManager.cls')`
- `('APCheckCopy', 'lister', 'app/source/apar/APCheckCopyLister.cls')`
- `('APClosedBatch', 'manager', 'app/source/apar/APClosedBatchManager.cls')`
- `('APClosedBatch', 'picker', 'app/source/apar/APClosedBatchPicker.cls')`
- `('APCloseSummary', 'manager', 'app/source/apar/APCloseSummaryManager.cls')`
- `('APCloseSummary', 'editor', 'app/source/apar/APCloseSummaryEditor.cls')`
- `('APDelegateApproverPick', 'manager', 'app/source/apar/APDelegateApproverPickManager.cls')`
- `('APDelegateApproverPick', 'picker', 'app/source/apar/APDelegateApproverpickPicker.cls')`
- `('APDetail', 'manager', 'app/source/apar/APDetailManager.cls')`
- `('APDiscount', 'manager', 'app/source/apar/APDiscountManager.cls')`
- `('APDiscount', 'editor', 'app/source/apar/APDiscountEditor.cls')`
- `('APDiscountPymt', 'manager', 'app/source/apar/APDiscountPymtManager.cls')`
- `('APDiscountPymt', 'reverse_manager', 'app/source/apar/APDiscountPymtReverseManager.cls')`
- `('APDiscountPymt', 'entry_manager', 'app/source/apar/APDiscountPymtEntryManager.cls')`
- `('APDiscountPymtEntry', 'manager', 'app/source/apar/APDiscountPymtEntryManager.cls')`
- `('APDiscountPymtReverse', 'manager', 'app/source/apar/APDiscountPymtReverseManager.cls')`
- `('APExchangeGainLoss', 'manager', 'app/source/apar/APExchangeGainLossManager.cls')`
- `('APExchangeGainLoss', 'entry_manager', 'app/source/apar/APExchangeGainLossEntryManager.cls')`
- `('APExchangeGainLossEntry', 'manager', 'app/source/apar/APExchangeGainLossEntryManager.cls')`
- `('APOPayments', 'manager', 'app/source/apar/APOPaymentsManager.cls')`
- `('APOpenBatch', 'manager', 'app/source/apar/APOpenBatchManager.cls')`
- `('APOpenBatch', 'picker', 'app/source/apar/APOpenBatchPicker.cls')`
- `('APOpenSummary', 'manager', 'app/source/apar/APOpenSummaryManager.cls')`
- `('APOpenSummary', 'editor', 'app/source/apar/APOpenSummaryEditor.cls')`
- `('APOutsourcedChecks', 'manager', 'app/source/apar/APOutsourcedChecksManager.cls')`
- `('APOutsourcedChecks', 'editor', 'app/source/apar/APOutsourcedChecksEditor.cls')`
- `('APPayment', 'manager', 'app/source/apar/APPaymentManager.cls')`
- `('APPayment', 'editor', 'app/source/apar/APPaymentEditor.cls')`
- `('APPayment', 'item_manager', 'app/source/apar/appaymentitemManager.cls')`
- `('appaymentitem', 'manager', 'app/source/apar/appaymentitemManager.cls')`
- `('APPaymentRequest', 'manager', 'app/source/apar/APPaymentRequestManager.cls')`
- `('APPaymentRequest', 'editor', 'app/source/apar/APPaymentRequestEditor.cls')`
- `('APPaymentRequest', 'lister', 'app/source/apar/APPaymentRequestLister.cls')`
- `('APPaymentRequest', 'entry_manager', 'app/source/apar/APPaymentRequestEntryManager.cls')`
- `('APPaymentRequestEntry', 'manager', 'app/source/apar/APPaymentRequestEntryManager.cls')`
- `('APPostedAdvance', 'manager', 'app/source/apar/APPostedAdvanceManager.cls')`
- `('APPostedAdvance', 'editor', 'app/source/apar/APPostedAdvanceEditor.cls')`
- `('APPostedAdvance', 'lister', 'app/source/apar/APPostedAdvanceLister.cls')`
- `('APPostedAdvance', 'reverse_manager', 'app/source/apar/APPostedAdvanceReverseManager.cls')`
- `('APPostedAdvance', 'entry_manager', 'app/source/apar/APPostedAdvanceEntryManager.cls')`
- `('APPostedAdvanceEntry', 'manager', 'app/source/apar/APPostedAdvanceEntryManager.cls')`
- `('APPostedAdvanceReverse', 'manager', 'app/source/apar/APPostedAdvanceReverseManager.cls')`
- `('APPostedPayment', 'manager', 'app/source/apar/APPostedPaymentManager.cls')`
- `('APPostedPayment', 'editor', 'app/source/apar/APPostedPaymentEditor.cls')`
- `('APPostedPayment', 'lister', 'app/source/apar/APPostedPaymentLister.cls')`
- `('APPostedPayment', 'batch_manager', 'app/source/apar/APPostedPaymentBatchManager.cls')`
- `('APPostedPayment', 'form_editor', 'app/source/apar/APPostedPaymentFormEditor.cls')`
- `('APPostedPayment', 'entry_manager', 'app/source/apar/APPostedPaymentEntryManager.cls')`
- `('APPostedPaymentBatch', 'manager', 'app/source/apar/APPostedPaymentBatchManager.cls')`
- `('APPostedPaymentBatch', 'lister', 'app/source/apar/APPostedPaymentBatchLister.cls')`
- `('APPostedPaymentEntry', 'manager', 'app/source/apar/APPostedPaymentEntryManager.cls')`
- `('APPrintChecks', 'manager', 'app/source/apar/APPrintChecksManager.cls')`
- `('APPrintChecks', 'editor', 'app/source/apar/APPrintChecksEditor.cls')`
- `('APPrintChecks', 'lister', 'app/source/apar/APPrintChecksLister.cls')`
- `('ApproveAdvances', 'manager', 'app/source/apar/ApproveAdvancesManager.cls')`
- `('ApproveAdvances', 'editor', 'app/source/apar/ApproveAdvancesEditor.cls')`
- `('ApproveAPBill', 'manager', 'app/source/apar/ApproveAPBillManager.cls')`
- `('ApproveAPBill', 'editor', 'app/source/apar/ApproveAPBillEditor.cls')`
- `('ApproveAPBill', 'lister', 'app/source/apar/ApproveAPBillLister.cls')`
- `('ApprovePayments', 'manager', 'app/source/apar/ApprovePaymentsManager.cls')`
- `('ApprovePayments', 'editor', 'app/source/apar/ApprovePaymentsEditor.cls')`
- `('ApprovePayments', 'lister', 'app/source/apar/ApprovePaymentsLister.cls')`
- `('ApproveVendor', 'manager', 'app/source/apar/ApproveVendorManager.cls')`
- `('ApproveVendor', 'editor', 'app/source/apar/ApproveVendorEditor.cls')`
- `('ApproveVendor', 'lister', 'app/source/apar/ApproveVendorLister.cls')`
- `('APPymt', 'manager', 'app/source/apar/APPymtManager.cls')`
- `('APPymt', 'editor', 'app/source/apar/APPymtEditor.cls')`
- `('APPymt', 'allowed_operations_handler', 'app/source/apar/APPymtAllowedOperationsHandler.cls')`
- `('APPymt', 'approval_manager', 'app/source/apar/APPymtApprovalManager.cls')`
- `('APPymt', 'reverse_manager', 'app/source/apar/APPymtReverseManager.cls')`
- `('APPymt', 'entry_manager', 'app/source/apar/APPymtEntryManager.cls')`
- `('APPymtApproval', 'manager', 'app/source/apar/APPymtApprovalManager.cls')`
- `('APPymtApproverPick', 'manager', 'app/source/apar/APPymtApproverPickManager.cls')`
- `('APPymtDetail', 'manager', 'app/source/apar/APPymtDetailManager.cls')`
- `('APPymtEntry', 'manager', 'app/source/apar/APPymtEntryManager.cls')`
- `('APPymtReverse', 'manager', 'app/source/apar/APPymtReverseManager.cls')`
- `('APQuickCheckBatch', 'manager', 'app/source/apar/APQuickCheckBatchManager.cls')`
- `('APQuickCheckBatch', 'picker', 'app/source/apar/APQuickCheckBatchPicker.cls')`
- `('APQuickPay', 'manager', 'app/source/apar/APQuickPayManager.cls')`
- `('APQuickPay', 'editor', 'app/source/apar/APQuickPayEditor.cls')`
- `('APQuickPay', 'lister', 'app/source/apar/APQuickPayLister.cls')`
- `('APQuickPay', 'entry_manager', 'app/source/apar/APQuickPayEntryManager.cls')`
- `('APQuickPayEntry', 'manager', 'app/source/apar/APQuickPayEntryManager.cls')`
- `('APRecord', 'manager', 'app/source/apar/APRecordManager.cls')`
- `('APRecurBill', 'manager', 'app/source/apar/APRecurBillManager.cls')`

_(truncated — 3770 total)_

## Repo vs DB coverage

### .ent files present in repo but missing in DB

OK — no issues found.

### .ent files present in DB but missing in repo

- `app/source/Billing/BS_ActivityLog.ent`
- `app/source/Billing/BS_Invoice.ent`
- `app/source/Billing/BS_InvoiceLineItem.ent`
- `app/source/Billing/BS_OrderLineItem.ent`
- `app/source/Billing/BS_PriceList.ent`
- `app/source/Billing/BS_PriceListLine.ent`
- `app/source/Billing/BS_Product.ent`
- `app/source/Billing/BS_Subscription.ent`
- `app/source/Billing/billingclientcompany.ent`
- `app/source/Billing/bs_invoiceedit.ent`
- `app/source/Billing/bs_invoicelist.ent`
- `app/source/Billing/bsterritory.ent`
- `app/source/apar/adminapbillapproverpick.ent`
- `app/source/apar/agingentry.ent`
- `app/source/apar/agingsetup.ent`
- `app/source/apar/apaccountlabel.ent`
- `app/source/apar/apadjustment.ent`
- `app/source/apar/apadjustmentbatch.ent`
- `app/source/apar/apadjustmentitem.ent`
- `app/source/apar/apadjustmentitemreverse.ent`
- `app/source/apar/apadjustmentreverse.ent`
- `app/source/apar/apadjustmenttaxentry.ent`
- `app/source/apar/apadminapproverpick.ent`
- `app/source/apar/apadvance.ent`
- `app/source/apar/apadvanceapproval.ent`
- `app/source/apar/apadvanceitem.ent`
- `app/source/apar/apadvancerequest.ent`
- `app/source/apar/apadvancereverse.ent`
- `app/source/apar/apagegraph.ent`
- `app/source/apar/apamortizationschedule.ent`
- `app/source/apar/apamortizationscheduleentry.ent`
- `app/source/apar/apamortizationtemplate.ent`
- `app/source/apar/apapprovaldelegate.ent`
- `app/source/apar/apapprovaldelegatedetail.ent`
- `app/source/apar/apapprovalpolicy.ent`
- `app/source/apar/apapprovalpolicydetail.ent`
- `app/source/apar/apapprovalrule.ent`
- `app/source/apar/apapprovalruledetail.ent`
- `app/source/apar/apapprovalruleset.ent`
- `app/source/apar/apapproverdelegategrouppick.ent`
- `app/source/apar/apapprovergrouppick.ent`
- `app/source/apar/apapproverpick.ent`
- `app/source/apar/aparrecurtaxentry.ent`
- `app/source/apar/apartaxentry.ent`
- `app/source/apar/apbatch.ent`
- `app/source/apar/apbill.ent`
- `app/source/apar/apbilladjustment.ent`
- `app/source/apar/apbillapproval.ent`
- `app/source/apar/apbillapproverpick.ent`
- `app/source/apar/apbillbatch.ent`
- `app/source/apar/apbillitem.ent`
- `app/source/apar/apbillitemreverse.ent`
- `app/source/apar/apbilljointpayee.ent`
- `app/source/apar/apbillpayment.ent`
- `app/source/apar/apbillreverse.ent`
- `app/source/apar/apbilltaxentry.ent`
- `app/source/apar/apcheckcopy.ent`
- `app/source/apar/apclosedbatch.ent`
- `app/source/apar/apclosesummary.ent`
- `app/source/apar/apdelegateapproverpick.ent`
- `app/source/apar/apdetail.ent`
- `app/source/apar/apdiscount.ent`
- `app/source/apar/apdiscountpymt.ent`
- `app/source/apar/apdiscountpymtentry.ent`
- `app/source/apar/apdiscountpymtreverse.ent`
- `app/source/apar/apexchangegainloss.ent`
- `app/source/apar/apexchangegainlossentry.ent`
- `app/source/apar/apopayments.ent`
- `app/source/apar/apopenbatch.ent`
- `app/source/apar/apopensummary.ent`
- `app/source/apar/apoutsourcedchecks.ent`
- `app/source/apar/appayment.ent`
- `app/source/apar/appaymentitem.ent`
- `app/source/apar/appaymentrequest.ent`
- `app/source/apar/appaymentrequestentry.ent`
- `app/source/apar/appostedadvance.ent`
- `app/source/apar/appostedadvanceentry.ent`
- `app/source/apar/appostedadvancereverse.ent`
- `app/source/apar/appostedpayment.ent`
- `app/source/apar/appostedpaymentbatch.ent`
- `app/source/apar/appostedpaymententry.ent`
- `app/source/apar/apprintchecks.ent`
- `app/source/apar/approveadvances.ent`
- `app/source/apar/approveapbill.ent`
- `app/source/apar/approvepayments.ent`
- `app/source/apar/approvevendor.ent`
- `app/source/apar/appymt.ent`
- `app/source/apar/appymtapproval.ent`
- `app/source/apar/appymtapproverpick.ent`
- `app/source/apar/appymtdetail.ent`
- `app/source/apar/appymtentry.ent`
- `app/source/apar/appymtreverse.ent`
- `app/source/apar/apquickcheckbatch.ent`
- `app/source/apar/apquickpay.ent`
- `app/source/apar/apquickpayentry.ent`
- `app/source/apar/aprecord.ent`
- `app/source/apar/aprecurbill.ent`
- `app/source/apar/aprecurbillentry.ent`
- `app/source/apar/aprecurbilltaxentry.ent`
- `app/source/apar/apsetup.ent`
- `app/source/apar/apstxfileupload.ent`
- `app/source/apar/apterm.ent`
- `app/source/apar/apxbatch.ent`
- `app/source/apar/araccountlabel.ent`
- `app/source/apar/aradjustment.ent`
- `app/source/apar/aradjustmentbatch.ent`
- `app/source/apar/aradjustmentitem.ent`
- `app/source/apar/aradjustmentitemreverse.ent`
- `app/source/apar/aradjustmentreverse.ent`
- `app/source/apar/aradjustmenttaxentry.ent`
- `app/source/apar/aradvance.ent`
- `app/source/apar/aradvanceitem.ent`
- `app/source/apar/aradvancereverse.ent`
- `app/source/apar/aradvancetaxentry.ent`
- `app/source/apar/aragegraph.ent`
- `app/source/apar/aramortizationschedule.ent`
- `app/source/apar/aramortizationscheduleentry.ent`
- `app/source/apar/aramortizationtemplate.ent`
- `app/source/apar/arbatch.ent`
- `app/source/apar/arclosedbatch.ent`
- `app/source/apar/arclosesummary.ent`
- `app/source/apar/ardetail.ent`
- `app/source/apar/ardiscount.ent`
- `app/source/apar/ardiscountpymt.ent`
- `app/source/apar/ardiscountpymtentry.ent`
- `app/source/apar/arelectronicpayment.ent`
- `app/source/apar/arexchangegainloss.ent`
- `app/source/apar/arexchangegainlossentry.ent`
- `app/source/apar/arinvoice.ent`
- `app/source/apar/arinvoiceadjustment.ent`
- `app/source/apar/arinvoicebatch.ent`
- `app/source/apar/arinvoiceitem.ent`
- `app/source/apar/arinvoiceitemreverse.ent`
- `app/source/apar/arinvoicepayment.ent`
- `app/source/apar/arinvoicereverse.ent`
- `app/source/apar/arinvoicetaxentry.ent`
- `app/source/apar/aristaxacctlabelpick.ent`
- `app/source/apar/aritemacctlabelpick.ent`
- `app/source/apar/armulticustomerpymt.ent`
- `app/source/apar/armulticustomerpymtreverse.ent`
- `app/source/apar/aropenbatch.ent`
- `app/source/apar/aropensummary.ent`
- `app/source/apar/arothersubtotalacctlabelpick.ent`
- `app/source/apar/arpayment.ent`
- `app/source/apar/arpaymentbatch.ent`
- `app/source/apar/arpaymentdefault.ent`
- `app/source/apar/arpaymentitem.ent`
- `app/source/apar/arpostedadvance.ent`
- `app/source/apar/arpostedadvanceitem.ent`
- `app/source/apar/arpostedoverpayment.ent`
- `app/source/apar/arpostedoverpaymententry.ent`
- `app/source/apar/arpostedpayment.ent`
- `app/source/apar/arpostedpaymentbatch.ent`
- `app/source/apar/arpostedpaymentitem.ent`
- `app/source/apar/arprintemailpopup.ent`
- `app/source/apar/arpymt.ent`
- `app/source/apar/arpymtdetail.ent`
- `app/source/apar/arpymtentry.ent`
- `app/source/apar/arquickdeposit.ent`
- `app/source/apar/arquickdepositbatch.ent`
- `app/source/apar/arquickdepositentry.ent`
- `app/source/apar/arrecord.ent`
- `app/source/apar/arrecurinvoice.ent`
- `app/source/apar/arrecurinvoiceentry.ent`
- `app/source/apar/arrecurinvoicetaxentry.ent`
- `app/source/apar/arrecurpayment.ent`
- `app/source/apar/arrefundbatch.ent`
- `app/source/apar/arsetup.ent`
- `app/source/apar/arsetupatentity.ent`
- `app/source/apar/arsubtotalacctlabelpick.ent`
- `app/source/apar/arterm.ent`
- `app/source/apar/bbtemplateitem.ent`
- `app/source/apar/bbtemplaterecs.ent`
- `app/source/apar/billbackrecordgenerator.ent`
- `app/source/apar/billbacktemplate.ent`
- `app/source/apar/checkrun.ent`
- `app/source/apar/checkrundetail.ent`
- `app/source/apar/checkrunfilter.ent`
- `app/source/apar/checkrunpick.ent`
- `app/source/apar/checkrunstorage.ent`
- `app/source/apar/custaging.ent`
- `app/source/apar/custagingdetail.ent`
- `app/source/apar/custagingheader.ent`
- `app/source/apar/custglgroup.ent`
- `app/source/apar/custmessage.ent`
- `app/source/apar/customer.ent`
- `app/source/apar/customeraging_report.ent`
- `app/source/apar/customerbankaccount.ent`
- `app/source/apar/customerbankaccountpick.ent`
- `app/source/apar/customeremailtemplate.ent`
- `app/source/apar/customerentitycontacts.ent`
- `app/source/apar/customergroup.ent`
- `app/source/apar/customergrpmember.ent`
- `app/source/apar/customeritemcrossref.ent`
- `app/source/apar/customerlettrage.ent`
- `app/source/apar/customerngrouppick.ent`
- `app/source/apar/customerpick.ent`
- `app/source/apar/customerrefund.ent`
- `app/source/apar/customerrefunddetail.ent`
- `app/source/apar/customerrefundentry.ent`

_(truncated — 1855 total)_

## Role distribution

### entity_roots role distribution

- `('manager', 1771)`
- `('editor', 605)`
- `('lister', 557)`
- `('picker', 360)`
- `('allowed_operations_handler', 156)`
- `('entry_manager', 79)`
- `('form_editor', 68)`
- `('pick_manager', 52)`
- `('pick_picker', 49)`
- `('item_manager', 26)`
- `('reverse_manager', 20)`
- `('batch_manager', 8)`
- `('approval_manager', 8)`
- `('batch_picker', 6)`
- `('entity_manager', 5)`

### manager roles with unexpectedly low weight

OK — no issues found.

## Ground truth checks

### ground truth summary (derived from entity_definitions + deterministic role weights)

- `entities_with_expected_roots=1769`
- `entities_with_perfect_match=1769`
- `total_expected_symbols=3347`
- `total_actual_symbols=3347`
- `total_correct_symbols=3347`
- `precision=1.0000`
- `recall=1.0000`

### ground-truth entities missing in entity_nodes

OK — no issues found.

### entities missing expected >=0.75 roots

OK — no issues found.

### entities with unexpected >=0.75 extra roots

OK — no issues found.

