import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
UNITS = sorted((ROOT / "deploy").glob("*.service"))


def unit_env(path: Path) -> dict[str, str]:
    lines = [ln.removeprefix("Environment=") for ln in path.read_text().splitlines() if ln.startswith("Environment=")]
    return dict(ln.split("=", 1) for ln in lines)


@pytest.mark.parametrize("unit", UNITS, ids=lambda p: p.name)
def test_unit_env_matches_mise(unit):
    env = unit_env(unit)
    mise = tomllib.loads((ROOT / "mise.toml").read_text())["env"]
    assert env["PGUSER"] == "venue_arb" and env["CLICKHOUSE_USER"] == "venue_arb"
    assert env["PREFECT_API_URL"]
    assert env["BWS_PROJECT_ID"] == mise["BWS_PROJECT_ID"]
    for k, v in mise.items():
        if k not in ("BWS_PROJECT_ID", "PYTHONPATH"):
            assert env.get(k) == v, k


def test_units_present():
    assert [p.name for p in UNITS] == ["venue-arb-collector.service", "venue-arb-flows.service", "venue-arb-perps.service", "venue-arb-publish.service"]
    assert not list((ROOT / "deploy").glob("*.timer"))


def test_unit_matches_collector_env():
    assert unit_env(ROOT / "deploy" / "venue-arb-perps.service") == unit_env(ROOT / "deploy" / "venue-arb-collector.service")


def test_server_unit_has_no_db_env():
    text = (ROOT / "deploy" / "server" / "venue-arb-web.service").read_text()
    env = unit_env(ROOT / "deploy" / "server" / "venue-arb-web.service")
    assert env["BUNDLE_DIR"] == "/srv/venue-arb/bundle"
    assert not [k for k in env if k.startswith(("PG", "CLICKHOUSE_", "BWS_", "PREFECT_"))]
    assert "User=venue-arb-web" in text and "ProtectSystem=strict" in text  # not the rsync push user


def test_readme_mentions_perps_unit_and_login():
    text = (ROOT / "README.md").read_text()
    assert "venue-arb-perps" in text
    assert "python -m venue_arb.sources.schwab login" in text


def test_readme_mentions_oracle_check():
    text = (ROOT / "README.md").read_text()
    assert "stock_perp_oracle_check" in text
    assert "data/private/" in text


def test_readme_documents_the_listing_day_edit():
    text = (ROOT / "README.md").read_text()
    assert "kind" in text and "listing day" in text.lower() and "publish history --hours 240" in text


def test_server_unit_caps_memory_and_restarts():
    text = (ROOT / "deploy" / "server" / "venue-arb-web.service").read_text()
    # a spike must kill the server (restarted in 5 s), not freeze the 1 GB VM as on 2026-10-29
    assert "MemoryMax=" in text and "MemoryHigh=" in text and "Restart=always" in text


def test_provision_adds_a_swapfile_once():
    text = (ROOT / "deploy" / "server" / "provision.sh").read_text()
    assert "/swapfile" in text and "swapon" in text and "/etc/fstab" in text
    assert "grep -q" in text  # idempotent: a re-run neither recreates the file nor duplicates the fstab line


def test_server_prune_cron_keeps_daily_rollups_longer():
    text = (ROOT / "deploy" / "server" / "prune.cron").read_text()
    assert "'pairs-*.parquet' -mtime +9" in text  # unchanged: one-minute hour files, ~9 days
    assert "'daily-*.parquet' -mtime +181" in text  # hourly rollups: the publisher keeps 180 days of them
