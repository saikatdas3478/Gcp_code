"""
schemas.py

Pydantic schemas for the SAP Health Check GCS-based processing pipeline.

These schemas are used for:
1. Progress streaming events.
2. Folder-level LLM input.
3. Folder-level LLM structured output.
4. VM-level consolidation input/output.
5. Final report generation.

This file does not contain business logic.
It only defines stable data contracts between ingestion, retrieval,
LLM recommendation generation, VM consolidation, and report writing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictBaseModel(BaseModel):
    """Base model with strict extra-field protection."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        populate_by_name=True,
    )


class ProcessingStatus(str, Enum):
    """Generic status for pipeline events."""

    STARTED = "started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProgressEventType(str, Enum):
    """Types of progress events emitted by the orchestrator."""

    GCS_VALIDATION = "gcs_validation"
    VM_DISCOVERY = "vm_discovery"
    VM_PROCESSING = "vm_processing"
    FOLDER_PROCESSING = "folder_processing"
    FILE_FILTERING = "file_filtering"
    FILE_READING = "file_reading"
    RETRIEVAL = "retrieval"
    LLM_GENERATION = "llm_generation"
    VM_CONSOLIDATION = "vm_consolidation"
    REPORT_WRITING = "report_writing"
    PIPELINE = "pipeline"


class Severity(str, Enum):
    """Severity of a recommendation."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class RecommendationCategory(str, Enum):
    """High-level SAP health check recommendation category."""

    OS = "os"
    HANA = "hana"
    PROFILE = "profile"
    NETWORK = "network"
    FILESYSTEM = "filesystem"
    SECURITY = "security"
    PERFORMANCE = "performance"
    CONFIGURATION = "configuration"
    COMPLIANCE = "compliance"
    OTHER = "other"


class ComplianceState(str, Enum):
    """Compliance state for GCP rule book checklist."""

    COMPLIANT = "compliant"
    RECOMMENDATION_ISSUED = "recommendation_issued"
    NOT_APPLICABLE = "not_applicable"
    NOT_CHECKED = "not_checked"


class CitationType(str, Enum):
    """Source type used for grounding recommendation."""

    INPUT_FILE = "input_file"
    SAP_NOTE = "sap_note"
    GCP_RULE_BOOK = "gcp_rule_book"
    PREVIOUS_ASSESSMENT_REPORT = "previous_assessment_report"
    GOOGLE_SEARCH = "google_search"
    OTHER = "other"


class ProgressEvent(StrictBaseModel):
    """
    Progress event that can be streamed to frontend, logs, or CLI.

    Example:
        {
            "event_type": "folder_processing",
            "status": "running",
            "message": "VM vm-01 / folder os: reading selected files.",
            "vm_name": "vm-01",
            "folder_name": "os"
        }
    """

    event_type: ProgressEventType
    status: ProcessingStatus
    message: str = Field(..., min_length=1)

    vm_name: Optional[str] = None
    folder_name: Optional[str] = None

    current: Optional[int] = None
    total: Optional[int] = None

    details: Dict[str, Any] = Field(default_factory=dict)
    timestamp_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class SourceFileMetadata(StrictBaseModel):
    """Metadata for one ingested source file."""

    gcs_uri: str = Field(..., min_length=1)
    relative_path: str = Field(..., min_length=1)
    folder_relative_path: str = Field(..., min_length=1)
    filename: str = Field(..., min_length=1)

    lines_read: int = Field(..., ge=0)
    truncated: bool = False

    @field_validator("gcs_uri")
    @classmethod
    def validate_gcs_uri(cls, value: str) -> str:
        if not value.startswith("gs://"):
            raise ValueError("gcs_uri must start with gs://")
        return value


class IgnoredFileMetadata(StrictBaseModel):
    """Metadata for one ignored file."""

    gcs_uri: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)

    @field_validator("gcs_uri")
    @classmethod
    def validate_gcs_uri(cls, value: str) -> str:
        if not value.startswith("gs://"):
            raise ValueError("gcs_uri must start with gs://")
        return value


class FolderIngestionSummary(StrictBaseModel):
    """Ingestion summary for one actual folder inside one VM folder."""

    vm_name: str = Field(..., min_length=1)
    folder_relative_path: str = Field(..., min_length=1)
    folder_gcs_prefix: str = Field(..., min_length=1)

    included_files: List[SourceFileMetadata] = Field(default_factory=list)
    ignored_files: List[IgnoredFileMetadata] = Field(default_factory=list)

    combined_text_char_count: int = Field(default=0, ge=0)

    @property
    def included_file_count(self) -> int:
        return len(self.included_files)

    @property
    def ignored_file_count(self) -> int:
        return len(self.ignored_files)


class VMIngestionSummary(StrictBaseModel):
    """Ingestion summary for one VM folder."""

    vm_name: str = Field(..., min_length=1)
    vm_gcs_prefix: str = Field(..., min_length=1)

    folders: List[FolderIngestionSummary] = Field(default_factory=list)
    ignored_files: List[IgnoredFileMetadata] = Field(default_factory=list)

    @property
    def folder_count(self) -> int:
        return len(self.folders)

    @property
    def included_file_count(self) -> int:
        return sum(folder.included_file_count for folder in self.folders)

    @property
    def ignored_file_count(self) -> int:
        return len(self.ignored_files) + sum(
            folder.ignored_file_count for folder in self.folders
        )


class RetrievalHit(StrictBaseModel):
    """One retrieved chunk from a RAG corpus or external source."""

    citation_type: CitationType
    corpus_id: Optional[str] = None
    corpus_name: Optional[str] = None

    query: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)

    source_uri: Optional[str] = None
    source_title: Optional[str] = None
    relevance_score: Optional[float] = None

    metadata: Dict[str, Any] = Field(default_factory=dict)


class RetrievalContext(StrictBaseModel):
    """
    Retrieval context passed to the LLM.

    This keeps SAP Notes, GCP rule book, previous reports, and other sources separated.
    """

    sap_notes_hits: List[RetrievalHit] = Field(default_factory=list)
    gcp_rule_book_hits: List[RetrievalHit] = Field(default_factory=list)
    previous_report_hits: List[RetrievalHit] = Field(default_factory=list)
    google_search_hits: List[RetrievalHit] = Field(default_factory=list)
    other_hits: List[RetrievalHit] = Field(default_factory=list)

    search_queries_used: List[str] = Field(default_factory=list)


class EvidenceLocation(StrictBaseModel):
    """Where an observed fact or recommendation came from."""

    source_type: CitationType
    source_uri: Optional[str] = None
    source_title: Optional[str] = None

    file_relative_path: Optional[str] = None
    line_range: Optional[str] = None

    corpus_id: Optional[str] = None
    note_number: Optional[str] = None
    rule_id: Optional[str] = None

    quote_or_summary: Optional[str] = None


class ObservedFact(StrictBaseModel):
    """
    A concrete fact found in the input logs/config files.

    This is important because the final recommendation must not only say
    what to do; it must also show the actual number/value/fact observed.
    """

    fact_name: str = Field(..., min_length=1)
    observed_value: Optional[str] = None
    expected_value: Optional[str] = None
    unit: Optional[str] = None

    description: str = Field(..., min_length=1)

    evidence: List[EvidenceLocation] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class RecommendationItem(StrictBaseModel):
    """One folder-level or VM-level recommendation."""

    recommendation_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)

    category: RecommendationCategory = RecommendationCategory.OTHER
    severity: Severity = Severity.MEDIUM

    observed_facts: List[ObservedFact] = Field(default_factory=list)

    recommendation: str = Field(..., min_length=1)
    reason: str = Field(
        ...,
        min_length=1,
        description="Small 2-4 line reason behind the recommendation.",
    )

    business_or_technical_impact: Optional[str] = None
    remediation_steps: List[str] = Field(default_factory=list)

    citations: List[EvidenceLocation] = Field(default_factory=list)

    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class ComplianceChecklistItem(StrictBaseModel):
    """One rule from the GCP/SAP rule book checklist."""

    rule_id: Optional[str] = None
    rule_name: str = Field(..., min_length=1)
    rule_description: Optional[str] = None

    parameter_or_check: Optional[str] = None
    expected_value: Optional[str] = None
    observed_value: Optional[str] = None

    compliance_state: ComplianceState
    reason: str = Field(..., min_length=1)

    related_recommendation_ids: List[str] = Field(default_factory=list)
    citations: List[EvidenceLocation] = Field(default_factory=list)


class FolderAnalysisInput(StrictBaseModel):
    """
    Input passed to one folder-level LLM call.

    One LLM call should happen per actual folder inside a VM folder.
    """

    root_gcs_uri: str = Field(..., min_length=1)

    vm_name: str = Field(..., min_length=1)
    folder_relative_path: str = Field(..., min_length=1)
    folder_gcs_prefix: str = Field(..., min_length=1)

    source_files: List[SourceFileMetadata] = Field(default_factory=list)
    combined_folder_text: str = Field(..., min_length=1)

    retrieval_context: RetrievalContext = Field(default_factory=RetrievalContext)

    max_lines_per_file: int = Field(default=1000, ge=1)
    processing_notes: List[str] = Field(default_factory=list)

    @field_validator("root_gcs_uri")
    @classmethod
    def validate_root_gcs_uri(cls, value: str) -> str:
        if not value.startswith("gs://"):
            raise ValueError("root_gcs_uri must start with gs://")
        return value


class FolderRecommendationOutput(StrictBaseModel):
    """
    Structured output expected from one folder-level LLM call.

    This output is later used for VM-level consolidation.
    """

    vm_name: str = Field(..., min_length=1)
    folder_relative_path: str = Field(..., min_length=1)

    short_summary: str = Field(..., min_length=1)

    observed_facts: List[ObservedFact] = Field(default_factory=list)
    recommendations: List[RecommendationItem] = Field(default_factory=list)
    compliance_checklist: List[ComplianceChecklistItem] = Field(default_factory=list)

    files_analyzed: List[SourceFileMetadata] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    retrieval_context_used: RetrievalContext = Field(default_factory=RetrievalContext)


class VMConsolidationInput(StrictBaseModel):
    """
    Input passed to one VM-level synthesis call.

    This combines all folder-level outputs for the same VM.
    """

    root_gcs_uri: str = Field(..., min_length=1)
    vm_name: str = Field(..., min_length=1)
    vm_gcs_prefix: str = Field(..., min_length=1)

    folder_outputs: List[FolderRecommendationOutput] = Field(default_factory=list)

    processing_notes: List[str] = Field(default_factory=list)

    @field_validator("root_gcs_uri")
    @classmethod
    def validate_root_gcs_uri(cls, value: str) -> str:
        if not value.startswith("gs://"):
            raise ValueError("root_gcs_uri must start with gs://")
        return value


class VMRecommendationOutput(StrictBaseModel):
    """Final consolidated recommendation output for one VM."""

    vm_name: str = Field(..., min_length=1)
    vm_gcs_prefix: str = Field(..., min_length=1)

    executive_summary: str = Field(..., min_length=1)

    folder_outputs: List[FolderRecommendationOutput] = Field(default_factory=list)
    consolidated_recommendations: List[RecommendationItem] = Field(default_factory=list)
    consolidated_compliance_checklist: List[ComplianceChecklistItem] = Field(
        default_factory=list
    )

    duplicate_or_merged_findings: List[str] = Field(default_factory=list)
    unresolved_items: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class PipelineProcessingSummary(StrictBaseModel):
    """Overall processing summary for the full run."""

    root_gcs_uri: str = Field(..., min_length=1)

    total_vm_folders_found: int = Field(default=0, ge=0)
    total_vm_folders_processed: int = Field(default=0, ge=0)

    total_actual_folders_processed: int = Field(default=0, ge=0)
    total_files_included: int = Field(default=0, ge=0)
    total_files_ignored: int = Field(default=0, ge=0)
    total_files_truncated: int = Field(default=0, ge=0)

    max_lines_per_file: int = Field(default=1000, ge=1)

    started_at_utc: Optional[datetime] = None
    completed_at_utc: Optional[datetime] = None

    status: ProcessingStatus = ProcessingStatus.COMPLETED
    errors: List[str] = Field(default_factory=list)

    @field_validator("root_gcs_uri")
    @classmethod
    def validate_root_gcs_uri(cls, value: str) -> str:
        if not value.startswith("gs://"):
            raise ValueError("root_gcs_uri must start with gs://")
        return value


class FinalHealthCheckReport(StrictBaseModel):
    """
    Final report object used by report_writer.py.

    This can be rendered to Markdown/TXT and saved to GCS.
    """

    report_title: str = "SAP HANA Health Check Recommendation Report"

    root_gcs_uri: str = Field(..., min_length=1)
    output_gcs_uri: Optional[str] = None

    generated_at_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    processing_summary: PipelineProcessingSummary
    vm_reports: List[VMRecommendationOutput] = Field(default_factory=list)

    global_summary: Optional[str] = None
    global_warnings: List[str] = Field(default_factory=list)

    @field_validator("root_gcs_uri")
    @classmethod
    def validate_root_gcs_uri(cls, value: str) -> str:
        if not value.startswith("gs://"):
            raise ValueError("root_gcs_uri must start with gs://")
        return value

    @field_validator("output_gcs_uri")
    @classmethod
    def validate_output_gcs_uri(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.startswith("gs://"):
            raise ValueError("output_gcs_uri must start with gs://")
        return value


class FolderLLMResponseFormat(StrictBaseModel):
    """
    Minimal schema to pass to the LLM as the required output contract
    for folder-level recommendation generation.

    You can use this model's JSON schema in the prompt if needed.
    """

    short_summary: str
    observed_facts: List[ObservedFact]
    recommendations: List[RecommendationItem]
    compliance_checklist: List[ComplianceChecklistItem]
    warnings: List[str] = Field(default_factory=list)


class VMConsolidationLLMResponseFormat(StrictBaseModel):
    """
    Minimal schema to pass to the LLM as the required output contract
    for VM-level consolidation.
    """

    executive_summary: str
    consolidated_recommendations: List[RecommendationItem]
    consolidated_compliance_checklist: List[ComplianceChecklistItem]
    duplicate_or_merged_findings: List[str] = Field(default_factory=list)
    unresolved_items: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
