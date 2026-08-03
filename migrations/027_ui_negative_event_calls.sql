-- Negative actionUI handler outcomes can have an event but no unique proving
-- script dependency. The table reconstruction is performed atomically by
-- catalog.migrations; this marker remains operator-visible.
BEGIN;
INSERT OR IGNORE INTO schema_migrations(name) VALUES ('027_ui_negative_event_calls');
COMMIT;
