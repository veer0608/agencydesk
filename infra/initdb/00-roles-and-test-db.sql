-- Runs once, on first boot of an empty data directory.
--
-- The whole isolation story rests on this file: the application connects as a
-- role that is NOT the owner of any table, so PostgreSQL row-level security is
-- actually enforced against it. (A table's owner bypasses RLS unless the table
-- is marked FORCE ROW LEVEL SECURITY -- we deliberately rely on that, because
-- our SECURITY DEFINER context helpers are owned by agencydesk_owner and need
-- to read memberships without recursing through the policies they feed.)

CREATE ROLE agencydesk_app LOGIN PASSWORD 'app_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

GRANT CONNECT ON DATABASE agencydesk TO agencydesk_app;

-- A separate database for the test suite so `make test` never touches dev data.
CREATE DATABASE agencydesk_test OWNER agencydesk_owner;
GRANT CONNECT ON DATABASE agencydesk_test TO agencydesk_app;
