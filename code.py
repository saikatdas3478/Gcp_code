python - <<'PY'
import os
from dotenv import load_dotenv

load_dotenv()

keys = [
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "ROOT_AGENT_MODEL",
    "CORPUS_ID_SAP_RULE_BOOK",
    "CORPUS_ID_SAP_PREVIOUS_REPS",
    "CORPUS_ID_SAP_NOTES_CHECK",
    "SAP_HC_ENABLE_GOOGLE_FIRST",
    "SAP_HC_ENABLE_SECTION_LINK_LOOKUP",
    "SAP_HC_ENABLE_SAP_NOTE_REDIRECT",
    "SAP_HC_ENABLE_PREVIOUS_REPORT_CROSS_CHECK",
]

for key in keys:
    print(key, "=", os.environ.get(key))
PY

uv run python -c "from sap_hc_agent.gcs_ingestion import ingest_gcs_root; r=ingest_gcs_root('gs://sap-hc-agent/test_data/', progress_callback=print); print('VM count:', len(r)); print('VM names:', [x.vm_name for x in r])"

uv run python -c "from sap_hc_agent.gcs_ingestion import ingest_gcs_root; r=ingest_gcs_root('gs://sap-hc-agent/test_data/', progress_callback=print); print('VM count:', len(r)); print('VM names:', [x.vm_name for x in r])"

uv run python - <<'PY'
from sap_hc_agent.gcs_ingestion import ingest_gcs_root, flatten_folder_bundles
from sap_hc_agent.retrieval_service import (
    detect_rulebook_sections,
    get_section_reference_urls,
    get_section_sap_notes,
)

results = ingest_gcs_root("gs://sap-hc-agent/test_data/", progress_callback=print)
bundles = flatten_folder_bundles(results)

print("\nTotal folder bundles:", len(bundles))

for bundle in bundles:
    sections = detect_rulebook_sections(bundle)
    urls = get_section_reference_urls(sections)
    notes = get_section_sap_notes(sections)

    print("\nVM:", bundle.vm_name)
    print("Folder:", bundle.folder_relative_path)
    print("Detected sections:", sections)
    print("Section URLs:", urls)
    print("Section SAP Notes:", notes)
PY

uv run python - <<'PY'
from sap_hc_agent.gcs_ingestion import ingest_gcs_root, flatten_folder_bundles
from sap_hc_agent.retrieval_service import (
    create_retrieval_callable,
    detect_rulebook_sections,
    get_section_reference_urls,
)

results = ingest_gcs_root("gs://sap-hc-agent/test_data/", progress_callback=print)
bundles = flatten_folder_bundles(results)

for bundle in bundles:
    sections = detect_rulebook_sections(bundle)
    urls = get_section_reference_urls(sections)

    if urls:
        print("\nTesting bundle:")
        print("VM:", bundle.vm_name)
        print("Folder:", bundle.folder_relative_path)
        print("Sections:", sections)
        print("URLs:", urls)

        ctx = create_retrieval_callable()(bundle)

        print("Google hits:", len(ctx.google_search_hits))
        print("SAP Notes hits:", len(ctx.sap_notes_hits))
        print("Rule Book hits:", len(ctx.gcp_rule_book_hits))
        print("Previous Report hits:", len(ctx.previous_report_hits))

        if ctx.google_search_hits:
            print("\nFirst Google hit:")
            print("Source:", ctx.google_search_hits[0].source_uri)
            print("Text:", ctx.google_search_hits[0].text[:1000])
        else:
            print("No Google enrichment found.")

        break
PY

uv run python - <<'PY'
from sap_hc_agent.gcs_ingestion import ingest_gcs_root, flatten_folder_bundles
from sap_hc_agent.retrieval_service import create_retrieval_callable, detect_rulebook_sections

results = ingest_gcs_root("gs://sap-hc-agent/test_data/", progress_callback=print)
bundles = flatten_folder_bundles(results)

for bundle in bundles:
    sections = detect_rulebook_sections(bundle)

    if "database_version_sp_level" in sections or "machine_types_certification" in sections:
        print("\nTesting SAP Note redirect")
        print("VM:", bundle.vm_name)
        print("Folder:", bundle.folder_relative_path)
        print("Sections:", sections)

        ctx = create_retrieval_callable()(bundle)

        sap_note_queries = [
            q for q in ctx.search_queries_used
            if "SAP Note" in q
        ]

        print("SAP Note queries:")
        for q in sap_note_queries:
            print("-", q)

        print("SAP Notes hits:", len(ctx.sap_notes_hits))

        if ctx.sap_notes_hits:
            print("First SAP Note hit:")
            print(ctx.sap_notes_hits[0].text[:800])

        break
PY

uv run python - <<'PY'
from sap_hc_agent.gcs_ingestion import ingest_gcs_root, flatten_folder_bundles
from sap_hc_agent.retrieval_service import create_retrieval_callable

results = ingest_gcs_root("gs://sap-hc-agent/test_data/", progress_callback=print)
bundle = flatten_folder_bundles(results)[0]

ctx = create_retrieval_callable()(bundle)

print("Previous report hits:", len(ctx.previous_report_hits))

for hit in ctx.previous_report_hits[:3]:
    print("\nSource:", hit.source_uri)
    print("Query:", hit.query)
    print("Text:", hit.text[:500])
PY
