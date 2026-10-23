from pathlib import Path

from db.migrate import split_statements


def test_split_statements_drops_comments():
    sql = "-- c\nCREATE TABLE a (x Int8);\n\nCREATE TABLE b (y Int8);"
    assert split_statements(sql) == ["CREATE TABLE a (x Int8)", "CREATE TABLE b (y Int8)"]


def test_stock_perp_migrations_are_additive_and_numbered():
    ch = Path("db/migrations/ch/008_stock_perps.sql").read_text(); pg = Path("db/migrations/pg/007_stock_perp_weekends.sql").read_text()
    assert ch.count("CREATE TABLE IF NOT EXISTS venue_arb.stock_perp_") == 5 and "DROP" not in ch.upper()
    assert "PARTITION BY toYYYYMM(ts)" in ch and "CREATE TABLE IF NOT EXISTS venue_arb.stock_perp_weekends" in pg
    assert all(s.startswith("CREATE") for s in split_statements(ch))


def test_migration_008_has_mode_and_session_in_order_by():
    ch = Path("db/migrations/ch/008_stock_perps.sql").read_text()
    edges = ch[ch.index("stock_perp_edges"):]
    assert edges.index("ref_kind") < edges.index("mode LowCardinality") < edges.index("session LowCardinality") < edges.index("reference")
    assert "ORDER BY (symbol, mode, direction, ts)" in edges
