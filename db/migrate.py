"""Apply numbered SQL migrations to Postgres (schema venue_arb) and ClickHouse (database venue_arb).

Applied names are tracked in Postgres venue_arb.schema_migrations for both engines. Run:
    secrets uv run python -m db.migrate
"""

from __future__ import annotations

from pathlib import Path

MIGRATIONS = Path(__file__).parent / "migrations"
_TRACK = ("CREATE TABLE IF NOT EXISTS venue_arb.schema_migrations "
          "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")


def split_statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def apply(pg_conn, ch_client) -> list[str]:
    pg_conn.execute(_TRACK)
    done = {r[0] for r in pg_conn.execute("SELECT name FROM venue_arb.schema_migrations")}
    applied = []
    for engine in ("pg", "ch"):
        for path in sorted((MIGRATIONS / engine).glob("*.sql")):
            name = f"{engine}/{path.name}"
            if name in done:
                continue
            sql = path.read_text()
            if engine == "pg":
                pg_conn.execute(sql)
            else:
                for stmt in split_statements(sql):
                    ch_client.command(stmt)
            pg_conn.execute("INSERT INTO venue_arb.schema_migrations (name) VALUES (%s)", (name,))
            applied.append(name)
    return applied


if __name__ == "__main__":
    from qa import db

    with db.pg() as conn:
        print(apply(conn, db.ch()) or "up to date")
