"""Ranking quality of the lexical (BM25) retriever and the retrieval tokenizer.

The lexical backend used to rank by raw term counts, so on the repository's own docs (the
Compose demo corpus) the longest document won nearly every question. These tests pin the
behaviours that fixed that, each on a corpus small enough to read.
"""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest
from app.embeddings import hashed_text_embedding, stem, tokenize
from app.retriever import KnowledgeDocument, LexicalRetriever


def _document(doc_id, content, title=None, owner=""):
    return KnowledgeDocument(
        id=doc_id,
        title=title or doc_id,
        source=f"{doc_id}.md",
        content=content,
        tokens=Counter(tokenize(content)),
        owner=owner,
    )


def _ids(retriever, query, top_k=3, tenant=None):
    results = asyncio.run(retriever.query(query, top_k, 2000, tenant=tenant))
    return [result.document.id for result in results]


def test_a_long_document_full_of_common_words_does_not_outrank_the_topical_one():
    filler = "The platform runs the workflow and the gateway records the run for the team. " * 80
    retriever = LexicalRetriever(
        [
            _document("workflows", filler + "Workflow costs are tracked per run."),
            _document("cost-model", "# Cost model\nThe cost of running the platform is per team.", "Cost model"),
            *(_document(f"guide-{n}", f"Run the platform step {n}. The platform team runs it.") for n in range(3)),
        ]
    )

    assert _ids(retriever, "How much does it cost to run the platform?")[0] == "cost-model"


def test_question_words_do_not_make_a_faq_win_every_question():
    retriever = LexicalRetriever(
        [
            _document("faq", "How do I start? How do I stop? How do I log in? What do I do next?"),
            _document("threat-model", "# Threat model\nThe threat model covers spoofing and tampering."),
        ]
    )

    assert _ids(retriever, "How do I read the threat model?")[0] == "threat-model"


def test_sentence_final_punctuation_does_not_hide_a_term():
    retriever = LexicalRetriever([_document("agents", "Coding agents must call the gateway.")])

    assert _ids(retriever, "gateway") == ["agents"]


def test_compound_tokens_match_their_parts_and_the_whole():
    retriever = LexicalRetriever(
        [
            _document("accelerators", "NVIDIA profiles request nvidia.com/gpu resources."),
            _document("other", "Profiles request memory."),
        ]
    )

    assert _ids(retriever, "Which GPU resource?")[0] == "accelerators"
    assert _ids(retriever, "nvidia.com/gpu")[0] == "accelerators"
    assert tokenize("Send X-Request-ID.") == ["send", "x-request-id", "x", "request", "id"]


def test_plural_and_singular_forms_match():
    retriever = LexicalRetriever(
        [
            _document("model-selection", "Pick a model and update the model catalog."),
            _document("policy", "Every policy is enforced at admission."),
        ]
    )

    assert _ids(retriever, "updating models") == ["model-selection"]
    assert _ids(retriever, "policies") == ["policy"]
    assert [stem(word) for word in ("models", "policies", "status", "class", "files", "goes")] == [
        "model",
        "policy",
        "status",
        "class",
        "file",
        "goes",
    ]


@pytest.mark.parametrize(
    ("query", "expected"),
    [("Größe", ["de"]), ("übersicht", ["de"]), ("東京", ["ja"]), ("天気", ["ja"])],
)
def test_non_ascii_queries_match_whole_words_not_ascii_fragments(query, expected):
    retriever = LexicalRetriever(
        [
            _document("de", "# Übersicht\nDie Größe der Datei ist begrenzt."),
            _document("ja", "# 日本語\n東京の天気は晴れです。"),
            # The old ASCII-only tokenizer split "Größe" into "gr" + "e" and matched this.
            _document("en", "The gateway enforces e gr budgets."),
        ]
    )

    assert _ids(retriever, query) == expected


def test_the_file_name_counts_as_part_of_the_title():
    retriever = LexicalRetriever(
        [
            _document("scope-and-non-goals", "# Scope\nWhat this project covers.", "Scope"),
            _document("faq", "# FAQ\nPeople ask about goals and plans.", "FAQ"),
        ]
    )

    assert _ids(retriever, "non-goals")[0] == "scope-and-non-goals"


def test_the_excerpt_is_anchored_on_the_rarest_query_term():
    body = "The platform overview. " * 30 + "Retention purges run nightly."
    retriever = LexicalRetriever([_document("retention", body), _document("other", "The platform.")])

    [result] = asyncio.run(retriever.query("the retention purge", 1, 120))

    assert "Retention purges" in result.excerpt


def test_scores_under_isolation_do_not_depend_on_other_tenants_documents():
    mine = [_document("a1", "budget alerts for the gateway", owner="team-a")]
    alone = LexicalRetriever(mine, tenant_isolation_enabled=True)
    crowded = LexicalRetriever(
        mine + [_document(f"b{n}", "budget budget budget", owner="team-b") for n in range(20)],
        tenant_isolation_enabled=True,
    )

    alone_score = asyncio.run(alone.query("budget", 1, 200, tenant="team-a"))[0].score
    crowded_score = asyncio.run(crowded.query("budget", 1, 200, tenant="team-a"))[0].score

    # Shared statistics would let team-a infer how common a term is in team-b's corpus.
    assert crowded_score == alone_score


def test_a_verbatim_phrase_outranks_scattered_terms():
    retriever = LexicalRetriever(
        [
            _document("scattered", "Audit logs are kept. The chain head is stored. Hash every record."),
            _document("phrase", "Each audit chain head hash links the next lifetime."),
        ]
    )

    assert _ids(retriever, "audit chain head hash")[0] == "phrase"


def test_hash_embeddings_are_unchanged_by_the_retrieval_tokenizer():
    # hash-text-v1 vectors are stored in Qdrant; the retrieval tokenizer moving on must
    # not move them. Pinned from the implementation before the tokenizer changed.
    text = "Coding agents send X-Request-ID to the gateway. Größe 東京 nvidia.com/gpu, v0.9.1!"

    vector = [round(value, 6) for value in hashed_text_embedding(text, 16)]

    assert vector == [
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.603023, 0.0, 0.0, 0.0, -0.301511, -0.603023, -0.301511, 0.0, -0.301511,
    ]  # fmt: skip
