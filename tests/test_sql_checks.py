from pathlib import Path


def test_opportunities_source_check_accepts_stock_perp_directions():
    sql = (Path(__file__).parent.parent / "sql_checks" / "opportunities_source_valid.sql").read_text()
    assert "'stock_perp'" in sql
    assert "'short_perp_long_stock'" in sql and "'long_perp_short_stock'" in sql
