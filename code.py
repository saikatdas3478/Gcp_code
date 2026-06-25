GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
ROOT_AGENT_MODEL=gemini-2.0-flash-001

SAP_HC_MAX_LINES_PER_FILE=1000
SAP_HC_MAX_PARALLEL_VMS=3
SAP_HC_LLM_TEMPERATURE=0.2
SAP_HC_MAX_OUTPUT_TOKENS=8192

RAG_DEFAULT_TOP_K=8
RAG_DEFAULT_VECTOR_DISTANCE_THRESHOLD=0.5
SAP_HC_PREVIOUS_REPORTS_TOP_K=5
SAP_HC_MAX_RETRIEVAL_QUERIES_PER_SOURCE=8
SAP_HC_MAX_QUERY_CHARS=600
SAP_HC_RETRIEVAL_MAX_WORKERS=6

SAP_HC_MAX_GOOGLE_DOCS=5
SAP_HC_MAX_GOOGLE_DOC_CHARS=6000
SAP_HC_MIN_RULEBOOK_MATCH_SCORE=0.08

SAP_HC_MAX_FOLDER_VECTOR_HITS=8

PORT=8080
ENV=local

Test_orch
import pytest

import sap_hc_agent.orchestrator as orchestrator
from sap_hc_agent.schemas import (
    FolderRecommendation,
    GCSPath,
    HealthCheckRequest,
    PipelineStatus,
    SourceFile,
    VMRecommendation,
)


def make_request():
    return HealthCheckRequest(
        gcs_bucket_path="gs://test-bucket/test_data/",
        sap_rule_book_corpus_id="rule-corpus",
        sap_notes_corpus_id="notes-corpus",
        previous_reports_corpus_id="previous-corpus",
        max_parallel_vms=2,
    )


def make_vm_recommendation(vm_name="VM-01"):
    source_file = SourceFile(
        gcs_uri=f"gs://test-bucket/test_data/{vm_name}/os/system.log",
        relative_path="os/system.log",
        folder_relative_path="os",
        filename="system.log",
        lines_read=10,
        truncated=False,
        content=None,
    )

    folder_recommendation = FolderRecommendation(
        vm_name=vm_name,
        folder_name="os",
        markdown=(
            "Hello, I have completed the comprehensive analysis of the provided VM folder data.\n\n"
            "VM Name:\n"
            "Folder Name:\n\n"
            "Individual Parameter Recommendations in below table format:\n\n"
            "| Original Parameter | Recommendation | Reasoning & Justification | Citations |\n"
            "|---|---|---|---|\n"
            "| saptune = disabled | Enable saptune. | saptune is disabled. | os/system.log |"
        ),
        included_files=[source_file],
        ignored_files=[],
        retrieval_context=None,
        warnings=[],
    )

    return VMRecommendation(
        vm_name=vm_name,
        vm_gcs_prefix=f"test_data/{vm_name}/",
        markdown=(
            "Hello, I have completed the comprehensive analysis of your VM parameter data.\n\n"
            "Here are my findings, separated into individual and combined recommendations.\n\n"
            f"VM Name: {vm_name}\n\n"
            "Individual Parameter Recommendations in below table format:\n\n"
            "| Original Parameter | Recommendation | Reasoning & Justification | Citations |\n"
            "|---|---|---|---|\n"
            "| saptune = disabled | Enable saptune. | saptune is disabled. | os/system.log |"
        ),
        folder_recommendations=[folder_recommendation],
        warnings=[],
    )


def test_build_output_gcs_uri():
    output = orchestrator.build_output_gcs_uri("gs://test-bucket/test_data/")

    assert output == "gs://test-bucket/test_data/sap_hc_output/sap_health_check_recommendations.md"


def test_parse_gcs_file_uri():
    bucket_name, blob_name = orchestrator.parse_gcs_file_uri(
        "gs://test-bucket/test_data/sap_hc_output/report.md"
    )

    assert bucket_name == "test-bucket"
    assert blob_name == "test_data/sap_hc_output/report.md"


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "test-bucket/path/report.md",
        "gs://test-bucket",
        "gs://test-bucket/",
    ],
)
def test_parse_gcs_file_uri_invalid(uri):
    with pytest.raises(orchestrator.OrchestratorError):
        orchestrator.parse_gcs_file_uri(uri)


def test_build_final_markdown_report_contains_vm_output():
    request = make_request()
    vm_recommendation = make_vm_recommendation("VM-01")

    markdown = orchestrator.build_final_markdown_report(
        request=request,
        vm_recommendations=[vm_recommendation],
        started_at_utc=orchestrator.utc_now(),
        completed_at_utc=orchestrator.utc_now(),
    )

    assert "# SAP HANA Health Check Recommendation Report" in markdown
    assert "# VM: VM-01" in markdown
    assert "Individual Parameter Recommendations" in markdown
    assert "saptune = disabled" in markdown


def test_write_markdown_to_gcs(monkeypatch):
    uploaded = {}

    class FakeBlob:
        def __init__(self, name):
            self.name = name

        def upload_from_string(self, data, content_type=None):
            uploaded["data"] = data
            uploaded["content_type"] = content_type
            uploaded["name"] = self.name

    class FakeBucket:
        def blob(self, name):
            return FakeBlob(name)

    class FakeClient:
        def bucket(self, bucket_name):
            uploaded["bucket_name"] = bucket_name
            return FakeBucket()

    output = orchestrator.write_markdown_to_gcs(
        markdown="# report",
        output_gcs_uri="gs://test-bucket/test_data/sap_hc_output/report.md",
        client=FakeClient(),
    )

    assert output == "gs://test-bucket/test_data/sap_hc_output/report.md"
    assert uploaded["bucket_name"] == "test-bucket"
    assert uploaded["name"] == "test_data/sap_hc_output/report.md"
    assert uploaded["data"] == "# report"
    assert uploaded["content_type"] == "text/markdown; charset=utf-8"


def test_run_pipeline_sync_success(monkeypatch):
    request = make_request()
    events = []

    monkeypatch.setattr(
        orchestrator.storage,
        "Client",
        lambda: object(),
    )

    monkeypatch.setattr(
        orchestrator,
        "validate_gcs_root_path",
        lambda gcs_bucket_path, client=None, progress_callback=None: GCSPath(
            bucket_name="test-bucket",
            prefix="test_data/",
        ),
    )

    monkeypatch.setattr(
        orchestrator,
        "list_vm_prefixes",
        lambda gcs_bucket_path, client=None, progress_callback=None: [
            ("VM-01", "test_data/VM-01/"),
            ("VM-02", "test_data/VM-02/"),
        ],
    )

    def fake_process_vm(
        request,
        bucket_name,
        vm_name,
        vm_prefix,
        progress_callback,
    ):
        progress_callback(
            {
                "event": "vm_processing_completed",
                "data": {
                    "message": f"{vm_name} completed",
                    "vm_name": vm_name,
                },
            }
        )
        return make_vm_recommendation(vm_name)

    monkeypatch.setattr(orchestrator, "process_vm", fake_process_vm)

    uploaded = {}

    def fake_write_markdown_to_gcs(markdown, output_gcs_uri, client=None):
        uploaded["markdown"] = markdown
        uploaded["output_gcs_uri"] = output_gcs_uri
        return output_gcs_uri

    monkeypatch.setattr(orchestrator, "write_markdown_to_gcs", fake_write_markdown_to_gcs)

    result = orchestrator.run_pipeline_sync(
        request=request,
        progress_callback=lambda event: events.append(event),
    )

    assert result.summary.status == PipelineStatus.COMPLETED
    assert result.summary.total_vm_folders_found == 2
    assert result.summary.total_vm_folders_processed == 2
    assert result.report_result.output_gcs_uri == "gs://test-bucket/test_data/sap_hc_output/sap_health_check_recommendations.md"
    assert uploaded["output_gcs_uri"] == result.report_result.output_gcs_uri
    assert "# VM: VM-01" in uploaded["markdown"]
    assert "# VM: VM-02" in uploaded["markdown"]
    assert any(event["event"] == "pipeline_completed" for event in events)


@pytest.mark.asyncio
async def test_run_health_check_stream(monkeypatch):
    request = make_request()

    def fake_run_pipeline_sync(request, progress_callback):
        progress_callback(
            {
                "event": "path_validated",
                "data": {
                    "message": "validated",
                },
            }
        )
        progress_callback(
            {
                "event": "pipeline_completed",
                "data": {
                    "message": "done",
                    "output_gcs_uri": "gs://test-bucket/test_data/sap_hc_output/sap_health_check_recommendations.md",
                },
            }
        )

    monkeypatch.setattr(orchestrator, "run_pipeline_sync", fake_run_pipeline_sync)

    events = []

    async for event in orchestrator.run_health_check_stream(request):
        events.append(event)

    assert len(events) == 2
    assert events[0]["event"] == "path_validated"
    assert events[1]["event"] == "pipeline_completed"

test_gcs

import io

import pytest

from sap_hc_agent.gcs_ingestion import (
    GCSIngestionError,
    build_combined_text,
    build_chunks_for_folder,
    ingest_vm_folder,
    list_vm_prefixes,
    parse_gcs_uri,
    read_first_n_lines_from_blob,
    validate_gcs_root_path,
)
from sap_hc_agent.schemas import FolderContext, SourceFile


class FakeBucket:
    def __init__(self, name, exists=True):
        self.name = name
        self._exists = exists

    def exists(self):
        return self._exists


class FakeBlob:
    def __init__(self, bucket, name, content=""):
        self.bucket = bucket
        self.name = name
        self._content = content

    def open(self, mode="rt", encoding="utf-8", errors="replace"):
        return io.StringIO(self._content)


class FakeBlobIterator:
    def __init__(self, blobs, prefixes=None):
        self._blobs = blobs
        self.prefixes = set(prefixes or [])

    def __iter__(self):
        return iter(self._blobs)


class FakeStorageClient:
    def __init__(self, bucket_name="test-bucket", bucket_exists=True):
        self.fake_bucket = FakeBucket(bucket_name, exists=bucket_exists)
        self.calls = []

    def bucket(self, bucket_name):
        return self.fake_bucket

    def list_blobs(
        self,
        bucket_or_name,
        prefix="",
        delimiter=None,
        max_results=None,
    ):
        self.calls.append(
            {
                "prefix": prefix,
                "delimiter": delimiter,
                "max_results": max_results,
            }
        )

        bucket = self.fake_bucket

        if max_results == 1:
            return FakeBlobIterator(
                [
                    FakeBlob(
                        bucket,
                        f"{prefix}VM-01/os/system.log",
                        "hello",
                    )
                ]
            )

        if delimiter == "/":
            return FakeBlobIterator(
                [],
                prefixes=[
                    f"{prefix}VM-01/",
                    f"{prefix}VM-02/",
                ],
            )

        return FakeBlobIterator(
            [
                FakeBlob(
                    bucket,
                    f"{prefix}os/system.log",
                    "line1\nline2\nline3\n",
                ),
                FakeBlob(
                    bucket,
                    f"{prefix}os/error.err",
                    "ignored",
                ),
                FakeBlob(
                    bucket,
                    f"{prefix}profile/default.pfl",
                    "profile_line_1\nprofile_line_2\n",
                ),
                FakeBlob(
                    bucket,
                    f"{prefix}profile/trans.log",
                    "ignored",
                ),
                FakeBlob(
                    bucket,
                    f"{prefix}hana/global.ini",
                    "global_allocation_limit = 120\n",
                ),
            ]
        )


def test_parse_gcs_uri_with_prefix():
    parsed = parse_gcs_uri("gs://test-bucket/test_data")

    assert parsed.bucket_name == "test-bucket"
    assert parsed.prefix == "test_data/"
    assert parsed.uri == "gs://test-bucket/test_data/"


def test_parse_gcs_uri_without_prefix():
    parsed = parse_gcs_uri("gs://test-bucket")

    assert parsed.bucket_name == "test-bucket"
    assert parsed.prefix == ""
    assert parsed.uri == "gs://test-bucket/"


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "test-bucket/path",
        "gs://",
    ],
)
def test_parse_gcs_uri_invalid(uri):
    with pytest.raises(GCSIngestionError):
        parse_gcs_uri(uri)


def test_validate_gcs_root_path_success():
    client = FakeStorageClient()

    parsed = validate_gcs_root_path(
        "gs://test-bucket/test_data/",
        client=client,
    )

    assert parsed.bucket_name == "test-bucket"
    assert parsed.prefix == "test_data/"


def test_validate_gcs_root_path_bucket_missing():
    client = FakeStorageClient(bucket_exists=False)

    with pytest.raises(GCSIngestionError):
        validate_gcs_root_path(
            "gs://test-bucket/test_data/",
            client=client,
        )


def test_list_vm_prefixes():
    client = FakeStorageClient()

    vm_prefixes = list_vm_prefixes(
        "gs://test-bucket/test_data/",
        client=client,
    )

    assert vm_prefixes == [
        ("VM-01", "test_data/VM-01/"),
        ("VM-02", "test_data/VM-02/"),
    ]


def test_read_first_n_lines_from_blob_truncates():
    bucket = FakeBucket("test-bucket")
    blob = FakeBlob(bucket, "test/file.log", "1\n2\n3\n4\n")

    content, lines_read, truncated = read_first_n_lines_from_blob(
        blob,
        max_lines=2,
    )

    assert content == "1\n2\n"
    assert lines_read == 2
    assert truncated is True


def test_read_first_n_lines_from_blob_reads_all_if_short():
    bucket = FakeBucket("test-bucket")
    blob = FakeBlob(bucket, "test/file.log", "1\n2\n")

    content, lines_read, truncated = read_first_n_lines_from_blob(
        blob,
        max_lines=1000,
    )

    assert content == "1\n2\n"
    assert lines_read == 2
    assert truncated is False


def test_ingest_vm_folder_filters_and_groups_files():
    client = FakeStorageClient()

    vm_context = ingest_vm_folder(
        bucket_name="test-bucket",
        vm_name="VM-01",
        vm_prefix="test_data/VM-01/",
        client=client,
        max_lines_per_file=2,
    )

    assert vm_context.vm_name == "VM-01"
    assert vm_context.folder_count == 3
    assert vm_context.included_file_count == 3
    assert vm_context.ignored_file_count == 2

    folders = {folder.folder_name: folder for folder in vm_context.folders}

    assert "os" in folders
    assert "profile" in folders
    assert "hana" in folders

    assert folders["os"].included_file_count == 1
    assert folders["profile"].included_file_count == 1
    assert folders["hana"].included_file_count == 1

    assert folders["os"].included_files[0].truncated is True
    assert folders["profile"].included_files[0].filename == "default.pfl"
    assert folders["hana"].included_files[0].filename == "global.ini"


def test_build_combined_text_contains_file_markers():
    source_file = SourceFile(
        gcs_uri="gs://bucket/root/VM-01/os/system.log",
        relative_path="os/system.log",
        folder_relative_path="os",
        filename="system.log",
        lines_read=2,
        truncated=False,
        content="line1\nline2\n",
    )

    combined = build_combined_text([source_file])

    assert "===== FILE START:" in combined
    assert "system.log" in combined
    assert "line1" in combined
    assert "===== FILE END:" in combined


def test_build_chunks_for_folder():
    source_file = SourceFile(
        gcs_uri="gs://bucket/root/VM-01/os/system.log",
        relative_path="os/system.log",
        folder_relative_path="os",
        filename="system.log",
        lines_read=4,
        truncated=False,
        content="a\nb\nc\nd\n",
    )

    folder_context = FolderContext(
        vm_name="VM-01",
        folder_name="os",
        folder_gcs_prefix="root/VM-01/os/",
        included_files=[source_file],
        ignored_files=[],
        combined_text="a\nb\nc\nd\n",
    )

    chunks = build_chunks_for_folder(
        folder_context,
        max_chars=4,
        overlap_chars=0,
    )

    assert len(chunks) >= 1
    assert chunks[0].vm_name == "VM-01"
    assert chunks[0].folder_name == "os"
    assert chunks[0].source_uri == source_file.gcs_uri

test_file

import pytest

from sap_hc_agent.file_filters import (
    ends_with_number_suffix,
    explain_file_filter_decision,
    has_backup_or_lst_suffix,
    is_profile_folder,
    should_include_file,
    should_include_file_detailed,
)


@pytest.mark.parametrize(
    "filename,folder,expected",
    [
        ("system.log", "os", True),
        ("config.txt", "os", True),
        ("global.ini", "hana", True),
        ("limits.conf", "os", True),
        ("file.master", "hana", True),
        ("share.smb", "network", True),
        ("misc.misc", "misc", True),
        ("network.net", "network", True),
    ],
)
def test_general_allowed_files_are_included(filename, folder, expected):
    include, reason = should_include_file(filename, folder)

    assert include is expected
    assert reason


@pytest.mark.parametrize(
    "filename,folder",
    [
        ("error.err", "os"),
        ("list.lst", "os"),
        ("config_bak", "os"),
        ("config.bak", "os"),
        ("config_lst", "os"),
        ("config.lst", "os"),
    ],
)
def test_global_ignored_files_are_excluded(filename, folder):
    include, reason = should_include_file(filename, folder)

    assert include is False
    assert reason


@pytest.mark.parametrize(
    "folder",
    [
        "profile",
        "profile/",
        "hana/profile",
        "hana/profile/",
        "VM-01/hana/profile",
        "/hana/profile/",
    ],
)
def test_profile_folder_detection(folder):
    assert is_profile_folder(folder) is True


@pytest.mark.parametrize(
    "folder",
    [
        "profiles",
        "hana/profiles",
        "os",
        "hana",
        "",
        None,
    ],
)
def test_non_profile_folder_detection(folder):
    assert is_profile_folder(folder) is False


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("DEFAULT.PFL.1", True),
        ("abc.2", True),
        ("abc.100", True),
        ("abc.log", False),
        ("abc", False),
    ],
)
def test_numeric_suffix_detection(filename, expected):
    assert ends_with_number_suffix(filename) is expected


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("abc_bak", True),
        ("abc.bak", True),
        ("abc_lst", True),
        ("abc.lst", True),
        ("abc.log", False),
    ],
)
def test_backup_or_lst_suffix_detection(filename, expected):
    assert has_backup_or_lst_suffix(filename) is expected


def test_profile_default_pfl_is_included():
    include, reason = should_include_file("default.pfl", "profile")

    assert include is True
    assert "default.pfl" in reason.lower()


@pytest.mark.parametrize(
    "filename",
    [
        "trans.log",
        "system.log",
        "DEFAULT.PFL.1",
        "DEFAULT.PFL.2",
        "DEFAULT.PFL.100",
        "old_profile.bak",
        "old_profile_bak",
        "profile.lst",
        "profile_lst",
        "error.err",
    ],
)
def test_profile_ignored_files_are_excluded(filename):
    include, reason = should_include_file(filename, "profile")

    assert include is False
    assert reason


def test_profile_non_excluded_file_is_included():
    include, reason = should_include_file("HDB_HDB00_host", "profile")

    assert include is True
    assert reason


def test_empty_filename_is_ignored():
    decision = should_include_file_detailed("", "os")

    assert decision.include is False
    assert decision.reason


def test_explain_file_filter_decision_returns_string():
    explanation = explain_file_filter_decision("system.log", "os")

    assert "INCLUDED" in explanation
    assert "system.log" in explanation
