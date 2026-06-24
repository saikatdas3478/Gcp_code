import json
import logging
from typing import Any, Dict, Optional

from google import genai
from google.genai import types as genai_types

from .folder_orchestrator import OrchestratorConfig, run_folder_orchestration
from .prompts import (
    return_description,
    return_folder_analysis_prompt,
    return_vm_consolidation_prompt,
)
from .report_writer import write_report_to_gcs
from .retrieval_service import create_retrieval_callable
from .schemas import (
    FolderAnalysisInput,
    FolderLLMResponseFormat,
    FolderRecommendationOutput,
    VMConsolidationInput,
    VMConsolidationLLMResponseFormat,
    VMRecommendationOutput,
)
logger = logging.getLogger(__name__)

genai_client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
)
def gcprule_rag_corpus_tool(query_text: str) -> Dict[str, Any]:
    return query_rag_corpus(
        corpus_id=CORPUS_ID_SAP_RULE_BOOK,
        query_text=query_text,
    )


def search_all_corpora_tool(query_text: str) -> Dict[str, Any]:
    return query_rag_corpus(
        corpus_id=CORPUS_ID_SAP_PREVIOUS_REPS,
        query_text=query_text,
    )


def sap_notes_corpus_tool(query_text: str) -> Dict[str, Any]:
    return query_rag_corpus(
        corpus_id=CORPUS_ID_SAP_NOTES_CHECK,
        query_text=query_text,
    )
def _extract_json_from_text(text: str) -> Dict[str, Any]:
    cleaned = text.strip()

    if cleaned.startswith("```json"):
        cleaned = cleaned.replace("```json", "", 1).strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.replace("```", "", 1).strip()

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    return json.loads(cleaned)


def _generate_json_response(prompt: str) -> Dict[str, Any]:
    response = genai_client.models.generate_content(
        model=os.getenv("ROOT_AGENT_MODEL"),
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=float(os.getenv("SAP_HC_LLM_TEMPERATURE", "0.2")),
            response_mime_type="application/json",
        ),
    )

    if not response.text:
        raise ValueError("LLM returned an empty response.")

    return _extract_json_from_text(response.text)


def folder_llm_callable(folder_input: FolderAnalysisInput) -> FolderRecommendationOutput:
    payload = folder_input.model_dump_json(indent=2)
    prompt = return_folder_analysis_prompt(payload)
    response_json = _generate_json_response(prompt)

    parsed = FolderLLMResponseFormat.model_validate(response_json)

    return FolderRecommendationOutput(
        vm_name=folder_input.vm_name,
        folder_relative_path=folder_input.folder_relative_path,
        short_summary=parsed.short_summary,
        observed_facts=parsed.observed_facts,
        recommendations=parsed.recommendations,
        compliance_checklist=parsed.compliance_checklist,
        files_analyzed=folder_input.source_files,
        warnings=parsed.warnings,
        retrieval_context_used=folder_input.retrieval_context,
    )


def vm_consolidation_callable(
    consolidation_input: VMConsolidationInput,
) -> VMRecommendationOutput:
    payload = consolidation_input.model_dump_json(indent=2)
    prompt = return_vm_consolidation_prompt(payload)
    response_json = _generate_json_response(prompt)

    parsed = VMConsolidationLLMResponseFormat.model_validate(response_json)

    return VMRecommendationOutput(
        vm_name=consolidation_input.vm_name,
        vm_gcs_prefix=consolidation_input.vm_gcs_prefix,
        executive_summary=parsed.executive_summary,
        folder_outputs=consolidation_input.folder_outputs,
        consolidated_recommendations=parsed.consolidated_recommendations,
        consolidated_compliance_checklist=parsed.consolidated_compliance_checklist,
        duplicate_or_merged_findings=parsed.duplicate_or_merged_findings,
        unresolved_items=parsed.unresolved_items,
        warnings=parsed.warnings,
    )


def run_sap_hc_gcs_pipeline(
    root_gcs_uri: str,
    output_gcs_uri: Optional[str] = None,
    report_format: str = "md",
) -> Dict[str, Any]:
    progress_messages = []

    def progress_callback(event):
        message = event.message if hasattr(event, "message") else str(event)
        progress_messages.append(message)
        logger.info(message)

    config = OrchestratorConfig(
        root_gcs_uri=root_gcs_uri,
        output_gcs_uri=output_gcs_uri,
        max_lines_per_file=int(os.getenv("SAP_HC_MAX_LINES_PER_FILE", "1000")),
        max_vm_workers=int(os.getenv("SAP_HC_MAX_VM_WORKERS", "3")),
        continue_on_folder_error=True,
        continue_on_vm_error=True,
    )

    retrieval_callable = create_retrieval_callable()

    pipeline_result = run_folder_orchestration(
        config=config,
        folder_llm_callable=folder_llm_callable,
        retrieval_callable=retrieval_callable,
        vm_consolidation_callable=vm_consolidation_callable,
        progress_callback=progress_callback,
    )

    final_output_uri = write_report_to_gcs(
        report=pipeline_result.report,
        output_gcs_uri=output_gcs_uri,
        report_format=report_format,
        progress_callback=lambda message: progress_messages.append(message),
    )

    return {
        "status": pipeline_result.report.processing_summary.status.value,
        "root_gcs_uri": root_gcs_uri,
        "output_gcs_uri": final_output_uri,
        "total_vm_folders_found": pipeline_result.report.processing_summary.total_vm_folders_found,
        "total_vm_folders_processed": pipeline_result.report.processing_summary.total_vm_folders_processed,
        "total_actual_folders_processed": pipeline_result.report.processing_summary.total_actual_folders_processed,
        "total_files_included": pipeline_result.report.processing_summary.total_files_included,
        "total_files_ignored": pipeline_result.report.processing_summary.total_files_ignored,
        "total_files_truncated": pipeline_result.report.processing_summary.total_files_truncated,
        "progress_messages": progress_messages[-50:],
        "errors": pipeline_result.report.processing_summary.errors,
    }

root_agent = Agent(
    name="Coordinator",
    model=os.getenv("ROOT_AGENT_MODEL"),
    instruction=return_description(),
    tools=[
        run_sap_hc_gcs_pipeline,
        search_all_corpora_tool,
        gcprule_rag_corpus_tool,
        sap_notes_corpus_tool,
        google_search_tool,
    ],
    output_key="root_agent_response",
)
