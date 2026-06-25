from __future__ import annotations

import os
from typing import Any, Dict, Optional

import vertexai
from dotenv import load_dotenv
from vertexai.preview import rag

load_dotenv()

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

if PROJECT_ID:
    vertexai.init(project=PROJECT_ID, location=LOCATION)

RAG_DEFAULT_TOP_K = os.environ.get("RAG_DEFAULT_TOP_K", "8")
RAG_DEFAULT_VECTOR_DISTANCE_THRESHOLD = os.environ.get(
    "RAG_DEFAULT_VECTOR_DISTANCE_THRESHOLD"
)


class RAGQueryError(Exception):
    pass


def _parse_optional_int(value: Any, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_optional_float(value: Any) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_corpus_path(corpus_id: str) -> str:
    cleaned_corpus_id = str(corpus_id or "").strip()

    if not cleaned_corpus_id:
        raise RAGQueryError("corpus_id cannot be empty")

    if cleaned_corpus_id.startswith("projects/"):
        return cleaned_corpus_id

    if not PROJECT_ID:
        raise RAGQueryError("GOOGLE_CLOUD_PROJECT environment variable is required")

    return f"projects/{PROJECT_ID}/locations/{LOCATION}/ragCorpora/{cleaned_corpus_id}"


def query_rag_corpus(
    corpus_id: str,
    query_text: str,
    top_k: Optional[int] = None,
    vector_distance_threshold: Optional[float] = None,
) -> Dict[str, Any]:
    if not query_text or not str(query_text).strip():
        return {
            "status": "error",
            "corpus_id": corpus_id,
            "query": query_text,
            "results": [],
            "count": 0,
            "error_message": "query_text cannot be empty",
            "message": "Failed to query corpus because query_text is empty",
        }

    resolved_top_k = _parse_optional_int(
        top_k,
        _parse_optional_int(RAG_DEFAULT_TOP_K, 8),
    )

    resolved_threshold = (
        _parse_optional_float(vector_distance_threshold)
        if vector_distance_threshold is not None
        else _parse_optional_float(RAG_DEFAULT_VECTOR_DISTANCE_THRESHOLD)
    )

    try:
        corpus_path = _build_corpus_path(corpus_id)

        rag_resource = rag.RagResource(
            rag_corpus=corpus_path,
        )

        retrieval_config_kwargs: Dict[str, Any] = {
            "top_k": resolved_top_k,
        }

        if resolved_threshold is not None:
            retrieval_config_kwargs["filter"] = rag.utils.resources.Filter(
                vector_distance_threshold=resolved_threshold
            )

        retrieval_config = rag.RagRetrievalConfig(**retrieval_config_kwargs)

        response = rag.retrieval_query(
            rag_resources=[rag_resource],
            text=str(query_text).strip(),
            rag_retrieval_config=retrieval_config,
        )

        results = []

        if hasattr(response, "contexts"):
            contexts = response.contexts

            if hasattr(contexts, "contexts"):
                contexts = contexts.contexts

            for context in contexts:
                results.append(
                    {
                        "text": getattr(context, "text", "") or "",
                        "source_uri": getattr(context, "source_uri", None),
                        "source_title": getattr(context, "source_display_name", None)
                        or getattr(context, "source_title", None),
                        "relevance_score": getattr(context, "relevance_score", None),
                    }
                )

        return {
            "status": "success",
            "corpus_id": corpus_id,
            "corpus_path": corpus_path,
            "query": query_text,
            "results": results,
            "count": len(results),
            "message": f"Found {len(results)} result(s) for query: {query_text}",
        }

    except Exception as exc:
        return {
            "status": "error",
            "corpus_id": corpus_id,
            "query": query_text,
            "results": [],
            "count": 0,
            "error_message": str(exc),
            "message": f"Failed to query corpus: {exc}",
        }

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
addopts = "-vv -s"
