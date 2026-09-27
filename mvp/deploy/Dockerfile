# AiHedgeFund BTCUSDT capture daemon -- containerized deploy artifact.
#
# Per 01-CONTEXT.md: this image is produced now so relocating capture to an
# always-on VPS later is a config change, not a rewrite. It is NOT used on
# this local Mac -- Phase 1's actual deployment is the caffeinate-wrapped
# foreground process (`./.venv/bin/python3 -m data.capture.daemon`, see
# daemon.py's module docstring and 01-02/03/04-SUMMARY.md's live checkpoints).
#
# Build from the mvp/ directory as the context:
#   docker build -f deploy/Dockerfile -t aihedgefund-capture mvp
# Run (data_root supplied ONLY at `docker run` time -- never baked into the
# image, consistent with config.py's "data_root has no default" rule):
#   docker run --rm \
#     -e CAPTURE_DATA_ROOT=/data \
#     -v /host/path/to/capture:/data \
#     aihedgefund-capture --symbol BTCUSDT
# The mounted /data volume must be writable by the "capture" user created
# below (non-root, per T-1-15 in this plan's threat model).

FROM python:3.13-slim

# uv manages the environment; UV_PYTHON_DOWNLOADS=never forces it to use
# this image's own pinned 3.13 interpreter for `uv sync` instead of
# fetching a standalone Python build over the network at build time.
ENV UV_PYTHON_DOWNLOADS=never
RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Only the runtime code + config are shipped -- tests/ and scripts/ are
# dev-only and deliberately excluded from the production image (smaller
# image, no test-only code shipped).
COPY data ./data
COPY configs ./configs

RUN useradd --create-home capture
USER capture

# Invoke the venv's interpreter directly, NOT `uv run` -- `uv run` holds
# the global ~/.cache/uv/.lock for the process's entire lifetime, which
# would also force a network-dependent `uv sync` re-check at every
# container start (needing the dev group's network access). This mirrors
# the exact fix Plan 02 made for local long-lived runs (see daemon.py's
# module docstring and 01-02-SUMMARY.md's "uv run" hazard finding).
ENTRYPOINT ["/app/.venv/bin/python3", "-m", "data.capture.daemon"]
