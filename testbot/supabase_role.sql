-- TestBot's own login on the shared database: it reads what the real bots
-- keep, and writes nowhere but its own schemas.
--
-- Its own schemas are the ones named testbot_*: one per bot
-- (testbot_convert_bot, ...) and testbot_family, which holds what the real
-- bots keep in `family` -- heartbeats, the bus, settings, the ⚡ wallet. The
-- database enforces the rest: this role cannot insert, update, delete or
-- create anything in any other schema, whatever TestBot's code does. And
-- testbot/switch.py checks, every time it starts on this database, that the
-- role it was given cannot either -- as the postgres user it refuses to start.
--
-- Run it once: Supabase -> SQL Editor, after replacing CHANGE-ME below with a
-- password of your own. Running it again changes nothing except the password.
--
-- Then, in testbot/token.env, the real DATABASE_URL with two things changed:
-- the user `postgres.<project ref>` becomes `testbot.<project ref>`, and the
-- password becomes the one set here.
--
--   TESTBOT_DATABASE_URL=postgresql://testbot.<project ref>:<password>@<same host>:5432/postgres

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'testbot') THEN
    CREATE ROLE testbot LOGIN;
  END IF;
END $$;

-- 24 connections at most: five bots, each a pool of three and a lease, is 20.
-- The cap is what keeps a TestBot that misbehaves from using up the
-- connections the real bots need. A statement over a minute is cancelled for
-- the same reason -- /sql through the test ManagerBot reads production.
ALTER ROLE testbot WITH LOGIN PASSWORD 'CHANGE-ME'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 24;
ALTER ROLE testbot SET statement_timeout = '60s';

-- Whoever runs this has to be able to act as testbot to hand it schemas.
GRANT testbot TO CURRENT_USER;

-- Every bot starts with CREATE SCHEMA IF NOT EXISTS, and Postgres checks the
-- right to create schemas in the database before it looks at whether the
-- schema is already there -- so without this, a bot fails to start on a
-- schema it owns. It lets testbot create new schemas; it gives it nothing in
-- the ones that exist.
DO $$
BEGIN
  EXECUTE format('GRANT CREATE ON DATABASE %I TO testbot', current_database());
END $$;

-- Its schemas. Runs of TestBot as postgres, before this role existed, may have
-- created some of them already; those are handed over with what is in them.
DO $$
DECLARE
  s text;
  r record;
BEGIN
  FOREACH s IN ARRAY ARRAY['testbot_family', 'testbot_sticker_bot', 'testbot_convert_bot',
                           'testbot_downloader_bot', 'testbot_anon_bot', 'testbot_manager_bot'] LOOP
    EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', s);
    EXECUTE format('ALTER SCHEMA %I OWNER TO testbot', s);
    FOR r IN
      SELECT c.relname, c.relkind
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = s AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
        -- A sequence behind a serial or identity column follows its table.
        AND NOT (c.relkind = 'S' AND EXISTS (
          SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a', 'i')))
    LOOP
      EXECUTE format('ALTER %s %I.%I OWNER TO testbot',
                     CASE r.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
                                    WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE'
                                    ELSE 'TABLE' END,
                     s, r.relname);
    END LOOP;
  END LOOP;
END $$;

-- Read, and only read, what the real bots keep -- tables made later included.
DO $$
DECLARE
  s text;
BEGIN
  FOREACH s IN ARRAY ARRAY['family', 'sticker_bot', 'convert_bot', 'downloader_bot', 'anon_bot', 'manager_bot'] LOOP
    IF EXISTS (SELECT FROM pg_namespace WHERE nspname = s) THEN
      EXECUTE format('GRANT USAGE ON SCHEMA %I TO testbot', s);
      EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO testbot', s);
      EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT ON TABLES TO testbot', s);
    END IF;
  END LOOP;
END $$;
