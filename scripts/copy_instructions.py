"""Sync query_synonyms table (learned instructions + synonyms) from local DB to Railway DB."""
import asyncio
import json
import os
import subprocess


async def main():
    import asyncpg

    # Read all rows from local DB
    result = subprocess.run(
        ["docker", "exec", "shan-ai-postgres", "psql", "-U", "shan_user", "-d", "shan_ai",
         "-t", "-A", "-c", "SELECT original, synonyms::text, source FROM query_synonyms ORDER BY id;"],
        capture_output=True
    )
    rows = []
    for line in result.stdout.decode("utf-8").strip().splitlines():
        parts = line.split("|")
        if len(parts) >= 3:
            original = parts[0]
            synonyms = json.loads(parts[1])
            source = parts[2]
            rows.append((original, synonyms, source))

    print(f"Local DB: found {len(rows)} query_synonym rows")

    # Connect to Railway DB (set RAILWAY_DATABASE_URL, e.g. postgresql://user:pass@host:port/db)
    railway_url = os.environ["RAILWAY_DATABASE_URL"]
    conn = await asyncpg.connect(railway_url)

    upserted = 0
    for original, synonyms, source in rows:
        synonyms_json = json.dumps(synonyms, ensure_ascii=False)
        existing = await conn.fetchval(
            "SELECT id FROM query_synonyms WHERE original = $1", original
        )
        if existing:
            await conn.execute(
                "UPDATE query_synonyms SET synonyms = $1::jsonb, source = $2 WHERE original = $3",
                synonyms_json, source, original
            )
        else:
            await conn.execute(
                "INSERT INTO query_synonyms (original, synonyms, source) VALUES ($1, $2::jsonb, $3)",
                original, synonyms_json, source
            )
        upserted += 1

    await conn.close()
    print(f"Railway DB: synced {upserted} rows. Done.")


asyncio.run(main())
