import pytest

pytestmark = pytest.mark.integration


def test_catalog_flow_idempotent():
    from qa import db

    from venue_arb.flows.catalog import catalog_flow

    def count():
        with db.pg() as conn:
            return conn.execute("SELECT count(*) FROM venue_arb.listings").fetchone()[0]

    first = catalog_flow()
    n1 = count()
    second = catalog_flow()
    assert count() == n1
    assert first["kalshi"] > 0 and first["polymarket"] > 0
    assert second["kalshi"] > 0 and second["polymarket"] > 0
