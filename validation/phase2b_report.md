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
- `(3493, 2)`
- `(3635, 2)`
- `(3662, 2)`
- `(3946, 2)`
- `(3952, 2)`
- `(4033, 2)`
- `(4331, 2)`
- `(4332, 2)`
- `(4725, 2)`
- `(5447, 2)`
- `(5656, 2)`
- `(6239, 2)`
- `(6570, 2)`
- `(7268, 2)`
- `(7281, 2)`
- `(7405, 2)`
- `(7529, 2)`
- `(7784, 2)`
- `(7794, 2)`
- `(7827, 2)`
- `(8475, 2)`
- `(8530, 2)`
- `(8659, 2)`
- `(8789, 2)`
- `(9141, 2)`
- `(9152, 2)`
- `(9283, 2)`
- `(9285, 2)`
- `(9293, 2)`
- `(9314, 2)`
- `(9317, 2)`
- `(9425, 2)`
- `(9796, 2)`
- `(9886, 2)`
- `(9909, 2)`
- `(10307, 2)`
- `(10382, 2)`
- `(10462, 2)`
- `(10626, 2)`
- `(11225, 2)`
- `(11445, 2)`
- `(11639, 2)`
- `(11645, 2)`
- `(11697, 2)`
- `(12437, 2)`
- `(12439, 2)`
- `(12526, 2)`
- `(13247, 2)`
- `(13278, 2)`
- `(13879, 2)`
- `(13938, 2)`
- `(14219, 2)`
- `(14402, 2)`
- `(15313, 2)`
- `(15409, 2)`
- `(15457, 2)`
- `(15872, 2)`
- `(15903, 2)`
- `(16119, 2)`
- `(16132, 2)`
- `(16566, 2)`
- `(16786, 2)`
- `(17180, 2)`
- `(17195, 2)`
- `(17213, 2)`
- `(17389, 2)`
- `(17488, 2)`
- `(17591, 2)`
- `(17660, 2)`
- `(18193, 2)`
- `(18231, 2)`
- `(18252, 2)`
- `(18664, 2)`
- `(18950, 2)`
- `(19082, 2)`
- `(19364, 2)`
- `(19395, 2)`
- `(19649, 2)`
- `(19651, 2)`
- `(19681, 2)`
- `(19689, 2)`
- `(19934, 2)`
- `(21116, 2)`
- `(21327, 2)`
- `(21382, 2)`
- `(21478, 2)`
- `(21552, 2)`
- `(21581, 2)`
- `(21662, 2)`
- `(21696, 2)`
- `(22021, 2)`
- `(22243, 2)`
- `(22596, 2)`
- `(22673, 2)`
- `(23271, 2)`
- `(23905, 2)`
- `(25046, 2)`
- `(26763, 2)`
- `(26862, 2)`
- `(27275, 2)`
- `(27870, 2)`
- `(27895, 2)`
- `(28132, 2)`
- `(28332, 2)`
- `(29104, 2)`
- `(29114, 2)`
- `(29468, 2)`
- `(29567, 2)`
- `(30141, 2)`
- `(30773, 2)`
- `(30927, 2)`
- `(30999, 2)`
- `(31040, 2)`
- `(31160, 2)`
- `(31320, 2)`
- `(31435, 2)`
- `(31465, 2)`
- `(31735, 2)`
- `(32005, 2)`
- `(32647, 2)`
- `(33000, 2)`
- `(33093, 2)`
- `(33745, 2)`
- `(33779, 2)`
- `(34422, 2)`
- `(34434, 2)`
- `(36269, 2)`
- `(36930, 2)`
- `(37722, 2)`
- `(37881, 2)`
- `(38046, 2)`
- `(38308, 2)`
- `(38368, 2)`
- `(38544, 2)`
- `(38621, 2)`
- `(38627, 2)`
- `(38681, 2)`
- `(38938, 2)`
- `(39107, 2)`
- `(39239, 2)`
- `(39659, 2)`
- `(42883, 2)`
- `(42887, 2)`
- `(42924, 2)`
- `(42985, 2)`
- `(57580, 2)`
- `(57784, 2)`
- `(57956, 2)`
- `(57958, 2)`
- `(58128, 2)`
- `(58266, 2)`
- `(58392, 2)`
- `(58511, 2)`
- `(58702, 2)`
- `(58764, 2)`
- `(59642, 2)`
- `(59908, 2)`
- `(60069, 2)`
- `(60378, 2)`
- `(60648, 2)`
- `(60987, 2)`
- `(61665, 2)`
- `(63358, 2)`
- `(68309, 2)`
- `(79876, 2)`
- `(80304, 2)`
- `(80447, 2)`
- `(81147, 2)`
- `(81475, 2)`
- `(82156, 2)`
- `(82311, 2)`
- `(82361, 2)`
- `(82452, 2)`
- `(85962, 2)`
- `(90662, 2)`
- `(90890, 2)`
- `(90912, 2)`
- `(90915, 2)`
- `(91711, 2)`
- `(91739, 2)`
- `(91780, 2)`
- `(92050, 2)`
- `(92474, 2)`
- `(93333, 2)`
- `(93335, 2)`
- `(93422, 2)`
- `(93632, 2)`
- `(93662, 2)`
- `(93821, 2)`
- `(93925, 2)`
- `(94084, 2)`
- `(94196, 2)`
- `(94602, 2)`
- `(94845, 2)`
- `(94847, 2)`
- `(95276, 2)`
- `(95631, 2)`
- `(95881, 2)`

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

