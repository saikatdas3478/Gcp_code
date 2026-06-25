from typing import Set  
RULEBOOK_SECTION_REFERENCE_LINKS: Dict[str, List[str]] = {  
    "os_details": [  
        "https://cloud.google.com/solutions/sap/docs/certification",  
    ],  
    "corosync": [  
        "https://cloud.google.com/solutions/sap/docs/sap-hana-ha-config-rhel",  
        "https://cloud.google.com/solutions/sap/docs/sap-hana-ha-config-sles",  
    ],  
    "sap_agents": [  
        "https://cloud.google.com/solutions/sap/docs/agent-for-sap/latest/install-config-on-vm",  
    ],  
    "ops_agent": [  
        "https://cloud.google.com/monitoring/agent/ops-agent",  
    ],  
    "machine_types_certification": [  
        "https://cloud.google.com/solutions/sap/docs/certification",  
    ],  
    "database_version_sp_level": [],  
    "saptune_sapconf": [],  
    "vm_deletion_protection": [],  
    "filesystem": [],  
    "network": [],  
    "hana_parameters": [],  
}  
  
  
RULEBOOK_SECTION_SAP_NOTES: Dict[str, List[str]] = {  
    "database_version_sp_level": [  
        "2378962",  
    ],  
    "machine_types_certification": [  
        "2456432",  
    ],  
    "os_details": [],  
    "corosync": [],  
    "sap_agents": [],  
    "ops_agent": [],  
    "saptune_sapconf": [],  
    "vm_deletion_protection": [],  
    "filesystem": [],  
    "network": [],  
    "hana_parameters": [],  
}  
  
    enable_google_first: bool = True  
    enable_section_link_lookup: bool = True  
    enable_sap_note_redirect: bool = True  
    enable_previous_report_cross_check: bool = True  
    google_search_model: Optional[str] = None  
    max_google_links_per_section: int = 5  
    max_google_context_chars: int = 2500  
  
        enable_google_first=parse_optional_bool(  
            os.environ.get("SAP_HC_ENABLE_GOOGLE_FIRST"),  
            True,  
        ),  
        enable_section_link_lookup=parse_optional_bool(  
            os.environ.get("SAP_HC_ENABLE_SECTION_LINK_LOOKUP"),  
            True,  
        ),  
        enable_sap_note_redirect=parse_optional_bool(  
            os.environ.get("SAP_HC_ENABLE_SAP_NOTE_REDIRECT"),  
            True,  
        ),  
        enable_previous_report_cross_check=parse_optional_bool(  
            os.environ.get("SAP_HC_ENABLE_PREVIOUS_REPORT_CROSS_CHECK"),  
            True,  
        ),  
        google_search_model=os.environ.get("SAP_HC_GOOGLE_SEARCH_MODEL")  
        or os.environ.get("ROOT_AGENT_MODEL"),  
        max_google_links_per_section=parse_optional_int(  
            os.environ.get("SAP_HC_MAX_GOOGLE_LINKS_PER_SECTION"),  
            5,  
        ),  
        max_google_context_chars=parse_optional_int(  
            os.environ.get("SAP_HC_MAX_GOOGLE_CONTEXT_CHARS"),  
            2500,  
        ),  
  
  
def parse_optional_bool(value: Optional[str], default: bool = False) -> bool:  
    if value is None or str(value).strip() == "":  
        return default  
  
    return str(value).strip().lower() in {"1", "true", "yes", "y"}  
  
  
SAP_NOTE_PATTERN = re.compile(r"\bSAP\s*Note\s*(\d{5,10})\b", re.IGNORECASE)  
  
  
def detect_rulebook_sections(bundle: FolderBundle) -> List[str]:  
    text = f"{bundle.folder_relative_path}\n{bundle.combined_text[:30000]}".lower()  
  
    detected: Set[str] = set()  
  
    if any(term in text for term in ["os release", "configured swap", "ram memory", "swap", "sles", "rhel"]):  
        detected.add("os_details")  
  
    if any(term in text for term in ["corosync", "totem", "secauth", "token_retransmits_before_loss_const", "consensus"]):  
        detected.add("corosync")  
  
    if any(term in text for term in ["google cloud agent", "sap installed agent", "google cloud sap agent"]):  
        detected.add("sap_agents")  
  
    if any(term in text for term in ["ops agent", "ops-agent", "not installed"]):  
        detected.add("ops_agent")  
  
    if any(term in text for term in ["machine type", "certification", "n1-highmem", "n2-highmem", "m2-ultramem"]):  
        detected.add("machine_types_certification")  
  
    if any(term in text for term in ["database version", "sp level", "hana 2.00", "sap note 2378962"]):  
        detected.add("database_version_sp_level")  
  
    if any(term in text for term in ["saptune", "sapconf", "s4hana-appserver", "hana profile"]):  
        detected.add("saptune_sapconf")  
  
    if any(term in text for term in ["deletion protection", "vm deletion protection"]):  
        detected.add("vm_deletion_protection")  
  
    if any(term in text for term in ["df -h", "lsblk", "/hana/data", "/hana/log", "/hana/shared", "filesystem"]):  
        detected.add("filesystem")  
  
    if any(term in text for term in ["ip route", "firewall", "netstat", "network", "local ip"]):  
        detected.add("network")  
  
    if any(term in text for term in ["global.ini", "indexserver.ini", "nameserver.ini", "daemon.ini"]):  
        detected.add("hana_parameters")  
  
    if not detected:  
        detected.add("hana_parameters")  
  
    return sorted(detected)  
  
  
def extract_sap_note_numbers_from_text(text: str) -> List[str]:  
    return dedupe_keep_order(SAP_NOTE_PATTERN.findall(text or ""))  
  
  
def extract_sap_note_numbers_from_hits(hits: Sequence[RetrievalHit]) -> List[str]:  
    note_numbers: List[str] = []  
  
    for hit in hits:  
        note_numbers.extend(extract_sap_note_numbers_from_text(hit.text))  
  
    return dedupe_keep_order(note_numbers)  
  
  
def get_section_reference_urls(section_keys: Sequence[str]) -> List[str]:  
    urls: List[str] = []  
  
    for section_key in section_keys:  
        urls.extend(RULEBOOK_SECTION_REFERENCE_LINKS.get(section_key, []))  
  
    return dedupe_keep_order(urls)  
  
  
def get_section_sap_notes(section_keys: Sequence[str]) -> List[str]:  
    note_numbers: List[str] = []  
  
    for section_key in section_keys:  
        note_numbers.extend(RULEBOOK_SECTION_SAP_NOTES.get(section_key, []))  
  
    return dedupe_keep_order(note_numbers)  
  
def build_google_section_enrichment_prompt(  
    bundle: FolderBundle,  
    section_key: str,  
    url: str,  
    config: RetrievalServiceConfig,  
) -> str:  
    observed_lines = "\n".join(  
        extract_candidate_lines(bundle.combined_text, max_lines=40)  
    )  
  
    return f"""  
You are validating SAP HANA on Google Cloud health-check recommendations.  
  
Section:  
{section_key}  
  
Reference URL:  
{url}  
  
VM:  
{bundle.vm_name}  
  
Folder:  
{bundle.folder_relative_path}  
  
Observed VM evidence:  
{observed_lines[:config.max_google_context_chars]}  
  
Use Google Search grounding to inspect the reference URL and related official Google Cloud/SAP documentation.  
  
Task:  
1. Find the latest official recommendation for this section.  
2. Compare it with the observed VM evidence.  
3. Extract the expected value, recommended setting, or best-practice condition where available.  
4. If the URL does not provide enough information, say clearly: "No concrete recommendation found from this link."  
  
Return concise text only.  
Do not invent facts.  
Prefer official Google Cloud SAP documentation and SAP official references.  
"""  
  
def run_google_search_for_section_link(  
    bundle: FolderBundle,  
    section_key: str,  
    url: str,  
    config: RetrievalServiceConfig,  
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
  
        prompt = build_google_section_enrichment_prompt(  
            bundle=bundle,  
            section_key=section_key,  
            url=url,  
            config=config,  
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
  
        source_uri = url  
        source_title = f"Google Search enrichment for {section_key}"  
  
        if grounding_sources:  
            source_uri = grounding_sources[0].get("uri") or source_uri  
            source_title = grounding_sources[0].get("title") or source_title  
  
        return RetrievalHit(  
            citation_type=CitationType.GOOGLE_SEARCH,  
            corpus_id=None,  
            corpus_name="google_search_section_link",  
            query=f"{section_key}: {url}",  
            text=text,  
            source_uri=source_uri,  
            source_title=source_title,  
            relevance_score=None,  
            metadata={  
                "section_key": section_key,  
                "configured_reference_url": url,  
                "grounding_sources": grounding_sources,  
            },  
        )  
  
    except Exception as exc:  
        return RetrievalHit(  
            citation_type=CitationType.GOOGLE_SEARCH,  
            corpus_id=None,  
            corpus_name="google_search_section_link_error",  
            query=f"{section_key}: {url}",  
            text=f"Google Search enrichment failed for section {section_key}: {str(exc)}",  
            source_uri=url,  
            source_title=f"Google Search enrichment error for {section_key}",  
            relevance_score=None,  
            metadata={  
                "section_key": section_key,  
                "configured_reference_url": url,  
                "error": str(exc),  
            },  
        )  
  
  
def run_google_first_section_enrichment(  
    bundle: FolderBundle,  
    config: RetrievalServiceConfig,  
) -> List[RetrievalHit]:  
    if not config.enable_google_first or not config.enable_section_link_lookup:  
        return []  
  
    section_keys = detect_rulebook_sections(bundle)  
    targets: List[Tuple[str, str]] = []  
  
    for section_key in section_keys:  
        urls = RULEBOOK_SECTION_REFERENCE_LINKS.get(section_key, [])  
        for url in urls[: config.max_google_links_per_section]:  
            targets.append((section_key, url))  
  
    if not targets:  
        return []  
  
    hits: List[RetrievalHit] = []  
    max_workers = max(1, min(config.max_workers, len(targets)))  
  
    with ThreadPoolExecutor(max_workers=max_workers) as executor:  
        future_to_target = {  
            executor.submit(  
                run_google_search_for_section_link,  
                bundle,  
                section_key,  
                url,  
                config,  
            ): (section_key, url)  
            for section_key, url in targets  
        }  
  
        for future in as_completed(future_to_target):  
            try:  
                hit = future.result()  
                if hit:  
                    hits.append(hit)  
            except Exception:  
                continue  
  
    return dedupe_hits(hits)  
  
def build_sap_note_redirect_queries(  
    bundle: FolderBundle,  
    section_keys: Sequence[str],  
    rule_hits: Sequence[RetrievalHit],  
    config: RetrievalServiceConfig,  
) -> List[str]:  
    note_numbers = []  
  
    note_numbers.extend(get_section_sap_notes(section_keys))  
    note_numbers.extend(extract_sap_note_numbers_from_text(bundle.combined_text))  
    note_numbers.extend(extract_sap_note_numbers_from_hits(rule_hits))  
  
    note_numbers = dedupe_keep_order(note_numbers)  
  
    queries = []  
  
    for note_number in note_numbers:  
        queries.append(f"SAP Note {note_number} SAP HANA recommendation")  
  
    return [  
        truncate_query(query, config.max_query_chars)  
        for query in dedupe_keep_order(queries)  
    ][: config.max_queries_per_corpus]  
  
def build_previous_report_cross_check_queries(  
    bundle: FolderBundle,  
    section_keys: Sequence[str],  
    google_hits: Sequence[RetrievalHit],  
    sap_note_hits: Sequence[RetrievalHit],  
    rule_hits: Sequence[RetrievalHit],  
    config: RetrievalServiceConfig,  
) -> List[str]:  
    theme = detect_folder_theme(bundle)  
  
    evidence_lines = extract_candidate_lines(bundle.combined_text, max_lines=15)  
  
    queries = [  
        f"Previous SAP assessment report cross check {theme}",  
    ]  
  
    for section_key in section_keys:  
        queries.append(f"Previous SAP assessment recommendation {section_key} {theme}")  
  
    for line in evidence_lines[:8]:  
        queries.append(f"Previous SAP assessment similar finding {line}")  
  
    for hit in list(google_hits)[:3] + list(sap_note_hits)[:3] + list(rule_hits)[:3]:  
        queries.append(f"Previous SAP assessment similar recommendation {hit.text[:300]}")  
  
    return [  
        truncate_query(query, config.max_query_chars)  
        for query in dedupe_keep_order(queries)  
    ][: config.max_queries_per_corpus]  
  
def build_retrieval_context(  
    bundle: FolderBundle,  
    config: Optional[RetrievalServiceConfig] = None,  
) -> RetrievalContext:  
    config = config or get_default_config()  
  
    section_keys = detect_rulebook_sections(bundle)  
  
    google_search_hits: List[RetrievalHit] = []  
    sap_notes_hits: List[RetrievalHit] = []  
    gcp_rule_book_hits: List[RetrievalHit] = []  
    previous_report_hits: List[RetrievalHit] = []  
    other_hits: List[RetrievalHit] = []  
  
    search_queries_used: List[str] = []  
  
    google_search_hits = run_google_first_section_enrichment(  
        bundle=bundle,  
        config=config,  
    )  
  
    search_queries_used.extend(hit.query for hit in google_search_hits)  
  
    base_tasks = build_corpus_tasks(bundle, config)  
  
    base_tasks = [  
        task for task in base_tasks  
        if not (  
            task.citation_type == CitationType.PREVIOUS_ASSESSMENT_REPORT  
            and config.enable_previous_report_cross_check  
        )  
    ]  
  
    all_base_hits: List[RetrievalHit] = []  
  
    if base_tasks:  
        max_workers = max(1, min(config.max_workers, len(base_tasks)))  
  
        with ThreadPoolExecutor(max_workers=max_workers) as executor:  
            future_to_task = {  
                executor.submit(run_single_corpus_query, task, config): task  
                for task in base_tasks  
            }  
  
            for future in as_completed(future_to_task):  
                task = future_to_task[future]  
                search_queries_used.append(task.query)  
  
                try:  
                    all_base_hits.extend(future.result())  
                except Exception:  
                    continue  
  
    all_base_hits = sort_hits(dedupe_hits(all_base_hits))  
    grouped = group_hits_by_type(all_base_hits)  
  
    sap_notes_hits.extend(grouped.get(CitationType.SAP_NOTE, []))  
    gcp_rule_book_hits.extend(grouped.get(CitationType.GCP_RULE_BOOK, []))  
    other_hits.extend(grouped.get(CitationType.OTHER, []))  
  
    if config.enable_sap_note_redirect and config.sap_notes_corpus_id:  
        sap_note_redirect_queries = build_sap_note_redirect_queries(  
            bundle=bundle,  
            section_keys=section_keys,  
            rule_hits=gcp_rule_book_hits,  
            config=config,  
        )  
  
        sap_note_tasks = [  
            CorpusQueryTask(  
                corpus_id=config.sap_notes_corpus_id,  
                corpus_name="sap_notes_redirect",  
                citation_type=CitationType.SAP_NOTE,  
                query=query,  
            )  
            for query in sap_note_redirect_queries  
        ]  
  
        if sap_note_tasks:  
            max_workers = max(1, min(config.max_workers, len(sap_note_tasks)))  
  
            with ThreadPoolExecutor(max_workers=max_workers) as executor:  
                future_to_task = {  
                    executor.submit(run_single_corpus_query, task, config): task  
                    for task in sap_note_tasks  
                }  
  
                for future in as_completed(future_to_task):  
                    task = future_to_task[future]  
                    search_queries_used.append(task.query)  
  
                    try:  
                        sap_notes_hits.extend(future.result())  
                    except Exception:  
                        continue  
  
    sap_notes_hits = sort_hits(dedupe_hits(sap_notes_hits))  
    gcp_rule_book_hits = sort_hits(dedupe_hits(gcp_rule_book_hits))  
    google_search_hits = sort_hits(dedupe_hits(google_search_hits))  
  
    if config.enable_previous_report_cross_check and config.sap_previous_reports_corpus_id:  
        previous_queries = build_previous_report_cross_check_queries(  
            bundle=bundle,  
            section_keys=section_keys,  
            google_hits=google_search_hits,  
            sap_note_hits=sap_notes_hits,  
            rule_hits=gcp_rule_book_hits,  
            config=config,  
        )  
  
        previous_tasks = [  
            CorpusQueryTask(  
                corpus_id=config.sap_previous_reports_corpus_id,  
                corpus_name="previous_reports_cross_check",  
                citation_type=CitationType.PREVIOUS_ASSESSMENT_REPORT,  
                query=query,  
            )  
            for query in previous_queries  
        ]  
  
        if previous_tasks:  
            max_workers = max(1, min(config.max_workers, len(previous_tasks)))  
  
            with ThreadPoolExecutor(max_workers=max_workers) as executor:  
                future_to_task = {  
                    executor.submit(run_single_corpus_query, task, config): task  
                    for task in previous_tasks  
                }  
  
                for future in as_completed(future_to_task):  
                    task = future_to_task[future]  
                    search_queries_used.append(task.query)  
  
                    try:  
                        previous_report_hits.extend(future.result())  
                    except Exception:  
                        continue  
  
    previous_report_hits = sort_hits(dedupe_hits(previous_report_hits))  
  
    return RetrievalContext(  
        sap_notes_hits=sap_notes_hits,  
        gcp_rule_book_hits=gcp_rule_book_hits,  
        previous_report_hits=previous_report_hits,  
        google_search_hits=google_search_hits,  
        other_hits=other_hits,  
        search_queries_used=dedupe_keep_order(search_queries_used),  
    )  
  
  
- Retrieval priority must be understood as:  
  1. Google Search enrichment from manually configured section links is the first and freshest source.  
  2. SAP Notes RAG is the second source, especially when the rule book or evidence mentions an SAP Note number.  
  3. GCP Rule Book RAG is the fallback and validation source.  
  4. Previous Assessment Reports RAG is a soft cross-check/reference source only.  
- Do not let previous assessment reports override official Google Cloud, SAP Notes, or GCP rule book recommendations.  
- If previous assessment reports differ from official sources, mention that only as a soft reference, not as the final recommendation.  
- If Google Search enrichment fails or says no concrete recommendation was found, use SAP Notes and GCP Rule Book RAG as fallback.  
  
SAP_HC_ENABLE_GOOGLE_FIRST=true  
SAP_HC_ENABLE_SECTION_LINK_LOOKUP=true  
SAP_HC_ENABLE_SAP_NOTE_REDIRECT=true  
SAP_HC_ENABLE_PREVIOUS_REPORT_CROSS_CHECK=true  
SAP_HC_MAX_GOOGLE_LINKS_PER_SECTION=5  
SAP_HC_MAX_GOOGLE_CONTEXT_CHARS=2500  
SAP_HC_GOOGLE_SEARCH_MODEL=gemini-2.0-flash-001  
