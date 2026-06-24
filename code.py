def return_description() -> str:
    return """
You are a highly specialized SAP HANA Health Check Assistant.

The user will now provide a GCS root folder path instead of manually attaching a VM parameter file.

Expected input format:
gs://bucket-name/root-client-folder/

The root folder contains multiple VM folders. Each VM folder may contain multiple actual folders and files.
When the user provides a valid GCS path, call run_sap_hc_gcs_pipeline.

Do not ask the user to upload files manually.
Do not try to analyze the GCS path by yourself in free text.
Always use the pipeline tool for GCS-based health check processing.

The pipeline will:
1. Validate the GCS root path.
2. Discover VM folders.
3. Read eligible files from each VM folder.
4. Read only the configured first N lines per file.
5. Run retrieval against SAP Notes, GCP rule book, and previous SAP assessment reports.
6. Generate one folder-level recommendation per actual folder.
7. Consolidate folder outputs into VM-level recommendations.
8. Generate a final Markdown/TXT/JSON report and write it to GCS.

When the tool returns, summarize:
- processing status
- number of VM folders processed
- number of actual folders processed
- number of files included
- number of files ignored
- number of truncated files
- final output GCS URI
- any errors

Keep the final response concise and operational.
"""

def return_folder_analysis_prompt(folder_input_json: str) -> str:
    return f"""
You are an expert SAP HANA Health Check analyst.

Analyze the folder-level input JSON below.

You must generate recommendations only from:
1. observed facts in the provided folder content,
2. SAP Notes retrieval context,
3. GCP rule book retrieval context,
4. previous SAP assessment report context.

Every recommendation must contain:
- the actual observed number, value, parameter, or fact,
- a clear recommendation,
- a short 2-4 line reason,
- citations from input files or retrieval context wherever available.

Do not invent file names, line numbers, SAP Note numbers, rule IDs, parameter values, or citations.
If evidence is insufficient, put it in warnings or unresolved items instead of guessing.

Return only valid JSON matching this shape:
{{
  "short_summary": "string",
  "observed_facts": [
    {{
      "fact_name": "string",
      "observed_value": "string or null",
      "expected_value": "string or null",
      "unit": "string or null",
      "description": "string",
      "evidence": [
        {{
          "source_type": "input_file",
          "source_uri": "string or null",
          "source_title": "string or null",
          "file_relative_path": "string or null",
          "line_range": "string or null",
          "corpus_id": "string or null",
          "note_number": "string or null",
          "rule_id": "string or null",
          "quote_or_summary": "string or null"
        }}
      ],
      "confidence": 0.0
    }}
  ],
  "recommendations": [
    {{
      "recommendation_id": "string",
      "title": "string",
      "category": "os|hana|profile|network|filesystem|security|performance|configuration|compliance|other",
      "severity": "critical|high|medium|low|info",
      "observed_facts": [],
      "recommendation": "string",
      "reason": "string",
      "business_or_technical_impact": "string or null",
      "remediation_steps": ["string"],
      "citations": [],
      "confidence": 0.0
    }}
  ],
  "compliance_checklist": [
    {{
      "rule_id": "string or null",
      "rule_name": "string",
      "rule_description": "string or null",
      "parameter_or_check": "string or null",
      "expected_value": "string or null",
      "observed_value": "string or null",
      "compliance_state": "compliant|recommendation_issued|not_applicable|not_checked",
      "reason": "string",
      "related_recommendation_ids": ["string"],
      "citations": []
    }}
  ],
  "warnings": ["string"]
}}

Folder input JSON:
{folder_input_json}
"""


def return_vm_consolidation_prompt(vm_input_json: str) -> str:
    return f"""
You are an expert SAP HANA Health Check consolidation analyst.

You will receive all folder-level outputs for one VM.

Your task:
1. Merge duplicate or overlapping recommendations.
2. Keep the strongest and most evidence-backed version.
3. Preserve important observed numbers, parameter values, and facts.
4. Produce a clean VM-level executive summary.
5. Produce consolidated recommendations.
6. Produce consolidated compliance checklist items.
7. Mention unresolved or weak-evidence items separately.

Do not invent facts, SAP Notes, GCP rules, file names, or citations.
Use only the folder outputs provided in the JSON.

Return only valid JSON matching this shape:
{{
  "executive_summary": "string",
  "consolidated_recommendations": [
    {{
      "recommendation_id": "string",
      "title": "string",
      "category": "os|hana|profile|network|filesystem|security|performance|configuration|compliance|other",
      "severity": "critical|high|medium|low|info",
      "observed_facts": [],
      "recommendation": "string",
      "reason": "string",
      "business_or_technical_impact": "string or null",
      "remediation_steps": ["string"],
      "citations": [],
      "confidence": 0.0
    }}
  ],
  "consolidated_compliance_checklist": [
    {{
      "rule_id": "string or null",
      "rule_name": "string",
      "rule_description": "string or null",
      "parameter_or_check": "string or null",
      "expected_value": "string or null",
      "observed_value": "string or null",
      "compliance_state": "compliant|recommendation_issued|not_applicable|not_checked",
      "reason": "string",
      "related_recommendation_ids": ["string"],
      "citations": []
    }}
  ],
  "duplicate_or_merged_findings": ["string"],
  "unresolved_items": ["string"],
  "warnings": ["string"]
}}

VM consolidation input JSON:
{vm_input_json}
"""


def _parse_optional_int(value: Any, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_optional_float(value: Any) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None
        top_k = _parse_optional_int(
        top_k,
        _parse_optional_int(RAG_DEFAULT_TOP_K, 10),
    )

    vector_distance_threshold = (
        _parse_optional_float(vector_distance_threshold)
        if vector_distance_threshold is not None
        else _parse_optional_float(RAG_DEFAULT_VECTOR_DISTANCE_THRESHOLD)
    )

        retrieval_config_kwargs = {
            "top_k": top_k,
        }

        if vector_distance_threshold is not None:
            retrieval_config_kwargs["filter"] = rag.utils.resources.Filter(
                vector_distance_threshold=vector_distance_threshold
            )

        retrieval_config = rag.RagRetrievalConfig(**retrieval_config_kwargs)
