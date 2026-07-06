UPDATE entity_nodes
SET entity_type = CASE name
    WHEN 'Base' THEN 'framework'
    WHEN 'GLObals' THEN 'framework'
    WHEN 'Schemamap' THEN 'metadata'
    WHEN 'Stdrepeat' THEN 'framework'
    WHEN 'Apxbatch' THEN 'queue'
    WHEN 'EmployeeAging' THEN 'report'
    WHEN 'TAXSummary' THEN 'report'
    WHEN 'Qdepositpayment' THEN 'payment_component'
    ELSE entity_type
END
WHERE name IN (
    'Base',
    'GLObals',
    'Schemamap',
    'Stdrepeat',
    'Apxbatch',
    'EmployeeAging',
    'TAXSummary',
    'Qdepositpayment'
);
