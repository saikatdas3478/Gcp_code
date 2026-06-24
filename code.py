from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .gcs_ingestion import FolderBundle
from .schemas import CitationType, RetrievalContext, RetrievalHit
from .tools import query_rag_corpus


SAP_NOTES_KEYWORDS = (
    "sap hana",
    "hana",
    "saptune",
    "sap note",
    "kernel",
    "parameter",
    "memory",
    "cpu",
    "io",
    "savepoint",
    "log",
    "persistence",
    "profile",
    "global.ini",
    "indexserver.ini",
    "nameserver.ini",
    "daemon.ini",
    "preprocessor.ini",
)

GCP_RULE_KEYWORDS = (
    "gcp",
    "google cloud",
    "compute engine",
    "hana certified",
    "machine type",
    "disk",
    "pd-ssd",
    "hyperdisk",
    "network",
    "os",
    "suse",
    "rhel",
    "filesystem",
    "backup",
    "nfs",
    "firewall",
    "ntp",
    "time",
    "saptune",
)

PREVIOUS_REPORT_KEYWORDS = (
    "recommendation",
    "assessment",
    "finding",
    "health check",
    "risk",
    "issue",
    "observed",
    "recommended",
    "compliant",
)


@dataclass
class RetrievalServiceConfig:
    sap_rule_book_corpus_id: Optional[str] = None
    sap_previous_reports_corpus_id: Optional[str] = None
    sap_notes_corpus_id: Optional[str] = None
    top_k: int = 8
    vector_distance_threshold: Optional[float] = None
    max_queries_per_corpus: int = 8
    max_query_chars: int = 600
    max_workers: int = 6
    include_previous_reports: bool = True
    include_google_search_placeholder: bool = False


@dataclass
class CorpusQueryTask:
    corpus_id: str
    corpus_name: str
    citation_type: CitationType
    query: str


class RetrievalServiceError(Exception):
    pass


def parse_optional_int(value: Optional[str], default: int) -> int:
    if value is None or str(value).strip() == "":
        return default

    try:
        return int(value)
    except ValueError:
        return default


def parse_optional_float(value: Optional[str]) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None

    try:
        return float(value)
    except ValueError:
        return None


def get_default_config() -> RetrievalServiceConfig:
    return RetrievalServiceConfig(
        sap_rule_book_corpus_id=os.environ.get("CORPUS_ID_SAP_RULE_BOOK"),
        sap_previous_reports_corpus_id=os.environ.get("CORPUS_ID_SAP_PREVIOUS_REPS"),
        sap_notes_corpus_id=os.environ.get("CORPUS_ID_SAP_NOTES_CHECK"),
        top_k=parse_optional_int(os.environ.get("RAG_DEFAULT_TOP_K"), 8),
        vector_distance_threshold=parse_optional_float(
            os.environ.get("RAG_DEFAULT_VECTOR_DISTANCE_THRESHOLD")
        ),
        max_queries_per_corpus=parse_optional_int(
            os.environ.get("SAP_HC_MAX_RETRIEVAL_QUERIES_PER_CORPUS"), 8
        ),
        max_query_chars=parse_optional_int(
            os.environ.get("SAP_HC_MAX_RETRIEVAL_QUERY_CHARS"), 600
        ),
        max_workers=parse_optional_int(
            os.environ.get("SAP_HC_RETRIEVAL_MAX_WORKERS"), 6
        ),
        include_previous_reports=os.environ.get(
            "SAP_HC_INCLUDE_PREVIOUS_REPORTS", "true"
        ).lower()
        in {"1", "true", "yes", "y"},
    )


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def truncate_query(query: str, max_query_chars: int) -> str:
    query = normalize_text(query)
    if len(query) <= max_query_chars:
        return query
    return query[:max_query_chars].rsplit(" ", 1)[0].strip()


def dedupe_keep_order(items: Iterable[str]) -> List[str]:
    seen = set()
    output = []

    for item in items:
        normalized = normalize_text(item)
        lowered = normalized.lower()

        if not normalized or lowered in seen:
            continue

        seen.add(lowered)
        output.append(normalized)

    return output


def extract_candidate_lines(text: str, max_lines: int = 120) -> List[str]:
    output = []

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()

        if not line:
            continue

        lowered = line.lower()

        if line.startswith("====="):
            continue

        if len(line) > 500:
            line = line[:500]

        if any(keyword in lowered for keyword in SAP_NOTES_KEYWORDS):
            output.append(line)
            continue

        if any(keyword in lowered for keyword in GCP_RULE_KEYWORDS):
            output.append(line)
            continue

        if re.search(r"\b(error|warning|failed|disabled|enabled|critical|recommend)\b", lowered):
            output.append(line)
            continue

        if re.search(r"\b\d+(\.\d+)?\s*(gb|mb|kb|tb|ms|sec|seconds|minutes|%)\b", lowered):
            output.append(line)
            continue

        if re.search(r"\b[a-zA-Z0-9_.-]+\s*[:=]\s*[^=\s].+", line):
            output.append(line)
            continue

        if len(output) >= max_lines:
            break

    return dedupe_keep_order(output)[:max_lines]


def extract_parameter_pairs(text: str, max_pairs: int = 80) -> List[str]:
    pairs = []

    patterns = [
        r"^\s*([A-Za-z0-9_.\-/]+)\s*=\s*(.+?)\s*$",
        r"^\s*([A-Za-z0-9_.\-/]+)\s*:\s*(.+?)\s*$",
        r"^\s*([A-Za-z0-9_.\-/]+)\s+(.+?)\s*$",
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

            if len(key) > 120 or len(value) > 250:
                continue

            if key.lower() in {"relative", "folder", "lines", "truncated"}:
                continue

            pairs.append(f"{key} = {value}")
            break

        if len(pairs) >= max_pairs:
            break

    return dedupe_keep_order(pairs)[:max_pairs]


def extract_file_names(bundle: FolderBundle, max_items: int = 20) -> List[str]:
    names = []

    for included_file in bundle.included_files:
        names.append(included_file.filename)
        names.append(included_file.relative_path)

    return dedupe_keep_order(names)[:max_items]


def detect_folder_theme(bundle: FolderBundle) -> str:
    folder = bundle.folder_relative_path.lower()
    text_sample = bundle.combined_text[:10000].lower()

    if "profile" in folder:
        return "SAP HANA profile parameters DEFAULT.PFL instance profiles"
    if "network" in folder or any(term in text_sample for term in ("ifconfig", "ip route", "netstat", "firewall")):
        return "network configuration firewall routes DNS ports SAP HANA"
    if "hana" in folder or any(term in text_sample for term in ("global.ini", "indexserver", "nameserver", "hdb")):
        return "SAP HANA database configuration parameters"
    if "os" in folder or any(term in text_sample for term in ("saptune", "sysctl", "limits.conf", "transparent huge")):
        return "operating system tuning SAP HANA Linux saptune"
    if "disk" in folder or "filesystem" in folder or any(term in text_sample for term in ("df -h", "lsblk", "/hana/data", "/hana/log")):
        return "filesystem storage disk layout SAP HANA persistence"
    return "SAP HANA health check configuration"


def build_base_queries(bundle: FolderBundle, config: RetrievalServiceConfig) -> List[str]:
    theme = detect_folder_theme(bundle)
    candidate_lines = extract_candidate_lines(bundle.combined_text)
    parameter_pairs = extract_parameter_pairs(bundle.combined_text)
    file_names = extract_file_names(bundle)

    queries = [
        f"{theme} recommendation best practice",
        f"{bundle.vm_name} {bundle.folder_relative_path} {theme}",
    ]

    for pair in parameter_pairs[:12]:
        queries.append(f"{theme} {pair}")

    for line in candidate_lines[:12]:
        queries.append(f"{theme} {line}")

    for file_name in file_names[:8]:
        queries.append(f"{theme} {file_name}")

    return [
        truncate_query(query, config.max_query_chars)
        for query in dedupe_keep_order(queries)
        if query
    ][: config.max_queries_per_corpus]


def build_sap_notes_queries(
    bundle: FolderBundle,
    config: RetrievalServiceConfig,
) -> List[str]:
    theme = detect_folder_theme(bundle)
    base_queries = build_base_queries(bundle, config)

    queries = [
        f"SAP Note SAP HANA {theme}",
        f"SAP HANA official SAP Notes {bundle.folder_relative_path}",
    ]

    for query in base_queries:
        queries.append(f"SAP Note {query}")

    return [
        truncate_query(query, config.max_query_chars)
        for query in dedupe_keep_order(queries)
    ][: config.max_queries_per_corpus]


def build_gcp_rule_book_queries(
    bundle: FolderBundle,
    config: RetrievalServiceConfig,
) -> List[str]:
    theme = detect_folder_theme(bundle)
    base_queries = build_base_queries(bundle, config)

    queries = [
        f"Google Cloud SAP HANA GCP rule book {theme}",
        f"GCP best practices for SAP HANA {bundle.folder_relative_path}",
    ]

    for query in base_queries:
        queries.append(f"GCP SAP HANA rule check {query}")

    return [
        truncate_query(query, config.max_query_chars)
        for query in dedupe_keep_order(queries)
    ][: config.max_queries_per_corpus]


def build_previous_report_queries(
    bundle: FolderBundle,
    config: RetrievalServiceConfig,
) -> List[str]:
    theme = detect_folder_theme(bundle)
    base_queries = build_base_queries(bundle, config)

    queries = [
        f"Previous SAP HANA assessment report {theme}",
        f"Historical recommendation SAP HANA {bundle.folder_relative_path}",
    ]

    for query in base_queries:
        queries.append(f"Previous assessment recommendation {query}")

    return [
        truncate_query(query, config.max_query_chars)
        for query in dedupe_keep_order(queries)
    ][: config.max_queries_per_corpus]


def build_corpus_tasks(
    bundle: FolderBundle,
    config: RetrievalServiceConfig,
) -> List[CorpusQueryTask]:
    tasks = []

    if config.sap_notes_corpus_id:
        for query in build_sap_notes_queries(bundle, config):
            tasks.append(
                CorpusQueryTask(
                    corpus_id=config.sap_notes_corpus_id,
                    corpus_name="sap_notes",
                    citation_type=CitationType.SAP_NOTE,
                    query=query,
                )
            )

    if config.sap_rule_book_corpus_id:
        for query in build_gcp_rule_book_queries(bundle, config):
            tasks.append(
                CorpusQueryTask(
                    corpus_id=config.sap_rule_book_corpus_id,
                    corpus_name="gcp_rule_book",
                    citation_type=CitationType.GCP_RULE_BOOK,
                    query=query,
                )
            )

    if config.include_previous_reports and config.sap_previous_reports_corpus_id:
        for query in build_previous_report_queries(bundle, config):
            tasks.append(
                CorpusQueryTask(
                    corpus_id=config.sap_previous_reports_corpus_id,
                    corpus_name="previous_reports",
                    citation_type=CitationType.PREVIOUS_ASSESSMENT_REPORT,
                    query=query,
                )
            )

    return tasks


def run_single_corpus_query(
    task: CorpusQueryTask,
    config: RetrievalServiceConfig,
) -> List[RetrievalHit]:
    response = query_rag_corpus(
        corpus_id=task.corpus_id,
        query_text=task.query,
        top_k=config.top_k,
        vector_distance_threshold=config.vector_distance_threshold,
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

        relevance_score = item.get("relevance_score")

        try:
            relevance_score = float(relevance_score) if relevance_score is not None else None
        except (TypeError, ValueError):
            relevance_score = None

        hits.append(
            RetrievalHit(
                citation_type=task.citation_type,
                corpus_id=task.corpus_id,
                corpus_name=task.corpus_name,
                query=task.query,
                text=text,
                source_uri=item.get("source_uri"),
                source_title=item.get("source_title"),
                relevance_score=relevance_score,
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


def hit_dedupe_key(hit: RetrievalHit) -> Tuple[str, str, str]:
    return (
        hit.citation_type.value,
        hit.source_uri or "",
        normalize_text(hit.text[:300]).lower(),
    )


def dedupe_hits(hits: Sequence[RetrievalHit]) -> List[RetrievalHit]:
    seen = set()
    output = []

    for hit in hits:
        key = hit_dedupe_key(hit)

        if key in seen:
            continue

        seen.add(key)
        output.append(hit)

    return output


def sort_hits(hits: Sequence[RetrievalHit]) -> List[RetrievalHit]:
    return sorted(
        hits,
        key=lambda hit: (
            hit.relevance_score is None,
            hit.relevance_score if hit.relevance_score is not None else 999999.0,
        ),
    )


def group_hits_by_type(hits: Sequence[RetrievalHit]) -> Dict[CitationType, List[RetrievalHit]]:
    grouped: Dict[CitationType, List[RetrievalHit]] = {}

    for hit in hits:
        grouped.setdefault(hit.citation_type, []).append(hit)

    return grouped


def build_retrieval_context(
    bundle: FolderBundle,
    config: Optional[RetrievalServiceConfig] = None,
) -> RetrievalContext:
    config = config or get_default_config()
    tasks = build_corpus_tasks(bundle, config)

    if not tasks:
        return RetrievalContext()

    all_hits: List[RetrievalHit] = []

    max_workers = max(1, min(config.max_workers, len(tasks)))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_task = {
            executor.submit(run_single_corpus_query, task, config): task
            for task in tasks
        }

        for future in as_completed(future_to_task):
            try:
                all_hits.extend(future.result())
            except Exception:
                continue

    all_hits = sort_hits(dedupe_hits(all_hits))
    grouped = group_hits_by_type(all_hits)

    return RetrievalContext(
        sap_notes_hits=grouped.get(CitationType.SAP_NOTE, []),
        gcp_rule_book_hits=grouped.get(CitationType.GCP_RULE_BOOK, []),
        previous_report_hits=grouped.get(CitationType.PREVIOUS_ASSESSMENT_REPORT, []),
        google_search_hits=grouped.get(CitationType.GOOGLE_SEARCH, []),
        other_hits=grouped.get(CitationType.OTHER, []),
        search_queries_used=dedupe_keep_order(task.query for task in tasks),
    )


class RetrievalService:
    def __init__(self, config: Optional[RetrievalServiceConfig] = None):
        self.config = config or get_default_config()

    def build_context(self, bundle: FolderBundle) -> RetrievalContext:
        return build_retrieval_context(bundle=bundle, config=self.config)

    def __call__(self, bundle: FolderBundle) -> RetrievalContext:
        return self.build_context(bundle)


def create_retrieval_callable(
    config: Optional[RetrievalServiceConfig] = None,
) -> RetrievalService:
    return RetrievalService(config=config)


def retrieval_context_to_text(context: RetrievalContext) -> str:
    sections = []

    def add_section(title: str, hits: List[RetrievalHit]) -> None:
        if not hits:
            return

        sections.append(f"\n## {title}\n")

        for index, hit in enumerate(hits, start=1):
            source = hit.source_uri or hit.source_title or hit.corpus_name or "unknown source"
            score = (
                f"{hit.relevance_score:.4f}"
                if hit.relevance_score is not None
                else "N/A"
            )
            sections.append(
                "\n".join(
                    [
                        f"### {index}. {source}",
                        f"Query: {hit.query}",
                        f"Score: {score}",
                        hit.text,
                    ]
                )
            )

    add_section("SAP Notes Context", context.sap_notes_hits)
    add_section("GCP Rule Book Context", context.gcp_rule_book_hits)
    add_section("Previous Assessment Reports Context", context.previous_report_hits)
    add_section("Google Search Context", context.google_search_hits)
    add_section("Other Context", context.other_hits)

    return "\n\n".join(sections).strip()
