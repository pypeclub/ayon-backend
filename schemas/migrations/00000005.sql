----------------
-- AYON 1.6.x --
----------------

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'sites'
        AND column_name = 'last_seen'
    ) THEN
        ALTER TABLE IF EXISTS sites
        ADD COLUMN last_seen TIMESTAMPTZ DEFAULT NOW();
    END IF;
END $$;
