CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY,
    name TEXT,                 -- ia-app | ia-core | ia-restapi-automation-tests | vendor-domain-service
    kind TEXT,                 -- monorepo | domain_service | test_suite
    language TEXT              -- php | java | ts
);

-- services retired
-- service_endpoints retired
