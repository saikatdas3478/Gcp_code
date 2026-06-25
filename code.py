from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        populate_by_name=True,
    )


class PipelineStatus(str, Enum):
    STARTED = "started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StreamEventName(str, Enum):
    REQUEST_RECEIVED = "request_received"
    PATH_VALIDATED = "path_validated"
    VM_DISCOVERY_STARTED = "vm_discovery_started"
    VM_DISCOVERY_COMPLETED = "vm_discovery_completed"
    VM_PROCESSING_STARTED = "vm_processing_started"
    VM_PROCESSING_COMPLETED = "vm_processing_completed"
    FILE_SCAN_STARTED = "file_scan_started"
    FILE_SCAN_COMPLETED = "file_scan_completed"
    FOLDER_CONTEXT_STARTED = "folder_context_started"
    FOLDER_CONTEXT_BUILT = "folder_context_built"
    RULEBOOK_LINK_MATCH_STARTED = "rulebook_link_match_started"
    RULEBOOK_LINK_MATCH_COMPLETED = "rulebook_link_match_completed"
    GOOGLE_DOC_FETCH_STARTED = "google_doc_fetch_started"
    GOOGLE_DOC_FETCH_COMPLETED = "google_doc_fetch_completed"
    RAG_QUERY_STARTED = "rag_query_started"
    RAG_QUERY_COMPLETED = "rag_query_completed"
    FOLDER_LLM_STARTED = "folder_llm_started"
    FOLDER_LLM_COMPLETED = "folder_llm_completed"
    VM_CONSOLIDATION_STARTED = "vm_consolidation_started"
    VM_CONSOLIDATION_COMPLETED = "vm_consolidation_completed"
    REPORT_WRITE_STARTED = "report_write_started"
    REPORT_WRITE_COMPLETED = "report_write_completed"
    PIPELINE_COMPLETED = "pipeline_completed"
    PIPELINE_FAILED = "pipeline_failed"


class SourceType(str, Enum):
    INPUT_FILE = "input_file"
    SAP_RULE_BOOK_RAG = "sap_rule_book_rag"
    SAP_NOTES_RAG = "sap_notes_rag"
    PREVIOUS_REPORTS_RAG = "previous_reports_rag"
    GOOGLE_DOC = "google_doc"
    RULEBOOK_LINK = "rulebook_link"
    FOLDER_VECTOR = "folder_vector"
    OTHER = "other"


class HealthCheckRequest(StrictBaseModel):
    gcs_bucket_path: str = Field(..., min_length=1)
    sap_rule_book_corpus_id: str = Field(..., min_length=1)
    sap_notes_corpus_id: str = Field(..., min_length=1)
    previous_reports_corpus_id: str = Field(..., min_length=1)
    max_parallel_vms: Optional[int] = Field(default=3, ge=1, le=10)

    @field_validator("gcs_bucket_path")
    @classmethod
    def validate_gcs_bucket_path(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith("gs://"):
            raise ValueError("gcs_bucket_path must start with gs://")
        if cleaned == "gs://":
            raise ValueError("gcs_bucket_path must include bucket name and root prefix")
        return cleaned.rstrip("/") + "/"

    @field_validator(
        "sap_rule_book_corpus_id",
        "sap_notes_corpus_id",
        "previous_reports_corpus_id",
    )
    @classmethod
    def validate_corpus_id(cls, value: str) -> str:
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("corpus id cannot be empty")
        return cleaned

    @field_validator("max_parallel_vms")
    @classmethod
    def validate_max_parallel_vms(cls, value: Optional[int]) -> int:
        if value is None:
            return 3
        return value


class StreamEvent(StrictBaseModel):
    event: StreamEventName
    status: PipelineStatus = PipelineStatus.RUNNING
    message: str = Field(..., min_length=1)
    vm_name: Optional[str] = None
    folder_name: Optional[str] = None
    current: Optional[int] = None
    total: Optional[int] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_sse_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event.value,
            "data": self.model_dump(mode="json"),
        }


class GCSPath(StrictBaseModel):
    bucket_name: str = Field(..., min_length=1)
    prefix: str = ""

    @property
    def uri(self) -> str:
        if self.prefix:
            return f"gs://{self.bucket_name}/{self.prefix.strip('/')}/"
        return f"gs://{self.bucket_name}/"


class IgnoredFile(StrictBaseModel):
    gcs_uri: str = Field(..., min_length=1)
    relative_path: Optional[str] = None
    reason: str = Field(..., min_length=1)


class SourceFile(StrictBaseModel):
    gcs_uri: str = Field(..., min_length=1)
    relative_path: str = Field(..., min_length=1)
    folder_relative_path: str = Field(..., min_length=1)
    filename: str = Field(..., min_length=1)
    lines_read: int = Field(..., ge=0)
    truncated: bool = False
    content: Optional[str] = None

    @field_validator("gcs_uri")
    @classmethod
    def validate_gcs_uri(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith("gs://"):
            raise ValueError("gcs_uri must start with gs://")
        return cleaned


class TextChunk(StrictBaseModel):
    chunk_id: str = Field(..., min_length=1)
    vm_name: str = Field(..., min_length=1)
    folder_name: str = Field(..., min_length=1)
    source_uri: Optional[str] = None
    relative_path: Optional[str] = None
    line_range: Optional[str] = None
    text: str = Field(..., min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FolderContext(StrictBaseModel):
    vm_name: str = Field(..., min_length=1)
    folder_name: str = Field(..., min_length=1)
    folder_gcs_prefix: str = Field(..., min_length=1)
    included_files: List[SourceFile] = Field(default_factory=list)
    ignored_files: List[IgnoredFile] = Field(default_factory=list)
    chunks: List[TextChunk] = Field(default_factory=list)
    combined_text: str = ""
    truncated_file_count: int = 0

    @property
    def included_file_count(self) -> int:
        return len(self.included_files)

    @property
    def ignored_file_count(self) -> int:
        return len(self.ignored_files)


class VMContext(StrictBaseModel):
    vm_name: str = Field(..., min_length=1)
    vm_gcs_prefix: str = Field(..., min_length=1)
    folders: List[FolderContext] = Field(default_factory=list)
    ignored_files: List[IgnoredFile] = Field(default_factory=list)

    @property
    def folder_count(self) -> int:
        return len(self.folders)

    @property
    def included_file_count(self) -> int:
        return sum(folder.included_file_count for folder in self.folders)

    @property
    def ignored_file_count(self) -> int:
        return len(self.ignored_files) + sum(folder.ignored_file_count for folder in self.folders)

    @property
    def truncated_file_count(self) -> int:
        return sum(folder.truncated_file_count for folder in self.folders)


class RulebookLinkMatch(StrictBaseModel):
    section_key: str = Field(..., min_length=1)
    matched_score: float = Field(default=0.0, ge=0.0)
    matched_terms: List[str] = Field(default_factory=list)
    urls: List[str] = Field(default_factory=list)


class GoogleDocContext(StrictBaseModel):
    section_key: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)
    title: Optional[str] = None
    text: Optional[str] = None
    status: PipelineStatus = PipelineStatus.COMPLETED
    error_message: Optional[str] = None


class RetrievalHit(StrictBaseModel):
    source_type: SourceType
    corpus_id: Optional[str] = None
    query: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    source_uri: Optional[str] = None
    source_title: Optional[str] = None
    relevance_score: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RetrievalContext(StrictBaseModel):
    rulebook_link_matches: List[RulebookLinkMatch] = Field(default_factory=list)
    google_doc_contexts: List[GoogleDocContext] = Field(default_factory=list)
    sap_rule_book_hits: List[RetrievalHit] = Field(default_factory=list)
    sap_notes_hits: List[RetrievalHit] = Field(default_factory=list)
    previous_report_style_hits: List[RetrievalHit] = Field(default_factory=list)
    folder_vector_hits: List[RetrievalHit] = Field(default_factory=list)
    queries_used: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class FolderRecommendation(StrictBaseModel):
    vm_name: str = Field(..., min_length=1)
    folder_name: str = Field(..., min_length=1)
    markdown: str = Field(..., min_length=1)
    included_files: List[SourceFile] = Field(default_factory=list)
    ignored_files: List[IgnoredFile] = Field(default_factory=list)
    retrieval_context: Optional[RetrievalContext] = None
    warnings: List[str] = Field(default_factory=list)


class VMRecommendation(StrictBaseModel):
    vm_name: str = Field(..., min_length=1)
    vm_gcs_prefix: str = Field(..., min_length=1)
    markdown: str = Field(..., min_length=1)
    folder_recommendations: List[FolderRecommendation] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ReportResult(StrictBaseModel):
    output_gcs_uri: str = Field(..., min_length=1)
    markdown: Optional[str] = None

    @field_validator("output_gcs_uri")
    @classmethod
    def validate_output_gcs_uri(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith("gs://"):
            raise ValueError("output_gcs_uri must start with gs://")
        if cleaned.endswith("/"):
            raise ValueError("output_gcs_uri must point to a file")
        return cleaned


class PipelineSummary(StrictBaseModel):
    gcs_bucket_path: str = Field(..., min_length=1)
    output_gcs_uri: Optional[str] = None
    status: PipelineStatus = PipelineStatus.COMPLETED
    total_vm_folders_found: int = Field(default=0, ge=0)
    total_vm_folders_processed: int = Field(default=0, ge=0)
    total_folders_processed: int = Field(default=0, ge=0)
    total_files_included: int = Field(default=0, ge=0)
    total_files_ignored: int = Field(default=0, ge=0)
    total_files_truncated: int = Field(default=0, ge=0)
    started_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at_utc: Optional[datetime] = None
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class PipelineResult(StrictBaseModel):
    summary: PipelineSummary
    vm_recommendations: List[VMRecommendation] = Field(default_factory=list)
    report_result: Optional[ReportResult] = None


class FolderProcessingInput(StrictBaseModel):
    request: HealthCheckRequest
    folder_context: FolderContext
    retrieval_context: RetrievalContext


class VMProcessingInput(StrictBaseModel):
    request: HealthCheckRequest
    vm_context: VMContext


class FolderProcessingOutput(StrictBaseModel):
    folder_context: FolderContext
    retrieval_context: RetrievalContext
    recommendation: FolderRecommendation


class VMProcessingOutput(StrictBaseModel):
    vm_context: VMContext
    recommendation: VMRecommendation


class LLMGenerationConfig(StrictBaseModel):
    model_name: str
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_output_tokens: Optional[int] = Field(default=None, ge=1)


class RuntimeConfig(StrictBaseModel):
    project_id: str = Field(..., min_length=1)
    location: str = Field(default="us-central1", min_length=1)
    max_lines_per_file: int = Field(default=1000, ge=1)
    max_parallel_vms: int = Field(default=3, ge=1, le=10)
    llm: LLMGenerationConfig

    @model_validator(mode="after")
    def validate_runtime(self) -> "RuntimeConfig":
        if self.max_parallel_vms < 1:
            self.max_parallel_vms = 3
        return self
