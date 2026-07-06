ALTER TABLE relationships ADD COLUMN resolution_class TEXT;
ALTER TABLE relationships ADD COLUMN resolution_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_relationships_resolution_class
ON relationships(resolution_class);
