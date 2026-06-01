"""Packaged authoritative-knowledge data for the dual-track RAG (D013).

Shipping the KB inside the package (rather than reaching for a repo-root path)
ensures it is available when the project is installed as a wheel/console script,
not only in an editable source tree (fixes C7).
"""
