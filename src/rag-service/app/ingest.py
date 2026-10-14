"""CLI to validate RAG source manifests and ingest chunked documents into Qdrant."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx
import yaml

from app.embeddings import EmbeddingProvider, build_embedding_provider
from app.retriever import UPSERT_BATCH_SIZE, document_id_for, title_from_content, unique_document_id
from app.settings import SANDBOX_ID_PATTERN

REQUIRED_SOURCE_FIELDS = {
    "id",
    "source",
    "classification",
    "retentionClass",
    "owner",
    "embeddingModel",
}
TEXT_SUFFIXES = {".md", ".markdown", ".txt"}


@dataclass(frozen=True)
class SourceRecord:
    """A validated RAG manifest source entry with its governance metadata."""

    id: str
    source: str
    classification: str
    retention_class: str
    owner: str
    embedding_model: str
    base_dir: Path

    @property
    def resolved_source(self) -> Path:
        """Return the source path resolved against the manifest's base directory."""
        path = Path(self.source)
        if path.is_absolute():
            return path
        return (self.base_dir / path).resolve()


@dataclass(frozen=True)
class ChunkRecord:
    """A single text chunk of a source document destined for the vector store."""

    source: SourceRecord
    document_path: Path
    chunk_index: int
    text: str
    collection_version: str
    # The document this chunk came from, as retrieval results name it. Without these the
    # service answered with the point UUID as both id and title.
    document_id: str = ""
    title: str = ""

    @property
    def point_id(self) -> str:
        """Return a stable UUID5 point id derived from version, source, path, and index."""
        key = f"{self.collection_version}:{self.source.id}:{self.document_path.as_posix()}:{self.chunk_index}"
        return str(uuid5(NAMESPACE_URL, key))

    def payload(self) -> dict[str, Any]:
        """Return the Qdrant point payload with content and governance metadata."""
        document_path = self.document_path.as_posix()
        return {
            "collection_version": self.collection_version,
            "source_id": self.source.id,
            "source": self.source.source,
            "classification": self.source.classification,
            "retentionClass": self.source.retention_class,
            "owner": self.source.owner,
            "embeddingModel": self.source.embedding_model,
            "document_path": document_path,
            "document_id": self.document_id or document_id_for(document_path),
            "title": self.title or self.document_path.stem,
            "chunk_index": self.chunk_index,
            "content": self.text,
        }


def load_manifest(path: Path) -> list[SourceRecord]:
    """Load and validate a RagSourceManifest, returning its source records."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"RAG source manifest {path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("RAG source manifest must be a YAML mapping")
    if data.get("apiVersion") != "platform.ai/v1alpha1":
        raise ValueError("RAG source manifest apiVersion must be platform.ai/v1alpha1")
    if data.get("kind") != "RagSourceManifest":
        raise ValueError("RAG source manifest kind must be RagSourceManifest")
    spec = data.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("RAG source manifest spec must be a mapping with a sources list")
    sources = spec.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("RAG source manifest spec.sources must be a non-empty list")

    records: list[SourceRecord] = []
    seen: set[str] = set()
    for index, item in enumerate(sources):
        if not isinstance(item, dict):
            raise ValueError(f"spec.sources[{index}] must be a mapping")
        missing = sorted(REQUIRED_SOURCE_FIELDS - set(item))
        if missing:
            raise ValueError(f"spec.sources[{index}] missing fields: {missing}")
        for field in sorted(REQUIRED_SOURCE_FIELDS):
            value = item[field]
            # A YAML null or list would otherwise be stored as the literal text "None" or
            # "['a']", e.g. an owner no tenant can ever match.
            if isinstance(value, bool) or not isinstance(value, str | int | float) or not str(value).strip():
                raise ValueError(f"spec.sources[{index}].{field} must be a non-empty string")
        source_id = str(item["id"]).strip()
        if source_id in seen:
            raise ValueError(f"duplicate source id: {source_id}")
        seen.add(source_id)
        records.append(
            SourceRecord(
                id=source_id,
                source=str(item["source"]).strip(),
                classification=str(item["classification"]).strip(),
                retention_class=str(item["retentionClass"]).strip(),
                owner=str(item["owner"]).strip(),
                embedding_model=str(item["embeddingModel"]).strip(),
                base_dir=path.parent,
            )
        )
    return records


def owner_warnings(sources: list[SourceRecord]) -> list[str]:
    """Return a warning for each source whose owner can never match a tenant.

    Tenant-isolated retrieval compares ``owner`` with the caller's sandbox id, which is
    always 1-63 lowercase letters, digits or hyphens. Not an error: with isolation off the
    owner is only governance metadata.
    """
    return [
        f"source {source.id}: owner {source.owner!r} is not a sandbox id (lowercase letters, digits, hyphens); "
        "tenant-isolated retrieval will never return its documents"
        for source in sources
        if not SANDBOX_ID_PATTERN.fullmatch(source.owner)
    ]


def iter_document_paths(source: SourceRecord) -> list[Path]:
    """Return the sorted text document paths contained in a source location."""
    root = source.resolved_source
    if root.is_file():
        return [root]
    if not root.is_dir():
        raise ValueError(f"source {source.id} path does not exist: {root}")
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in TEXT_SUFFIXES
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    ]


# Where a chunk may end, best first: paragraph, line, sentence, word.
_CHUNK_BREAKS = (("\n\n", 0), ("\n", 0), (". ", 1), (" ", 0))
_WHITESPACE = re.compile(r"\s")


def _chunk_end(text: str, start: int, end: int, chunk_chars: int) -> int:
    """Return where a chunk starting at ``start`` should end, at most at ``end``.

    The best break in the second half of the window wins; with none, the chunk is cut at
    ``end`` (one long unbroken token). Cutting at the window edge used to split words.
    """
    floor = start + chunk_chars // 2
    for separator, keep in _CHUNK_BREAKS:
        position = text.rfind(separator, floor, end)
        if position > floor:
            return position + keep
    return end


def chunk_text(value: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    """Split text into overlapping chunks that end on paragraph, line, sentence or word breaks.

    The overlap carried into the next chunk starts on a word and is at most half of the
    chunk it repeats, so every step advances by at least a quarter chunk. It used to be
    able to step back to one character past the previous start, so an overlap above half
    a chunk produced one chunk per character on paragraph-rich text.
    """
    compact = "\n".join(line.rstrip() for line in value.splitlines()).strip()
    if not compact:
        return []
    length = len(compact)
    chunks: list[str] = []
    start = 0
    while start < length:
        end = min(start + chunk_chars, length)
        if end < length:
            end = _chunk_end(compact, start, end, chunk_chars)
        chunk = compact[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        next_start = end - min(overlap_chars, (end - start) // 2)
        if next_start < end:
            # Begin the overlap on a word rather than inside one.
            space = _WHITESPACE.search(compact, next_start, end)
            if space is not None:
                next_start = space.end()
        start = max(next_start, start + 1)
    return chunks


def _read_source_document(source: SourceRecord, path: Path) -> str:
    """Read a source document as UTF-8 (a leading byte-order mark is dropped).

    Ingestion is the governed write path, so an undecodable file fails the run with its
    name instead of being written with replacement characters.
    """
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"source {source.id}: {path} is not valid UTF-8 (first bad byte at offset {exc.start}); "
            "re-encode it as UTF-8 or remove it from the source"
        ) from exc


def build_chunks(
    sources: list[SourceRecord],
    chunk_chars: int,
    overlap_chars: int,
    collection_version: str = "v1",
) -> list[ChunkRecord]:
    """Read every source document and return its chunk records for ingestion.

    Each chunk names its document (the id and title retrieval results show). Document ids
    follow the knowledge-directory rule and are unique across the whole manifest.
    """
    chunks: list[ChunkRecord] = []
    seen_ids: set[str] = set()
    for source in sources:
        root = source.resolved_source
        for path in iter_document_paths(source):
            relative = path.relative_to(root) if root.is_dir() else Path(path.name)
            text = _read_source_document(source, path)
            document_id = unique_document_id(document_id_for(relative.as_posix()), seen_ids)
            title = title_from_content(path, text)
            for index, chunk in enumerate(chunk_text(text, chunk_chars, overlap_chars)):
                chunks.append(
                    ChunkRecord(
                        source=source,
                        document_path=Path(relative),
                        chunk_index=index,
                        text=chunk,
                        collection_version=collection_version,
                        document_id=document_id,
                        title=title,
                    )
                )
    return chunks


def _collection_dimensions(payload: dict[str, Any]) -> int | None:
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return None
    vectors = result.get("config", {}).get("params", {}).get("vectors")
    if isinstance(vectors, dict) and isinstance(vectors.get("size"), int):
        return vectors["size"]
    return None


def ensure_collection(
    client: httpx.Client,
    base_url: str,
    collection: str,
    dimensions: int,
) -> str:
    """Create the Qdrant collection if absent and verify its vector dimensions."""
    url = f"{base_url.rstrip('/')}/collections/{collection}"
    response = client.get(url)
    if response.status_code == 404:
        created = client.put(
            url,
            json={"vectors": {"size": dimensions, "distance": "Cosine"}},
        )
        created.raise_for_status()
        return "created"
    response.raise_for_status()
    existing_dimensions = _collection_dimensions(response.json())
    if existing_dimensions is not None and existing_dimensions != dimensions:
        raise ValueError(f"collection {collection} has {existing_dimensions} dimensions; expected {dimensions}")
    return "ready"


def upsert_chunks(
    chunks: list[ChunkRecord],
    provider: EmbeddingProvider,
    base_url: str,
    collection: str,
    timeout_seconds: float,
    batch_size: int = UPSERT_BATCH_SIZE,
    ingested_at_time: datetime | None = None,
) -> dict[str, Any]:
    """Embed and upsert chunk points into Qdrant, returning an ingestion summary.

    Chunks go in batches of ``batch_size``: each batch is embedded with batched requests
    and written with one upsert. A single upsert of the whole corpus used to exceed
    Qdrant's 32 MB request limit at roughly 7,000 chunks, and one embedding request (on a
    fresh connection) per chunk dominated the run time.
    """
    now = ingested_at_time or datetime.now(UTC)
    ingested_at = now.isoformat()
    ingested_at_epoch = int(now.timestamp())

    def _point(chunk: ChunkRecord, vector: list[float]) -> dict[str, Any]:
        payload = chunk.payload()
        # Stamp ingestion time so age-based retention purge can range-filter on ingestedAtEpoch.
        payload["ingestedAt"] = ingested_at
        payload["ingestedAtEpoch"] = ingested_at_epoch
        return {"id": chunk.point_id, "vector": vector, "payload": payload}

    with httpx.Client(timeout=timeout_seconds) as client:
        collection_status = ensure_collection(client, base_url, collection, provider.dimensions)
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = provider.embed_many([chunk.text for chunk in batch])
            response = client.put(
                f"{base_url.rstrip('/')}/collections/{collection}/points",
                params={"wait": "true"},
                json={"points": [_point(chunk, vector) for chunk, vector in zip(batch, vectors, strict=True)]},
            )
            response.raise_for_status()
    return {
        "collection": collection,
        "collection_status": collection_status,
        "upserted_chunks": len(chunks),
    }


def prune_stale_chunks(
    source_ids: list[str],
    collection_version: str,
    written_at_epoch: int,
    base_url: str,
    collection: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Delete the points of these sources that an ingestion at ``written_at_epoch`` did not write.

    Chunk point ids are stable per document and position, so re-ingesting overwrites what
    still exists, but the tail chunks of a document that got shorter, and every chunk of a
    file removed from its source, stayed retrievable indefinitely. Points written by the run
    carry its epoch; anything older for the same sources and version is stale. A run that
    started later (a concurrent re-ingest) has a later epoch and is left alone.
    """
    must: list[dict[str, Any]] = [
        {"key": "source_id", "match": {"any": source_ids}},
        {"key": "collection_version", "match": {"value": collection_version}},
        {"key": "ingestedAtEpoch", "range": {"lt": written_at_epoch}},
    ]
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/collections/{collection}/points/delete",
            params={"wait": "true"},
            json={"filter": {"must": must}},
        )
        response.raise_for_status()
    return {"pruned_sources": source_ids, "pruned_before_epoch": written_at_epoch}


def delete_older_than(
    cutoff_epoch: int,
    base_url: str,
    collection: str,
    timeout_seconds: float,
    collection_version: str | None = None,
) -> dict[str, Any]:
    """Delete points ingested before ``cutoff_epoch`` (age-based retention enforcement)."""
    must: list[dict[str, Any]] = [{"key": "ingestedAtEpoch", "range": {"lt": cutoff_epoch}}]
    if collection_version:
        must.append({"key": "collection_version", "match": {"value": collection_version}})
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/collections/{collection}/points/delete",
            params={"wait": "true"},
            json={"filter": {"must": must}},
        )
        response.raise_for_status()
    return {
        "status": "purged",
        "collection": collection,
        "cutoff_epoch": cutoff_epoch,
        "collection_version": collection_version or "all",
    }


def delete_source(
    source_id: str,
    base_url: str,
    collection: str,
    timeout_seconds: float,
    collection_version: str | None = None,
) -> dict[str, Any]:
    """Delete every point for a source id from Qdrant.

    Enables right-to-erasure and source removal: dropping a source from the manifest
    otherwise orphans its vectors forever. Optionally scope the delete to one
    collection version; omit it to purge the source across all versions.
    """
    must: list[dict[str, Any]] = [{"key": "source_id", "match": {"value": source_id}}]
    if collection_version:
        must.append({"key": "collection_version", "match": {"value": collection_version}})
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/collections/{collection}/points/delete",
            params={"wait": "true"},
            json={"filter": {"must": must}},
        )
        response.raise_for_status()
    return {
        "status": "deleted",
        "collection": collection,
        "deleted_source_id": source_id,
        "collection_version": collection_version or "all",
    }


def main() -> int:
    """Parse CLI arguments, then check, write, or delete manifest chunks in Qdrant."""
    parser = argparse.ArgumentParser(
        description="Validate, ingest, or purge platform.ai/v1alpha1 RAG source manifests in Qdrant.",
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument("--source-id", default="", help="Source id to purge with --delete.")
    parser.add_argument("--older-than-days", type=int, default=0, help="Age threshold for --purge.")
    parser.add_argument("--backend", choices=("qdrant",), default="qdrant")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--delete", action="store_true", help="Delete all points for --source-id from Qdrant.")
    mode.add_argument("--purge", action="store_true", help="Delete points older than --older-than-days.")
    parser.add_argument("--qdrant-url", default="")
    parser.add_argument("--collection", default="agentworkflows")
    parser.add_argument("--collection-version", default="v1")
    parser.add_argument("--dimensions", type=int, default=384)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--embedding-provider", default="hash")
    parser.add_argument("--embedding-base-url", default="")
    parser.add_argument("--embedding-model", default="hash-text-v1")
    parser.add_argument("--chunk-chars", type=int, default=1200)
    parser.add_argument("--overlap-chars", type=int, default=120)
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="With --write, keep chunks of these sources that this run did not write "
        "(from removed files or shortened documents).",
    )
    args = parser.parse_args()
    try:
        return _run(args)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        # A bad manifest, an unreadable document or an unreachable store is an operator
        # error with a clear cause, not a crash: report it without a traceback.
        raise SystemExit(f"error: {exc}") from None


def _run(args: argparse.Namespace) -> int:
    """Run the mode selected on the command line; raises SystemExit for invalid arguments."""
    if args.dimensions <= 0:
        raise SystemExit("--dimensions must be greater than zero")
    if not args.collection_version.strip():
        raise SystemExit("--collection-version must not be empty")

    if args.delete:
        if not args.source_id.strip():
            raise SystemExit("--source-id is required with --delete")
        if not args.qdrant_url:
            raise SystemExit("--qdrant-url is required with --delete")
        delete_summary = delete_source(
            args.source_id.strip(),
            args.qdrant_url,
            args.collection,
            args.timeout_seconds,
            args.collection_version,
        )
        print(json.dumps(delete_summary, indent=2, sort_keys=True))
        return 0

    if args.purge:
        if args.older_than_days <= 0:
            raise SystemExit("--older-than-days must be greater than zero with --purge")
        if not args.qdrant_url:
            raise SystemExit("--qdrant-url is required with --purge")
        cutoff_epoch = int(datetime.now(UTC).timestamp()) - args.older_than_days * 86400
        purge_summary = delete_older_than(
            cutoff_epoch,
            args.qdrant_url,
            args.collection,
            args.timeout_seconds,
            args.collection_version,
        )
        purge_summary["older_than_days"] = args.older_than_days
        print(json.dumps(purge_summary, indent=2, sort_keys=True))
        return 0

    if not args.source:
        raise SystemExit("--source is required with --check or --write")
    if args.chunk_chars <= 0:
        raise SystemExit("--chunk-chars must be greater than zero")
    if args.overlap_chars < 0 or args.overlap_chars >= args.chunk_chars:
        raise SystemExit("--overlap-chars must be zero or less than --chunk-chars")

    sources = load_manifest(args.source)
    for warning in owner_warnings(sources):
        print(f"warning: {warning}", file=sys.stderr)
    chunks = build_chunks(sources, args.chunk_chars, args.overlap_chars, args.collection_version)
    provider = build_embedding_provider(
        args.embedding_provider,
        args.dimensions,
        args.embedding_model,
        args.embedding_base_url,
        args.timeout_seconds,
    )
    summary: dict[str, Any] = {
        "status": "checked",
        "backend": args.backend,
        "embedding_provider": provider.name,
        "embedding_model": provider.model,
        "dimensions": provider.dimensions,
        "sources": len(sources),
        "documents": sum(len(iter_document_paths(source)) for source in sources),
        "chunks": len(chunks),
        "collection": args.collection,
        "collection_version": args.collection_version,
    }
    if args.write:
        if not args.qdrant_url:
            raise SystemExit("--qdrant-url is required with --write")
        written_at = datetime.now(UTC)
        summary.update(
            upsert_chunks(
                chunks,
                provider,
                args.qdrant_url,
                args.collection,
                args.timeout_seconds,
                ingested_at_time=written_at,
            )
        )
        if not args.no_prune:
            # Only after every batch landed: a failed run leaves the previous chunks in place.
            summary.update(
                prune_stale_chunks(
                    [source.id for source in sources],
                    args.collection_version,
                    int(written_at.timestamp()),
                    args.qdrant_url,
                    args.collection,
                    args.timeout_seconds,
                )
            )
        summary["status"] = "written"
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
