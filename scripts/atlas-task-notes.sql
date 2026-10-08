-- Atlas Task.notes is varchar(255) in the upstream database.
-- See details reads this field; full procedures/evidence regularly exceed 255 characters.
-- Retain the JDBC VARCHAR type for the existing Hibernate validate configuration,
-- while removing its length limit. Existing notes and task results are preserved.
-- Re-run on each new Atlas database; the change survives container/image restarts.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
ALTER TABLE public.task ALTER COLUMN notes TYPE character varying;
COMMIT;
SELECT column_name, data_type, character_maximum_length
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'task' AND column_name = 'notes';
