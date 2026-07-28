# PR 48488 Impact Analysis

Source PR: [https://github.com/intacct/ia-app/pull/48488](https://github.com/intacct/ia-app/pull/48488)

## Scope

This PR is a narrow hotfix in [`app/source/apar/ARInvoiceManager.cls`](./app/source/apar/ARInvoiceManager.cls). It adds null-safe handling for missing `ITEMS`, `SUBTOTALS`, and `TAXSOLUTIONID` values in invoice create/edit paths.

## Impacted Components Ranked By Risk

### 1. `ARInvoiceManager`

Highest risk because the patch changes core invoice processing branches:

- partial-edit / historical / SCM merge path
- subtotal dimension copy path for `SimpleTax`
- tax-engine checks during create/edit validation
- empty transaction validation for taxed invoices

The code now tolerates missing arrays and missing tax solution IDs, which prevents runtime failures but can also change control flow for malformed or sparse payloads.

### 2. `ARInvoiceEditor`

Medium-high risk because it directly references `ARInvoiceManager` and is the primary user-facing edit surface for AR invoices.

Relevant file:

- [`app/source/apar/ARInvoiceEditor.cls`](./app/source/apar/ARInvoiceEditor.cls)

### 3. `ARInvoiceAPIValidator`

Medium risk because the patch changes how sparse payloads flow into manager validation. API behavior may shift from hard failure to controlled validation.

Relevant file:

- [`app/source/apar/ARInvoiceAPIValidator.cls`](./app/source/apar/ARInvoiceAPIValidator.cls)

### 4. `ARInvoiceLister`

Low-medium risk. The patch does not directly change list/query behavior, but the invoice domain is shared and list/edit navigation can surface assumptions about invoice completeness.

Relevant file:

- [`app/source/apar/ARInvoiceLister.cls`](./app/source/apar/ARInvoiceLister.cls)

### 5. Related invoice family managers

Lower risk, but still worth a smoke regression because they share the AR invoice domain:

- [`app/source/apar/ARInvoiceReverseManager.cls`](./app/source/apar/ARInvoiceReverseManager.cls)
- [`app/source/apar/ARInvoiceItemManager.cls`](./app/source/apar/ARInvoiceItemManager.cls)
- [`app/source/apar/ARInvoiceBatchManager.cls`](./app/source/apar/ARInvoiceBatchManager.cls)

### 6. Existing regression tests

Medium risk due to likely coverage gaps for null subtotal/tax payload handling.

Relevant tests:

- [`app/tests/source/core/filter/GetListTest.php`](./app/tests/source/core/filter/GetListTest.php)
- [`app/tests/source/api/framework/doctype/APIDocTypeObjectTest.php`](./app/tests/source/api/framework/doctype/APIDocTypeObjectTest.php)
- [`app/tests/source/api/framework/doctype/APIDocTypeObjectWithAppTest.php`](./app/tests/source/api/framework/doctype/APIDocTypeObjectWithAppTest.php)

## Blast Radius

### Entity relationships

The catalog shows `ARInvoiceManager` is referenced by:

- `ARInvoiceEditor`
- `ARRecurInvoiceFormEditor`
- `ARSetupEditor`
- `ARSubtotals`
- `GetTaxEngineForNotSubscribedTaxes`
- `UserPolicy`

This means the change can affect editor flows, subtotal handling, tax setup behavior, and policy-related access checks indirectly.

### Manager/editor/lister classes

The direct AR invoice class family includes:

- `ARInvoiceManager`
- `ARInvoiceEditor`
- `ARInvoiceLister`
- `ARInvoiceItemManager`
- `ARInvoiceReverseManager`
- `ARInvoiceBatchManager`
- `ARInvoiceAllowedOperationsHandler`
- `ARInvoicePicker`

The patch is concentrated in `ARInvoiceManager`, but those surrounding classes are the first places likely to surface regressions.

### API schemas and endpoints

The repository index shows AR invoice API-related symbols and tests, including:

- `ARInvoiceAPIValidator`
- `APIDocTypeObjectTest`
- `APIDocTypeObjectWithAppTest`

The patch does not directly change a schema or endpoint definition, but it does change how sparse request payloads are tolerated during validation and manager execution.

### DB migrations

No DB migration files are touched by this PR.

Regression concern is operational rather than schema-level: the code path now accepts missing arrays/fields that may have previously failed early.

### Existing tests

Existing AR invoice tests appear to cover:

- list behavior
- API doctype href conversion
- query utility handling for AR invoice objects

They do not obviously cover:

- missing `ITEMS`
- missing `SUBTOTALS`
- missing `TAXSOLUTIONID`
- sparse subtotal rows during `SimpleTax` dimension propagation

## Missing BDD Scenarios To Add

1. Create invoice with missing `ITEMS` and `SUBTOTALS`
   - Given an AR invoice payload with no `ITEMS` and no `SUBTOTALS`
   - When the manager processes the transaction
   - Then it should fail or succeed according to business rules without a null/index runtime error

2. Partial edit with missing `ITEMS` / `SUBTOTALS`
   - Given an invoice in partial-edit or historical mode
   - And `ITEMS` or `SUBTOTALS` is omitted
   - When `mergeSubtotalsToEntries(...)` runs
   - Then the transaction should remain stable

3. SimpleTax invoice with sparse subtotal rows
   - Given `TAXSOLUTIONID` resolves to `SimpleTax`
   - And `SUBTOTALS` contains fewer rows than `origSubtotals`
   - When dimensions are copied back
   - Then missing subtotal rows should be initialized before copy

4. Missing `TAXSOLUTIONID` in tax-engine checks
   - Given a create or edit payload without `TAXSOLUTIONID`
   - When the manager evaluates tax-engine branches
   - Then it should not fatal on a null access

5. No-line transaction with non-`NONE` tax engine
   - Given an invoice payload with empty `ITEMS` and `SUBTOTALS`
   - And a non-`NONE` tax engine
   - When the create path validates transaction presence
   - Then it should return the intended business error

6. Mixed index integrity for subtotal dimension copy
   - Given `origSubtotals` has N entries
   - And `SUBTOTALS` is shorter or sparse
   - When dimensions are copied
   - Then every required row should be materialized first

## Regression Suite Proposal By Priority

### P0

- Unit or integration test for `ARInvoiceManager` create/edit with omitted `ITEMS`, `SUBTOTALS`, and `TAXSOLUTIONID`
- Partial edit / historical path coverage for `mergeSubtotalsToEntries(...)`

### P1

- `SimpleTax` subtotal dimension propagation with sparse subtotal rows
- Empty transaction + tax engine rule validation

### P2

- API/doctype regression in:
  - [`app/tests/source/api/framework/doctype/APIDocTypeObjectTest.php`](./app/tests/source/api/framework/doctype/APIDocTypeObjectTest.php)
  - [`app/tests/source/api/framework/doctype/APIDocTypeObjectWithAppTest.php`](./app/tests/source/api/framework/doctype/APIDocTypeObjectWithAppTest.php)

### P3

- AR invoice list smoke coverage in:
  - [`app/tests/source/core/filter/GetListTest.php`](./app/tests/source/core/filter/GetListTest.php)

## Short Verdict

This is a low-line-count but high-risk runtime hotfix. It is most likely safe, but the regression focus should be on invoice create/edit flows with sparse payloads, especially partial edit and `SimpleTax` subtotal propagation.
