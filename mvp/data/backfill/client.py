"""Binance archive backfill client: date-narrowed S3 listing, streaming
checksum-verified download, and explicit-member zip extraction.

Every network call goes through an injectable `fetch`/`open_stream` callable
(defaulting to stdlib `urllib.request`) so tests exercise real parsing logic
against fixture bytes without touching the network -- see 03-RESEARCH.md's
Code Examples section, which this module implements almost verbatim, plus
the namespace-aware XML fix the real bucket's response requires (measured
this session: `data.binance.vision`'s `ListBucketResult` carries the default
namespace `http://s3.amazonaws.com/doc/2006-03-01/`, so a bare
`root.findall("Key")` silently returns nothing).

Threat model (03-01-PLAN.md):
- T-03-02 (Tampering / corrupted-download-silently-ingested): `download_and_verify`
  streams into `dest_tmp` while updating a running sha256, compares against the
  `.CHECKSUM` sidecar, and deletes `dest_tmp` + raises `ChecksumError` on mismatch
  BEFORE the caller ever renames it into a "verified" location.
- T-03-01 (Tampering / zip-slip): `extract_expected_member` extracts only the
  one explicitly-named member, never `extractall()`/`namelist()` iteration, and
  additionally rejects any traversal component in the expected name itself as a
  defense-in-depth layer on top of `zipfile.extract()`'s own post-3.6 path
  sanitization.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

S3_BUCKET_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/"
S3_NAMESPACE = "{http://s3.amazonaws.com/doc/2006-03-01/}"
ARCHIVE_BASE_URL = "https://data.binance.vision/"

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

Fetcher = Callable[[str], bytes]
StreamOpener = Callable[[str], object]


class ChecksumError(ValueError):
    """Raised when a downloaded/staged file's sha256 does not match its
    `.CHECKSUM` sidecar."""


class ArchiveMemberError(ValueError):
    """Raised when a zip's expected member is absent, or its name is unsafe."""


def _default_fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def _default_open_stream(url: str):
    return urllib.request.urlopen(url, timeout=60)


def _keys_from_xml(text: str) -> tuple[list[str], bool, str | None]:
    """Parse an S3 `ListBucketResult` XML document, namespace-aware."""
    root = ET.fromstring(text)
    ns = S3_NAMESPACE
    keys = [el.text for el in root.iter(f"{ns}Key") if el.text]
    truncated_el = root.find(f"{ns}IsTruncated")
    is_truncated = truncated_el is not None and truncated_el.text == "true"
    next_marker_el = root.find(f"{ns}NextMarker")
    next_marker = next_marker_el.text if next_marker_el is not None else None
    return keys, is_truncated, next_marker


def list_paginated(prefix: str, *, fetch: Fetcher = _default_fetch) -> list[str]:
    """S3 v1 `Marker`/`NextMarker` pagination loop over `prefix`.

    Used defensively for any prefix that might exceed `max-keys=1000` -- a
    per-month prefix never does (measured: 28 keys/month, `IsTruncated=false`),
    but an unbounded prefix (e.g. the whole `trades/BTCUSDT/` tree) would.
    """
    keys: list[str] = []
    marker = ""
    while True:
        url = (
            f"{S3_BUCKET_URL}?delimiter=/&prefix={urllib.parse.quote(prefix)}"
            f"&marker={urllib.parse.quote(marker)}"
        )
        text = fetch(url).decode()
        page_keys, is_truncated, next_marker = _keys_from_xml(text)
        keys.extend(page_keys)
        if not is_truncated or not next_marker:
            break
        marker = next_marker
    return keys


def list_month(
    symbol: str,
    year_month: str,
    market: str = "futures-um",
    dataset: str = "trades",
    *,
    fetch: Fetcher = _default_fetch,
) -> list[str]:
    """List archive keys for one `(symbol, year_month)`, e.g. `year_month="2026-09"`.

    The URL template below is the one measured `futures-um`/`trades` shape
    (03-CONTEXT.md decision: hardcode it for now rather than generalize from
    an unmeasured unit-registry field). A future dataset needs its own
    measured template, not a guessed substitution.
    """
    if (market, dataset) != ("futures-um", "trades"):
        raise ValueError(
            "list_month only has the measured futures-um/trades URL template; "
            f"got (market={market!r}, dataset={dataset!r})"
        )
    prefix = f"data/futures/um/daily/trades/{symbol}/{symbol}-trades-{year_month}"
    return list_paginated(prefix, fetch=fetch)


def _parse_checksum_sidecar(raw: bytes) -> str:
    """Return the sha256 hex digest that is the sidecar's first whitespace-
    separated token, raising `ChecksumError` if it is not 64 hex characters
    (guards against silently comparing against a garbage "expected" digest,
    e.g. an HTML error-page body from a 404)."""
    text = raw.decode(errors="replace").strip()
    token = text.split()[0] if text else ""
    if not _HEX64_RE.match(token):
        raise ChecksumError(f"malformed .CHECKSUM sidecar content: {text!r}")
    return token


def fetch_checksum(checksum_url: str, *, fetch: Fetcher = _default_fetch) -> str:
    """Fetch and parse a `.CHECKSUM` sidecar's expected sha256 digest."""
    return _parse_checksum_sidecar(fetch(checksum_url))


def verify_file(path: Path, expected_digest: str) -> str:
    """Stream-hash an already-on-disk file and compare against `expected_digest`.

    Reusable by both a fresh download (via `download_and_verify`) and a
    pre-existing cached file with no live HTTP response in this run (Plan
    03-01's own real-slice invocation against the already-downloaded probe
    zip) -- one hash-compare implementation, not two.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    if digest != expected_digest:
        raise ChecksumError(
            f"checksum mismatch for {path}: expected {expected_digest}, got {digest}"
        )
    return digest


def download_and_verify(
    url: str,
    checksum_url: str,
    dest_tmp: Path,
    *,
    fetch: Fetcher = _default_fetch,
    open_stream: StreamOpener = _default_open_stream,
) -> str:
    """Stream `url` into `dest_tmp` in fixed-size chunks while updating a
    running sha256, verify against `checksum_url`'s sidecar, and return the
    verified digest.

    On mismatch, `dest_tmp` is deleted and `ChecksumError` raised BEFORE
    returning -- the corrupted/truncated file never reaches a "verified"
    state (T-03-02).
    """
    expected = fetch_checksum(checksum_url, fetch=fetch)
    dest_tmp.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with open_stream(url) as resp, open(dest_tmp, "wb") as f:
        for chunk in iter(lambda: resp.read(1 << 20), b""):
            h.update(chunk)
            f.write(chunk)
    digest = h.hexdigest()
    if digest != expected:
        dest_tmp.unlink(missing_ok=True)
        raise ChecksumError(
            f"checksum mismatch for {url}: expected {expected}, got {digest}"
        )
    return digest


def extract_expected_member(zip_path: Path, expected_name: str, dest_dir: Path) -> Path:
    """Extract only `expected_name` from `zip_path` into `dest_dir`, by
    explicit name -- never `extractall()`, never iterating `namelist()`.

    Raises `ArchiveMemberError` if `expected_name` is not a member (naming
    the actual member list) or if `expected_name` itself contains a
    traversal/absolute-path component -- defense-in-depth on top of
    `zipfile.extract()`'s own post-3.6 sanitization (T-03-01).
    """
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if expected_name not in names:
            raise ArchiveMemberError(
                f"expected member {expected_name!r} not found in {zip_path}; "
                f"actual members: {names}"
            )
        member_path = Path(expected_name)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ArchiveMemberError(f"unsafe member name: {expected_name!r}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        extracted = zf.extract(expected_name, dest_dir)
    return Path(extracted)


@dataclass
class BackfillClient:
    """Idempotent per-`(symbol, date)` downloader: skips re-download if the
    `.verified` marker for that day already exists in `staging_root`."""

    staging_root: Path
    market: str = "futures-um"
    dataset: str = "trades"
    fetch: Fetcher = field(default=_default_fetch)
    open_stream: StreamOpener = field(default=_default_open_stream)

    def _day_dir(self, symbol: str, date: str) -> Path:
        return self.staging_root / symbol / date

    def _verified_marker(self, symbol: str, date: str) -> Path:
        return self._day_dir(symbol, date) / ".verified"

    def _zip_name(self, symbol: str, date: str) -> str:
        return f"{symbol}-{self.dataset}-{date}.zip"

    def ensure_downloaded(self, symbol: str, date: str) -> Path:
        """Idempotently download + checksum-verify the zip for `(symbol, date)`.

        Returns the final zip path. A second call for an already-verified
        `(symbol, date)` makes zero `fetch`/`open_stream` calls.
        """
        day_dir = self._day_dir(symbol, date)
        marker = self._verified_marker(symbol, date)
        zip_path = day_dir / self._zip_name(symbol, date)

        if marker.exists() and zip_path.exists():
            return zip_path

        day_dir.mkdir(parents=True, exist_ok=True)
        url = f"{ARCHIVE_BASE_URL}data/futures/um/daily/{self.dataset}/{symbol}/{self._zip_name(symbol, date)}"
        checksum_url = url + ".CHECKSUM"
        tmp_path = zip_path.with_suffix(zip_path.suffix + ".tmp")

        download_and_verify(
            url, checksum_url, tmp_path, fetch=self.fetch, open_stream=self.open_stream
        )
        tmp_path.replace(zip_path)

        marker_tmp = marker.with_suffix(marker.suffix + ".tmp")
        marker_tmp.write_text("verified\n")
        marker_tmp.replace(marker)
        return zip_path
