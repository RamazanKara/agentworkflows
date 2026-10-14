"""Text embedding providers: deterministic hashing and OpenAI-compatible HTTP backends."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol

import httpx

# Hash embeddings hash ASCII word tokens. This pattern is part of the hash-text-v1 vector
# contract: changing it would move every stored vector without a model-name change, so it
# stays frozen even though the retrieval tokenizer below has moved on.
HASH_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.:/-]*")
# Retrieval terms: words of any script. ".", ":", "/" and "-" may join a compound token
# (nvidia.com/gpu, x-request-id, v0.9.1) but never end one, so "gateway." at the end of a
# sentence is the term "gateway".
TOKEN_PATTERN = re.compile(r"[^\W_](?:[\w.:/-]*[^\W_])?")
_COMPOUND_SEPARATORS = re.compile(r"[._:/-]+")
# Scripts written without spaces between words. A run of them is one regex token, so it
# also contributes overlapping character bigrams, the usual lexical unit for CJK search.
_UNSPACED_RUN = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]{2,}")


# English function and question words. In technical documentation "how", "do" and "i" are
# rare enough to look significant, so without this list a FAQ full of "How do I ..." wins
# every question. Only query-side weighting uses it; nothing is dropped from the index.
_STOPWORD_TEXT = (
    "a about all an and any are as at be been but by can could did do does for from had has have how i "
    "if in into is it its me much my no not of on or our should so some such than that the their them "
    "then there these they this to was we were what when where which who whom whose why will with would "
    "you your"
)
STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def content_terms(terms: list[str]) -> list[str]:
    """Return the terms without stopwords, or all of them when nothing else would remain."""
    kept = [term for term in terms if term not in STOPWORDS]
    return kept or terms


def stem(term: str) -> str:
    """Conflate English plurals ("models" -> "model", "policies" -> "policy").

    The rules of Lucene's EnglishMinimalStemmer, applied to ASCII words only. Being
    minimal, they rarely conflate words that mean different things.
    """
    if len(term) < 3 or term[-1] != "s" or not term.isascii() or not term.isalpha():
        return term
    if term[-2] in "us":
        return term
    if term[-2] == "e":
        if len(term) > 3 and term[-3] == "i" and term[-4] not in "ae":
            return term[:-3] + "y"
        if term[-3] in "iaoe":
            return term
    return term[:-1]


# Inputs per embeddings request. TEI, a common self-hosted server, rejects batches above its
# default --max-client-batch-size of 32; vLLM, Ollama and hosted APIs accept at least that.
EMBEDDING_BATCH_SIZE = 32


def _batches(values: list[str], size: int) -> list[list[str]]:
    return [values[start : start + size] for start in range(0, len(values), size)]


class EmbeddingProvider(Protocol):
    """Protocol for embedding providers that map text to a fixed-size vector."""

    name: str
    model: str
    dimensions: int

    def embed(self, value: str) -> list[float]: ...

    async def embed_async(self, value: str) -> list[float]: ...

    def embed_many(self, values: list[str]) -> list[list[float]]: ...

    async def embed_many_async(self, values: list[str]) -> list[list[float]]: ...

    async def ready(self) -> bool:
        raise NotImplementedError


def tokenize(value: str) -> list[str]:
    """Lowercase the text and return its retrieval terms, in order.

    A compound token is kept whole and also contributes its parts, so a query for "gpu"
    matches ``nvidia.com/gpu``; a run of CJK characters also contributes its bigrams.
    """
    lowered = value.lower()
    tokens = TOKEN_PATTERN.findall(lowered)
    terms = list(tokens)
    for token in [token for token in tokens if not token.isalnum()]:
        terms.extend(part for part in _COMPOUND_SEPARATORS.split(token) if part)
    if not lowered.isascii() and _UNSPACED_RUN.search(lowered):
        for token in [token for token in tokens if not token.isascii()]:
            for run in _UNSPACED_RUN.findall(token):
                if run != token or len(run) > 2:
                    terms.extend(run[index : index + 2] for index in range(len(run) - 1))
    return terms


def hashed_text_embedding(value: str, dimensions: int) -> list[float]:
    """Return a deterministic, L2-normalized hashed bag-of-words embedding."""
    terms = HASH_TOKEN_PATTERN.findall(value.lower())
    if not terms:
        return [0.0] * dimensions
    vector = [0.0] * dimensions
    for term, count in Counter(terms).items():
        digest = hashlib.sha256(term.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(item * item for item in vector))
    if norm == 0:
        return vector
    return [item / norm for item in vector]


class HashEmbeddingProvider:
    """Offline embedding provider backed by deterministic hashing of tokens."""

    name = "hash"

    def __init__(self, dimensions: int, model: str = "hash-text-v1") -> None:
        self.dimensions = dimensions
        self.model = model

    def embed(self, value: str) -> list[float]:
        """Return the hashed embedding vector for the given text."""
        return hashed_text_embedding(value, self.dimensions)

    async def embed_async(self, value: str) -> list[float]:
        """Async wrapper around :meth:`embed`; hashing is CPU-bound and non-blocking."""
        return self.embed(value)

    def embed_many(self, values: list[str]) -> list[list[float]]:
        """Return the hashed embedding of each text, in order."""
        return [self.embed(value) for value in values]

    async def embed_many_async(self, values: list[str]) -> list[list[float]]:
        """Async wrapper around :meth:`embed_many`."""
        return self.embed_many(values)

    async def ready(self) -> bool:
        """The in-process deterministic provider has no external dependency."""
        return True


class OpenAICompatibleEmbeddingProvider:
    """Embedding provider that calls an OpenAI-compatible ``/v1/embeddings`` API."""

    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        dimensions: int,
        timeout_seconds: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self._async_client: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        """Return the shared async client, creating it on first use."""
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self._async_client

    async def aclose(self) -> None:
        """Close the shared async client; called on service shutdown."""
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    def _vector(self, embedding: object) -> list[float]:
        """Validate one returned embedding: finite numbers of the configured dimensionality."""
        if not isinstance(embedding, list):
            raise ValueError("embedding response did not contain data[0].embedding")
        try:
            vector = [float(item) for item in embedding]
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("embedding values must be finite numbers") from exc
        if not all(math.isfinite(item) for item in vector):
            raise ValueError("embedding values must be finite numbers")
        if len(vector) != self.dimensions:
            raise ValueError(f"embedding returned {len(vector)} dimensions; expected {self.dimensions}")
        return vector

    def _parse(self, payload: object) -> list[float]:
        data = payload.get("data") if isinstance(payload, dict) else None
        embedding = data[0].get("embedding") if isinstance(data, list) and data and isinstance(data[0], dict) else None
        return self._vector(embedding)

    def _parse_many(self, payload: object, count: int) -> list[list[float]]:
        """Return the ``count`` embeddings of a batch response in input order.

        Items are ordered by their ``index`` when the server reports one, as the OpenAI API
        does; a response with the wrong number of items is rejected rather than misaligned.
        """
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != count or not all(isinstance(item, dict) for item in data):
            raise ValueError(f"embedding response must contain {count} data items")
        if all(isinstance(item.get("index"), int) for item in data):
            data = sorted(data, key=lambda item: item["index"])
            if [item["index"] for item in data] != list(range(count)):
                raise ValueError("embedding response indexes do not match the request")
        return [self._vector(item.get("embedding")) for item in data]

    def embed(self, value: str) -> list[float]:
        """Request an embedding from the API and validate its dimensionality."""
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/v1/embeddings",
                json={"model": self.model, "input": value},
            )
            response.raise_for_status()
            payload = response.json()
        return self._parse(payload)

    async def embed_async(self, value: str) -> list[float]:
        """Request an embedding over async HTTP so the event loop is not blocked."""
        response = await self._client().post(
            f"{self.base_url}/v1/embeddings",
            json={"model": self.model, "input": value},
        )
        response.raise_for_status()
        return self._parse(response.json())

    def _batch_timeout(self, size: int) -> float:
        """Return the timeout for one batch request: the per-input timeout times its inputs.

        The configured timeout was sized for one input per request (the chart passes the
        vector store's 1 s). A CPU-hosted model can take longer than that for 32 inputs,
        and the batch must not fail where the same inputs sent one by one would not.
        """
        return self.timeout_seconds * size

    def embed_many(self, values: list[str]) -> list[list[float]]:
        """Embed many texts over one connection, ``EMBEDDING_BATCH_SIZE`` inputs per request."""
        vectors: list[list[float]] = []
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for batch in _batches(values, EMBEDDING_BATCH_SIZE):
                response = client.post(
                    f"{self.base_url}/v1/embeddings",
                    json={"model": self.model, "input": batch},
                    timeout=self._batch_timeout(len(batch)),
                )
                response.raise_for_status()
                vectors.extend(self._parse_many(response.json(), len(batch)))
        return vectors

    async def embed_many_async(self, values: list[str]) -> list[list[float]]:
        """Embed many texts, ``EMBEDDING_BATCH_SIZE`` inputs per request."""
        vectors: list[list[float]] = []
        for batch in _batches(values, EMBEDDING_BATCH_SIZE):
            response = await self._client().post(
                f"{self.base_url}/v1/embeddings",
                json={"model": self.model, "input": batch},
                timeout=self._batch_timeout(len(batch)),
            )
            response.raise_for_status()
            vectors.extend(self._parse_many(response.json(), len(batch)))
        return vectors

    async def ready(self) -> bool:
        """Probe the provider's standard runtime health endpoint without running inference."""
        try:
            response = await self._client().get(f"{self.base_url}/health")
            response.raise_for_status()
        except httpx.HTTPError:
            return False
        return True


def build_embedding_provider(
    provider: str,
    dimensions: int,
    model: str,
    base_url: str,
    timeout_seconds: float,
) -> EmbeddingProvider:
    """Construct the configured embedding provider, validating required options."""
    if provider == "hash":
        return HashEmbeddingProvider(dimensions=dimensions, model=model or "hash-text-v1")
    if provider == "openai-compatible":
        if not base_url:
            raise ValueError("embedding_base_url must be set for openai-compatible embeddings")
        if not model:
            raise ValueError("embedding_model must be set for openai-compatible embeddings")
        return OpenAICompatibleEmbeddingProvider(base_url, model, dimensions, timeout_seconds)
    raise ValueError("embedding provider must be either 'hash' or 'openai-compatible'")
