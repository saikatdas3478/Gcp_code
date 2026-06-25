from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types as genai_types

from .prompts import (
    return_folder_recommendation_prompt,
    return_vm_consolidation_prompt,
)
from .schemas import (
    FolderContext,
    FolderRecommendation,
    HealthCheckRequest,
    RetrievalContext,
    RuntimeConfig,
    SourceFile,
    VMRecommendation,
)


class RecommendationServiceError(Exception):
    pass


def parse_optional_int(value: Optional[str]) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_optional_float(value: Optional[str], default: float) -> float:
    if value is None or str(value).strip() == "":
        return default

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_project_id() -> str:
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")

    if not project_id:
        raise RecommendationServiceError("GOOGLE_CLOUD_PROJECT environment variable is required.")

    return project_id


def get_location() -> str:
    return os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")


def get_model_name() -> str:
    model_name = os.getenv("ROOT_AGENT_MODEL")

    if not model_name:
        raise RecommendationServiceError("ROOT_AGENT_MODEL environment variable is required.")

    return model_name


def get_default_runtime_config() -> RuntimeConfig:
    model_name = get_model_name()

    return RuntimeConfig(
        project_id=get_project_id(),
        location=get_location(),
        max_lines_per_file=int(os.getenv("SAP_HC_MAX_LINES_PER_FILE", "1000")),
        max_parallel_vms=int(os.getenv("SAP_HC_MAX_PARALLEL_VMS", "3")),
        llm={
            "model_name": model_name,
            "temperature": parse_optional_float(os.getenv("SAP_HC_LLM_TEMPERATURE"), 0.2),
            "max_output_tokens": parse_optional_int(os.getenv("SAP_HC_MAX_OUTPUT_TOKENS")),
        },
    )


def get_genai_client(runtime_config: RuntimeConfig) -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=runtime_config.project_id,
        location=runtime_config.location,
    )


def strip_markdown_code_fence(text: str) -> str:
    cleaned = str(text or "").strip()

    if cleaned.startswith("```markdown"):
        cleaned = cleaned.replace("```markdown", "", 1).strip()
    elif cleaned.startswith("```md"):
        cleaned = cleaned.replace("```md", "", 1).strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.replace("```", "", 1).strip()

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    return cleaned


def normalize_markdown_output(markdown: str) -> str:
    cleaned = strip_markdown_code_fence(markdown)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    if not cleaned:
        raise RecommendationServiceError("LLM returned an empty recommendation.")

    return cleaned


def build_generate_config(runtime_config: RuntimeConfig) -> genai_types.GenerateContentConfig:
    config: Dict[str, Any] = {
        "temperature": runtime_config.llm.temperature,
    }

    if runtime_config.llm.max_output_tokens:
        config["max_output_tokens"] = runtime_config.llm.max_output_tokens

    return genai_types.GenerateContentConfig(**config)


def generate_markdown_response(
    prompt: str,
    client: Optional[genai.Client] = None,
    runtime_config: Optional[RuntimeConfig] = None,
) -> str:
    runtime_config = runtime_config or get_default_runtime_config()
    client = client or get_genai_client(runtime_config)

    response = client.models.generate_content(
        model=runtime_config.llm.model_name,
        contents=prompt,
        config=build_generate_config(runtime_config),
    )

    if not response.text:
        raise RecommendationServiceError("LLM returned an empty response.")

    return normalize_markdown_output(response.text)


def source_file_for_prompt(source_file: SourceFile) -> Dict[str, Any]:
    return {
        "gcs_uri": source_file.gcs_uri,
        "relative_path": source_file.relative_path,
        "folder_relative_path": source_file.folder_relative_path,
        "filename": source_file.filename,
        "lines_read": source_file.lines_read,
        "truncated": source_file.truncated,
        "content": source_file.content,
    }


def folder_context_for_prompt(folder_context: FolderContext) -> Dict[str, Any]:
    return {
        "vm_name": folder_context.vm_name,
        "folder_name": folder_context.folder_name,
        "folder_gcs_prefix": folder_context.folder_gcs_prefix,
        "included_file_count": folder_context.included_file_count,
        "ignored_file_count": folder_context.ignored_file_count,
        "truncated_file_count": folder_context.truncated_file_count,
        "included_files": [
            source_file_for_prompt(source_file)
            for source_file in folder_context.included_files
        ],
        "ignored_files": [
            ignored_file.model_dump(mode="json")
            for ignored_file in folder_context.ignored_files
        ],
        "combined_text": folder_context.combined_text,
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "source_uri": chunk.source_uri,
                "relative_path": chunk.relative_path,
                "line_range": chunk.line_range,
                "text": chunk.text,
                "metadata": chunk.metadata,
            }
            for chunk in folder_context.chunks
        ],
    }


def retrieval_hit_for_prompt(hit: Any) -> Dict[str, Any]:
    if hasattr(hit, "model_dump"):
        data = hit.model_dump(mode="json")
    elif isinstance(hit, dict):
        data = hit
    else:
        data = {"text": str(hit)}

    if data.get("text"):
        data["text"] = str(data["text"])[:6000]

    return data


def retrieval_context_for_prompt(retrieval_context: RetrievalContext) -> Dict[str, Any]:
    return {
        "rulebook_link_matches": [
            item.model_dump(mode="json")
            for item in retrieval_context.rulebook_link_matches
        ],
        "google_doc_contexts": [
            {
                **item.model_dump(mode="json"),
                "text": item.text[:6000] if item.text else None,
            }
            for item in retrieval_context.google_doc_contexts
        ],
        "sap_rule_book_hits": [
            retrieval_hit_for_prompt(hit)
            for hit in retrieval_context.sap_rule_book_hits
        ],
        "sap_notes_hits": [
            retrieval_hit_for_prompt(hit)
            for hit in retrieval_context.sap_notes_hits
        ],
        "previous_report_style_hits": [
            retrieval_hit_for_prompt(hit)
            for hit in retrieval_context.previous_report_style_hits
        ],
        "folder_vector_hits": [
            retrieval_hit_for_prompt(hit)
            for hit in retrieval_context.folder_vector_hits
        ],
        "queries_used": retrieval_context.queries_used,
        "warnings": retrieval_context.warnings,
        "previous_report_usage_rule": (
            "Previous assessment report hits are outdated examples. "
            "Use them only for report style, writing pattern, and recommendation phrasing. "
            "Never use values from previous reports as current observed values."
        ),
    }


def folder_context_json_for_prompt(folder_context: FolderContext) -> str:
    return json.dumps(
        folder_context_for_prompt(folder_context),
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def retrieval_context_json_for_prompt(retrieval_context: RetrievalContext) -> str:
    return json.dumps(
        retrieval_context_for_prompt(retrieval_context),
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def generate_folder_recommendation(
    request: HealthCheckRequest,
    folder_context: FolderContext,
    retrieval_context: RetrievalContext,
    client: Optional[genai.Client] = None,
    runtime_config: Optional[RuntimeConfig] = None,
) -> FolderRecommendation:
    prompt = return_folder_recommendation_prompt(
        folder_context_json=folder_context_json_for_prompt(folder_context),
        retrieval_context_json=retrieval_context_json_for_prompt(retrieval_context),
    )

    markdown = generate_markdown_response(
        prompt=prompt,
        client=client,
        runtime_config=runtime_config,
    )

    return FolderRecommendation(
        vm_name=folder_context.vm_name,
        folder_name=folder_context.folder_name,
        markdown=markdown,
        included_files=folder_context.included_files,
        ignored_files=folder_context.ignored_files,
        retrieval_context=retrieval_context,
        warnings=retrieval_context.warnings,
    )


def folder_recommendation_for_vm_prompt(
    folder_recommendation: FolderRecommendation,
) -> Dict[str, Any]:
    return {
        "vm_name": folder_recommendation.vm_name,
        "folder_name": folder_recommendation.folder_name,
        "markdown": folder_recommendation.markdown,
        "included_files": [
            {
                "gcs_uri": item.gcs_uri,
                "relative_path": item.relative_path,
                "lines_read": item.lines_read,
                "truncated": item.truncated,
            }
            for item in folder_recommendation.included_files
        ],
        "ignored_files": [
            item.model_dump(mode="json")
            for item in folder_recommendation.ignored_files
        ],
        "warnings": folder_recommendation.warnings,
    }


def build_vm_consolidation_payload(
    request: HealthCheckRequest,
    vm_name: str,
    vm_gcs_prefix: str,
    folder_recommendations: List[FolderRecommendation],
) -> str:
    payload = {
        "gcs_bucket_path": request.gcs_bucket_path,
        "vm_name": vm_name,
        "vm_gcs_prefix": vm_gcs_prefix,
        "folder_recommendation_count": len(folder_recommendations),
        "folder_recommendations": [
            folder_recommendation_for_vm_prompt(folder_recommendation)
            for folder_recommendation in folder_recommendations
        ],
        "strict_output_instruction": (
            "Return only the old approved SAP Health Check Markdown style. "
            "Do not return JSON. Do not return code fences."
        ),
    }

    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def generate_vm_consolidation(
    request: HealthCheckRequest,
    vm_name: str,
    vm_gcs_prefix: str,
    folder_recommendations: List[FolderRecommendation],
    client: Optional[genai.Client] = None,
    runtime_config: Optional[RuntimeConfig] = None,
) -> VMRecommendation:
    vm_input_json = build_vm_consolidation_payload(
        request=request,
        vm_name=vm_name,
        vm_gcs_prefix=vm_gcs_prefix,
        folder_recommendations=folder_recommendations,
    )

    prompt = return_vm_consolidation_prompt(vm_input_json)

    markdown = generate_markdown_response(
        prompt=prompt,
        client=client,
        runtime_config=runtime_config,
    )

    warnings: List[str] = []

    for folder_recommendation in folder_recommendations:
        warnings.extend(folder_recommendation.warnings)

    return VMRecommendation(
        vm_name=vm_name,
        vm_gcs_prefix=vm_gcs_prefix,
        markdown=markdown,
        folder_recommendations=folder_recommendations,
        warnings=warnings,
    )


class RecommendationService:
    def __init__(self, runtime_config: Optional[RuntimeConfig] = None):
        self.runtime_config = runtime_config or get_default_runtime_config()
        self.client = get_genai_client(self.runtime_config)

    def generate_folder(
        self,
        request: HealthCheckRequest,
        folder_context: FolderContext,
        retrieval_context: RetrievalContext,
    ) -> FolderRecommendation:
        return generate_folder_recommendation(
            request=request,
            folder_context=folder_context,
            retrieval_context=retrieval_context,
            client=self.client,
            runtime_config=self.runtime_config,
        )

    def consolidate_vm(
        self,
        request: HealthCheckRequest,
        vm_name: str,
        vm_gcs_prefix: str,
        folder_recommendations: List[FolderRecommendation],
    ) -> VMRecommendation:
        return generate_vm_consolidation(
            request=request,
            vm_name=vm_name,
            vm_gcs_prefix=vm_gcs_prefix,
            folder_recommendations=folder_recommendations,
            client=self.client,
            runtime_config=self.runtime_config,
        )


def create_recommendation_service(
    runtime_config: Optional[RuntimeConfig] = None,
) -> RecommendationService:
    return RecommendationService(runtime_config=runtime_config)
