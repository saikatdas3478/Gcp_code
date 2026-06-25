from google import genai
from google.genai import types as genai_types

    enable_google_link_enrichment: bool = True
    enable_google_fallback_from_rule_text: bool = True
    google_search_model: Optional[str] = None
    max_google_links_per_folder: int = 5
    max_google_context_chars: int = 2500

def parse_optional_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default

    return str(value).strip().lower() in {"1", "true", "yes", "y"}

        enable_google_link_enrichment=parse_optional_bool(
            os.environ.get("SAP_HC_ENABLE_GOOGLE_LINK_ENRICHMENT"),
            True,
        ),
        enable_google_fallback_from_rule_text=parse_optional_bool(
            os.environ.get("SAP_HC_ENABLE_GOOGLE_FALLBACK_FROM_RULE_TEXT"),
            True,
        ),
        google_search_model=os.environ.get("SAP_HC_GOOGLE_SEARCH_MODEL")
        or os.environ.get("ROOT_AGENT_MODEL"),
        max_google_links_per_folder=parse_optional_int(
            os.environ.get("SAP_HC_MAX_GOOGLE_LINKS_PER_FOLDER"),
            5,
        ),
        max_google_context_chars=parse_optional_int(
            os.environ.get("SAP_HC_MAX_GOOGLE_CONTEXT_CHARS"),
            2500,
        ),

URL_PATTERN = re.compile(r"https?://[^\s<>\]\)\}\"']+")


def clean_url(url: str) -> str:
    return url.strip().rstrip(".,;)")


def extract_urls_from_text(text: str) -> List[str]:
    urls = URL_PATTERN.findall(text or "")
    return dedupe_keep_order(clean_url(url) for url in urls if url)


def extract_urls_from_hits(hits: Sequence[RetrievalHit]) -> List[Tuple[str, RetrievalHit]]:
    output: List[Tuple[str, RetrievalHit]] = []

    for hit in hits:
        urls = extract_urls_from_text(hit.text)

        for url in urls:
            output.append((url, hit))

    seen = set()
    deduped: List[Tuple[str, RetrievalHit]] = []

    for url, hit in output:
        if url in seen:
            continue

        seen.add(url)
        deduped.append((url, hit))

    return deduped


def build_google_link_enrichment_prompt(
    bundle: FolderBundle,
    source_hit: RetrievalHit,
    config: RetrievalServiceConfig,
    url: Optional[str] = None,
) -> str:
    folder_theme = detect_folder_theme(bundle)

    observed_lines = "\n".join(
        extract_candidate_lines(bundle.combined_text, max_lines=30)
    )

    rulebook_text = source_hit.text[: config.max_google_context_chars]

    if url:
        link_instruction = f"""
A relevant Google/SAP documentation link was found in the GCP rule book section:
{url}

Use Google Search grounding to inspect this link and extract the latest recommendation relevant to the VM evidence.
"""
    else:
        link_instruction = """
No explicit URL was found in the retrieved rule-book text.
Use Google Search grounding to find the most relevant official Google Cloud SAP documentation for this rule-book section and VM evidence.
Prefer official cloud.google.com, docs.google.com, SAP-on-Google-Cloud, or SAP official sources.
"""

    return f"""
You are enriching a SAP HANA on Google Cloud health-check recommendation.

Folder theme:
{folder_theme}

VM name:
{bundle.vm_name}

Folder:
{bundle.folder_relative_path}

Observed VM evidence:
{observed_lines}

Retrieved GCP rule-book section:
{rulebook_text}

{link_instruction}

Return a concise recommendation enrichment with:
1. latest official recommendation,
2. exact setting/value/condition if available,
3. why it matters,
4. source URL/title if available.

Do not invent facts. If the searched link or official document does not contain enough information, say that clearly.
"""


def extract_grounding_sources(response: Any) -> List[Dict[str, str]]:
    sources: List[Dict[str, str]] = []

    try:
        candidates = getattr(response, "candidates", None) or []

        for candidate in candidates:
            grounding_metadata = getattr(candidate, "grounding_metadata", None)
            grounding_chunks = getattr(grounding_metadata, "grounding_chunks", None) or []

            for chunk in grounding_chunks:
                web = getattr(chunk, "web", None)

                if not web:
                    continue

                uri = getattr(web, "uri", None)
                title = getattr(web, "title", None)

                if uri or title:
                    sources.append(
                        {
                            "uri": uri or "",
                            "title": title or "",
                        }
                    )
    except Exception:
        return sources

    return sources


def run_google_search_enrichment(
    bundle: FolderBundle,
    source_hit: RetrievalHit,
    config: RetrievalServiceConfig,
    url: Optional[str] = None,
) -> Optional[RetrievalHit]:
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    model = config.google_search_model or os.environ.get("ROOT_AGENT_MODEL")

    if not project_id or not model:
        return None

    try:
        client = genai.Client(
            vertexai=True,
            project=project_id,
            location=location,
        )

        prompt = build_google_link_enrichment_prompt(
            bundle=bundle,
            source_hit=source_hit,
            config=config,
            url=url,
        )

        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=0.1,
                tools=[
                    genai_types.Tool(
                        google_search=genai_types.GoogleSearch()
                    )
                ],
            ),
        )

        text = normalize_text(response.text or "")

        if not text:
            return None

        grounding_sources = extract_grounding_sources(response)

        source_uri = url or source_hit.source_uri

        if grounding_sources and grounding_sources[0].get("uri"):
            source_uri = grounding_sources[0]["uri"]

        return RetrievalHit(
            citation_type=CitationType.GOOGLE_SEARCH,
            corpus_id=None,
            corpus_name="google_search_link_enrichment",
            query=url or source_hit.query,
            text=text,
            source_uri=source_uri,
            source_title=(
                grounding_sources[0].get("title")
                if grounding_sources
                else "Google Search enrichment"
            ),
            relevance_score=None,
            metadata={
                "source_rule_book_uri": source_hit.source_uri,
                "source_rule_book_query": source_hit.query,
                "link_found_in_rule_book": url,
                "grounding_sources": grounding_sources,
            },
        )

    except Exception:
        return None


def run_google_enrichment_for_rule_hits(
    bundle: FolderBundle,
    rule_hits: Sequence[RetrievalHit],
    config: RetrievalServiceConfig,
) -> List[RetrievalHit]:
    if not config.enable_google_link_enrichment:
        return []

    targets: List[Tuple[Optional[str], RetrievalHit]] = []

    linked_targets = extract_urls_from_hits(rule_hits)

    for url, hit in linked_targets:
        targets.append((url, hit))

    if not targets and config.enable_google_fallback_from_rule_text:
        for hit in rule_hits[: config.max_google_links_per_folder]:
            targets.append((None, hit))

    targets = targets[: config.max_google_links_per_folder]

    if not targets:
        return []

    enriched_hits: List[RetrievalHit] = []

    max_workers = max(1, min(config.max_workers, len(targets)))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_target = {
            executor.submit(
                run_google_search_enrichment,
                bundle,
                hit,
                config,
                url,
            ): (url, hit)
            for url, hit in targets
        }

        for future in as_completed(future_to_target):
            try:
                result = future.result()
                if result:
                    enriched_hits.append(result)
            except Exception:
                continue

    return dedupe_hits(enriched_hits)

        all_hits = sort_hits(dedupe_hits(all_hits))
    grouped = group_hits_by_type(all_hits)

    sap_notes_hits = grouped.get(CitationType.SAP_NOTE, [])
    gcp_rule_book_hits = grouped.get(CitationType.GCP_RULE_BOOK, [])
    previous_report_hits = grouped.get(CitationType.PREVIOUS_ASSESSMENT_REPORT, [])
    google_search_hits = grouped.get(CitationType.GOOGLE_SEARCH, [])
    other_hits = grouped.get(CitationType.OTHER, [])

    google_enrichment_hits = run_google_enrichment_for_rule_hits(
        bundle=bundle,
        rule_hits=gcp_rule_book_hits,
        config=config,
    )

    google_search_hits = sort_hits(
        dedupe_hits(
            list(google_search_hits) + list(google_enrichment_hits)
        )
    )

    return RetrievalContext(
        sap_notes_hits=sap_notes_hits,
        gcp_rule_book_hits=gcp_rule_book_hits,
        previous_report_hits=previous_report_hits,
        google_search_hits=google_search_hits,
        other_hits=other_hits,
        search_queries_used=dedupe_keep_order(task.query for task in tasks),
    )


- If retrieval_context.google_search_hits is present, treat it as latest Google Search / official documentation enrichment from links found in the GCP rule book.
- Prefer relevant Google Search enrichment over stale rule-book text when both discuss the same check.
- If Google Search enrichment is missing or says evidence is insufficient, use the SAP rule book RAG context as fallback.

- In Citations, include Google Search enrichment source URLs/titles when available.

SAP_HC_ENABLE_GOOGLE_LINK_ENRICHMENT=true
SAP_HC_ENABLE_GOOGLE_FALLBACK_FROM_RULE_TEXT=true
SAP_HC_MAX_GOOGLE_LINKS_PER_FOLDER=5
SAP_HC_MAX_GOOGLE_CONTEXT_CHARS=2500
SAP_HC_GOOGLE_SEARCH_MODEL=gemini-2.0-flash-001
