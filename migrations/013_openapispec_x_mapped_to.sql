ALTER TABLE openapispec_index ADD COLUMN x_mapped_to TEXT;
CREATE INDEX IF NOT EXISTS idx_openapispec_x_mapped_to ON openapispec_index(x_mapped_to);
