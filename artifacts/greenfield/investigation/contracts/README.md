# gwdata Step 2 Contract Templates (Draft)

These files are scaffolds for Greenfield Step 2 and are **not authoritative** yet.

Files:
- `ia-gwdata-gl.step2.contract.template.yaml`
- `ia-gwdata-project.step2.contract.template.yaml`
- `ia-gwdata-contract.step2.contract.template.yaml`
- `ia-gwdata-ap.step2.contract.template.yaml`

## Why draft-only

Current repository evidence is inventory-level (`repository_inventory`) with `ci_linkage.status=unavailable` and no executable test-job linkage for the source revision.

## Before setting `status: active`

1. Replace `__REQUIRED_EXACT_IA_APP_PATH__` with exact Step 1 source file path(s).
2. Fill `owner` and `test_owner` with real ownership values.
3. Replace `protected_behavior` with a precise behavior statement.
4. Keep `revision` pinned to the exact Step 1 source revision.
5. Verify test obligation path exists at the inspected downstream revision.
6. Add normalized CI evidence (`schema_version: 0.1`) bound to the same source revision.

## Validation command

```bash
PYTHONPATH=. ./.venv/bin/python - <<'PY'
from pathlib import Path
from greenfield.step2_contract import load_contract
for p in sorted(Path('artifacts/greenfield/investigation/contracts').glob('*.yaml')):
    print(p.name, '->', 'ok' if load_contract(p) else 'fail')
PY
```
