"""Tests for data.backfill.client (S3 listing, checksum-verified download,
BackfillClient idempotency) and data.backfill.downloader (03-03-PLAN.md
Task 1: monthly-vs-daily regime selection, skip-and-continue per-date
idempotency). No test hits the real network: `fetch`/`open_stream` are
injected fakes."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from data.backfill import downloader
from data.backfill.client import (
    BackfillClient,
    ChecksumError,
    download_and_verify,
    fetch_checksum,
    list_month,
    list_paginated,
    verify_file,
)
from data.ingest.normalize import raw_partition_exists, write_raw_partition
from data.unit_registry import get_unit_entry

SAMPLE_LISTING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Name>data.binance.vision</Name><Prefix>data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2026-09</Prefix><Marker></Marker><MaxKeys>1000</MaxKeys><Delimiter>/</Delimiter><IsTruncated>false</IsTruncated><Contents><Key>data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2026-09-01.zip</Key><LastModified>2026-09-02T07:40:48.000Z</LastModified><Size>26293521</Size></Contents><Contents><Key>data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2026-09-01.zip.CHECKSUM</Key><LastModified>2026-09-02T07:40:47.000Z</LastModified><Size>96</Size></Contents><Contents><Key>data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2026-09-12.zip</Key><LastModified>2026-09-13T06:56:57.000Z</LastModified><Size>5974453</Size></Contents></ListBucketResult>"""

PAGE_1_TRUNCATED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>true</IsTruncated><NextMarker>data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2021-01-19.zip</NextMarker><Contents><Key>data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2020-01-01.zip</Key></Contents></ListBucketResult>"""

PAGE_2_FINAL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated><Contents><Key>data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2021-01-20.zip</Key></Contents></ListBucketResult>"""


def test_list_month_returns_keys_from_mocked_response():
    calls = []

    def fake_fetch(url: str) -> bytes:
        calls.append(url)
        return SAMPLE_LISTING_XML.encode()

    keys = list_month("BTCUSDT", "2026-09", fetch=fake_fetch)
    assert keys == [
        "data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2026-09-01.zip",
        "data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2026-09-01.zip.CHECKSUM",
        "data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2026-09-12.zip",
    ]
    assert len(calls) == 1
    assert (
        "prefix=data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2026-09" in calls[0]
    )


def test_list_month_rejects_unmeasured_market_dataset():
    with pytest.raises(ValueError, match="futures-um/trades"):
        list_month("BTCUSDT", "2026-09", market="spot", dataset="trades")


def test_list_paginated_follows_next_marker():
    pages = [PAGE_1_TRUNCATED_XML, PAGE_2_FINAL_XML]
    calls = []

    def fake_fetch(url: str) -> bytes:
        calls.append(url)
        return pages[len(calls) - 1].encode()

    keys = list_paginated("data/futures/um/daily/trades/BTCUSDT/", fetch=fake_fetch)
    assert keys == [
        "data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2020-01-01.zip",
        "data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2021-01-20.zip",
    ]
    assert len(calls) == 2


def test_fetch_checksum_parses_first_token():
    def fake_fetch(url: str) -> bytes:
        return b"5aaf475f90b199390c2e80c620a7b3fc1cc0309b648d9a96862888f69c92d78c  BTCUSDT-trades-2026-09-12.zip\n"

    digest = fetch_checksum("http://example/x.CHECKSUM", fetch=fake_fetch)
    assert digest == "5aaf475f90b199390c2e80c620a7b3fc1cc0309b648d9a96862888f69c92d78c"


def test_fetch_checksum_rejects_malformed_sidecar():
    def fake_fetch(url: str) -> bytes:
        return b"<html>404 Not Found</html>"

    with pytest.raises(ChecksumError, match="malformed"):
        fetch_checksum("http://example/x.CHECKSUM", fetch=fake_fetch)


class _FakeStream:
    """Minimal context manager wrapping BytesIO, mimicking urlopen's response."""

    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


def test_download_and_verify_success(tmp_path: Path):
    body = b"pretend zip bytes" * 1000
    digest = hashlib.sha256(body).hexdigest()

    def fake_fetch(url: str) -> bytes:
        return f"{digest}  file.zip\n".encode()

    def fake_open_stream(url: str):
        return _FakeStream(body)

    dest = tmp_path / "staged.zip.tmp"
    result_digest = download_and_verify(
        "http://example/file.zip",
        "http://example/file.zip.CHECKSUM",
        dest,
        fetch=fake_fetch,
        open_stream=fake_open_stream,
    )
    assert result_digest == digest
    assert dest.read_bytes() == body


def test_download_and_verify_checksum_mismatch_raises_and_deletes_file(tmp_path: Path):
    body = b"corrupted bytes"
    wrong_digest = "0" * 64

    def fake_fetch(url: str) -> bytes:
        return f"{wrong_digest}  file.zip\n".encode()

    def fake_open_stream(url: str):
        return _FakeStream(body)

    dest = tmp_path / "staged.zip.tmp"
    with pytest.raises(ChecksumError, match="checksum mismatch"):
        download_and_verify(
            "http://example/file.zip",
            "http://example/file.zip.CHECKSUM",
            dest,
            fetch=fake_fetch,
            open_stream=fake_open_stream,
        )
    assert not dest.exists()


def test_verify_file_against_on_disk_file(tmp_path: Path):
    body = b"already staged bytes"
    path = tmp_path / "staged.zip"
    path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    assert verify_file(path, digest) == digest


def test_verify_file_mismatch_raises(tmp_path: Path):
    path = tmp_path / "staged.zip"
    path.write_bytes(b"bytes")
    with pytest.raises(ChecksumError):
        verify_file(path, "0" * 64)


def test_backfill_client_second_run_does_not_redownload(tmp_path: Path):
    body = b"zip bytes" * 500
    digest = hashlib.sha256(body).hexdigest()
    calls = {"fetch": 0, "open_stream": 0}

    def fake_fetch(url: str) -> bytes:
        calls["fetch"] += 1
        return f"{digest}  BTCUSDT-trades-2026-09-12.zip\n".encode()

    def fake_open_stream(url: str):
        calls["open_stream"] += 1
        return _FakeStream(body)

    client = BackfillClient(
        staging_root=tmp_path, fetch=fake_fetch, open_stream=fake_open_stream
    )
    first_path = client.ensure_downloaded("BTCUSDT", "2026-09-12")
    assert first_path.exists()
    assert calls["fetch"] == 1
    assert calls["open_stream"] == 1

    second_path = client.ensure_downloaded("BTCUSDT", "2026-09-12")
    assert second_path == first_path
    assert calls["fetch"] == 1, "second run must not re-fetch the checksum"
    assert calls["open_stream"] == 1, "second run must not re-download the zip"


def test_backfill_client_monthly_granularity_hits_monthly_url_path(tmp_path: Path):
    """granularity='monthly' must hit .../monthly/... not .../daily/...,
    with `date` shaped YYYY-MM (per client.py's docstring)."""
    body = b"zip bytes" * 10
    digest = hashlib.sha256(body).hexdigest()
    urls_hit = []

    def fake_fetch(url: str) -> bytes:
        urls_hit.append(url)
        return f"{digest}  BTCUSDT-trades-2026-06.zip\n".encode()

    def fake_open_stream(url: str):
        urls_hit.append(url)
        return _FakeStream(body)

    client = BackfillClient(
        staging_root=tmp_path,
        granularity="monthly",
        fetch=fake_fetch,
        open_stream=fake_open_stream,
    )
    path = client.ensure_downloaded("BTCUSDT", "2026-06")
    assert path.name == "BTCUSDT-trades-2026-06.zip"
    assert any("/monthly/" in u for u in urls_hit)
    assert not any("/daily/" in u for u in urls_hit)


def test_backfill_client_rejects_unknown_granularity(tmp_path: Path):
    with pytest.raises(ValueError, match="granularity"):
        BackfillClient(staging_root=tmp_path, granularity="weekly")


# --- data.backfill.downloader ---------------------------------------------


def test_daterange_inclusive_both_ends():
    assert downloader.daterange("2026-08-30", "2026-09-02") == [
        "2026-08-30",
        "2026-08-31",
        "2026-09-01",
        "2026-09-02",
    ]


def test_daterange_empty_when_end_before_start():
    assert downloader.daterange("2026-09-02", "2026-08-30") == []


def test_month_dates_handles_31_day_and_leap_free_month():
    aug = downloader.month_dates("2026-08")
    assert len(aug) == 31
    assert aug[0] == "2026-08-01"
    assert aug[-1] == "2026-08-31"
    sep = downloader.month_dates("2026-09")
    assert len(sep) == 30


def test_day_bounds_ms_matches_known_archive_row():
    # 2026-09-12's first measured archive row time is 1789171200002 ms
    # (00:00:00.002Z), per evidence/PROBE-RESULTS.md section 1.
    start_ms, end_ms = downloader.day_bounds_ms("2026-09-12")
    assert start_ms == 1789171200000
    assert end_ms == start_ms + 86_400_000
    assert start_ms <= 1789171200002 < end_ms


def _zip_bytes(member_name: str, csv_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member_name, csv_text)
    return buf.getvalue()


def _archive_row(trade_id: int, day: date, seconds_into_day: int) -> str:
    ts_ms = (
        int(
            datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()
            * 1000
        )
        + seconds_into_day * 1000
    )
    maker = "true" if trade_id % 2 == 0 else "false"
    return f"{trade_id},100.0,1.0,100.0,{ts_ms},{maker}"


def _make_monthly_fakes(csv_text: str, zip_name: str):
    body = _zip_bytes(zip_name.replace(".zip", ".csv"), csv_text)
    digest = hashlib.sha256(body).hexdigest()

    def fake_fetch(url: str) -> bytes:
        return f"{digest}  {zip_name}\n".encode()

    def fake_open_stream(url: str):
        return _FakeStream(body)

    return fake_fetch, fake_open_stream


def test_run_backfill_dispatches_monthly_then_daily_across_regime_boundary(
    tmp_path: Path, monkeypatch
):
    """A mocked date range spanning 2026-08-30 .. 2026-09-02 (the
    monthly/daily regime boundary) must route August dates through
    ingest_monthly (once, for the whole month, with the range's start/end
    passed through) and September dates through ingest_daily_date (one
    call per date) -- Task 1's 'unit-tested against a mocked date range
    spanning both regimes' done criterion."""
    calls: list[tuple] = []

    def fake_ingest_monthly(
        symbol,
        year_month,
        *,
        client,
        lake_root_path,
        unit_entry,
        start_date=None,
        end_date=None,
    ):
        calls.append(("monthly", year_month, start_date, end_date))
        dates = [
            d
            for d in downloader.month_dates(year_month)
            if (not start_date or d >= start_date) and (not end_date or d <= end_date)
        ]
        return [{"date": d, "status": "written", "rows": 1} for d in dates]

    def fake_ingest_daily_date(symbol, date_str, *, client, lake_root_path, unit_entry):
        calls.append(("daily", date_str))
        return {"date": date_str, "status": "written", "rows": 1}

    def fake_published(symbol, year_month, *, fetch=None):
        calls.append(("published", year_month))
        return set(downloader.month_dates(year_month))

    monkeypatch.setattr(downloader, "ingest_monthly", fake_ingest_monthly)
    monkeypatch.setattr(downloader, "ingest_daily_date", fake_ingest_daily_date)
    monkeypatch.setattr(downloader, "published_daily_dates", fake_published)

    lake_root_path = tmp_path / "lake"
    staging_root_path = tmp_path / "staging"
    lake_root_path.mkdir()
    staging_root_path.mkdir()

    summary = downloader.run_backfill(
        "BTCUSDT",
        "2026-08-30",
        "2026-09-02",
        staging_root_path=staging_root_path,
        lake_root_path=lake_root_path,
    )

    monthly_calls = [c for c in calls if c[0] == "monthly"]
    daily_calls = sorted(c for c in calls if c[0] == "daily")
    assert monthly_calls == [("monthly", "2026-08", "2026-08-30", "2026-09-02")]
    assert daily_calls == [
        ("daily", "2026-09-01"),
        ("daily", "2026-09-02"),
    ]
    # 2 August dates (via the monthly fake) + 2 September dates (via the
    # daily fake) = 4 "written" entries.
    assert summary["written"] == 4
    assert summary["not_found"] == 0
    assert summary["not_published"] == 0


def test_ingest_daily_date_second_call_is_noop_skip_no_network(tmp_path: Path):
    """A second ingest_daily_date call for an already-written date makes
    zero fetch/open_stream calls and returns status='already_present' --
    the resumability guarantee at the raw-tier level."""
    lake_root_path = tmp_path / "lake"
    staging_root_path = tmp_path / "staging"
    lake_root_path.mkdir()
    staging_root_path.mkdir()
    unit_entry = get_unit_entry("futures-um", "trades")

    csv_text = "id,price,qty,quote_qty,time,is_buyer_maker\n" + "\n".join(
        _archive_row(i, date(2026, 9, 1), i) for i in range(1, 4)
    )
    fake_fetch, fake_open_stream = _make_monthly_fakes(
        csv_text, "BTCUSDT-trades-2026-09-01.zip"
    )
    calls = {"fetch": 0, "open_stream": 0}

    def counting_fetch(url):
        calls["fetch"] += 1
        return fake_fetch(url)

    def counting_open_stream(url):
        calls["open_stream"] += 1
        return fake_open_stream(url)

    client = BackfillClient(
        staging_root=staging_root_path,
        granularity="daily",
        fetch=counting_fetch,
        open_stream=counting_open_stream,
    )

    first = downloader.ingest_daily_date(
        "BTCUSDT",
        "2026-09-01",
        client=client,
        lake_root_path=lake_root_path,
        unit_entry=unit_entry,
    )
    assert first["status"] == "written"
    assert first["rows"] == 3
    assert calls["fetch"] == 1
    assert calls["open_stream"] == 1

    second = downloader.ingest_daily_date(
        "BTCUSDT",
        "2026-09-01",
        client=client,
        lake_root_path=lake_root_path,
        unit_entry=unit_entry,
    )
    assert second["status"] == "already_present"
    assert calls["fetch"] == 1, "no network call on the already-written re-run"
    assert calls["open_stream"] == 1


def test_ingest_monthly_skips_already_written_day_and_still_processes_adjacent_day(
    tmp_path: Path,
):
    """The per-date skip-and-continue idiom: a day whose raw partition
    already exists is skipped (no FileExistsError propagating out of the
    loop), while an adjacent not-yet-written day in the same range still
    gets processed -- Task 1's central resumability done criterion."""
    lake_root_path = tmp_path / "lake"
    staging_root_path = tmp_path / "staging"
    lake_root_path.mkdir()
    staging_root_path.mkdir()
    unit_entry = get_unit_entry("futures-um", "trades")

    day1, day2 = date(2026, 8, 30), date(2026, 8, 31)
    csv_text = "id,price,qty,quote_qty,time,is_buyer_maker\n" + "\n".join(
        [_archive_row(i, day1, i) for i in range(1, 4)]
        + [_archive_row(i, day2, i) for i in range(4, 7)]
    )
    fake_fetch, fake_open_stream = _make_monthly_fakes(
        csv_text, "BTCUSDT-trades-2026-08.zip"
    )

    # Pre-populate day1's raw partition directly (simulating an already-
    # completed prior run), matching write_raw_partition's own write shape.
    from data.ingest.normalize import normalize_archive_frame
    import polars as pl

    preexisting = pl.DataFrame(
        {
            "id": [999],
            "price": ["1.0"],
            "qty": ["1.0"],
            "quote_qty": ["1.0"],
            "time": [
                int(datetime(2026, 8, 30, tzinfo=timezone.utc).timestamp() * 1000) + 500
            ],
            "is_buyer_maker": [True],
        }
    )
    pre_frame = normalize_archive_frame(preexisting, unit_entry, "BTCUSDT", 1)
    write_raw_partition(
        pre_frame, "BTCUSDT", "trade", "archive", "2026-08-30", lake_root_path
    )
    assert raw_partition_exists(
        lake_root_path, "BTCUSDT", "trade", "archive", "2026-08-30"
    )

    client = BackfillClient(
        staging_root=staging_root_path,
        granularity="monthly",
        fetch=fake_fetch,
        open_stream=fake_open_stream,
    )

    results = downloader.ingest_monthly(
        "BTCUSDT",
        "2026-08",
        client=client,
        lake_root_path=lake_root_path,
        unit_entry=unit_entry,
        start_date="2026-08-30",
        end_date="2026-08-31",
    )

    by_date = {r["date"]: r for r in results}
    assert by_date["2026-08-30"]["status"] == "already_present"
    assert by_date["2026-08-31"]["status"] == "written"
    assert by_date["2026-08-31"]["rows"] == 3
    assert raw_partition_exists(
        lake_root_path, "BTCUSDT", "trade", "archive", "2026-08-31"
    )


def test_ingest_monthly_catches_racey_file_exists_error_and_continues(
    tmp_path: Path, monkeypatch
):
    """If write_raw_partition itself raises FileExistsError (a race between
    the pre-check and the write), ingest_monthly catches exactly that
    exception for the one date and continues to the next date, rather than
    the whole month's loop aborting."""
    lake_root_path = tmp_path / "lake"
    staging_root_path = tmp_path / "staging"
    lake_root_path.mkdir()
    staging_root_path.mkdir()
    unit_entry = get_unit_entry("futures-um", "trades")

    day1, day2 = date(2026, 8, 30), date(2026, 8, 31)
    csv_text = "id,price,qty,quote_qty,time,is_buyer_maker\n" + "\n".join(
        [_archive_row(i, day1, i) for i in range(1, 4)]
        + [_archive_row(i, day2, i) for i in range(4, 7)]
    )
    fake_fetch, fake_open_stream = _make_monthly_fakes(
        csv_text, "BTCUSDT-trades-2026-08.zip"
    )

    # Write day1's partition AFTER the fact, but keep raw_partition_exists
    # reporting False for it during ingest_monthly's pre-check, so the
    # write itself is what raises FileExistsError (simulating a race).
    from data.ingest.normalize import normalize_archive_frame
    import polars as pl

    preexisting = pl.DataFrame(
        {
            "id": [999],
            "price": ["1.0"],
            "qty": ["1.0"],
            "quote_qty": ["1.0"],
            "time": [
                int(datetime(2026, 8, 30, tzinfo=timezone.utc).timestamp() * 1000) + 500
            ],
            "is_buyer_maker": [True],
        }
    )
    pre_frame = normalize_archive_frame(preexisting, unit_entry, "BTCUSDT", 1)
    write_raw_partition(
        pre_frame, "BTCUSDT", "trade", "archive", "2026-08-30", lake_root_path
    )

    real_exists = downloader.raw_partition_exists
    call_count = {"n": 0}

    def flaky_exists(lake_root_path_, symbol, stream, source, date_str):
        call_count["n"] += 1
        if date_str == "2026-08-30":
            return False  # pre-check lies once, forcing the race path
        return real_exists(lake_root_path_, symbol, stream, source, date_str)

    monkeypatch.setattr(downloader, "raw_partition_exists", flaky_exists)

    client = BackfillClient(
        staging_root=staging_root_path,
        granularity="monthly",
        fetch=fake_fetch,
        open_stream=fake_open_stream,
    )

    results = downloader.ingest_monthly(
        "BTCUSDT",
        "2026-08",
        client=client,
        lake_root_path=lake_root_path,
        unit_entry=unit_entry,
        start_date="2026-08-30",
        end_date="2026-08-31",
    )

    by_date = {r["date"]: r for r in results}
    assert by_date["2026-08-30"]["status"] == "already_present"
    assert by_date["2026-08-31"]["status"] == "written"
