CREATE EXTENSION IF NOT EXISTS "pg_trgm";
ALTER EXTENSION pg_trgm SET SCHEMA public;

CREATE TABLE IF NOT EXISTS public.config(
  key VARCHAR NOT NULL PRIMARY KEY,
  value JSONB NOT NULL DEFAULT '{}'::JSONB
);

-- Server updates

CREATE TABLE IF NOT EXISTS public.server_updates(
  id SERIAL PRIMARY KEY,
  version VARCHAR NOT NULL,
  data JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS server_updates_version_idx ON public.server_updates(version);

-- Projects

CREATE TABLE IF NOT EXISTS public.projects(
    name VARCHAR NOT NULL PRIMARY KEY,
    code VARCHAR NOT NULL,
    library BOOLEAN NOT NULL DEFAULT FALSE,
    config JSONB NOT NULL DEFAULT '{}'::JSONB,

    attrib JSONB NOT NULL DEFAULT '{}'::JSONB,
    data JSONB NOT NULL DEFAULT '{}'::JSONB,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS projectname_idx ON public.projects(LOWER(name));
CREATE UNIQUE INDEX IF NOT EXISTS projectcode_idx ON public.projects(LOWER(code));

-- Users

CREATE TABLE IF NOT EXISTS public.users(
    name VARCHAR NOT NULL PRIMARY KEY,

    attrib JSONB NOT NULL DEFAULT '{}'::JSONB,
    data JSONB NOT NULL DEFAULT '{}'::JSONB,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS username_idx ON public.users (LOWER(name));


-- Product types

CREATE TABLE IF NOT EXISTS public.product_types(
  name VARCHAR NOT NULL PRIMARY KEY,
  data JSONB NOT NULL DEFAULT '{}'::JSONB
);


------------
-- Events --
------------

CREATE TABLE IF NOT EXISTS public.events(
  id UUID NOT NULL PRIMARY KEY,
  hash VARCHAR NOT NULL,
  topic VARCHAR NOT NULL,
  sender VARCHAR,
  sender_type VARCHAR,
  project_name VARCHAR,
  user_name VARCHAR,
  depends_on UUID REFERENCES public.events(id),
  status VARCHAR NOT NULL
    DEFAULT 'finished'
    CHECK (status IN (
      'pending',
      'in_progress',
      'finished',
      'failed',
      'aborted',
      'restarted'
    )
  ),
  retries INTEGER NOT NULL DEFAULT 0,
  description TEXT NOT NULL DEFAULT '',
  summary JSONB NOT NULL DEFAULT '{}'::JSONB,
  payload JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  creation_order SERIAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS unique_event_hash ON events(hash);
CREATE UNIQUE INDEX IF NOT EXISTS unique_creation_order ON events(creation_order);

CREATE INDEX IF NOT EXISTS event_topic_idx ON events USING GIN (topic public.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS event_depends_on_idx ON events(depends_on);
CREATE INDEX IF NOT EXISTS event_project_name_idx ON events (project_name);
CREATE INDEX IF NOT EXISTS event_user_name_idx ON events (user_name);
CREATE INDEX IF NOT EXISTS event_created_at_idx ON events (created_at);
CREATE INDEX IF NOT EXISTS event_updated_at_idx ON events (updated_at);
CREATE INDEX IF NOT EXISTS event_status_idx ON events (status);
CREATE INDEX IF NOT EXISTS event_retries_idx ON events (retries);
CREATE INDEX IF NOT EXISTS events_sender_type_idx ON events(sender_type);

--------------
-- Settings --
--------------

CREATE TABLE IF NOT EXISTS public.bundles(
  name VARCHAR NOT NULL PRIMARY KEY,
  is_production BOOLEAN NOT NULL DEFAULT FALSE,
  is_staging BOOLEAN NOT NULL DEFAULT FALSE,
  is_archived BOOLEAN NOT NULL DEFAULT FALSE,
  is_dev BOOLEAN NOT NULL DEFAULT FALSE,
  active_user VARCHAR REFERENCES public.users(name) ON DELETE SET NULL ON UPDATE CASCADE,
  data JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- only one bundle per active user
CREATE UNIQUE INDEX IF NOT EXISTS bundle_active_user_idx ON bundles(active_user) WHERE active_user IS NOT NULL;
-- case insensitive name index
CREATE UNIQUE INDEX IF NOT EXISTS bundle_name_idx ON bundles(LOWER(name));
-- allow only one bundle to be production
CREATE UNIQUE INDEX IF NOT EXISTS bundle_production_idx ON bundles(is_production) WHERE is_production;
-- allow only one bundle to be staging
CREATE UNIQUE INDEX IF NOT EXISTS bundle_staging_idx ON bundles(is_staging) WHERE is_staging;



CREATE TABLE IF NOT EXISTS public.sites(
  id VARCHAR NOT NULL PRIMARY KEY,
  last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  data JSONB NOT NULL DEFAULT '{}'::JSONB
);


CREATE TABLE IF NOT EXISTS public.access_groups(
    name VARCHAR NOT NULL PRIMARY KEY,
    data JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE IF NOT EXISTS public.attributes(
    name VARCHAR NOT NULL PRIMARY KEY,
    position INTEGER,
    scope VARCHAR[],
    builtin BOOLEAN NOT NULL DEFAULT FALSE,
    data JSONB NOT NULL DEFAULT '{}':: JSONB
);


CREATE TABLE IF NOT EXISTS public.anatomy_presets(
  name VARCHAR NOT NULL,
  version VARCHAR NOT NULL DEFAULT '4.0.0',
  is_primary BOOLEAN NOT NULL DEFAULT FALSE,
  data JSONB NOT NULL DEFAULT '{}'::JSONB,
  PRIMARY KEY (name, version)
);


CREATE TABLE IF NOT EXISTS public.settings(
  addon_name VARCHAR NOT NULL,
  addon_version VARCHAR NOT NULL,
  variant VARCHAR NOT NULL DEFAULT 'production',
  data JSONB NOT NULL DEFAULT '{}'::JSONB,
  PRIMARY KEY (addon_name, addon_version, variant)
);

CREATE TABLE IF NOT EXISTS public.site_settings(
  addon_name VARCHAR NOT NULL,
  addon_version VARCHAR NOT NULL,
  site_id VARCHAR NOT NULL REFERENCES public.sites(id) ON DELETE CASCADE,
  user_name VARCHAR NOT NULL REFERENCES public.users(name) ON DELETE CASCADE ON UPDATE CASCADE,
  data JSONB NOT NULL DEFAULT '{}'::JSONB,
  PRIMARY KEY (addon_name, addon_version, site_id, user_name)
);



CREATE TABLE IF NOT EXISTS public.addon_data(
  addon_name VARCHAR NOT NULL,
  addon_version VARCHAR NOT NULL,
  key VARCHAR NOT NULL,
  data JSONB NOT NULL DEFAULT '{}'::JSONB,
  PRIMARY KEY (addon_name, addon_version, key)
);

CREATE TABLE IF NOT EXISTS public.secrets(
  name VARCHAR NOT NULL PRIMARY KEY,
  value VARCHAR NOT NULL
);


--------------
-- SERVICES --
--------------

CREATE TABLE IF NOT EXISTS public.hosts(
  name VARCHAR NOT NULL PRIMARY KEY,
  last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  health JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE IF NOT EXISTS public.services(
  name VARCHAR PRIMARY KEY,
  hostname VARCHAR REFERENCES public.hosts(name) ON DELETE CASCADE ON UPDATE CASCADE,
  addon_name VARCHAR NOT NULL,
  addon_version VARCHAR NOT NULL,
  service VARCHAR NOT NULL,
  should_run BOOLEAN NOT NULL DEFAULT TRUE,
  is_running BOOLEAN NOT NULL DEFAULT FALSE,
  last_seen TIMESTAMP,
  data JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE IF NOT EXISTS public.licenses(
    id UUID NOT NULL PRIMARY KEY,
    data JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE IF NOT EXISTS public.traffic_stats(
    date DATE NOT NULL,
    service VARCHAR NOT NULL,
    ingress BIGINT NOT NULL DEFAULT 0,
    egress BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (date, service)
);

CREATE TABLE IF NOT EXISTS public.user_stats(
    date DATE NOT NULL PRIMARY KEY,
    users JSONB NOT NULL DEFAULT '{}'::JSONB
);


-- CREATE THE SITE ID
INSERT INTO config VALUES ('instanceId', to_jsonb(gen_random_uuid()::text)) ON CONFLICT DO NOTHING;


-----------
-- INBOX --
-----------

DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT 'DROP FUNCTION ' || oid::regprocedure || ';' as drop_command
        FROM pg_proc
        WHERE proname = 'get_user_inbox'
    LOOP
        EXECUTE r.drop_command;
    END LOOP;
END $$;


CREATE OR REPLACE FUNCTION get_user_inbox(
  user_name TEXT,
  show_active_projects BOOLEAN DEFAULT NULL,
  show_active_messages BOOLEAN DEFAULT NULL,
  show_unread_messages BOOLEAN DEFAULT NULL,
  before TIMESTAMPTZ DEFAULT NULL,
  last INTEGER DEFAULT 100,
  additional_filters TEXT DEFAULT ''
)
RETURNS TABLE (
    project_name TEXT,
    reference_id UUID,
    activity_id UUID,
    reference_type VARCHAR,
    entity_type VARCHAR,
    entity_id UUID,
    entity_name VARCHAR,
    entity_path VARCHAR,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    creation_order INTEGER,
    activity_type VARCHAR,
    body TEXT,
    activity_data JSONB,
    reference_data JSONB,
    active BOOLEAN

) AS $$
DECLARE
    project RECORD;
    query TEXT;

BEGIN
    FOR project IN
      SELECT p.name FROM projects AS p JOIN users AS u ON u.name = user_name
      WHERE (show_active_projects IS NULL OR p.active = show_active_projects)
      AND (
        (
          (u.data->'isManager')::boolean
          OR (u.data->'isAdmin')::boolean
        )
        OR (u.data->'accessGroups'->p.name IS NOT NULL)
      )
    LOOP
        query := format('
            SELECT
                ''%s'' AS project_name,

                t.reference_id as reference_id,
                t.activity_id as activity_id,
                t.reference_type as reference_type,
                t.entity_type as entity_type,
                t.entity_id as entity_id,
                t.entity_name as entity_name,
                t.entity_path as entity_path,

                t.created_at as created_at,
                t.updated_at as updated_at,
                t.creation_order as creation_order,

                t.activity_type as activity_type,
                t.body as body,
                t.activity_data as activity_data,
                t.reference_data as reference_data,
                t.active as active

            FROM
                project_%s.activity_feed t
            WHERE
                t.entity_type = ''user''
            AND t.entity_name = %L
            AND t.reference_type != ''author''
            AND t.updated_at <= COALESCE(%L, NOW())
            AND t.activity_data->>''author'' != %L
            %s
            %s
            %s
            ORDER BY t.updated_at DESC
            LIMIT %s
        ',

          project.name,
          project.name,
          user_name,
          before,
          user_name,

        CASE
            WHEN show_active_messages IS TRUE THEN 'AND t.active IS TRUE'
            WHEN show_active_messages IS FALSE THEN 'AND t.active IS FALSE'
            ELSE ''
        END,

        CASE
            WHEN show_unread_messages IS FALSE THEN 'AND (t.reference_data->>''read'')::boolean'
            WHEN show_unread_messages IS TRUE THEN 'AND not coalesce((t.reference_data->>''read'')::boolean, false)'
            ELSE ''
        END,

        additional_filters,
        last
        );

        RETURN QUERY EXECUTE query;
    END LOOP;
END;
$$ LANGUAGE plpgsql;
