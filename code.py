from __future__ import annotations

import html
import math
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .schemas import (
    FolderContext,
    GoogleDocContext,
    PipelineStatus,
    RetrievalContext,
    RetrievalHit,
    RulebookLinkMatch,
    SourceType,
    TextChunk,
)

try:
    from .rulebook_links import RULEBOOK_SECTION_REFERENCE_LINKS
except Exception:
    RULEBOOK_SECTION_REFERENCE_LINKS = {}

try:
    from .tools import query_rag_corpus
except Exception:
    query_rag_corpus = None


ProgressCallback = Optional[Callable[[dict], None]]


@dataclass
class RetrievalServiceConfig:
    sap_rule_book_corpus_id: str
    sap_notes_corpus_id: str
    previous_reports_corpus_id: str
    rag_top_k: int = 8
    previous_reports_top_k: int = 5
    vector_distance_threshold: Optional[float] = None
    max_queries_per_source: int = 8
    max_query_chars: int = 600
    max_workers: int = 6
    max_google_docs: int = 5
    max_google_doc_chars: int = 6000
    min_rulebook_match_score: float = 0.08
    max_folder_vector_hits: int = 8


class RetrievalServiceError(Exception):
    pass


def emit_event(
    callback: ProgressCallback,
    event: str,
    message: str,
    vm_name: Optional[str] = None,
    folder_name: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    if callback:
        callback(
            {
                "event": event,
                "data": {
                    "message": message,
                    "vm_name": vm_name,
                    "folder_name": folder_name,
                    **(data or {}),
                },
            }
        )


def parse_int(value: Optional[Any], default: int) -> int:
    if value is None or str(value).strip() == "":
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_float(value: Optional[Any]) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_config(
    sap_rule_book_corpus_id: str,
    sap_notes_corpus_id: str,
    previous_reports_corpus_id: str,
) -> RetrievalServiceConfig:
    return RetrievalServiceConfig(
        sap_rule_book_corpus_id=sap_rule_book_corpus_id,
        sap_notes_corpus_id=sap_notes_corpus_id,
        previous_reports_corpus_id=previous_reports_corpus_id,
        rag_top_k=parse_int(os.getenv("RAG_DEFAULT_TOP_K"), 8),
        previous_reports_top_k=parse_int(os.getenv("SAP_HC_PREVIOUS_REPORTS_TOP_K"), 5),
        vector_distance_threshold=parse_float(
            os.getenv("RAG_DEFAULT_VECTOR_DISTANCE_THRESHOLD")
        ),
        max_queries_per_source=parse_int(
            os.getenv("SAP_HC_MAX_RETRIEVAL_QUERIES_PER_SOURCE"), 8
        ),
        max_query_chars=parse_int(os.getenv("SAP_HC_MAX_QUERY_CHARS"), 600),
        max_workers=parse_int(os.getenv("SAP_HC_RETRIEVAL_MAX_WORKERS"), 6),
        max_google_docs=parse_int(os.getenv("SAP_HC_MAX_GOOGLE_DOCS"), 5),
        max_google_doc_chars=parse_int(os.getenv("SAP_HC_MAX_GOOGLE_DOC_CHARS"), 6000),
        min_rulebook_match_score=parse_float(
            os.getenv("SAP_HC_MIN_RULEBOOK_MATCH_SCORE")
        )
        or 0.08,
        max_folder_vector_hits=parse_int(os.getenv("SAP_HC_MAX_FOLDER_VECTOR_HITS"), 8),
    )


def normalize_text(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def truncate_text(text: str, max_chars: int) -> str:
    cleaned = normalize_text(text)

    if len(cleaned) <= max_chars:
        return cleaned

    return cleaned[:max_chars].rsplit(" ", 1)[0].strip()


def dedupe_keep_order(items: Iterable[str]) -> List[str]:
    seen = set()
    output = []

    for item in items:
        cleaned = normalize_text(item)
        key = cleaned.lower()

        if not cleaned or key in seen:
            continue

        seen.add(key)
        output.append(cleaned)

    return output


def tokenize(text: str) -> List[str]:
    return [
        token
        for token in re.findall(r"[a-zA-Z0-9_./:-]+", (text or "").lower())
        if len(token) > 1
    ]


def weighted_terms(text: str, max_terms: int = 80) -> List[str]:
    tokens = tokenize(text)

    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "are",
        "was",
        "were",
        "you",
        "your",
        "not",
        "all",
        "any",
        "can",
        "has",
        "have",
        "had",
        "its",
        "into",
        "file",
        "start",
        "end",
        "relative",
        "path",
        "folder",
        "lines",
        "included",
        "truncated",
        "configured",
        "limit",
        "yes",
        "no",
    }

    counter = Counter(token for token in tokens if token not in stopwords)
    return [term for term, _ in counter.most_common(max_terms)]


def cosine_similarity(counter_a: Counter, counter_b: Counter) -> float:
    if not counter_a or not counter_b:
        return 0.0

    common = set(counter_a) & set(counter_b)
    numerator = sum(counter_a[key] * counter_b[key] for key in common)

    denom_a = math.sqrt(sum(value * value for value in counter_a.values()))
    denom_b = math.sqrt(sum(value * value for value in counter_b.values()))

    if denom_a == 0 or denom_b == 0:
        return 0.0

    return numerator / (denom_a * denom_b)


def text_counter(text: str) -> Counter:
    return Counter(tokenize(text))


def extract_candidate_lines(text: str, max_lines: int = 120) -> List[str]:
    output = []

    patterns = [
        r"\b(error|warning|failed|disabled|enabled|critical|recommend|not set|missing)\b",
        r"\b(saptune|sapconf|hana|hdb|global\.ini|indexserver|nameserver|daemon|preprocessor)\b",
        r"\b(memory|cpu|disk|filesystem|network|firewall|ntp|time|kernel|parameter|backup|log)\b",
        r"\b\d+(\.\d+)?\s*(gb|mb|kb|tb|ms|sec|seconds|minutes|%)\b",
        r"\b[a-zA-Z0-9_.\-/]+\s*[:=]\s*[^=\s].+",
    ]

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("====="):
            continue

        lowered = line.lower()

        if any(re.search(pattern, lowered) for pattern in patterns):
            output.append(line[:700])

        if len(output) >= max_lines:
            break

    return dedupe_keep_order(output)


def extract_parameter_pairs(text: str, max_pairs: int = 100) -> List[str]:
    pairs = []

    patterns = [
        r"^\s*([A-Za-z0-9_.\-/]+)\s*=\s*(.+?)\s*$",
        r"^\s*([A-Za-z0-9_.\-/]+)\s*:\s*(.+?)\s*$",
    ]

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("====="):
            continue

        for pattern in patterns:
            match = re.match(pattern, line)
            if not match:
                continue

            key = match.group(1).strip()
            value = match.group(2).strip()

            if not key or not value:
                continue

            if len(key) > 140 or len(value) > 300:
                continue

            pairs.append(f"{key} = {value}")
            break

        if len(pairs) >= max_pairs:
            break

    return dedupe_keep_order(pairs)


def detect_folder_theme(folder_context: FolderContext) -> str:
    folder_name = folder_context.folder_name.lower()
    sample_text = folder_context.combined_text[:15000].lower()

    if "profile" in folder_name:
        return "SAP HANA profile DEFAULT.PFL instance profile parameters"
    if any(term in folder_name for term in ("network", "net")) or any(
        term in sample_text for term in ("ifconfig", "ip route", "netstat", "firewall", "iptables")
    ):
        return "network firewall routing DNS SAP HANA connectivity"
    if any(term in folder_name for term in ("hana", "hdb", "db")) or any(
        term in sample_text
        for term in ("global.ini", "indexserver", "nameserver", "hdb", "tenant")
    ):
        return "SAP HANA database configuration parameters"
    if any(term in folder_name for term in ("os", "linux", "sys")) or any(
        term in sample_text
        for term in ("saptune", "sapconf", "sysctl", "limits.conf", "transparent huge")
    ):
        return "operating system tuning Linux SAP HANA saptune"
    if any(term in folder_name for term in ("disk", "fs", "filesystem", "mount")) or any(
        term in sample_text
        for term in ("df -h", "lsblk", "/hana/data", "/hana/log", "/hana/shared")
    ):
        return "filesystem storage disk layout SAP HANA persistence"
    if any(term in folder_name for term in ("backup", "log")):
        return "SAP HANA backup logging persistence configuration"
    return "SAP HANA health check configuration"


def build_queries(folder_context: FolderContext, config: RetrievalServiceConfig) -> List[str]:
    theme = detect_folder_theme(folder_context)
    candidate_lines = extract_candidate_lines(folder_context.combined_text)
    parameter_pairs = extract_parameter_pairs(folder_context.combined_text)
    file_terms = [
        source_file.filename
        for source_file in folder_context.included_files[:20]
        if source_file.filename
    ]
    top_terms = weighted_terms(folder_context.combined_text, max_terms=30)

    queries = [
        f"{theme} recommendation best practice",
        f"{theme} SAP HANA health check",
        f"{folder_context.vm_name} {folder_context.folder_name} {theme}",
    ]

    for pair in parameter_pairs[:12]:
        queries.append(f"{theme} {pair}")

    for line in candidate_lines[:12]:
        queries.append(f"{theme} {line}")

    for filename in file_terms[:8]:
        queries.append(f"{theme} {filename}")

    if top_terms:
        queries.append(f"{theme} {' '.join(top_terms[:20])}")

    queries = dedupe_keep_order(
        truncate_text(query, config.max_query_chars) for query in queries
    )

    return queries[: config.max_queries_per_source]


def build_previous_report_queries(
    folder_context: FolderContext,
    config: RetrievalServiceConfig,
) -> List[str]:
    theme = detect_folder_theme(folder_context)
    parameter_pairs = extract_parameter_pairs(folder_context.combined_text)
    candidate_lines = extract_candidate_lines(folder_context.combined_text)
    queries = [
        f"previous SAP HANA assessment report recommendation style {theme}",
        f"manual SAP HANA health check recommendation writing pattern {theme}",
    ]

    for pair in parameter_pairs[:6]:
        queries.append(f"previous assessment recommendation style {pair}")

    for line in candidate_lines[:6]:
        queries.append(f"previous report finding recommendation pattern {line}")

    queries = dedupe_keep_order(
        truncate_text(query, config.max_query_chars) for query in queries
    )

    return queries[: config.max_queries_per_source]


def run_rag_query(
    corpus_id: str,
    query: str,
    source_type: SourceType,
    top_k: int,
    vector_distance_threshold: Optional[float],
) -> List[RetrievalHit]:
    if query_rag_corpus is None:
        return []

    response = query_rag_corpus(
        corpus_id=corpus_id,
        query_text=query,
        top_k=top_k,
        vector_distance_threshold=vector_distance_threshold,
    )

    if not isinstance(response, dict):
        return []

    if response.get("status") != "success":
        return []

    hits = []

    for item in response.get("results", []) or []:
        text = normalize_text(str(item.get("text") or ""))

        if not text:
            continue

        score = item.get("relevance_score")

        try:
            score = float(score) if score is not None else None
        except (TypeError, ValueError):
            score = None

        hits.append(
            RetrievalHit(
                source_type=source_type,
                corpus_id=corpus_id,
                query=query,
                text=text,
                source_uri=item.get("source_uri"),
                source_title=item.get("source_title"),
                relevance_score=score,
                metadata={
                    key: value
                    for key, value in item.items()
                    if key
                    not in {
                        "text",
                        "source_uri",
                        "source_title",
                        "relevance_score",
                    }
                },
            )
        )

    return hits


def dedupe_hits(hits: Iterable[RetrievalHit]) -> List[RetrievalHit]:
    seen = set()
    output = []

    for hit in hits:
        key = (
            hit.source_type.value,
            hit.source_uri or "",
            normalize_text(hit.text[:500]).lower(),
        )

        if key in seen:
            continue

        seen.add(key)
        output.append(hit)

    return output


def sort_hits(hits: Iterable[RetrievalHit]) -> List[RetrievalHit]:
    return sorted(
        hits,
        key=lambda item: (
            item.relevance_score is None,
            item.relevance_score if item.relevance_score is not None else 999999.0,
        ),
    )


def build_folder_vector_hits(
    folder_context: FolderContext,
    queries: Sequence[str],
    max_hits: int,
) -> List[RetrievalHit]:
    if not folder_context.chunks or not queries:
        return []

    chunk_counters = [(chunk, text_counter(chunk.text)) for chunk in folder_context.chunks]
    hits: List[RetrievalHit] = []

    for query in queries[:5]:
        query_counter = text_counter(query)

        scored_chunks: List[Tuple[float, TextChunk]] = []

        for chunk, chunk_counter in chunk_counters:
            score = cosine_similarity(query_counter, chunk_counter)

            if score > 0:
                scored_chunks.append((score, chunk))

        scored_chunks.sort(key=lambda item: item[0], reverse=True)

        for score, chunk in scored_chunks[:max_hits]:
            hits.append(
                RetrievalHit(
                    source_type=SourceType.FOLDER_VECTOR,
                    corpus_id=None,
                    query=query,
                    text=chunk.text,
                    source_uri=chunk.source_uri,
                    source_title=chunk.relative_path,
                    relevance_score=score,
                    metadata={
                        "vm_name": chunk.vm_name,
                        "folder_name": chunk.folder_name,
                        "line_range": chunk.line_range,
                        **chunk.metadata,
                    },
                )
            )

    return dedupe_hits(sort_hits(hits))[:max_hits]


def normalize_urls(value: Any) -> List[str]:
    if value is None:
        return []

    if isinstance(value, str):
        return [value]

    if isinstance(value, dict):
        urls = []

        for item in value.values():
            urls.extend(normalize_urls(item))

        return urls

    if isinstance(value, (list, tuple, set)):
        urls = []

        for item in value:
            urls.extend(normalize_urls(item))

        return urls

    return []


def match_rulebook_sections(
    folder_context: FolderContext,
    config: RetrievalServiceConfig,
) -> List[RulebookLinkMatch]:
    if not RULEBOOK_SECTION_REFERENCE_LINKS:
        return []

    folder_text = folder_context.combined_text[:100000]
    folder_counter = text_counter(folder_text)
    top_terms = set(weighted_terms(folder_text, max_terms=200))

    matches: List[RulebookLinkMatch] = []

    for section_key, link_value in RULEBOOK_SECTION_REFERENCE_LINKS.items():
        section_text = str(section_key)
        section_counter = text_counter(section_text)
        section_terms = set(tokenize(section_text))

        cosine_score = cosine_similarity(folder_counter, section_counter)
        overlap_terms = sorted(section_terms & top_terms)
        overlap_score = len(overlap_terms) / max(len(section_terms), 1)
        final_score = max(cosine_score, overlap_score)

        if final_score < config.min_rulebook_match_score:
            continue

        urls = normalize_urls(link_value)

        if not urls:
            continue

        matches.append(
            RulebookLinkMatch(
                section_key=section_text,
                matched_score=final_score,
                matched_terms=overlap_terms[:20],
                urls=urls,
            )
        )

    matches.sort(key=lambda item: item.matched_score, reverse=True)
    return matches[: config.max_google_docs]


class SimpleHTMLTextExtractor:
    def __init__(self, html_text: str):
        self.html_text = html_text

    def extract(self) -> str:
        text = self.html_text
        text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
        text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
        text = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", text)
        text = re.sub(r"(?is)<header.*?>.*?</header>", " ", text)
        text = re.sub(r"(?is)<footer.*?>.*?</footer>", " ", text)
        text = re.sub(r"(?is)<nav.*?>.*?</nav>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</p>", "\n", text)
        text = re.sub(r"(?i)</h[1-6]>", "\n", text)
        text = re.sub(r"(?is)<.*?>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
        return text.strip()


def fetch_url_text(
    url: str,
    max_chars: int,
    timeout: int = 15,
) -> Tuple[Optional[str], Optional[str]]:
    request = urllib.request.Request(
        url=url,
        headers={
            "User-Agent": "Mozilla/5.0 SAPHealthCheckBot/1.0",
            "Accept": "text/html,text/plain,application/xhtml+xml",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(max_chars * 4)
            content_type = response.headers.get("content-type", "")
            charset_match = re.search(r"charset=([A-Za-z0-9_-]+)", content_type)
            encoding = charset_match.group(1) if charset_match else "utf-8"
            decoded = raw.decode(encoding, errors="replace")
            extracted = SimpleHTMLTextExtractor(decoded).extract()
            return truncate_text(extracted, max_chars), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP error {exc.code}"
    except urllib.error.URLError as exc:
        return None, f"URL error {exc.reason}"
    except Exception as exc:
        return None, str(exc)


def fetch_google_doc_contexts(
    matches: Sequence[RulebookLinkMatch],
    config: RetrievalServiceConfig,
    progress_callback: ProgressCallback = None,
    vm_name: Optional[str] = None,
    folder_name: Optional[str] = None,
) -> Tuple[List[GoogleDocContext], List[str]]:
    contexts: List[GoogleDocContext] = []
    warnings: List[str] = []

    tasks = []

    for match in matches:
        for url in match.urls[:2]:
            tasks.append((match.section_key, url))

    if not tasks:
        return contexts, warnings

    max_workers = max(1, min(config.max_workers, len(tasks)))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_task = {
            executor.submit(fetch_url_text, url, config.max_google_doc_chars): (
                section_key,
                url,
            )
            for section_key, url in tasks
        }

        for future in as_completed(future_to_task):
            section_key, url = future_to_task[future]

            try:
                text, error = future.result()
            except Exception as exc:
                text, error = None, str(exc)

            if error or not text:
                warnings.append(f"Skipped Google documentation URL {url}: {error or 'no text extracted'}")
                continue

            contexts.append(
                GoogleDocContext(
                    section_key=section_key,
                    url=url,
                    title=section_key,
                    text=text,
                    status=PipelineStatus.COMPLETED,
                    error_message=None,
                )
            )

    emit_event(
        progress_callback,
        "google_doc_fetch_completed",
        f"Fetched {len(contexts)} relevant Google documentation context(s).",
        vm_name=vm_name,
        folder_name=folder_name,
        data={
            "successful_docs": len(contexts),
            "skipped_docs": len(warnings),
        },
    )

    return contexts, warnings


def run_parallel_rag_queries(
    folder_context: FolderContext,
    config: RetrievalServiceConfig,
    progress_callback: ProgressCallback = None,
) -> Tuple[List[RetrievalHit], List[RetrievalHit], List[RetrievalHit], List[str]]:
    queries = build_queries(folder_context, config)
    previous_queries = build_previous_report_queries(folder_context, config)
    all_hits: List[RetrievalHit] = []
    warnings: List[str] = []

    tasks = []

    for query in queries:
        tasks.append(
            (
                config.sap_rule_book_corpus_id,
                query,
                SourceType.SAP_RULE_BOOK_RAG,
                config.rag_top_k,
            )
        )
        tasks.append(
            (
                config.sap_notes_corpus_id,
                query,
                SourceType.SAP_NOTES_RAG,
                config.rag_top_k,
            )
        )

    for query in previous_queries:
        tasks.append(
            (
                config.previous_reports_corpus_id,
                query,
                SourceType.PREVIOUS_REPORTS_RAG,
                config.previous_reports_top_k,
            )
        )

    if not tasks:
        return [], [], [], []

    max_workers = max(1, min(config.max_workers, len(tasks)))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_task = {
            executor.submit(
                run_rag_query,
                corpus_id,
                query,
                source_type,
                top_k,
                config.vector_distance_threshold,
            ): (corpus_id, query, source_type)
            for corpus_id, query, source_type, top_k in tasks
        }

        for future in as_completed(future_to_task):
            corpus_id, query, source_type = future_to_task[future]

            try:
                all_hits.extend(future.result())
            except Exception as exc:
                warnings.append(
                    f"RAG query failed for {source_type.value} corpus {corpus_id}: {exc}"
                )

    all_hits = dedupe_hits(sort_hits(all_hits))

    sap_rule_book_hits = [
        hit for hit in all_hits if hit.source_type == SourceType.SAP_RULE_BOOK_RAG
    ]
    sap_notes_hits = [
        hit for hit in all_hits if hit.source_type == SourceType.SAP_NOTES_RAG
    ]
    previous_report_hits = [
        hit for hit in all_hits if hit.source_type == SourceType.PREVIOUS_REPORTS_RAG
    ]

    return sap_rule_book_hits, sap_notes_hits, previous_report_hits, warnings


def build_retrieval_context(
    folder_context: FolderContext,
    sap_rule_book_corpus_id: str,
    sap_notes_corpus_id: str,
    previous_reports_corpus_id: str,
    progress_callback: ProgressCallback = None,
) -> RetrievalContext:
    config = build_config(
        sap_rule_book_corpus_id=sap_rule_book_corpus_id,
        sap_notes_corpus_id=sap_notes_corpus_id,
        previous_reports_corpus_id=previous_reports_corpus_id,
    )

    emit_event(
        progress_callback,
        "rulebook_link_match_started",
        "Matching folder content with rulebook section reference links.",
        vm_name=folder_context.vm_name,
        folder_name=folder_context.folder_name,
    )

    rulebook_matches = match_rulebook_sections(folder_context, config)

    emit_event(
        progress_callback,
        "rulebook_link_match_completed",
        f"Matched {len(rulebook_matches)} rulebook section reference link(s).",
        vm_name=folder_context.vm_name,
        folder_name=folder_context.folder_name,
        data={
            "matches": [
                {
                    "section_key": match.section_key,
                    "matched_score": match.matched_score,
                    "matched_terms": match.matched_terms,
                    "url_count": len(match.urls),
                }
                for match in rulebook_matches
            ]
        },
    )

    emit_event(
        progress_callback,
        "google_doc_fetch_started",
        "Fetching matched Google documentation references.",
        vm_name=folder_context.vm_name,
        folder_name=folder_context.folder_name,
    )

    google_doc_contexts, google_warnings = fetch_google_doc_contexts(
        matches=rulebook_matches,
        config=config,
        progress_callback=progress_callback,
        vm_name=folder_context.vm_name,
        folder_name=folder_context.folder_name,
    )

    emit_event(
        progress_callback,
        "rag_query_started",
        "Querying SAP Rule Book RAG, SAP Notes RAG, and previous reports RAG.",
        vm_name=folder_context.vm_name,
        folder_name=folder_context.folder_name,
    )

    sap_rule_book_hits, sap_notes_hits, previous_report_hits, rag_warnings = (
        run_parallel_rag_queries(
            folder_context=folder_context,
            config=config,
            progress_callback=progress_callback,
        )
    )

    queries = build_queries(folder_context, config)
    previous_queries = build_previous_report_queries(folder_context, config)

    folder_vector_hits = build_folder_vector_hits(
        folder_context=folder_context,
        queries=queries,
        max_hits=config.max_folder_vector_hits,
    )

    emit_event(
        progress_callback,
        "rag_query_completed",
        "Completed retrieval from RAG corpora and folder vector search.",
        vm_name=folder_context.vm_name,
        folder_name=folder_context.folder_name,
        data={
            "sap_rule_book_hits": len(sap_rule_book_hits),
            "sap_notes_hits": len(sap_notes_hits),
            "previous_report_style_hits": len(previous_report_hits),
            "folder_vector_hits": len(folder_vector_hits),
        },
    )

    return RetrievalContext(
        rulebook_link_matches=rulebook_matches,
        google_doc_contexts=google_doc_contexts,
        sap_rule_book_hits=sap_rule_book_hits,
        sap_notes_hits=sap_notes_hits,
        previous_report_style_hits=previous_report_hits,
        folder_vector_hits=folder_vector_hits,
        queries_used=dedupe_keep_order([*queries, *previous_queries]),
        warnings=[*google_warnings, *rag_warnings],
    )


class RetrievalService:
    def __init__(
        self,
        sap_rule_book_corpus_id: str,
        sap_notes_corpus_id: str,
        previous_reports_corpus_id: str,
    ):
        self.sap_rule_book_corpus_id = sap_rule_book_corpus_id
        self.sap_notes_corpus_id = sap_notes_corpus_id
        self.previous_reports_corpus_id = previous_reports_corpus_id

    def build_context(
        self,
        folder_context: FolderContext,
        progress_callback: ProgressCallback = None,
    ) -> RetrievalContext:
        return build_retrieval_context(
            folder_context=folder_context,
            sap_rule_book_corpus_id=self.sap_rule_book_corpus_id,
            sap_notes_corpus_id=self.sap_notes_corpus_id,
            previous_reports_corpus_id=self.previous_reports_corpus_id,
            progress_callback=progress_callback,
        )

    def __call__(
        self,
        folder_context: FolderContext,
        progress_callback: ProgressCallback = None,
    ) -> RetrievalContext:
        return self.build_context(
            folder_context=folder_context,
            progress_callback=progress_callback,
        )


def create_retrieval_service(
    sap_rule_book_corpus_id: str,
    sap_notes_corpus_id: str,
    previous_reports_corpus_id: str,
) -> RetrievalService:
    return RetrievalService(
        sap_rule_book_corpus_id=sap_rule_book_corpus_id,
        sap_notes_corpus_id=sap_notes_corpus_id,
        previous_reports_corpus_id=previous_reports_corpus_id,
    )
