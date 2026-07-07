CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY,
    name TEXT,                 -- ia-app | ia-core | ia-restapi-automation-tests | vendor-domain-service
    kind TEXT,                 -- monorepo | domain_service | test_suite
    language TEXT              -- php | java | ts
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER,
    name TEXT,                 -- vendor-domain-service
    entity_id INTEGER          -- optional; when the service maps to one domain object
);

CREATE TABLE IF NOT EXISTS service_endpoints (
    id INTEGER PRIMARY KEY,
    service_id INTEGER,
    method TEXT,
    path TEXT,
    rest_endpoint_id INTEGER   -- optional link to ia-app REST endpoint
);
