from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from google.cloud import storage

from .gcs_ingestion import (
    DEFAULT_MAX_LINES_PER_FILE,
    FolderBundle,
    GCSIngestionError,
    VMIngestionResult,
    ingest_vm_folder,
    list_vm_prefixes,
    parse_gcs_uri,
)
from .schemas import (
    FinalHealthCheckReport,
    FolderAnalysisInput,
    FolderRecommendationOutput,
    PipelineProcessingSummary,
    ProcessingStatus,
    ProgressEvent,
    ProgressEventType,
    RetrievalContext,
    SourceFileMetadata,
    VMConsolidationInput,
    VMRecommendationOutput,
)


ProgressCallback = Optional[Callable[[ProgressEvent], None]]
RetrievalCallable = Callable[[FolderBundle], RetrievalContext]
FolderLLMCallable = Callable[[FolderAnalysisInput], FolderRecommendationOutput]
VMConsolidationCallable = Callable[[VMConsolidationInput], VMRecommendationOutput]


@dataclass
class FolderProcessingResult:
    vm_name: str
    folder_relative_path: str
    output: FolderRecommendationOutput


@dataclass
class VMProcessingResult:
    vm_name: str
    ingestion_result: VMIngestionResult
    folder_outputs: List[FolderRecommendationOutput]
    vm_output: VMRecommendationOutput


@dataclass
class PipelineResult:
    report: FinalHealthCheckReport
    vm_results: List[VMProcessingResult]


@dataclass
class OrchestratorConfig:
    root_gcs_uri: str
    output_gcs_uri: Optional[str] = None
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE
    max_vm_workers: int = 3
    continue_on_folder_error: bool = True
    continue_on_vm_error: bool = True


class FolderOrchestrationError(Exception):
    pass


def emit_progress(
    callback: ProgressCallback,
    event_type: ProgressEventType,
    status: ProcessingStatus,
    message: str,
    vm_name: Optional[str] = None,
    folder_name: Optional[str] = None,
    current: Optional[int] = None,
    total: Optional[int] = None,
    details: Optional[Dict[str, object]] = None,
) -> None:
    if callback is None:
        return

    callback(
        ProgressEvent(
            event_type=event_type,
            status=status,
            message=message,
            vm_name=vm_name,
            folder_name=folder_name,
            current=current,
            total=total,
            details=details or {},
        )
    )


def progress_event_to_text_callback(
    callback: ProgressCallback,
    event_type: ProgressEventType,
    vm_name: Optional[str] = None,
    folder_name: Optional[str] = None,
) -> Callable[[str], None]:
    def inner(message: str) -> None:
        emit_progress(
            callback=callback,
            event_type=event_type,
            status=ProcessingStatus.RUNNING,
            message=message,
            vm_name=vm_name,
            folder_name=folder_name,
        )

    return inner


def source_file_metadata_from_bundle(bundle: FolderBundle) -> List[SourceFileMetadata]:
    return [
        SourceFileMetadata(
            gcs_uri=item.gcs_uri,
            relative_path=item.relative_path,
            folder_relative_path=item.folder_relative_path,
            filename=item.filename,
            lines_read=item.lines_read,
            truncated=item.truncated,
        )
        for item in bundle.included_files
    ]


def default_retrieval_callable(_: FolderBundle) -> RetrievalContext:
    return RetrievalContext()


def default_vm_consolidation_callable(
    consolidation_input: VMConsolidationInput,
) -> VMRecommendationOutput:
    recommendations = []
    checklist = []
    warnings = []

    for folder_output in consolidation_input.folder_outputs:
        recommendations.extend(folder_output.recommendations)
        checklist.extend(folder_output.compliance_checklist)
        warnings.extend(folder_output.warnings)

    executive_summary = (
        f"Processed {len(consolidation_input.folder_outputs)} folder-level "
        f"recommendation output(s) for VM {consolidation_input.vm_name}."
    )

    return VMRecommendationOutput(
        vm_name=consolidation_input.vm_name,
        vm_gcs_prefix=consolidation_input.vm_gcs_prefix,
        executive_summary=executive_summary,
        folder_outputs=consolidation_input.folder_outputs,
        consolidated_recommendations=recommendations,
        consolidated_compliance_checklist=checklist,
        duplicate_or_merged_findings=[],
        unresolved_items=[],
        warnings=warnings,
    )


def process_folder_bundle(
    root_gcs_uri: str,
    bundle: FolderBundle,
    retrieval_callable: RetrievalCallable,
    folder_llm_callable: FolderLLMCallable,
    max_lines_per_file: int,
    progress_callback: ProgressCallback = None,
) -> FolderRecommendationOutput:
    emit_progress(
        callback=progress_callback,
        event_type=ProgressEventType.FOLDER_PROCESSING,
        status=ProcessingStatus.STARTED,
        message=f"VM {bundle.vm_name} / folder {bundle.folder_relative_path}: started folder processing.",
        vm_name=bundle.vm_name,
        folder_name=bundle.folder_relative_path,
    )

    emit_progress(
        callback=progress_callback,
        event_type=ProgressEventType.RETRIEVAL,
        status=ProcessingStatus.RUNNING,
        message=f"VM {bundle.vm_name} / folder {bundle.folder_relative_path}: running retrieval.",
        vm_name=bundle.vm_name,
        folder_name=bundle.folder_relative_path,
    )

    retrieval_context = retrieval_callable(bundle)

    emit_progress(
        callback=progress_callback,
        event_type=ProgressEventType.RETRIEVAL,
        status=ProcessingStatus.COMPLETED,
        message=f"VM {bundle.vm_name} / folder {bundle.folder_relative_path}: retrieval completed.",
        vm_name=bundle.vm_name,
        folder_name=bundle.folder_relative_path,
        details={
            "sap_notes_hits": len(retrieval_context.sap_notes_hits),
            "gcp_rule_book_hits": len(retrieval_context.gcp_rule_book_hits),
            "previous_report_hits": len(retrieval_context.previous_report_hits),
            "google_search_hits": len(retrieval_context.google_search_hits),
            "other_hits": len(retrieval_context.other_hits),
        },
    )

    folder_input = FolderAnalysisInput(
        root_gcs_uri=root_gcs_uri,
        vm_name=bundle.vm_name,
        folder_relative_path=bundle.folder_relative_path,
        folder_gcs_prefix=bundle.folder_gcs_prefix,
        source_files=source_file_metadata_from_bundle(bundle),
        combined_folder_text=bundle.combined_text,
        retrieval_context=retrieval_context,
        max_lines_per_file=max_lines_per_file,
        processing_notes=[
            "Only the configured number of lines per selected file were included.",
            "Files ignored by deterministic filter rules were not sent for recommendation generation.",
        ],
    )

    emit_progress(
        callback=progress_callback,
        event_type=ProgressEventType.LLM_GENERATION,
        status=ProcessingStatus.RUNNING,
        message=f"VM {bundle.vm_name} / folder {bundle.folder_relative_path}: generating folder recommendation.",
        vm_name=bundle.vm_name,
        folder_name=bundle.folder_relative_path,
    )

    folder_output = folder_llm_callable(folder_input)

    if folder_output.vm_name != bundle.vm_name:
        folder_output.vm_name = bundle.vm_name

    if folder_output.folder_relative_path != bundle.folder_relative_path:
        folder_output.folder_relative_path = bundle.folder_relative_path

    if not folder_output.files_analyzed:
        folder_output.files_analyzed = folder_input.source_files

    if not folder_output.retrieval_context_used:
        folder_output.retrieval_context_used = retrieval_context

    emit_progress(
        callback=progress_callback,
        event_type=ProgressEventType.LLM_GENERATION,
        status=ProcessingStatus.COMPLETED,
        message=f"VM {bundle.vm_name} / folder {bundle.folder_relative_path}: folder recommendation generated.",
        vm_name=bundle.vm_name,
        folder_name=bundle.folder_relative_path,
        details={
            "observed_facts": len(folder_output.observed_facts),
            "recommendations": len(folder_output.recommendations),
            "checklist_items": len(folder_output.compliance_checklist),
        },
    )

    emit_progress(
        callback=progress_callback,
        event_type=ProgressEventType.FOLDER_PROCESSING,
        status=ProcessingStatus.COMPLETED,
        message=f"VM {bundle.vm_name} / folder {bundle.folder_relative_path}: completed folder processing.",
        vm_name=bundle.vm_name,
        folder_name=bundle.folder_relative_path,
    )

    return folder_output


def process_vm_folder(
    root_gcs_uri: str,
    bucket_name: str,
    vm_name: str,
    vm_prefix: str,
    client: storage.Client,
    retrieval_callable: RetrievalCallable,
    folder_llm_callable: FolderLLMCallable,
    vm_consolidation_callable: VMConsolidationCallable,
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    continue_on_folder_error: bool = True,
    progress_callback: ProgressCallback = None,
) -> VMProcessingResult:
    emit_progress(
        callback=progress_callback,
        event_type=ProgressEventType.VM_PROCESSING,
        status=ProcessingStatus.STARTED,
        message=f"VM {vm_name}: started processing.",
        vm_name=vm_name,
    )

    ingestion_result = ingest_vm_folder(
        bucket_name=bucket_name,
        vm_name=vm_name,
        vm_prefix=vm_prefix,
        client=client,
        max_lines_per_file=max_lines_per_file,
        progress_callback=progress_event_to_text_callback(
            callback=progress_callback,
            event_type=ProgressEventType.FILE_READING,
            vm_name=vm_name,
        ),
    )

    folder_outputs: List[FolderRecommendationOutput] = []
    folder_errors: List[str] = []

    total_folders = len(ingestion_result.folder_bundles)

    for index, bundle in enumerate(ingestion_result.folder_bundles, start=1):
        emit_progress(
            callback=progress_callback,
            event_type=ProgressEventType.FOLDER_PROCESSING,
            status=ProcessingStatus.RUNNING,
            message=f"VM {vm_name}: processing folder {index}/{total_folders}: {bundle.folder_relative_path}.",
            vm_name=vm_name,
            folder_name=bundle.folder_relative_path,
            current=index,
            total=total_folders,
        )

        try:
            folder_output = process_folder_bundle(
                root_gcs_uri=root_gcs_uri,
                bundle=bundle,
                retrieval_callable=retrieval_callable,
                folder_llm_callable=folder_llm_callable,
                max_lines_per_file=max_lines_per_file,
                progress_callback=progress_callback,
            )
            folder_outputs.append(folder_output)
        except Exception as exc:
            error_message = (
                f"VM {vm_name} / folder {bundle.folder_relative_path}: "
                f"folder processing failed: {exc}"
            )
            folder_errors.append(error_message)

            emit_progress(
                callback=progress_callback,
                event_type=ProgressEventType.FOLDER_PROCESSING,
                status=ProcessingStatus.FAILED,
                message=error_message,
                vm_name=vm_name,
                folder_name=bundle.folder_relative_path,
            )

            if not continue_on_folder_error:
                raise FolderOrchestrationError(error_message) from exc

    emit_progress(
        callback=progress_callback,
        event_type=ProgressEventType.VM_CONSOLIDATION,
        status=ProcessingStatus.RUNNING,
        message=f"VM {vm_name}: consolidating folder outputs.",
        vm_name=vm_name,
    )

    consolidation_input = VMConsolidationInput(
        root_gcs_uri=root_gcs_uri,
        vm_name=vm_name,
        vm_gcs_prefix=vm_prefix,
        folder_outputs=folder_outputs,
        processing_notes=folder_errors,
    )

    vm_output = vm_consolidation_callable(consolidation_input)

    if folder_errors:
        vm_output.warnings.extend(folder_errors)

    emit_progress(
        callback=progress_callback,
        event_type=ProgressEventType.VM_CONSOLIDATION,
        status=ProcessingStatus.COMPLETED,
        message=f"VM {vm_name}: consolidation completed.",
        vm_name=vm_name,
        details={
            "folder_outputs": len(folder_outputs),
            "consolidated_recommendations": len(vm_output.consolidated_recommendations),
            "consolidated_checklist_items": len(vm_output.consolidated_compliance_checklist),
            "folder_errors": len(folder_errors),
        },
    )

    emit_progress(
        callback=progress_callback,
        event_type=ProgressEventType.VM_PROCESSING,
        status=ProcessingStatus.COMPLETED,
        message=f"VM {vm_name}: completed processing.",
        vm_name=vm_name,
    )

    return VMProcessingResult(
        vm_name=vm_name,
        ingestion_result=ingestion_result,
        folder_outputs=folder_outputs,
        vm_output=vm_output,
    )


def build_pipeline_summary(
    root_gcs_uri: str,
    vm_folders_found: int,
    vm_results: List[VMProcessingResult],
    max_lines_per_file: int,
    started_at_utc: datetime,
    completed_at_utc: datetime,
    errors: Optional[List[str]] = None,
) -> PipelineProcessingSummary:
    total_actual_folders_processed = 0
    total_files_included = 0
    total_files_ignored = 0
    total_files_truncated = 0

    for vm_result in vm_results:
        ingestion_result = vm_result.ingestion_result
        total_actual_folders_processed += len(ingestion_result.folder_bundles)
        total_files_included += ingestion_result.included_file_count
        total_files_ignored += ingestion_result.ignored_file_count

        for bundle in ingestion_result.folder_bundles:
            for included_file in bundle.included_files:
                if included_file.truncated:
                    total_files_truncated += 1

    status = ProcessingStatus.COMPLETED if not errors else ProcessingStatus.FAILED

    return PipelineProcessingSummary(
        root_gcs_uri=root_gcs_uri,
        total_vm_folders_found=vm_folders_found,
        total_vm_folders_processed=len(vm_results),
        total_actual_folders_processed=total_actual_folders_processed,
        total_files_included=total_files_included,
        total_files_ignored=total_files_ignored,
        total_files_truncated=total_files_truncated,
        max_lines_per_file=max_lines_per_file,
        started_at_utc=started_at_utc,
        completed_at_utc=completed_at_utc,
        status=status,
        errors=errors or [],
    )


def run_folder_orchestration(
    config: OrchestratorConfig,
    folder_llm_callable: FolderLLMCallable,
    retrieval_callable: Optional[RetrievalCallable] = None,
    vm_consolidation_callable: Optional[VMConsolidationCallable] = None,
    storage_client: Optional[storage.Client] = None,
    progress_callback: ProgressCallback = None,
) -> PipelineResult:
    started_at_utc = datetime.now(timezone.utc)
    retrieval_callable = retrieval_callable or default_retrieval_callable
    vm_consolidation_callable = (
        vm_consolidation_callable or default_vm_consolidation_callable
    )
    storage_client = storage_client or storage.Client()

    emit_progress(
        callback=progress_callback,
        event_type=ProgressEventType.PIPELINE,
        status=ProcessingStatus.STARTED,
        message=f"Pipeline started for root path: {config.root_gcs_uri}",
    )

    gcs_path = parse_gcs_uri(config.root_gcs_uri)

    emit_progress(
        callback=progress_callback,
        event_type=ProgressEventType.GCS_VALIDATION,
        status=ProcessingStatus.RUNNING,
        message=f"Validating GCS root path: {config.root_gcs_uri}",
    )

    vm_prefixes = list_vm_prefixes(
        root_gcs_uri=config.root_gcs_uri,
        client=storage_client,
        progress_callback=progress_event_to_text_callback(
            callback=progress_callback,
            event_type=ProgressEventType.VM_DISCOVERY,
        ),
    )

    emit_progress(
        callback=progress_callback,
        event_type=ProgressEventType.VM_DISCOVERY,
        status=ProcessingStatus.COMPLETED,
        message=f"Discovered {len(vm_prefixes)} VM folder(s).",
        total=len(vm_prefixes),
        details={"vm_names": [vm_name for vm_name, _ in vm_prefixes]},
    )

    vm_results: List[VMProcessingResult] = []
    errors: List[str] = []

    with ThreadPoolExecutor(max_workers=config.max_vm_workers) as executor:
        future_to_vm = {
            executor.submit(
                process_vm_folder,
                config.root_gcs_uri,
                gcs_path.bucket_name,
                vm_name,
                vm_prefix,
                storage_client,
                retrieval_callable,
                folder_llm_callable,
                vm_consolidation_callable,
                config.max_lines_per_file,
                config.continue_on_folder_error,
                progress_callback,
            ): vm_name
            for vm_name, vm_prefix in vm_prefixes
        }

        for future in as_completed(future_to_vm):
            vm_name = future_to_vm[future]

            try:
                vm_result = future.result()
                vm_results.append(vm_result)
            except Exception as exc:
                error_message = f"VM {vm_name}: processing failed: {exc}"
                errors.append(error_message)

                emit_progress(
                    callback=progress_callback,
                    event_type=ProgressEventType.VM_PROCESSING,
                    status=ProcessingStatus.FAILED,
                    message=error_message,
                    vm_name=vm_name,
                )

                if not config.continue_on_vm_error:
                    raise FolderOrchestrationError(error_message) from exc

    vm_results.sort(key=lambda item: item.vm_name)

    completed_at_utc = datetime.now(timezone.utc)

    processing_summary = build_pipeline_summary(
        root_gcs_uri=config.root_gcs_uri,
        vm_folders_found=len(vm_prefixes),
        vm_results=vm_results,
        max_lines_per_file=config.max_lines_per_file,
        started_at_utc=started_at_utc,
        completed_at_utc=completed_at_utc,
        errors=errors,
    )

    report = FinalHealthCheckReport(
        root_gcs_uri=config.root_gcs_uri,
        output_gcs_uri=config.output_gcs_uri,
        processing_summary=processing_summary,
        vm_reports=[vm_result.vm_output for vm_result in vm_results],
        global_summary=(
            f"Processed {len(vm_results)} out of {len(vm_prefixes)} discovered VM folder(s). "
            f"Generated {sum(len(vm.vm_output.consolidated_recommendations) for vm in vm_results)} consolidated recommendation(s)."
        ),
        global_warnings=errors,
    )

    final_status = ProcessingStatus.COMPLETED if not errors else ProcessingStatus.FAILED

    emit_progress(
        callback=progress_callback,
        event_type=ProgressEventType.PIPELINE,
        status=final_status,
        message="Pipeline completed." if not errors else "Pipeline completed with errors.",
        details={
            "vm_folders_found": len(vm_prefixes),
            "vm_folders_processed": len(vm_results),
            "errors": errors,
        },
    )

    return PipelineResult(report=report, vm_results=vm_results)


def run_folder_orchestration_from_gcs_path(
    root_gcs_uri: str,
    folder_llm_callable: FolderLLMCallable,
    retrieval_callable: Optional[RetrievalCallable] = None,
    vm_consolidation_callable: Optional[VMConsolidationCallable] = None,
    output_gcs_uri: Optional[str] = None,
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    max_vm_workers: int = 3,
    progress_callback: ProgressCallback = None,
) -> PipelineResult:
    config = OrchestratorConfig(
        root_gcs_uri=root_gcs_uri,
        output_gcs_uri=output_gcs_uri,
        max_lines_per_file=max_lines_per_file,
        max_vm_workers=max_vm_workers,
    )

    return run_folder_orchestration(
        config=config,
        folder_llm_callable=folder_llm_callable,
        retrieval_callable=retrieval_callable,
        vm_consolidation_callable=vm_consolidation_callable,
        progress_callback=progress_callback,
    )
