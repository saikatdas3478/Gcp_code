def is_output_path(path: str) -> bool:
    parts = PurePosixPath(str(path or "").strip("/")).parts
    return any("output" in part.lower() for part in parts)

for vm_prefix in vm_prefixes:
    normalized_prefix = vm_prefix.rstrip("/") + "/"

    if is_output_path(normalized_prefix):
        continue

    if normalized_prefix == gcs_path.prefix:
        vm_name = PurePosixPath(gcs_path.prefix.rstrip("/")).name or "root"
    else:
        vm_name = normalized_prefix.rstrip("/").split("/")[-1]

    if "output" in vm_name.lower():
        continue

    vm_results.append((vm_name, normalized_prefix))

if folder_name == "." or is_output_path(folder_name) or is_output_path(relative_path):
    ignored_file = IgnoredFile(
        gcs_uri=gcs_uri,
        relative_path=relative_path,
        reason="Ignored because root-level files and output folders are excluded from health-check processing.",
    )
    vm_ignored.append(ignored_file)
    continue

def return_folder_recommendation_prompt(
    folder_context_json: str,
    retrieval_context_json: str,
) -> str:
    return f"""
You are a highly specialized SAP HANA Health Check Assistant.

You will analyze one VM sub-folder at a time and generate a focused folder-level SAP HANA health check recommendation report.

Strict output rules:
- Return Markdown only.
- Do not return JSON.
- Do not return code blocks.
- Do not write any greeting such as "Hello, I have completed the comprehensive analysis".
- Do not write any closing sentence such as "I hope these recommendations are helpful".
- Do not write static filler text.
- Do not create placeholder rows.
- Do not write "No recommendation issued based on the available evidence".
- Do not write "N/A" rows just to fill a table.
- Do not include a table section if it has no meaningful evidence-backed rows.
- If a table has no useful evidence-backed rows, skip that whole table section completely.
- Use current observed values only from the provided VM folder logs/config files.
- Use SAP Rule Book RAG and SAP Notes RAG as recommendation evidence.
- Use Google documentation context only when it is provided and relevant.
- Use previous assessment reports only as style and recommendation-pattern guidance.
- Never use previous assessment report values as current observed values.
- Do not invent parameter values, file names, line numbers, SAP Note numbers, GCP rule IDs, URLs, or citations.
- Try hard to generate useful findings from the evidence. Only skip a table when there is truly no meaningful finding for that table.
- Every recommendation row must contain an actual observed parameter, configuration, fact, error, missing setting, or detected pattern.
- Reasoning must be short, crisp, and evidence-backed.

Folder context JSON:
{folder_context_json}

Retrieval context JSON:
{retrieval_context_json}

Generate the folder-level recommendation using only the sections that have actual findings.

Start with:

### Folder Recommendation Summary

Write a short LLM-generated summary of the important findings from the tables you generate below. This must be specific to this folder. Mention the most important risks, configuration gaps, or compliant observations found from the evidence.

Then generate any of these tables only when they have actual evidence-backed rows:

### Individual Parameter Recommendations

| Original Parameter | Recommendation | Reasoning & Justification | Citations |
|---|---|---|---|

### Combined Pattern Recommendations

| Original Parameters | Recommendation | Reasoning & Justification | Citations |
|---|---|---|---|

### Compliance & Checklist Report

| Rule / Check | Parameter Found | Observed Value | Expected Value | Status | Reasoning | Citations |
|---|---|---|---|---|---|---|

Allowed checklist statuses:
- Compliant
- Recommendation Issued
- Not Applicable
- Not Checked

Important:
If you cannot create a meaningful row for a table, omit that entire table section.
Do not say that no recommendation was found.
Do not end with a generic closing message.
""".strip()


def return_folder_analysis_prompt(folder_input_json: str) -> str:
    return f"""
You are a highly specialized SAP HANA Health Check Assistant.

Return Markdown only.

Do not write greetings.
Do not write closing messages.
Do not create empty placeholder sections.
Do not write "No recommendation issued based on the available evidence".
Skip any table section that has no meaningful evidence-backed rows.

Folder input JSON:
{folder_input_json}

Required output style:

### Folder Recommendation Summary

Write a short specific summary of the actual findings from this folder.

Then include only the tables that have real rows:

### Individual Parameter Recommendations

| Original Parameter | Recommendation | Reasoning & Justification | Citations |
|---|---|---|---|

### Combined Pattern Recommendations

| Original Parameters | Recommendation | Reasoning & Justification | Citations |
|---|---|---|---|

### Compliance & Checklist Report

| Rule / Check | Parameter Found | Observed Value | Expected Value | Status | Reasoning | Citations |
|---|---|---|---|---|---|---|
""".strip()


SECTION_HEADINGS = {
    "individual parameter recommendations",
    "combined pattern recommendations",
    "compliance & checklist report",
}


def is_static_noise_line(line: str) -> bool:
    lowered = line.strip().lower()

    if not lowered:
        return False

    noise_patterns = [
        "hello, i have completed",
        "i hope these recommendations are helpful",
        "please let me know if you have any questions",
        "no recommendation issued based on the available evidence",
        "no recommendation issued based on available evidence",
    ]

    return any(pattern in lowered for pattern in noise_patterns)


def is_table_separator_or_header(line: str) -> bool:
    stripped = line.strip().lower()

    if not stripped.startswith("|"):
        return False

    if set(stripped.replace("|", "").replace(":", "").replace("-", "").strip()) == set():
        return True

    header_terms = [
        "original parameter",
        "original parameters",
        "recommendation",
        "reasoning",
        "citations",
        "rule / check",
        "parameter found",
        "observed value",
        "expected value",
        "status",
    ]

    return any(term in stripped for term in header_terms)


def is_placeholder_table_row(line: str) -> bool:
    stripped = line.strip().lower()

    if not stripped.startswith("|"):
        return False

    placeholder_terms = [
        "no recommendation issued",
        "no meaningful finding",
        "no folder-level recommendation",
        "not enough evidence",
    ]

    if any(term in stripped for term in placeholder_terms):
        return True

    cells = [
        cell.strip().lower()
        for cell in stripped.strip("|").split("|")
    ]

    meaningful_cells = [
        cell
        for cell in cells
        if cell not in {"", "n/a", "na", "none", "no", "not checked"}
    ]

    return len(meaningful_cells) == 0


def has_meaningful_table_row(lines: List[str]) -> bool:
    for line in lines:
        stripped = line.strip()

        if not stripped.startswith("|"):
            continue

        if is_table_separator_or_header(stripped):
            continue

        if is_placeholder_table_row(stripped):
            continue

        return True

    return False


def is_section_heading(line: str) -> bool:
    lowered = line.strip().lower().replace("#", "").strip()
    return lowered in SECTION_HEADINGS


def remove_empty_sections(markdown: str) -> str:
    lines = markdown.splitlines()
    output: List[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]

        if not is_section_heading(line):
            output.append(line)
            index += 1
            continue

        section_lines = [line]
        index += 1

        while index < len(lines) and not is_section_heading(lines[index]):
            section_lines.append(lines[index])
            index += 1

        if has_meaningful_table_row(section_lines):
            output.extend(section_lines)

    return "\n".join(output)


def clean_folder_markdown(markdown: str) -> str:
    lines = []

    for line in str(markdown or "").splitlines():
        if is_static_noise_line(line):
            continue

        if is_placeholder_table_row(line):
            continue

        lines.append(line.rstrip())

    cleaned = "\n".join(lines)
    cleaned = remove_empty_sections(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    return cleaned

def normalize_markdown_output(markdown: str) -> str:
    cleaned = strip_markdown_code_fence(markdown)
    cleaned = clean_folder_markdown(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    if not cleaned:
        raise RecommendationServiceError("LLM returned an empty recommendation after cleanup.")

    return cleaned

def build_deterministic_vm_markdown(
    vm_name: str,
    vm_gcs_prefix: str,
    folder_recommendations: List[FolderRecommendation],
    folder_errors: Optional[List[str]] = None,
) -> str:
    lines: List[str] = [
        f"VM Name: {vm_name}",
        f"VM GCS Prefix: {vm_gcs_prefix}",
        "",
    ]

    if folder_recommendations:
        for index, folder_recommendation in enumerate(folder_recommendations, start=1):
            folder_markdown = clean_folder_markdown(folder_recommendation.markdown)

            if not folder_markdown.strip():
                continue

            folder_display_name = folder_recommendation.folder_name

            if folder_display_name == "." or "output" in folder_display_name.lower():
                continue

            lines.extend(
                [
                    "---",
                    "",
                    f"## Folder {index}: {folder_display_name}",
                    "",
                    folder_markdown.strip(),
                    "",
                ]
            )

    if folder_errors:
        useful_errors = [
            error
            for error in folder_errors
            if error and "output" not in error.lower()
        ]

        if useful_errors:
            lines.extend(
                [
                    "---",
                    "",
                    "## Folder Processing Errors",
                    "",
                ]
            )

            for error in useful_errors:
                lines.append(f"- {error}")

            lines.append("")

    return "\n".join(lines).strip()

def build_final_markdown_report(
    request: HealthCheckRequest,
    vm_recommendations: List[VMRecommendation],
    started_at_utc: datetime,
    completed_at_utc: datetime,
) -> str:
    valid_vm_recommendations = [
        vm_recommendation
        for vm_recommendation in vm_recommendations
        if "output" not in vm_recommendation.vm_name.lower()
    ]

    lines: List[str] = [
        "# SAP HANA Health Check Recommendation Report",
        "",
        "Hello, I have completed the comprehensive SAP HANA health check analysis for the provided GCS input path.",
        "",
        f"Input GCS Path: {request.gcs_bucket_path}",
        "",
    ]

    if valid_vm_recommendations:
        lines.append("## VMs and Folders Analyzed")
        lines.append("")

        for vm_recommendation in sorted(
            valid_vm_recommendations,
            key=lambda item: item.vm_name.lower(),
        ):
            folder_names = [
                folder.folder_name
                for folder in vm_recommendation.folder_recommendations
                if folder.folder_name != "." and "output" not in folder.folder_name.lower()
            ]

            if folder_names:
                lines.append(f"- **{vm_recommendation.vm_name}**: {', '.join(folder_names)}")
            else:
                lines.append(f"- **{vm_recommendation.vm_name}**")

        lines.append("")

    if not valid_vm_recommendations:
        lines.extend(
            [
                "No valid VM recommendation output was generated.",
                "",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    for vm_recommendation in sorted(
        valid_vm_recommendations,
        key=lambda item: item.vm_name.lower(),
    ):
        markdown = str(vm_recommendation.markdown or "").strip()

        if not markdown:
            continue

        lines.extend(
            [
                "---",
                "",
                f"# VM: {vm_recommendation.vm_name}",
                "",
                markdown,
                "",
            ]
        )

    return "\n".join(lines).strip() + "\n"
