#!/usr/bin/env python3
"""Validate, ingest, or purge RAG source manifests in Qdrant.

This path is kept for runbooks and tenant-offboard.py; the implementation is the RAG
service's own ingestion CLI (src/rag-service/app/ingest.py, also run by the chart's
ingestion job), so batching, stale-chunk pruning and manifest validation exist once.
Run it with an interpreter that has the RAG runtime dependencies, for example
src/rag-service/.venv/bin/python or the gateway environment. See --help for the flags.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAG_SERVICE_DIR = Path(__file__).resolve().parents[1] / "src/rag-service"
sys.path.insert(0, str(RAG_SERVICE_DIR))

from app.ingest import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
