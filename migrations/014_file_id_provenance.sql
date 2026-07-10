-- 014_file_id_provenance.sql
-- Add file_id provenance columns and indexes for security/workflow tables.
-- Run once against an existing DB after 013_openapispec_x_mapped_to.sql.

ALTER TABLE workflows ADD COLUMN file_id INTEGER;

ALTER TABLE security_operations ADD COLUMN file_id INTEGER;
ALTER TABLE security_operation_allowops ADD COLUMN file_id INTEGER;
ALTER TABLE security_policies ADD COLUMN file_id INTEGER;
ALTER TABLE security_menus ADD COLUMN file_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_workflows_file_id
    ON workflows(file_id);

CREATE INDEX IF NOT EXISTS idx_security_operations_file_id
    ON security_operations(file_id);

CREATE INDEX IF NOT EXISTS idx_security_allowops_file_id
    ON security_operation_allowops(file_id);

CREATE INDEX IF NOT EXISTS idx_security_policies_file_id
    ON security_policies(file_id);

CREATE INDEX IF NOT EXISTS idx_security_menus_file_id
    ON security_menus(file_id);
