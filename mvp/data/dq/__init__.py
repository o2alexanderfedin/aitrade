"""Daily data-quality report package (DATA-07): six checks (`checks.py`),
the report entrypoint (`report.py`), and the acknowledgement mechanism
enforced by `data/store.py:load_curated`.

Deliberately does not import `report` here -- `report.py` imports from
`data.store` (to resolve manifests) and `data.store` will, in a later
step of this same plan, need to read DQ report status without importing
`data.dq.report` back (that would be a cycle). Keep this package's
`__init__.py` free of eager submodule imports so either direction stays
importable.
"""
