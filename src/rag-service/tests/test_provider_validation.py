"""Invalid upstream vectors and scores must use the provider error paths."""

import pytest
from app.embeddings import OpenAICompatibleEmbeddingProvider
from app.reranker import OpenAICompatibleReranker


@pytest.mark.parametrize(
    "payload",
    [
        {"data": None},
        {"data": []},
        {"data": [None]},
        {"data": {}},
        {"data": [{"embedding": [None]}]},
        {"data": [{"embedding": [float("nan")]}]},
        {"data": [{"embedding": [float("inf")]}]},
        {"data": [{"embedding": [float("-inf")]}]},
    ],
)
def test_invalid_embedding_raises_value_error(payload):
    provider = OpenAICompatibleEmbeddingProvider("http://embedding", "model", 1, 2.0)
    with pytest.raises(ValueError):
        provider._parse(payload)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_reranker_score_raises_value_error(score):
    provider = OpenAICompatibleReranker("http://rerank", "model", 2.0)
    with pytest.raises(ValueError):
        provider._parse({"results": [{"index": 0, "relevance_score": score}]}, 1)
