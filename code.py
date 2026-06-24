from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import PurePosixPath
from typing import Callable, List, Optional, Sequence

from google.cloud import storage

from .gcs_ingestion import GCSIngestionError, parse_gcs_uri
from .schemas import (
    CitationType,
    ComplianceChecklistItem,
    EvidenceLocation,
    FinalHealthCheckReport,
    FolderRecommendationOutput,
    ObservedFact,
    PipelineProcessingSummary,
    RecommendationItem,
    VMRecommendationOutput,
)


ProgressCallback = Optional[Callable[[str], None]]


class ReportWriterError(Exception):
    pass


def emit_progress(callback: ProgressCallback, message: str) -> None:
    if callback:
        callback(message)


def format_datetime(value: Optional[datetime]) -> str:
    if value is None:
        return "N/A"
    return value.isoformat()


def safe_text(value: Optional[object]) -> str:
    if value is None:
        return "N/A"
    text = str(value).strip()
    return text if text else "N/A"


def join_list(values: Sequence[str], separator: str = ", ") -> str:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return separator.join(cleaned) if cleaned else "N/A"


def normalize_markdown_cell(value: Optional[object]) -> str:
    text = safe_text(value)
    text = text.replace("\n", "<br>")
    text = text.replace("|", "\\|")
    return text


def evidence_to_text(evidence: EvidenceLocation) -> str:
    parts: List[str] = []

    parts.append(f"type={evidence.source_type.value}")

    if evidence.source_uri:
        parts.append(f"source={evidence.source_uri}")

    if evidence.source_title:
        parts.append(f"title={evidence.source_title}")

    if evidence.file_relative_path:
        parts.append(f"file={evidence.file_relative_path}")

    if evidence.line_range:
        parts.append(f"lines={evidence.line_range}")

    if evidence.corpus_id:
        parts.append(f"corpus={evidence.corpus_id}")

    if evidence.note_number:
        parts.append(f"note={evidence.note_number}")

    if evidence.rule_id:
        parts.append(f"rule={evidence.rule_id}")

    if evidence.quote_or_summary:
        parts.append(f"summary={evidence.quote_or_summary}")

    return "; ".join(parts)


def evidence_list_to_text(evidence_items: Sequence[EvidenceLocation]) -> str:
    if not evidence_items:
        return "N/A"

    return "<br>".join(
        f"{index}. {evidence_to_text(item)}"
        for index, item in enumerate(evidence_items, start=1)
    )


def observed_fact_to_text(fact: ObservedFact) -> str:
    parts = [
        f"Fact: {fact.fact_name}",
        f"Observed: {safe_text(fact.observed_value)}",
    ]

    if fact.expected_value:
        parts.append(f"Expected: {fact.expected_value}")

    if fact.unit:
        parts.append(f"Unit: {fact.unit}")

    parts.append(f"Description: {fact.description}")

    if fact.confidence is not None:
        parts.append(f"Confidence: {fact.confidence:.2f}")

    if fact.evidence:
        parts.append(f"Evidence: {evidence_list_to_text(fact.evidence)}")

    return "<br>".join(parts)


def observed_facts_to_text(facts: Sequence[ObservedFact]) -> str:
    if not facts:
        return "N/A"

    return "<br><br>".join(
        f"{index}. {observed_fact_to_text(fact)}"
        for index, fact in enumerate(facts, start=1)
    )


def recommendation_to_markdown_table_rows(
    recommendations: Sequence[RecommendationItem],
) -> List[str]:
    rows: List[str] = []

    for item in recommendations:
        rows.append(
            "| "
            + " | ".join(
                [
                    normalize_markdown_cell(item.recommendation_id),
                    normalize_markdown_cell(item.title),
                    normalize_markdown_cell(item.category.value),
                    normalize_markdown_cell(item.severity.value),
                    normalize_markdown_cell(observed_facts_to_text(item.observed_facts)),
                    normalize_markdown_cell(item.recommendation),
                    normalize_markdown_cell(item.reason),
                    normalize_markdown_cell(item.business_or_technical_impact),
                    normalize_markdown_cell("<br>".join(item.remediation_steps) if item.remediation_steps else "N/A"),
                    normalize_markdown_cell(evidence_list_to_text(item.citations)),
                    normalize_markdown_cell(f"{item.confidence:.2f}" if item.confidence is not None else "N/A"),
                ]
            )
            + " |"
        )

    return rows


def render_recommendations_section(
    title: str,
    recommendations: Sequence[RecommendationItem],
) -> str:
    lines: List[str] = [f"### {title}", ""]

    if not recommendations:
        lines.append("No recommendations generated.")
        lines.append("")
        return "\n".join(lines)

    lines.extend(
        [
            "| ID | Title | Category | Severity | Observed Facts | Recommendation | Reason | Impact | Remediation Steps | Citations | Confidence |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )

    lines.extend(recommendation_to_markdown_table_rows(recommendations))
    lines.append("")

    return "\n".join(lines)


def compliance_to_markdown_table_rows(
    checklist: Sequence[ComplianceChecklistItem],
) -> List[str]:
    rows: List[str] = []

    for item in checklist:
        rows.append(
            "| "
            + " | ".join(
                [
                    normalize_markdown_cell(item.rule_id),
                    normalize_markdown_cell(item.rule_name),
                    normalize_markdown_cell(item.parameter_or_check),
                    normalize_markdown_cell(item.expected_value),
                    normalize_markdown_cell(item.observed_value),
                    normalize_markdown_cell(item.compliance_state.value),
                    normalize_markdown_cell(item.reason),
                    normalize_markdown_cell(join_list(item.related_recommendation_ids)),
                    normalize_markdown_cell(evidence_list_to_text(item.citations)),
                ]
            )
            + " |"
        )

    return rows


def render_compliance_section(
    title: str,
    checklist: Sequence[ComplianceChecklistItem],
) -> str:
    lines: List[str] = [f"### {title}", ""]

    if not checklist:
        lines.append("No compliance checklist items generated.")
        lines.append("")
        return "\n".join(lines)

    lines.extend(
        [
            "| Rule ID | Rule Name | Parameter / Check | Expected Value | Observed Value | State | Reason | Related Recommendations | Citations |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )

    lines.extend(compliance_to_markdown_table_rows(checklist))
    lines.append("")

    return "\n".join(lines)


def render_observed_facts_section(
    facts: Sequence[ObservedFact],
) -> str:
    lines: List[str] = ["### Observed Facts", ""]

    if not facts:
        lines.append("No observed facts generated.")
        lines.append("")
        return "\n".join(lines)

    lines.extend(
        [
            "| Fact | Observed Value | Expected Value | Unit | Description | Evidence | Confidence |",
            "|---|---|---|---|---|---|---|",
        ]
    )

    for fact in facts:
        lines.append(
            "| "
            + " | ".join(
                [
                    normalize_markdown_cell(fact.fact_name),
                    normalize_markdown_cell(fact.observed_value),
                    normalize_markdown_cell(fact.expected_value),
                    normalize_markdown_cell(fact.unit),
                    normalize_markdown_cell(fact.description),
                    normalize_markdown_cell(evidence_list_to_text(fact.evidence)),
                    normalize_markdown_cell(f"{fact.confidence:.2f}" if fact.confidence is not None else "N/A"),
                ]
            )
            + " |"
        )

    lines.append("")
    return "\n".join(lines)


def render_files_analyzed_section(folder_output: FolderRecommendationOutput) -> str:
    lines: List[str] = ["### Files Analyzed", ""]

    if not folder_output.files_analyzed:
        lines.append("No file metadata available.")
        lines.append("")
        return "\n".join(lines)

    lines.extend(
        [
            "| File | GCS URI | Lines Read | Truncated |",
            "|---|---|---|---|",
        ]
    )

    for item in folder_output.files_analyzed:
        lines.append(
            "| "
            + " | ".join(
                [
                    normalize_markdown_cell(item.relative_path),
                    normalize_markdown_cell(item.gcs_uri),
                    normalize_markdown_cell(item.lines_read),
                    normalize_markdown_cell("Yes" if item.truncated else "No"),
                ]
            )
            + " |"
        )

    lines.append("")
    return "\n".join(lines)


def render_folder_output(folder_output: FolderRecommendationOutput) -> str:
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
        f"**Executive Summary:** {vm_output.executive_summary}",
        "",
    ]

    lines.append(render_recommendations_section("Consolidated VM Recommendations", vm_output.consolidated_recommendations))
    lines.append(render_compliance_section("Consolidated VM Compliance Checklist", vm_output.consolidated_compliance_checklist))

    if vm_output.duplicate_or_merged_findings:
        lines.append("### Duplicate or Merged Findings")
        lines.append("")
        for item in vm_output.duplicate_or_merged_findings:
            lines.append(f"- {item}")
        lines.append("")

    if vm_output.unresolved_items:
        lines.append("### Unresolved Items")
        lines.append("")
        for item in vm_output.unresolved_items:
            lines.append(f"- {item}")
        lines.append("")

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

    return "\n".join(lines)


def render_processing_summary(summary: PipelineProcessingSummary) -> str:
    lines = [
        "# Processing Summary",
        "",
        f"- Root GCS URI: `{summary.root_gcs_uri}`",
        f"- Status: `{summary.status.value}`",
        f"- VM folders found: {summary.total_vm_folders_found}",
        f"- VM folders processed: {summary.total_vm_folders_processed}",
        f"- Actual folders processed: {summary.total_actual_folders_processed}",
        f"- Files included: {summary.total_files_included}",
        f"- Files ignored: {summary.total_files_ignored}",
        f"- Files truncated: {summary.total_files_truncated}",
        f"- Max lines per file: {summary.max_lines_per_file}",
        f"- Started at UTC: {format_datetime(summary.started_at_utc)}",
        f"- Completed at UTC: {format_datetime(summary.completed_at_utc)}",
        "",
    ]

    if summary.errors:
        lines.append("## Processing Errors")
        lines.append("")
        for error in summary.errors:
            lines.append(f"- {error}")
        lines.append("")

    return "\n".join(lines)


def render_final_report_markdown(report: FinalHealthCheckReport) -> str:
    lines: List[str] = [
        f"# {report.report_title}",
        "",
        f"**Generated at UTC:** {format_datetime(report.generated_at_utc)}",
        "",
        f"**Root GCS URI:** `{report.root_gcs_uri}`",
        "",
    ]

    if report.output_gcs_uri:
        lines.extend([f"**Output GCS URI:** `{report.output_gcs_uri}`", ""])

    if report.global_summary:
        lines.extend(["# Global Summary", "", report.global_summary, ""])

    if report.global_warnings:
        lines.extend(["# Global Warnings", ""])
        for warning in report.global_warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append(render_processing_summary(report.processing_summary))

    if not report.vm_reports:
        lines.extend(["# VM Reports", "", "No VM reports generated.", ""])
        return "\n".join(lines)

    lines.append("# VM Reports")
    lines.append("")

    for vm_report in report.vm_reports:
        lines.append(render_vm_output(vm_report))
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def render_final_report_text(report: FinalHealthCheckReport) -> str:
    markdown = render_final_report_markdown(report)
    text = markdown.replace("<br>", "\n")
    text = text.replace("`", "")
    text = text.replace("**", "")
    text = text.replace("|", " ")
    text = text.replace("---", "")
    return text


def render_final_report_json(report: FinalHealthCheckReport) -> str:
    return report.model_dump_json(indent=2)


def resolve_output_gcs_uri(
    root_gcs_uri: str,
    output_gcs_uri: Optional[str] = None,
    filename: str = "sap_health_check_recommendations.md",
) -> str:
    if output_gcs_uri and output_gcs_uri.strip():
        cleaned = output_gcs_uri.strip()

        if not cleaned.startswith("gs://"):
            raise ReportWriterError("output_gcs_uri must start with gs://")

        if cleaned.endswith("/"):
            return cleaned + filename

        parsed = parse_gcs_uri(cleaned)
        suffix = PurePosixPath(parsed.prefix).suffix.lower()

        if suffix in {".md", ".txt", ".json"}:
            return cleaned

        return cleaned.rstrip("/") + "/" + filename

    parsed_root = parse_gcs_uri(root_gcs_uri)
    base_prefix = parsed_root.prefix.rstrip("/")

    if base_prefix:
        return f"gs://{parsed_root.bucket_name}/{base_prefix}/sap_hc_output/{filename}"

    return f"gs://{parsed_root.bucket_name}/sap_hc_output/{filename}"


def upload_text_to_gcs(
    text: str,
    output_gcs_uri: str,
    content_type: str = "text/markdown; charset=utf-8",
    client: Optional[storage.Client] = None,
    progress_callback: ProgressCallback = None,
) -> str:
    try:
        parsed = parse_gcs_uri(output_gcs_uri)
    except GCSIngestionError as exc:
        raise ReportWriterError(str(exc)) from exc

    if not parsed.prefix or parsed.prefix.endswith("/"):
        raise ReportWriterError("output_gcs_uri must point to a file, not only a folder.")

    client = client or storage.Client()
    bucket = client.bucket(parsed.bucket_name)
    blob = bucket.blob(parsed.prefix)

    emit_progress(progress_callback, f"Writing report to {output_gcs_uri}")

    blob.upload_from_string(
        data=text,
        content_type=content_type,
    )

    emit_progress(progress_callback, f"Report written successfully to {output_gcs_uri}")

    return output_gcs_uri


def write_markdown_report_to_gcs(
    report: FinalHealthCheckReport,
    output_gcs_uri: Optional[str] = None,
    client: Optional[storage.Client] = None,
    progress_callback: ProgressCallback = None,
) -> str:
    resolved_uri = resolve_output_gcs_uri(
        root_gcs_uri=report.root_gcs_uri,
        output_gcs_uri=output_gcs_uri or report.output_gcs_uri,
        filename="sap_health_check_recommendations.md",
    )

    report.output_gcs_uri = resolved_uri
    markdown = render_final_report_markdown(report)

    return upload_text_to_gcs(
        text=markdown,
        output_gcs_uri=resolved_uri,
        content_type="text/markdown; charset=utf-8",
        client=client,
        progress_callback=progress_callback,
    )


def write_text_report_to_gcs(
    report: FinalHealthCheckReport,
    output_gcs_uri: Optional[str] = None,
    client: Optional[storage.Client] = None,
    progress_callback: ProgressCallback = None,
) -> str:
    resolved_uri = resolve_output_gcs_uri(
        root_gcs_uri=report.root_gcs_uri,
        output_gcs_uri=output_gcs_uri or report.output_gcs_uri,
        filename="sap_health_check_recommendations.txt",
    )

    report.output_gcs_uri = resolved_uri
    text = render_final_report_text(report)

    return upload_text_to_gcs(
        text=text,
        output_gcs_uri=resolved_uri,
        content_type="text/plain; charset=utf-8",
        client=client,
        progress_callback=progress_callback,
    )


def write_json_report_to_gcs(
    report: FinalHealthCheckReport,
    output_gcs_uri: Optional[str] = None,
    client: Optional[storage.Client] = None,
    progress_callback: ProgressCallback = None,
) -> str:
    resolved_uri = resolve_output_gcs_uri(
        root_gcs_uri=report.root_gcs_uri,
        output_gcs_uri=output_gcs_uri or report.output_gcs_uri,
        filename="sap_health_check_recommendations.json",
    )

    report.output_gcs_uri = resolved_uri
    json_text = render_final_report_json(report)

    return upload_text_to_gcs(
        text=json_text,
        output_gcs_uri=resolved_uri,
        content_type="application/json; charset=utf-8",
        client=client,
        progress_callback=progress_callback,
    )


def write_report_to_gcs(
    report: FinalHealthCheckReport,
    output_gcs_uri: Optional[str] = None,
    report_format: str = "md",
    client: Optional[storage.Client] = None,
    progress_callback: ProgressCallback = None,
) -> str:
    normalized_format = report_format.strip().lower()

    if normalized_format in {"md", "markdown"}:
        return write_markdown_report_to_gcs(
            report=report,
            output_gcs_uri=output_gcs_uri,
            client=client,
            progress_callback=progress_callback,
        )

    if normalized_format in {"txt", "text"}:
        return write_text_report_to_gcs(
            report=report,
            output_gcs_uri=output_gcs_uri,
            client=client,
            progress_callback=progress_callback,
        )

    if normalized_format == "json":
        return write_json_report_to_gcs(
            report=report,
            output_gcs_uri=output_gcs_uri,
            client=client,
            progress_callback=progress_callback,
        )

    raise ReportWriterError(
        f"Unsupported report format: {report_format}. Supported formats: md, txt, json."
    )


def write_report_to_local_file(
    report: FinalHealthCheckReport,
    local_path: str,
    report_format: str = "md",
) -> str:
    normalized_format = report_format.strip().lower()

    if normalized_format in {"md", "markdown"}:
        content = render_final_report_markdown(report)
    elif normalized_format in {"txt", "text"}:
        content = render_final_report_text(report)
    elif normalized_format == "json":
        content = render_final_report_json(report)
    else:
        raise ReportWriterError(
            f"Unsupported report format: {report_format}. Supported formats: md, txt, json."
        )

    directory = os.path.dirname(local_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(local_path, "w", encoding="utf-8") as file_obj:
        file_obj.write(content)

    return local_path
