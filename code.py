from __future__ import annotations

import asyncio
import os
import queue
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from google.cloud import storage

from .gcs_ingestion import (
    DEFAULT_MAX_LINES_PER_FILE,
    ingest_vm_folder,
    list_vm_prefixes,
    parse_gcs_uri,
    validate_gcs_root_path,
)
from .recommendation_service import create_recommendation_service
from .retrieval_service import create_retrieval_service
from .schemas import (
    FolderRecommendation,
    HealthCheckRequest,
    PipelineResult,
    PipelineStatus,
    PipelineSummary,
    ReportResult,
    VMRecommendation,
)


EventQueue = queue.Queue
ProgressCallback = Callable[[Dict[str, Any]], None]


class OrchestratorError(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def emit_event(
    callback: ProgressCallback,
    event: str,
    message: str,
    status: str = "running",
    vm_name: Optional[str] = None,
    folder_name: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    payload = {
        "event": event,
        "data": {
            "status": status,
            "message": message,
            "timestamp_utc": utc_now().isoformat(),
            **(data or {}),
        },
    }

    if vm_name:
        payload["data"]["vm_name"] = vm_name

    if folder_name:
        payload["data"]["folder_name"] = folder_name

    callback(payload)


def build_output_gcs_uri(gcs_bucket_path: str) -> str:
    parsed = parse_gcs_uri(gcs_bucket_path)
    base_prefix = parsed.prefix.rstrip("/")

    if base_prefix:
        return (
            f"gs://{parsed.bucket_name}/"
            f"{base_prefix}/sap_hc_output/sap_health_check_recommendations.md"
        )

    return f"gs://{parsed.bucket_name}/sap_hc_output/sap_health_check_recommendations.md"


def parse_gcs_file_uri(gcs_uri: str) -> tuple[str, str]:
    if not gcs_uri or not gcs_uri.startswith("gs://"):
        raise OrchestratorError("Output GCS URI must start with gs://")

    raw = gcs_uri.replace("gs://", "", 1)
    parts = raw.split("/", 1)

    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise OrchestratorError("Output GCS URI must include bucket and file path")

    return parts[0].strip(), parts[1].strip("/")


def write_markdown_to_gcs(
    markdown: str,
    output_gcs_uri: str,
    client: Optional[storage.Client] = None,
) -> str:
    client = client or storage.Client()
    bucket_name, blob_name = parse_gcs_file_uri(output_gcs_uri)

    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(
        markdown,
        content_type="text/markdown; charset=utf-8",
    )

    return output_gcs_uri


def build_final_markdown_report(
    request: HealthCheckRequest,
    vm_recommendations: List[VMRecommendation],
    started_at_utc: datetime,
    completed_at_utc: datetime,
) -> str:
    lines: List[str] = [
        "# SAP HANA Health Check Recommendation Report",
        "",
        f"Generated at UTC: {completed_at_utc.isoformat()}",
        f"Input GCS Path: {request.gcs_bucket_path}",
        f"Started at UTC: {started_at_utc.isoformat()}",
        f"Completed at UTC: {completed_at_utc.isoformat()}",
        "",
    ]

    if not vm_recommendations:
        lines.extend(
            [
                "No VM recommendation output was generated.",
                "",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    for vm_recommendation in sorted(
        vm_recommendations,
        key=lambda item: item.vm_name.lower(),
    ):
        lines.extend(
            [
                f"---",
                "",
                f"# VM: {vm_recommendation.vm_name}",
                "",
                vm_recommendation.markdown.strip(),
                "",
            ]
        )

    return "\n".join(lines).strip() + "\n"


def ingestion_progress_to_event(
    callback: ProgressCallback,
    vm_name: str,
) -> Callable[[str], None]:
    def inner(message: str) -> None:
        lowered = message.lower()

        if "scanning files" in lowered:
            event_name = "file_scan_started"
        elif "scan completed" in lowered:
            event_name = "file_scan_completed"
        elif "processing vm" in lowered:
            event_name = "vm_processing_started"
        else:
            event_name = "file_scan_status"

        emit_event(
            callback=callback,
            event=event_name,
            message=message,
            vm_name=vm_name,
        )

    return inner


def retrieval_progress_to_event(
    callback: ProgressCallback,
) -> Callable[[Dict[str, Any]], None]:
    def inner(raw_event: Dict[str, Any]) -> None:
        event_name = raw_event.get("event", "retrieval_status")
        data = raw_event.get("data", raw_event)
        message = data.get("message", event_name)
        vm_name = data.get("vm_name")
        folder_name = data.get("folder_name")

        emit_event(
            callback=callback,
            event=event_name,
            message=message,
            vm_name=vm_name,
            folder_name=folder_name,
            data={key: value for key, value in data.items() if key != "message"},
        )

    return inner


def process_vm(
    request: HealthCheckRequest,
    bucket_name: str,
    vm_name: str,
    vm_prefix: str,
    progress_callback: ProgressCallback,
) -> VMRecommendation:
    client = storage.Client()

    emit_event(
        callback=progress_callback,
        event="vm_processing_started",
        message=f"{vm_name}: VM processing started.",
        vm_name=vm_name,
    )

    vm_context = ingest_vm_folder(
        bucket_name=bucket_name,
        vm_name=vm_name,
        vm_prefix=vm_prefix,
        client=client,
        max_lines_per_file=int(os.getenv("SAP_HC_MAX_LINES_PER_FILE", DEFAULT_MAX_LINES_PER_FILE)),
        progress_callback=ingestion_progress_to_event(progress_callback, vm_name),
    )

    emit_event(
        callback=progress_callback,
        event="file_scan_completed",
        message=f"{vm_name}: file scan completed.",
        vm_name=vm_name,
        data={
            "folder_count": vm_context.folder_count,
            "included_file_count": vm_context.included_file_count,
            "ignored_file_count": vm_context.ignored_file_count,
            "truncated_file_count": vm_context.truncated_file_count,
        },
    )

    retrieval_service = create_retrieval_service(
        sap_rule_book_corpus_id=request.sap_rule_book_corpus_id,
        sap_notes_corpus_id=request.sap_notes_corpus_id,
        previous_reports_corpus_id=request.previous_reports_corpus_id,
    )

    recommendation_service = create_recommendation_service()

    folder_recommendations: List[FolderRecommendation] = []
    folder_errors: List[str] = []

    for index, folder_context in enumerate(vm_context.folders, start=1):
        emit_event(
            callback=progress_callback,
            event="folder_context_started",
            message=(
                f"{vm_name}/{folder_context.folder_name}: "
                f"folder context processing started."
            ),
            vm_name=vm_name,
            folder_name=folder_context.folder_name,
            data={
                "current_folder": index,
                "total_folders": vm_context.folder_count,
            },
        )

        emit_event(
            callback=progress_callback,
            event="folder_context_built",
            message=(
                f"{vm_name}/{folder_context.folder_name}: "
                f"folder context built."
            ),
            vm_name=vm_name,
            folder_name=folder_context.folder_name,
            data={
                "included_file_count": folder_context.included_file_count,
                "ignored_file_count": folder_context.ignored_file_count,
                "truncated_file_count": folder_context.truncated_file_count,
                "chunk_count": len(folder_context.chunks),
            },
        )

        try:
            retrieval_context = retrieval_service.build_context(
                folder_context=folder_context,
                progress_callback=retrieval_progress_to_event(progress_callback),
            )

            emit_event(
                callback=progress_callback,
                event="folder_llm_started",
                message=(
                    f"{vm_name}/{folder_context.folder_name}: "
                    f"folder-level recommendation generation started."
                ),
                vm_name=vm_name,
                folder_name=folder_context.folder_name,
            )

            folder_recommendation = recommendation_service.generate_folder(
                request=request,
                folder_context=folder_context,
                retrieval_context=retrieval_context,
            )

            folder_recommendations.append(folder_recommendation)

            emit_event(
                callback=progress_callback,
                event="folder_llm_completed",
                message=(
                    f"{vm_name}/{folder_context.folder_name}: "
                    f"folder-level recommendation generated."
                ),
                vm_name=vm_name,
                folder_name=folder_context.folder_name,
                data={
                    "markdown_char_count": len(folder_recommendation.markdown),
                },
            )

        except Exception as exc:
            error_message = (
                f"{vm_name}/{folder_context.folder_name}: "
                f"folder processing failed: {exc}"
            )
            folder_errors.append(error_message)

            emit_event(
                callback=progress_callback,
                event="folder_processing_failed",
                message=error_message,
                status="failed",
                vm_name=vm_name,
                folder_name=folder_context.folder_name,
                data={
                    "traceback": traceback.format_exc(),
                },
            )

    emit_event(
        callback=progress_callback,
        event="vm_consolidation_started",
        message=f"{vm_name}: VM-level consolidation started.",
        vm_name=vm_name,
        data={
            "folder_recommendation_count": len(folder_recommendations),
            "folder_error_count": len(folder_errors),
        },
    )

    if folder_recommendations:
        vm_recommendation = recommendation_service.consolidate_vm(
            request=request,
            vm_name=vm_name,
            vm_gcs_prefix=vm_prefix,
            folder_recommendations=folder_recommendations,
        )

        vm_recommendation.warnings.extend(folder_errors)
    else:
        vm_recommendation = VMRecommendation(
            vm_name=vm_name,
            vm_gcs_prefix=vm_prefix,
            markdown=(
                "Hello, I have completed the comprehensive analysis of your VM parameter data.\n\n"
                "Here are my findings, separated into individual and combined recommendations.\n\n"
                f"VM Name: {vm_name}\n\n"
                "Individual Parameter Recommendations in below table format:\n\n"
                "| Original Parameter | Recommendation | Reasoning & Justification | Citations |\n"
                "|---|---|---|---|\n"
                "| N/A | No recommendation issued based on the available evidence. | "
                "No folder-level recommendations could be generated for this VM. | N/A |\n\n"
                "Combined Pattern Recommendations in below table format:\n\n"
                "| Original Parameters | Recommendation | Reasoning & Justification | Citations |\n"
                "|---|---|---|---|\n"
                "| N/A | No recommendation issued based on the available evidence. | "
                "No combined folder evidence was available for consolidation. | N/A |\n\n"
                "Compliance & Checklist Report in below table format:\n\n"
                "| Rule / Check | Parameter Found | Observed Value | Expected Value | Status | Reasoning | Citations |\n"
                "|---|---|---|---|---|---|---|\n"
                "| N/A | No | N/A | N/A | Not Checked | "
                "No valid folder-level recommendation output was generated. | N/A |\n\n"
                "I hope these recommendations are helpful. Please let me know if you have any questions or require further clarification on any of these points."
            ),
            folder_recommendations=[],
            warnings=folder_errors,
        )

    emit_event(
        callback=progress_callback,
        event="vm_consolidation_completed",
        message=f"{vm_name}: VM-level consolidation completed.",
        vm_name=vm_name,
        data={
            "folder_recommendation_count": len(folder_recommendations),
            "warning_count": len(vm_recommendation.warnings),
            "markdown_char_count": len(vm_recommendation.markdown),
        },
    )

    emit_event(
        callback=progress_callback,
        event="vm_processing_completed",
        message=f"{vm_name}: VM processing completed.",
        vm_name=vm_name,
    )

    return vm_recommendation


def run_pipeline_sync(
    request: HealthCheckRequest,
    progress_callback: ProgressCallback,
) -> PipelineResult:
    started_at_utc = utc_now()
    storage_client = storage.Client()

    emit_event(
        callback=progress_callback,
        event="path_validation_started",
        message=f"Validating GCS path {request.gcs_bucket_path}",
    )

    gcs_path = validate_gcs_root_path(
        request.gcs_bucket_path,
        client=storage_client,
        progress_callback=lambda message: emit_event(
            callback=progress_callback,
            event="path_validated",
            message=message,
        ),
    )

    emit_event(
        callback=progress_callback,
        event="path_validated",
        message=f"Validated GCS path {request.gcs_bucket_path}",
        data={
            "bucket_name": gcs_path.bucket_name,
            "prefix": gcs_path.prefix,
        },
    )

    emit_event(
        callback=progress_callback,
        event="vm_discovery_started",
        message=f"Discovering VM folders under {request.gcs_bucket_path}",
    )

    vm_prefixes = list_vm_prefixes(
        request.gcs_bucket_path,
        client=storage_client,
        progress_callback=lambda message: emit_event(
            callback=progress_callback,
            event="vm_discovery_status",
            message=message,
        ),
    )

    emit_event(
        callback=progress_callback,
        event="vm_discovery_completed",
        message=f"Discovered {len(vm_prefixes)} VM folder(s).",
        data={
            "vm_count": len(vm_prefixes),
            "vm_names": [vm_name for vm_name, _ in vm_prefixes],
        },
    )

    max_parallel_vms = request.max_parallel_vms or 3
    vm_recommendations: List[VMRecommendation] = []
    errors: List[str] = []

    with ThreadPoolExecutor(max_workers=max_parallel_vms) as executor:
        future_to_vm = {
            executor.submit(
                process_vm,
                request,
                gcs_path.bucket_name,
                vm_name,
                vm_prefix,
                progress_callback,
            ): vm_name
            for vm_name, vm_prefix in vm_prefixes
        }

        for future in as_completed(future_to_vm):
            vm_name = future_to_vm[future]

            try:
                vm_recommendations.append(future.result())
            except Exception as exc:
                error_message = f"{vm_name}: VM processing failed: {exc}"
                errors.append(error_message)

                emit_event(
                    callback=progress_callback,
                    event="vm_processing_failed",
                    message=error_message,
                    status="failed",
                    vm_name=vm_name,
                    data={
                        "traceback": traceback.format_exc(),
                    },
                )

    completed_at_utc = utc_now()

    output_gcs_uri = build_output_gcs_uri(request.gcs_bucket_path)

    emit_event(
        callback=progress_callback,
        event="report_write_started",
        message=f"Writing final report to {output_gcs_uri}",
        data={
            "output_gcs_uri": output_gcs_uri,
        },
    )

    final_markdown = build_final_markdown_report(
        request=request,
        vm_recommendations=vm_recommendations,
        started_at_utc=started_at_utc,
        completed_at_utc=completed_at_utc,
    )

    write_markdown_to_gcs(
        markdown=final_markdown,
        output_gcs_uri=output_gcs_uri,
        client=storage_client,
    )

    emit_event(
        callback=progress_callback,
        event="report_write_completed",
        message=f"Final report written to {output_gcs_uri}",
        data={
            "output_gcs_uri": output_gcs_uri,
            "markdown_char_count": len(final_markdown),
        },
    )

    summary = PipelineSummary(
        gcs_bucket_path=request.gcs_bucket_path,
        output_gcs_uri=output_gcs_uri,
        status=PipelineStatus.COMPLETED if not errors else PipelineStatus.FAILED,
        total_vm_folders_found=len(vm_prefixes),
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
        errors=errors,
        warnings=[
            warning
            for vm_recommendation in vm_recommendations
            for warning in vm_recommendation.warnings
        ],
    )

    result = PipelineResult(
        summary=summary,
        vm_recommendations=vm_recommendations,
        report_result=ReportResult(
            output_gcs_uri=output_gcs_uri,
            markdown=None,
        ),
    )

    emit_event(
        callback=progress_callback,
        event="pipeline_completed" if not errors else "pipeline_failed",
        message="Pipeline completed successfully." if not errors else "Pipeline completed with errors.",
        status="completed" if not errors else "failed",
        data={
            "output_gcs_uri": output_gcs_uri,
            "total_vm_folders_found": summary.total_vm_folders_found,
            "total_vm_folders_processed": summary.total_vm_folders_processed,
            "total_folders_processed": summary.total_folders_processed,
            "total_files_included": summary.total_files_included,
            "total_files_ignored": summary.total_files_ignored,
            "total_files_truncated": summary.total_files_truncated,
            "errors": errors,
        },
    )

    return result


async def run_health_check_stream(
    request: HealthCheckRequest,
) -> AsyncGenerator[Dict[str, Any], None]:
    event_queue: EventQueue = queue.Queue()
    sentinel = object()

    def queue_callback(event: Dict[str, Any]) -> None:
        event_queue.put(event)

    def worker() -> None:
        try:
            run_pipeline_sync(
                request=request,
                progress_callback=queue_callback,
            )
        except Exception as exc:
            queue_callback(
                {
                    "event": "pipeline_failed",
                    "data": {
                        "status": "failed",
                        "message": str(exc),
                        "timestamp_utc": utc_now().isoformat(),
                        "traceback": traceback.format_exc(),
                    },
                }
            )
        finally:
            event_queue.put(sentinel)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    while True:
        item = await asyncio.to_thread(event_queue.get)

        if item is sentinel:
            break

        yield item
