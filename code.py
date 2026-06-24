markdown_report: Optional[str] = None

class FolderRecommendationOutput(StrictBaseModel):
    vm_name: str = Field(..., min_length=1)
    folder_relative_path: str = Field(..., min_length=1)

    short_summary: str = Field(..., min_length=1)
    markdown_report: Optional[str] = None

    observed_facts: List[ObservedFact] = Field(default_factory=list)
    recommendations: List[RecommendationItem] = Field(default_factory=list)
    compliance_checklist: List[ComplianceChecklistItem] = Field(default_factory=list)

    files_analyzed: List[SourceFileMetadata] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    retrieval_context_used: RetrievalContext = Field(default_factory=RetrievalContext)

markdown_report: Optional[str] = None

class VMRecommendationOutput(StrictBaseModel):
    vm_name: str = Field(..., min_length=1)
    vm_gcs_prefix: str = Field(..., min_length=1)

    executive_summary: str = Field(..., min_length=1)
    markdown_report: Optional[str] = None

    folder_outputs: List[FolderRecommendationOutput] = Field(default_factory=list)
    consolidated_recommendations: List[RecommendationItem] = Field(default_factory=list)
    consolidated_compliance_checklist: List[ComplianceChecklistItem] = Field(
        default_factory=list
    )

    duplicate_or_merged_findings: List[str] = Field(default_factory=list)
    unresolved_items: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


def return_folder_analysis_prompt(folder_input_json: str) -> str:
    return f"""
You are a highly specialized SAP HANA Health Check Assistant and provide recommendations based on the provided VM folder logs/configuration data.

Your purpose is to deliver expert, in-depth health check recommendations for SAP HANA systems.
You achieve this by analyzing the provided folder-level VM data, identifying both individual and interdependent parameter patterns,
and generating actionable, evidence-based advice to optimize performance, security, stability, and compliance.

You must keep the recommendation output style exactly like the previous SAP Health Check report style.

Important:
- Do not return JSON.
- Do not return code blocks.
- Do not invent parameter values, file names, SAP Note numbers, GCP rule IDs, or citations.
- Use only the provided folder input, retrieved SAP Notes context, GCP rule book context, and previous report context.
- Every recommendation must include the observed value/fact/number wherever available.
- Reasoning should be short, crisp, and evidence-backed.
- If evidence is insufficient, mention it clearly.

Final Output Format:

Hello, I have completed the comprehensive analysis of the provided VM folder data.

VM Name:
Folder Name:

Individual Parameter Recommendations in below table format:

| Original Parameter | Recommendation | Reasoning & Justification | Citations |
|---|---|---|---|

Combined Pattern Recommendations in below table format:

| Original Parameters | Recommendation | Reasoning & Justification | Citations |
|---|---|---|---|

Compliance & Checklist Report in below table format:

| Rule / Check | Parameter Found | Observed Value | Expected Value | Status | Reasoning | Citations |
|---|---|---|---|---|---|---|

Use these status values:
- Compliant
- Recommendation Issued
- Not Applicable
- Not Checked

End with a short note:
I hope these recommendations are helpful. Please let me know if you have any questions or require further clarification on any of these points.

Folder input JSON:
{folder_input_json}
"""


def return_vm_consolidation_prompt(vm_input_json: str) -> str:
    return f"""
You are a highly specialized SAP HANA Health Check consolidation analyst.

You will receive all folder-level SAP Health Check outputs for one VM.

Your task:
1. Merge duplicate or overlapping recommendations.
2. Keep the strongest and most evidence-backed version.
3. Preserve observed values, numbers, parameters, file references, SAP Notes, and GCP rule citations wherever available.
4. Produce a final VM-level recommendation report in the same old SAP Health Check output style.
5. Do not return JSON.
6. Do not return code blocks.
7. Do not invent missing facts or citations.

Final Output Format:

Hello, I have completed the comprehensive analysis of your VM parameter data.
Here are my findings, separated into individual and combined recommendations.

VM Name:

Individual Parameter Recommendations in below table format:

| Original Parameter | Recommendation | Reasoning & Justification | Citations |
|---|---|---|---|

Combined Pattern Recommendations in below table format:

| Original Parameters | Recommendation | Reasoning & Justification | Citations |
|---|---|---|---|

Compliance & Checklist Report in below table format:

| Rule / Check | Parameter Found | Observed Value | Expected Value | Status | Reasoning | Citations |
|---|---|---|---|---|---|---|

Use these status values:
- Compliant
- Recommendation Issued
- Not Applicable
- Not Checked

End with:
I hope these recommendations are helpful. Please let me know if you have any questions or require further clarification on any of these points.

VM consolidation input JSON:
{vm_input_json}
"""


def _generate_text_response(prompt: str) -> str:
    response = genai_client.models.generate_content(
        model=os.getenv("ROOT_AGENT_MODEL"),
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=float(os.getenv("SAP_HC_LLM_TEMPERATURE", "0.2")),
        ),
    )

    if not response.text:
        raise ValueError("LLM returned an empty response.")

    return response.text.strip()


def folder_llm_callable(folder_input: FolderAnalysisInput) -> FolderRecommendationOutput:
    payload = folder_input.model_dump_json(indent=2)
    prompt = return_folder_analysis_prompt(payload)
    markdown_report = _generate_text_response(prompt)

    return FolderRecommendationOutput(
        vm_name=folder_input.vm_name,
        folder_relative_path=folder_input.folder_relative_path,
        short_summary=(
            f"Completed SAP Health Check recommendation generation for "
            f"folder {folder_input.folder_relative_path}."
        ),
        markdown_report=markdown_report,
        observed_facts=[],
        recommendations=[],
        compliance_checklist=[],
        files_analyzed=folder_input.source_files,
        warnings=[],
        retrieval_context_used=folder_input.retrieval_context,
    )

def vm_consolidation_callable(
    consolidation_input: VMConsolidationInput,
) -> VMRecommendationOutput:
    payload = consolidation_input.model_dump_json(indent=2)
    prompt = return_vm_consolidation_prompt(payload)
    markdown_report = _generate_text_response(prompt)

    return VMRecommendationOutput(
        vm_name=consolidation_input.vm_name,
        vm_gcs_prefix=consolidation_input.vm_gcs_prefix,
        executive_summary=(
            f"Completed consolidated SAP Health Check recommendation report "
            f"for VM {consolidation_input.vm_name}."
        ),
        markdown_report=markdown_report,
        folder_outputs=consolidation_input.folder_outputs,
        consolidated_recommendations=[],
        consolidated_compliance_checklist=[],
        duplicate_or_merged_findings=[],
        unresolved_items=[],
        warnings=[],
    )



def render_folder_output(folder_output: FolderRecommendationOutput) -> str:
    if folder_output.markdown_report:
        lines: List[str] = [
            f"## Folder: {folder_output.folder_relative_path}",
            "",
            folder_output.markdown_report.strip(),
            "",
        ]

        if folder_output.warnings:
            lines.append("### Warnings")
            lines.append("")
            for warning in folder_output.warnings:
                lines.append(f"- {warning}")
            lines.append("")

        return "\n".join(lines)

    lines: List[str] = [
        f"## Folder: {folder_output.folder_relative_path}",
        "",
        f"**Summary:** {folder_output.short_summary}",
        "",
    ]

    lines.append(render_files_analyzed_section(folder_output))
    lines.append(render_observed_facts_section(folder_output.observed_facts))
    lines.append(render_recommendations_section("Folder Recommendations", folder_output.recommendations))
    lines.append(render_compliance_section("Folder Compliance Checklist", folder_output.compliance_checklist))

    if folder_output.warnings:
        lines.append("### Warnings")
        lines.append("")
        for warning in folder_output.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines)



def render_vm_output(vm_output: VMRecommendationOutput) -> str:
    lines: List[str] = [
        f"# VM: {vm_output.vm_name}",
        "",
        f"**VM GCS Prefix:** `{vm_output.vm_gcs_prefix}`",
        "",
    ]

    if vm_output.markdown_report:
        lines.append(vm_output.markdown_report.strip())
        lines.append("")
    else:
        lines.append(f"**Executive Summary:** {vm_output.executive_summary}")
        lines.append("")
        lines.append(render_recommendations_section("Consolidated VM Recommendations", vm_output.consolidated_recommendations))
        lines.append(render_compliance_section("Consolidated VM Compliance Checklist", vm_output.consolidated_compliance_checklist))

    if vm_output.warnings:
        lines.append("### VM Warnings")
        lines.append("")
        for warning in vm_output.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    if vm_output.folder_outputs:
        lines.append("# Folder-Level Details")
        lines.append("")

        for folder_output in vm_output.folder_outputs:
            lines.append(render_folder_output(folder_output))
            lines.append("")

    return "\n".join(lines)


