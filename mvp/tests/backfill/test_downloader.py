"""Tests for data.backfill.client -- S3 listing, checksum-verified download,
and BackfillClient idempotency. No test hits the real network: `fetch`/
`open_stream` are injected fakes."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from data.backfill.client import (
    BackfillClient,
    ChecksumError,
    download_and_verify,
    fetch_checksum,
    list_month,
    list_paginated,
    verify_file,
)

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
