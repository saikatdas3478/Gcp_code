from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import List, Optional, Tuple

from google.cloud import storage

from .gcs_ingestion import parse_gcs_uri
from .schemas import HealthCheckRequest, PipelineSummary, ReportResult, VMRecommendation


class ReportWriterError(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_gcs_file_uri(gcs_uri: str) -> Tuple[str, str]:
    if not gcs_uri or not gcs_uri.strip().startswith("gs://"):
        raise ReportWriterError("GCS URI must start with gs://")

    raw = gcs_uri.strip().replace("gs://", "", 1)
    parts = raw.split("/", 1)

    if len(parts) != 2:
        raise ReportWriterError("GCS URI must include bucket name and file path")

    bucket_name = parts[0].strip()
    blob_name = parts[1].strip("/")

    if not bucket_name:
        raise ReportWriterError("GCS bucket name is empty")

    if not blob_name:
        raise ReportWriterError("GCS file path is empty")

    if blob_name.endswith("/"):
        raise ReportWriterError("GCS output URI must point to a file")

    return bucket_name, blob_name


def build_output_gcs_uri(gcs_bucket_path: str) -> str:
    parsed = parse_gcs_uri(gcs_bucket_path)
    base_prefix = parsed.prefix.rstrip("/")

    if base_prefix:
        return (
            f"gs://{parsed.bucket_name}/"
            f"{base_prefix}/sap_hc_output/sap_health_check_recommendations.md"
        )

    return f"gs://{parsed.bucket_name}/sap_hc_output/sap_health_check_recommendations.md"


def normalize_markdown(markdown: Optional[str]) -> str:
    cleaned = str(markdown or "").strip()
    return cleaned


def render_report_header(
    request: HealthCheckRequest,
    started_at_utc: datetime,
    completed_at_utc: datetime,
) -> str:
    lines = [
        "# SAP HANA Health Check Recommendation Report",
        "",
        f"Generated at UTC: {completed_at_utc.isoformat()}",
        f"Input GCS Path: {request.gcs_bucket_path}",
        f"Started at UTC: {started_at_utc.isoformat()}",
        f"Completed at UTC: {completed_at_utc.isoformat()}",
        "",
        "This report contains VM-wise SAP HANA health check recommendations generated from the provided GCS input path.",
        "",
    ]

    return "\n".join(lines)


def render_empty_report() -> str:
    lines = [
        "No VM recommendation output was generated.",
        "",
    ]

    return "\n".join(lines)


def render_vm_recommendation(vm_recommendation: VMRecommendation) -> str:
    markdown = normalize_markdown(vm_recommendation.markdown)

    lines: List[str] = [
        "---",
        "",
        f"# VM: {vm_recommendation.vm_name}",
        "",
    ]

    if markdown:
        lines.extend(
            [
                markdown,
                "",
            ]
        )
    else:
        lines.extend(
            [
                "Hello, I have completed the comprehensive analysis of your VM parameter data.",
                "",
                "Here are my findings, separated into individual and combined recommendations.",
                "",
                f"VM Name: {vm_recommendation.vm_name}",
                "",
                "Individual Parameter Recommendations in below table format:",
                "",
                "| Original Parameter | Recommendation | Reasoning & Justification | Citations |",
                "|---|---|---|---|",
                "| N/A | No recommendation issued based on the available evidence. | No VM-level recommendation markdown was generated. | N/A |",
                "",
                "Combined Pattern Recommendations in below table format:",
                "",
                "| Original Parameters | Recommendation | Reasoning & Justification | Citations |",
                "|---|---|---|---|",
                "| N/A | No recommendation issued based on the available evidence. | No combined recommendation markdown was generated. | N/A |",
                "",
                "Compliance & Checklist Report in below table format:",
                "",
                "| Rule / Check | Parameter Found | Observed Value | Expected Value | Status | Reasoning | Citations |",
                "|---|---|---|---|---|---|---|",
                "| N/A | No | N/A | N/A | Not Checked | VM-level recommendation markdown was not generated. | N/A |",
                "",
                "I hope these recommendations are helpful. Please let me know if you have any questions or require further clarification on any of these points.",
                "",
            ]
        )

    if vm_recommendation.warnings:
        lines.extend(
            [
                "## VM Processing Warnings",
                "",
            ]
        )

        for warning in vm_recommendation.warnings:
            lines.append(f"- {warning}")

        lines.append("")

    return "\n".join(lines)


def build_final_markdown_report(
    request: HealthCheckRequest,
    vm_recommendations: List[VMRecommendation],
    started_at_utc: Optional[datetime] = None,
    completed_at_utc: Optional[datetime] = None,
) -> str:
    started_at_utc = started_at_utc or utc_now()
    completed_at_utc = completed_at_utc or utc_now()

    lines: List[str] = [
        render_report_header(
            request=request,
            started_at_utc=started_at_utc,
            completed_at_utc=completed_at_utc,
        )
    ]

    if not vm_recommendations:
        lines.append(render_empty_report())
        return "\n".join(lines).strip() + "\n"

    for vm_recommendation in sorted(
        vm_recommendations,
        key=lambda item: item.vm_name.lower(),
    ):
        lines.append(render_vm_recommendation(vm_recommendation))

    return "\n".join(lines).strip() + "\n"


def upload_markdown_to_gcs(
    markdown: str,
    output_gcs_uri: str,
    client: Optional[storage.Client] = None,
) -> ReportResult:
    bucket_name, blob_name = parse_gcs_file_uri(output_gcs_uri)

    client = client or storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    blob.upload_from_string(
        markdown,
        content_type="text/markdown; charset=utf-8",
    )

    return ReportResult(
        output_gcs_uri=output_gcs_uri,
        markdown=None,
    )


def write_final_report_to_gcs(
    request: HealthCheckRequest,
    vm_recommendations: List[VMRecommendation],
    started_at_utc: Optional[datetime] = None,
    completed_at_utc: Optional[datetime] = None,
    client: Optional[storage.Client] = None,
) -> ReportResult:
    output_gcs_uri = build_output_gcs_uri(request.gcs_bucket_path)

    markdown = build_final_markdown_report(
        request=request,
        vm_recommendations=vm_recommendations,
        started_at_utc=started_at_utc,
        completed_at_utc=completed_at_utc,
    )

    return upload_markdown_to_gcs(
        markdown=markdown,
        output_gcs_uri=output_gcs_uri,
        client=client,
    )


def write_markdown_to_local_file(
    markdown: str,
    local_path: str,
) -> str:
    path = PurePosixPath(local_path)

    if path.parent and str(path.parent) != ".":
        import os

        os.makedirs(str(path.parent), exist_ok=True)

    with open(local_path, "w", encoding="utf-8") as file_obj:
        file_obj.write(markdown)

    return local_path


def build_pipeline_summary_from_report(
    request: HealthCheckRequest,
    vm_recommendations: List[VMRecommendation],
    output_gcs_uri: str,
    started_at_utc: datetime,
    completed_at_utc: datetime,
    errors: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
) -> PipelineSummary:
    return PipelineSummary(
        gcs_bucket_path=request.gcs_bucket_path,
        output_gcs_uri=output_gcs_uri,
        total_vm_folders_found=len(vm_recommendations),
        total_vm_folders_processed=len(vm_recommendations),
        total_folders_processed=sum(
            len(vm_recommendation.folder_recommendations)
            for vm_recommendation in vm_recommendations
        ),
        total_files_included=sum(
            sum(
                len(folder_recommendation.included_files)
                for folder_recommendation in vm_recommendation.folder_recommendations
            )
            for vm_recommendation in vm_recommendations
        ),
        total_files_ignored=sum(
            sum(
                len(folder_recommendation.ignored_files)
                for folder_recommendation in vm_recommendation.folder_recommendations
            )
            for vm_recommendation in vm_recommendations
        ),
        total_files_truncated=sum(
            sum(
                1
                for folder_recommendation in vm_recommendation.folder_recommendations
                for source_file in folder_recommendation.included_files
                if source_file.truncated
            )
            for vm_recommendation in vm_recommendations
        ),
        started_at_utc=started_at_utc,
        completed_at_utc=completed_at_utc,
        errors=errors or [],
        warnings=warnings or [],
    )
