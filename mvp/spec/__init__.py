"""Machine-readable catalogue source of truth (features/labels) and its renderer.

`mvp/spec/features.toml` and `mvp/spec/labels.toml` are the single source of truth
for feature/label names; `catalogue.py` loads them into typed registries; `render.py`
regenerates `mvp/spec.md`'s marker-delimited catalogue tables from them. See
`mvp/spec.md`'s "Feature catalogue" / "Label catalogue" sections.
"""
