----------------
-- AUX TABLES --
----------------

CREATE TABLE thumbnails(
    id UUID NOT NULL PRIMARY KEY,
    mime VARCHAR NOT NULL,
    data BYTEA NOT NULL,
    meta JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE thumbnails ALTER COLUMN data SET STORAGE EXTERNAL;


CREATE TABLE task_types(
    name VARCHAR NOT NULL PRIMARY KEY,
    position INTEGER NOT NULL DEFAULT 0,
    data JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE folder_types(
    name VARCHAR NOT NULL PRIMARY KEY,
    position INTEGER NOT NULL DEFAULT 0,
    data JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE statuses(
    name VARCHAR NOT NULL PRIMARY KEY,
    position INTEGER NOT NULL DEFAULT 0,
    data JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE tags(
    name VARCHAR NOT NULL PRIMARY KEY,
    position INTEGER NOT NULL DEFAULT 0,
    data JSONB NOT NULL DEFAULT '{}'::JSONB
);


--
-- activities
--

CREATE TABLE IF NOT EXISTS activities (
    id UUID PRIMARY KEY, -- generate uuid1 in python
    activity_type VARCHAR NOT NULL,
    body TEXT NOT NULL,
    tags VARCHAR[] NOT NULL DEFAULT ARRAY[]::VARCHAR[],
    data JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    creation_order SERIAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_type ON activities(activity_type);
CREATE INDEX IF NOT EXISTS idx_activity_tags ON activities USING gin(tags);

CREATE TABLE IF NOT EXISTS activity_references (
    id UUID PRIMARY KEY, -- generate uuid1 in python
    activity_id UUID NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    reference_type VARCHAR NOT NULL,
    entity_type VARCHAR NOT NULL, -- referenced entity type
    entity_id UUID,      -- referenced entity id
    entity_name VARCHAR, -- if entity_type is user, this will be the user name
    active BOOLEAN NOT NULL DEFAULT TRUE,
    data JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    creation_order SERIAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_id ON activity_references(activity_id);
CREATE INDEX IF NOT EXISTS idx_activity_entity_type ON activity_references(entity_type);
CREATE INDEX IF NOT EXISTS idx_activity_entity_id ON activity_references(entity_id);
CREATE INDEX IF NOT EXISTS idx_activity_entity_name ON activity_references(entity_name);
CREATE INDEX IF NOT EXISTS idx_activity_reference_type ON activity_references(reference_type);
CREATE INDEX IF NOT EXISTS idx_activity_reference_created_at ON activity_references(created_at);
CREATE INDEX IF NOT EXISTS idx_activity_reference_updated_at ON activity_references(updated_at);
CREATE INDEX IF NOT EXISTS idx_activity_reference_active ON activity_references(active);
CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_reference_unique ON activity_references(activity_id, entity_id, entity_name, reference_type);


-- This will be implemented later.
-- Now we can create the table, but until we start populating it
-- and invalidating it, hierarchy crawling won't work (but won't break)

CREATE TABLE IF NOT EXISTS entity_paths (
    entity_id UUID PRIMARY KEY,
    entity_type VARCHAR NOT NULL,
    path VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS entity_paths_path_idx ON entity_paths USING GIN (path public.gin_trgm_ops);


CREATE OR REPLACE VIEW activity_feed AS
  SELECT
    ref.id as reference_id,
    ref.activity_id as activity_id,
    ref.reference_type as reference_type,

    -- what entity we're referencing
    ref.entity_type as entity_type,
    ref.entity_id as entity_id, -- for project level entities and other activities
    ref.entity_name as entity_name, -- for users
    ref_paths.path as entity_path, -- entity hierarchy position

    -- sorting stuff
    ref.created_at,
    ref.updated_at,
    ref.creation_order,

    -- actual activity
    act.activity_type as activity_type,
    act.body as body,
    act.tags as tags,
    act.data as activity_data,
    ref.data as reference_data,
    ref.active as active

  FROM
    activity_references as ref
  INNER JOIN
    activities as act ON ref.activity_id = act.id
  LEFT JOIN
    entity_paths as ref_paths ON ref.entity_id = ref_paths.entity_id;


CREATE TABLE IF NOT EXISTS files (
  id UUID PRIMARY KEY,
  size BIGINT NOT NULL,
  author VARCHAR,
  activity_id UUID REFERENCES activities(id) ON DELETE SET NULL,
  data JSONB NOT NULL DEFAULT '{}'::JSONB, -- contains mime, original file name etc
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_files_activity_id ON files(activity_id);

-------------------
-- BASE ENTITIES --
-------------------


-------------
-- FOLDERS --
-------------

CREATE TABLE folders(
    id UUID NOT NULL PRIMARY KEY,

    name VARCHAR NOT NULL,
    label VARCHAR,
    folder_type VARCHAR NOT NULL REFERENCES folder_types(name) ON UPDATE CASCADE,
    parent_id UUID REFERENCES folders(id) ON DELETE CASCADE,
    thumbnail_id UUID REFERENCES thumbnails(id) ON DELETE SET NULL,

    attrib JSONB NOT NULL DEFAULT '{}'::JSONB,
    data JSONB NOT NULL DEFAULT '{}'::JSONB,
    status VARCHAR NOT NULL REFERENCES statuses(name) ON UPDATE CASCADE,
    tags VARCHAR[] NOT NULL DEFAULT ARRAY[]::VARCHAR[],
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    creation_order SERIAL NOT NULL
);

CREATE INDEX folder_parent_idx ON folders(parent_id);
CREATE UNIQUE INDEX folder_creation_order_idx ON folders(creation_order);

-- Two partial indices are used as a workaround for root folders (which have parent_id NULL)

CREATE UNIQUE INDEX folder_unique_name_parent ON folders (parent_id, name)
    WHERE (active IS TRUE AND parent_id IS NOT NULL);

CREATE UNIQUE INDEX folder_root_unique_name ON folders (name)
    WHERE (active IS TRUE AND parent_id IS NULL);


-- Hierarchy view
-- Materialized view used as a shorthand to get folder parents/full path

CREATE MATERIALIZED VIEW hierarchy
AS
    WITH htable AS (
        WITH RECURSIVE hierarchy AS (
            SELECT id, name, parent_id, 1 as pos, id as base_id
            FROM
                folders
            UNION
                SELECT e.id, e.name, e.parent_id, pos + 1, base_id
                FROM folders e
                INNER JOIN hierarchy s ON s.parent_id = e.id
        ) SELECT
            base_id,
            string_agg(name, '/' ORDER BY pos DESC) as path
        FROM
            hierarchy
        GROUP BY base_id
   )
   SELECT base_id AS id, path FROM htable;

CREATE UNIQUE INDEX hierarchy_id ON hierarchy (id);


CREATE TABLE exported_attributes(
  folder_id UUID NOT NULL PRIMARY KEY REFERENCES folders(id) ON DELETE CASCADE,
  path VARCHAR NOT NULL,
  attrib JSONB NOT NULL DEFAULT '{}'::JSONB
);

-----------
-- TASKS --
-----------

CREATE TABLE tasks(
    id UUID NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL,
    label VARCHAR,
    folder_id UUID NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    task_type VARCHAR REFERENCES task_types(name) ON UPDATE CASCADE,
    assignees VARCHAR[] NOT NULL DEFAULT ARRAY[]::VARCHAR[],
    thumbnail_id UUID REFERENCES thumbnails(id) ON DELETE SET NULL,

    attrib JSONB NOT NULL DEFAULT '{}'::JSONB,
    data JSONB NOT NULL DEFAULT '{}'::JSONB,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR NOT NULL REFERENCES statuses(name) ON UPDATE CASCADE,
    tags VARCHAR[] NOT NULL DEFAULT ARRAY[]::VARCHAR[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    creation_order SERIAL NOT NULL
);

CREATE INDEX task_parent_idx ON tasks(folder_id);
CREATE INDEX task_type_idx ON tasks(task_type);
CREATE UNIQUE INDEX task_creation_order_idx ON tasks(creation_order);
CREATE UNIQUE INDEX task_unique_name ON tasks(folder_id, name);

-------------
-- PRODUCTS --
-------------

CREATE TABLE products(
    id UUID NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL,

    folder_id UUID NOT NULL REFERENCES folders(id),
    product_type VARCHAR NOT NULL REFERENCES public.product_types(name) ON UPDATE CASCADE,

    attrib JSONB NOT NULL DEFAULT '{}'::JSONB,
    data JSONB NOT NULL DEFAULT '{}'::JSONB,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR NOT NULL REFERENCES statuses(name) ON UPDATE CASCADE,
    tags VARCHAR[] NOT NULL DEFAULT ARRAY[]::VARCHAR[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    creation_order SERIAL NOT NULL
);

CREATE INDEX product_parent_idx ON products(folder_id);
CREATE INDEX product_type_idx ON products(product_type);
CREATE UNIQUE INDEX product_creation_order_idx ON products(creation_order);
CREATE UNIQUE INDEX product_unique_name_parent ON products (folder_id, name) WHERE (active IS TRUE);

--------------
-- VERSIONS --
--------------

CREATE TABLE versions(
    id UUID NOT NULL PRIMARY KEY,

    version INTEGER NOT NULL,
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
    thumbnail_id UUID REFERENCES thumbnails(id) ON DELETE SET NULL,
    author VARCHAR, -- REFERENCES public.users(name) ON UPDATE CASCADE ON DELETE SET NULL,

    attrib JSONB NOT NULL DEFAULT '{}'::JSONB,
    data JSONB NOT NULL DEFAULT '{}'::JSONB,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR NOT NULL REFERENCES statuses(name) ON UPDATE CASCADE,
    tags VARCHAR[] NOT NULL DEFAULT ARRAY[]::VARCHAR[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    creation_order SERIAL NOT NULL
);

CREATE INDEX version_parent_idx ON versions(product_id);
CREATE UNIQUE INDEX version_creation_order_idx ON versions(creation_order);
CREATE UNIQUE INDEX version_unique_version_parent ON versions (product_id, version) WHERE (active IS TRUE);

-- Version list VIEW
-- Materialized view used as a shorthand to get product versions

CREATE MATERIALIZED VIEW version_list
AS
    SELECT
        v.product_id AS product_id,
        array_agg(v.id ORDER BY v.version ) AS ids,
        array_agg(v.version ORDER BY v.version ) AS versions
    FROM
        versions AS v
    GROUP BY v.product_id;

CREATE UNIQUE INDEX version_list_id ON version_list (product_id);

---------------------
-- REPRESENTATIONS --
---------------------

CREATE TABLE representations(
    id UUID NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL,

    version_id UUID NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    files JSONB NOT NULL DEFAULT '[]'::JSONB,

    attrib JSONB NOT NULL DEFAULT '{}'::JSONB,
    data JSONB NOT NULL DEFAULT '{}'::JSONB,
    traits JSONB,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR NOT NULL REFERENCES statuses(name) ON UPDATE CASCADE,
    tags VARCHAR[] NOT NULL DEFAULT ARRAY[]::VARCHAR[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    creation_order SERIAL NOT NULL
);

CREATE INDEX representation_parent_idx ON representations(version_id);
CREATE UNIQUE INDEX representation_unique_name_on_version ON representations (version_id, name) WHERE (active IS TRUE);
CREATE UNIQUE INDEX representation_creation_order_idx ON representations(creation_order);

---------------
-- WORKFILES --
---------------

CREATE TABLE workfiles(
    id UUID NOT NULL PRIMARY KEY,
    path VARCHAR NOT NULL UNIQUE,
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,

    thumbnail_id UUID REFERENCES thumbnails(id) ON DELETE SET NULL,

    created_by VARCHAR REFERENCES public.users(name) ON UPDATE CASCADE ON DELETE SET NULL,
    updated_by VARCHAR REFERENCES public.users(name) ON UPDATE CASCADE ON DELETE SET NULL,

    attrib JSONB NOT NULL DEFAULT '{}'::JSONB,
    data JSONB NOT NULL DEFAULT '{}'::JSONB,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR NOT NULL REFERENCES statuses(name) ON UPDATE CASCADE,
    tags VARCHAR[] NOT NULL DEFAULT ARRAY[]::VARCHAR[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    creation_order SERIAL NOT NULL
);

-----------
-- Lists --
-----------


CREATE TABLE entity_lists(
  id UUID NOT NULL PRIMARY KEY,
  label VARCHAR NOT NULL,
  list_type VARCHAR NOT NULL, 
  access JSONB NOT NULL DEFAULT '{}'::JSONB,
  template JSONB NOT NULL DEFAULT '{}'::JSONB,

  attrib JSONB NOT NULL DEFAULT '{}'::JSONB,
  data JSONB NOT NULL DEFAULT '{}'::JSONB,
  tags VARCHAR[] NOT NULL DEFAULT ARRAY[]::VARCHAR[],

  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);


CREATE TABLE entity_list_items(
  id UUID NOT NULL PRIMARY KEY,
  entity_list_id UUID NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
  entity_type VARCHAR NOT NULL,
  entity_id UUID NOT NULL,
  position INTEGER NOT NULL,

  attrib JSONB NOT NULL DEFAULT '{}'::JSONB,
  data JSONB NOT NULL DEFAULT '{}'::JSONB,
  tags VARCHAR[] NOT NULL DEFAULT ARRAY[]::VARCHAR[],

  created_by VARCHAR REFERENCES public.users(name) ON UPDATE CASCADE ON DELETE SET NULL,
  updated_by VARCHAR REFERENCES public.users(name) ON UPDATE CASCADE ON DELETE SET NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
  creation_order SERIAL NOT NULL
);


-----------
-- LINKS --
-----------

CREATE TABLE link_types(
    name varchar NOT NULL PRIMARY KEY,
    input_type VARCHAR NOT NULL,
    output_type VARCHAR NOT NULL,
    link_type VARCHAR NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE UNIQUE INDEX link_type_unique_idx ON link_types(input_type, output_type, link_type);

CREATE TABLE links (
    id UUID NOT NULL PRIMARY KEY,
    name VARCHAR,
    link_type VARCHAR NOT NULL REFERENCES link_types(name) ON DELETE CASCADE,
    input_id UUID NOT NULL,
    output_id UUID NOT NULL,
    author VARCHAR, -- REFERENCES public.users(name) ON UPDATE CASCADE ON DELETE SET NULL,
    data JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    creation_order SERIAL NOT NULL
);

CREATE INDEX link_input_idx ON links(input_id);
CREATE INDEX link_output_idx ON links(output_id);
CREATE UNIQUE INDEX link_creation_order_idx ON links(creation_order);

--------------
-- SETTINGS --
--------------

-- Project specific overrides of access groups and addon settings
-- The table structure is the same as in the public schema

CREATE TABLE IF NOT EXISTS access_groups(
    name VARCHAR NOT NULL PRIMARY KEY REFERENCES public.access_groups(name) ON DELETE CASCADE,
    data JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE settings(
  addon_name VARCHAR NOT NULL,
  addon_version VARCHAR NOT NULL,
  variant VARCHAR NOT NULL DEFAULT 'production',
  data JSONB NOT NULL DEFAULT '{}'::JSONB,
  PRIMARY KEY (addon_name, addon_version, variant)
);

CREATE TABLE project_site_settings(
  addon_name VARCHAR NOT NULL,
  addon_version VARCHAR NOT NULL,
  site_id VARCHAR REFERENCES public.sites(id) ON DELETE CASCADE,
  user_name VARCHAR REFERENCES public.users(name) ON DELETE CASCADE ON UPDATE CASCADE,
  data JSONB NOT NULL DEFAULT '{}'::JSONB,
  PRIMARY KEY (addon_name, addon_version, site_id, user_name)
);

CREATE TABLE IF NOT EXISTS addon_data(
  addon_name VARCHAR NOT NULL,
  addon_version VARCHAR NOT NULL,
  key VARCHAR NOT NULL,
  data JSONB NOT NULL DEFAULT '{}'::JSONB,
  PRIMARY KEY (addon_name, addon_version, key)
);

CREATE TABLE IF NOT EXISTS custom_roots(
  site_id VARCHAR NOT NULL REFERENCES public.sites(id) ON DELETE CASCADE,
  user_name VARCHAR NOT NULL REFERENCES public.users(name) ON DELETE CASCADE ON UPDATE CASCADE,
  data JSONB NOT NULL DEFAULT '{}'::JSONB,
  PRIMARY KEY (site_id, user_name)
);
