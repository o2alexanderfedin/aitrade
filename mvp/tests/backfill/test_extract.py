"""Tests for data.backfill.client.extract_expected_member -- explicit-name
extraction, never extractall()/namelist() iteration (T-03-01)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from data.backfill.client import ArchiveMemberError, extract_expected_member


def _make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_extract_expected_member_success(tmp_path: Path):
    zip_path = _make_zip(
        tmp_path / "archive.zip",
        {
            "BTCUSDT-trades-2026-09-12.csv": b"id,price,qty\n1,100,1\n",
            "BTCUSDT-trades-2026-09-12.CHECKSUM": b"deadbeef  archive.zip\n",
        },
    )
    dest_dir = tmp_path / "extracted"
    result = extract_expected_member(
        zip_path, "BTCUSDT-trades-2026-09-12.csv", dest_dir
    )
    assert result == dest_dir / "BTCUSDT-trades-2026-09-12.csv"
    assert result.read_bytes() == b"id,price,qty\n1,100,1\n"
    # Only the expected member was extracted, not the CHECKSUM sidecar too.
    assert list(dest_dir.iterdir()) == [result]


def test_extract_expected_member_missing_raises(tmp_path: Path):
    zip_path = _make_zip(tmp_path / "archive.zip", {"other.csv": b"x"})
    dest_dir = tmp_path / "extracted"
    with pytest.raises(ArchiveMemberError, match="not found"):
        extract_expected_member(zip_path, "expected.csv", dest_dir)


def test_extract_expected_member_path_traversal_name_blocked(tmp_path: Path):
    zip_path = _make_zip(tmp_path / "archive.zip", {"../../evil.csv": b"malicious"})
    dest_dir = tmp_path / "extracted"
    dest_dir.mkdir()

    with pytest.raises(ArchiveMemberError, match="unsafe member name"):
        extract_expected_member(zip_path, "../../evil.csv", dest_dir)

    # Nothing must have escaped dest_dir (or been written at all).
    escaped = tmp_path / "evil.csv"
    assert not escaped.exists()
    assert list(dest_dir.iterdir()) == []


def test_extract_expected_member_absolute_name_blocked(tmp_path: Path):
    zip_path = _make_zip(tmp_path / "archive.zip", {"/etc/evil.csv": b"malicious"})
    dest_dir = tmp_path / "extracted"
    dest_dir.mkdir()

    with pytest.raises(ArchiveMemberError, match="unsafe member name"):
        extract_expected_member(zip_path, "/etc/evil.csv", dest_dir)

    assert not Path("/etc/evil.csv").exists()
