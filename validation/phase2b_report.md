# Phase 2B.1 Validation Report

## Scan output checks

### entity_definitions companion_classes keys must match expected role set

OK — no issues found.

## JSONL vs DB checks

### entity_nodes missing expected columns

OK — no issues found.

### entities in JSONL but missing in entity_nodes

OK — no issues found.

### entities in entity_nodes but missing in JSONL

OK — no issues found.

### entity metadata mismatches between JSONL and entity_nodes

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

### entity_nodes without ent_file

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

### non-domain entities with 0 seed roots at weight >= 0.75

OK — no issues found.

### unclassified entities with 0 seed roots at weight >= 0.75

OK — no issues found.

### non-domain entity counts

OK — no issues found.

### symbols acting as root for multiple entities

- `(94, 2)`
- `(380, 2)`
- `(3494, 2)`
- `(3636, 2)`
- `(3663, 2)`
- `(3947, 2)`
- `(3953, 2)`
- `(4034, 2)`
- `(4332, 2)`
- `(4333, 2)`
- `(4726, 2)`
- `(5448, 2)`
- `(5657, 2)`
- `(6240, 2)`
- `(6571, 2)`
- `(7269, 2)`
- `(7282, 2)`
- `(7406, 2)`
- `(7530, 2)`
- `(7785, 2)`
- `(7795, 2)`
- `(7828, 2)`
- `(8476, 2)`
- `(8531, 2)`
- `(8660, 2)`
- `(8790, 2)`
- `(9142, 2)`
- `(9153, 2)`
- `(9284, 2)`
- `(9286, 2)`
- `(9294, 2)`
- `(9315, 2)`
- `(9318, 2)`
- `(9426, 2)`
- `(9797, 2)`
- `(9887, 2)`
- `(9910, 2)`
- `(10308, 2)`
- `(10383, 2)`
- `(10463, 2)`
- `(10627, 2)`
- `(11226, 2)`
- `(11446, 2)`
- `(11640, 2)`
- `(11646, 2)`
- `(11698, 2)`
- `(12438, 2)`
- `(12440, 2)`
- `(12527, 2)`
- `(13248, 2)`
- `(13279, 2)`
- `(13880, 2)`
- `(13939, 2)`
- `(14220, 2)`
- `(14403, 2)`
- `(15314, 2)`
- `(15410, 2)`
- `(15458, 2)`
- `(15873, 2)`
- `(15904, 2)`
- `(16120, 2)`
- `(16133, 2)`
- `(16567, 2)`
- `(16787, 2)`
- `(17181, 2)`
- `(17196, 2)`
- `(17214, 2)`
- `(17390, 2)`
- `(17489, 2)`
- `(17592, 2)`
- `(17661, 2)`
- `(18194, 2)`
- `(18232, 2)`
- `(18253, 2)`
- `(18665, 2)`
- `(18951, 2)`
- `(19083, 2)`
- `(19365, 2)`
- `(19396, 2)`
- `(19650, 2)`
- `(19652, 2)`
- `(19682, 2)`
- `(19690, 2)`
- `(19935, 2)`
- `(21117, 2)`
- `(21328, 2)`
- `(21383, 2)`
- `(21479, 2)`
- `(21553, 2)`
- `(21582, 2)`
- `(21663, 2)`
- `(21697, 2)`
- `(22022, 2)`
- `(22244, 2)`
- `(22597, 2)`
- `(22674, 2)`
- `(23272, 2)`
- `(23906, 2)`
- `(25047, 2)`
- `(26764, 2)`
- `(26863, 2)`
- `(27276, 2)`
- `(27871, 2)`
- `(27896, 2)`
- `(28133, 2)`
- `(28333, 2)`
- `(29105, 2)`
- `(29115, 2)`
- `(29469, 2)`
- `(29568, 2)`
- `(30142, 2)`
- `(30774, 2)`
- `(30928, 2)`
- `(31000, 2)`
- `(31041, 2)`
- `(31161, 2)`
- `(31321, 2)`
- `(31436, 2)`
- `(31466, 2)`
- `(31736, 2)`
- `(32006, 2)`
- `(32648, 2)`
- `(33001, 2)`
- `(33094, 2)`
- `(33746, 2)`
- `(33780, 2)`
- `(34423, 2)`
- `(34435, 2)`
- `(36270, 2)`
- `(36931, 2)`
- `(37723, 2)`
- `(37882, 2)`
- `(38047, 2)`
- `(38309, 2)`
- `(38369, 2)`
- `(38545, 2)`
- `(38622, 2)`
- `(38628, 2)`
- `(38682, 2)`
- `(38939, 2)`
- `(39108, 2)`
- `(39240, 2)`
- `(39660, 2)`
- `(42884, 2)`
- `(42888, 2)`
- `(42925, 2)`
- `(42986, 2)`
- `(57581, 2)`
- `(57785, 2)`
- `(57957, 2)`
- `(57959, 2)`
- `(58129, 2)`
- `(58267, 2)`
- `(58393, 2)`
- `(58512, 2)`
- `(58703, 2)`
- `(58765, 2)`
- `(59643, 2)`
- `(59909, 2)`
- `(60070, 2)`
- `(60379, 2)`
- `(60649, 2)`
- `(60988, 2)`
- `(61668, 2)`
- `(63361, 2)`
- `(68312, 2)`
- `(79904, 2)`
- `(80332, 2)`
- `(80475, 2)`
- `(81175, 2)`
- `(81503, 2)`
- `(82184, 2)`
- `(82339, 2)`
- `(82389, 2)`
- `(82480, 2)`
- `(85990, 2)`
- `(90690, 2)`
- `(90918, 2)`
- `(90940, 2)`
- `(90943, 2)`
- `(91739, 2)`
- `(91767, 2)`
- `(91808, 2)`
- `(92078, 2)`
- `(92502, 2)`
- `(93361, 2)`
- `(93363, 2)`
- `(93450, 2)`
- `(93660, 2)`
- `(93690, 2)`
- `(93849, 2)`
- `(93953, 2)`
- `(94112, 2)`
- `(94224, 2)`
- `(94630, 2)`
- `(94873, 2)`
- `(94875, 2)`
- `(95304, 2)`
- `(95659, 2)`
- `(95909, 2)`

_(truncated — 247 total)_

## Filesystem checks

### entity_nodes missing expected columns

OK — no issues found.

### entity_nodes with .ent files missing on disk

OK — no issues found.

### companion class files referenced in entity_mappings missing on disk

OK — no issues found.

## Repo vs DB coverage

### entity_nodes missing expected columns

OK — no issues found.

### .ent files present in repo but missing in DB

OK — no issues found.

### .ent files present in DB but missing in repo

OK — no issues found.

## Role distribution

### entity_roots role distribution

- `('manager', 1766)`
- `('editor', 605)`
- `('lister', 555)`
- `('picker', 360)`
- `('allowed_operations_handler', 154)`
- `('entry_manager', 79)`
- `('form_editor', 67)`
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
- `total_expected_symbols=3345`
- `total_actual_symbols=3345`
- `total_correct_symbols=3345`
- `precision=1.0000`
- `recall=1.0000`

### ground-truth entities missing in entity_nodes

OK — no issues found.

### entities missing expected >=0.75 roots

OK — no issues found.

### entities with unexpected >=0.75 extra roots

OK — no issues found.

