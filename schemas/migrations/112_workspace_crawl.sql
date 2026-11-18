-- Crawl admission stays closed until every process validates its authority and exclusions.
ALTER TABLE loom.knowledge_sources ADD COLUMN IF NOT EXISTS options_json String DEFAULT '{}';
ALTER TABLE loom.knowledge_sources ADD COLUMN IF NOT EXISTS registrant_subject String DEFAULT '';
ALTER TABLE loom.knowledge_sources ADD COLUMN IF NOT EXISTS approval_enc String DEFAULT '';
ALTER TABLE loom.knowledge_sources ADD COLUMN IF NOT EXISTS crawl_generation String DEFAULT '';
