"""One-off generator for the committed CI fixture lake under
`tests/fixtures/lake` + `tests/fixtures/lake_registry` (03-VERIFICATION.md
gap-closure finding 1's "CI-native fixture leg").

NOT invoked by CI or by any test -- its OUTPUT is what's committed and
scanned. Re-run this manually (`./.venv/bin/python3 -m
tests.fixtures.generate_manifest_fixture`, from `mvp/`) only if the
fixture ever needs to be regenerated (e.g. `data.store.issue_manifest`'s
body schema changes); commit the regenerated `lake/` + `lake_registry/`
output as a normal diff. `issue_manifest` embeds `time.time_ns()` as
`built_at`, so re-running changes the manifest_id even with byte-identical
partition content -- this is fine, the CI check only cares that the
committed tree self-verifies, not that its id matches any prior run.

Deliberately a tiny (~1.5 KB parquet + ~1 KB JSON) fixture, NOT the real
lake: `data/lake_paths.py`'s "physical data never in git" rule is about the
real multi-GB curated tier; this is a purpose-built, minimal CI artifact
whose entire reason to exist is to be git-committed so `--full --lake-root
tests/fixtures/lake --registry-root tests/fixtures/lake_registry` has
something to scan on every GitHub Actions runner, where the real SSD-backed
lake is structurally never mounted.

Git does not preserve file `mtime`, so this fixture is only ever exercised
via `verify_manifest` (`--full`, sha256) -- see `tests/fixtures/.gitattributes`,
which marks the fixture parquet `binary` so no line-ending filter ever
touches the bytes the committed `sha256` covers.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import polars as pl

from data.store import issue_manifest

FIXTURE_ROOT = Path(__file__).resolve().parent
LAKE_ROOT = FIXTURE_ROOT / "lake"
REGISTRY_ROOT = FIXTURE_ROOT / "lake_registry"

REL_PARTITION_PATH = (
    "curated/symbol=BTCUSDT/stream=trade/date=2026-01-01/part-1.parquet"
)


def generate() -> dict:
    final_path = LAKE_ROOT / REL_PARTITION_PATH
    final_path.parent.mkdir(parents=True, exist_ok=True)

    df = pl.DataFrame(
        {
            "trade_id": [1, 2, 3],
            "etime": [
                1_735_689_600_000_000_000,
                1_735_689_601_000_000_000,
                1_735_689_602_000_000_000,
            ],
            "price": [42_000.0, 42_001.5, 42_003.25],
            "qty": [0.01, 0.02, 0.015],
        }
    )
    df.write_parquet(final_path, compression="zstd")
    st = final_path.stat()

    partition = {
        "date": "2026-01-01",
        "path": REL_PARTITION_PATH,
        "sha256": hashlib.sha256(final_path.read_bytes()).hexdigest(),
        "rows": df.height,
        "size_bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "etime_min": int(df["etime"].min()),
        "etime_max": int(df["etime"].max()),
    }

    return issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[partition],
        code_hash="ci-fixture-0001",
        registry_root=REGISTRY_ROOT,
    )


def main() -> int:
    manifest = generate()
    print(f"generated fixture manifest {manifest['manifest_id']}")
    print(f"  lake_root:     {LAKE_ROOT}")
    print(f"  registry_root: {REGISTRY_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
