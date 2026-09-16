"""Small synthetic archive-CSV fixture: header + a handful of rows matching
the measured `futures-um.trades` 6-column schema
(`id,price,qty,quote_qty,time,is_buyer_maker`), so ingest tests don't depend
on the real 42MB probe file."""

from __future__ import annotations

from pathlib import Path

SAMPLE_ARCHIVE_CSV_HEADER = "id,price,qty,quote_qty,time,is_buyer_maker"

SAMPLE_ARCHIVE_CSV_ROWS = [
    "8072574559,63000.10,0.010,630.001,1789171200002,true",
    "8072574560,63000.50,0.020,1260.010,1789171200015,false",
    "8072574561,62999.90,0.005,314.9995,1789171200302,true",
]


def write_sample_archive_csv(path: Path) -> Path:
    """Write the sample header + rows to `path`; returns `path`."""
    path.write_text(
        SAMPLE_ARCHIVE_CSV_HEADER + "\n" + "\n".join(SAMPLE_ARCHIVE_CSV_ROWS) + "\n"
    )
    return path
