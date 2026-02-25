#!/usr/bin/env python3
import os
import sys

try:
    from databricks import sql
except ImportError:
    print("Missing dependency: databricks-sql-connector. Install with: pip install databricks-sql-connector", file=sys.stderr)
    sys.exit(1)

host = os.environ.get("DATABRICKS_HOST")
token = os.environ.get("DATABRICKS_TOKEN")
http_path = os.environ.get("DATABRICKS_HTTP_PATH")

missing = [name for name, val in [
    ("DATABRICKS_HOST", host),
    ("DATABRICKS_TOKEN", token),
    ("DATABRICKS_HTTP_PATH", http_path),
] if not val]

if missing:
    print("Missing required env vars: " + ", ".join(missing), file=sys.stderr)
    sys.exit(2)

query = """
SELECT table_schema, table_name
FROM system.information_schema.tables
WHERE table_catalog = 'samples'
ORDER BY table_schema, table_name
""".strip()

with sql.connect(server_hostname=host, http_path=http_path, access_token=token) as conn:
    with conn.cursor() as cur:
        cur.execute(query)
        rows = cur.fetchall()

for schema, table in rows:
    print(f"{schema}.{table}")
