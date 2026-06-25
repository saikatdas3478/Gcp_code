from __future__ import annotations

from pathlib import PurePosixPath
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from google.cloud import storage

from .file_filters import should_include_file
from .schemas import FolderContext, GCSPath, IgnoredFile, SourceFile, TextChunk, VMContext


ProgressCallback = Optional[Callable[[str], None]]

DEFAULT_MAX_LINES_PER_FILE = 1000
DEFAULT_CHUNK_MAX_CHARS = 4000
DEFAULT_CHUNK_OVERLAP_CHARS = 400


class GCSIngestionError(Exception):
    pass


def emit_progress(callback: ProgressCallback, message: str) -> None:
    if callback:
        callback(message)


def parse_gcs_uri(gcs_uri: str) -> GCSPath:
    if not gcs_uri or not gcs_uri.strip().startswith("gs://"):
        raise GCSIngestionError("GCS path must start with gs://")

    raw_path = gcs_uri.strip().replace("gs://", "", 1)

    if not raw_path:
        raise GCSIngestionError("GCS path must include a bucket name")

    parts = raw_path.split("/", 1)
    bucket_name = parts[0].strip()

    if not bucket_name:
        raise GCSIngestionError("GCS bucket name is empty")

    prefix = ""

    if len(parts) > 1:
        prefix = parts[1].strip("/")

    if prefix:
        prefix = f"{prefix}/"

    return GCSPath(bucket_name=bucket_name, prefix=prefix)


def get_storage_client(project_id: Optional[str] = None) -> storage.Client:
    return storage.Client(project=project_id)


def blob_to_gcs_uri(bucket_name: str, blob_name: str) -> str:
    return f"gs://{bucket_name}/{blob_name}"


def normalize_blob_name(blob_name: str) -> str:
    return str(PurePosixPath(blob_name))


def is_directory_marker(blob_name: str) -> bool:
    return blob_name.endswith("/")


def get_relative_path(blob_name: str, base_prefix: str) -> str:
    if blob_name.startswith(base_prefix):
        return blob_name[len(base_prefix) :].lstrip("/")
    return blob_name


def get_folder_relative_path(relative_file_path: str) -> str:
    parent = str(PurePosixPath(relative_file_path).parent)
    return "." if parent == "." else parent


def build_folder_gcs_prefix(vm_prefix: str, folder_name: str) -> str:
    if folder_name == ".":
        return vm_prefix.rstrip("/") + "/"

    return str(PurePosixPath(vm_prefix) / folder_name).rstrip("/") + "/"


def validate_gcs_root_path(
    gcs_bucket_path: str,
    client: Optional[storage.Client] = None,
    progress_callback: ProgressCallback = None,
) -> GCSPath:
    gcs_path = parse_gcs_uri(gcs_bucket_path)
    client = client or get_storage_client()

    emit_progress(progress_callback, f"Validating GCS path {gcs_path.uri}")

    bucket = client.bucket(gcs_path.bucket_name)

    if not bucket.exists():
        raise GCSIngestionError(f"GCS bucket does not exist: {gcs_path.bucket_name}")

    blobs = list(
        client.list_blobs(
            bucket_or_name=bucket,
            prefix=gcs_path.prefix,
            max_results=1,
        )
    )

    if not blobs:
        raise GCSIngestionError(f"No files or folders found under {gcs_path.uri}")

    emit_progress(progress_callback, f"Validated GCS path {gcs_path.uri}")

    return gcs_path


def list_vm_prefixes(
    gcs_bucket_path: str,
    client: Optional[storage.Client] = None,
    progress_callback: ProgressCallback = None,
) -> List[Tuple[str, str]]:
    gcs_path = parse_gcs_uri(gcs_bucket_path)
    client = client or get_storage_client()
    bucket = client.bucket(gcs_path.bucket_name)

    emit_progress(progress_callback, f"Discovering VM folders under {gcs_path.uri}")

    blobs_iter = client.list_blobs(
        bucket_or_name=bucket,
        prefix=gcs_path.prefix,
        delimiter="/",
    )

    root_files = []

    for blob in blobs_iter:
        if not is_directory_marker(blob.name):
            root_files.append(blob.name)

    vm_prefixes = sorted(blobs_iter.prefixes)

    if root_files:
        vm_prefixes.append(gcs_path.prefix)

    vm_results: List[Tuple[str, str]] = []

    for vm_prefix in vm_prefixes:
        normalized_prefix = vm_prefix.rstrip("/") + "/"

        if normalized_prefix == gcs_path.prefix:
            vm_name = PurePosixPath(gcs_path.prefix.rstrip("/")).name or "root"
        else:
            vm_name = normalized_prefix.rstrip("/").split("/")[-1]

        vm_results.append((vm_name, normalized_prefix))

    deduped: Dict[str, str] = {}

    for vm_name, vm_prefix in vm_results:
        deduped[vm_prefix] = vm_name

    final_results = [(vm_name, vm_prefix) for vm_prefix, vm_name in deduped.items()]
    final_results.sort(key=lambda item: item[0].lower())

    if not final_results:
        raise GCSIngestionError(f"No VM folders found under {gcs_path.uri}")

    emit_progress(
        progress_callback,
        f"Discovered {len(final_results)} VM folder(s) under {gcs_path.uri}",
    )

    return final_results


def read_first_n_lines_from_blob(
    blob: storage.Blob,
    max_lines: int = DEFAULT_MAX_LINES_PER_FILE,
    encoding: str = "utf-8",
) -> Tuple[str, int, bool]:
    lines: List[str] = []
    truncated = False

    try:
        with blob.open("rt", encoding=encoding, errors="replace") as file_obj:
            for line_number, line in enumerate(file_obj, start=1):
                if line_number > max_lines:
                    truncated = True
                    break
                lines.append(line)
    except Exception as exc:
        raise GCSIngestionError(
            f"Failed to read gs://{blob.bucket.name}/{blob.name}: {exc}"
        ) from exc

    return "".join(lines), len(lines), truncated


def build_file_section(source_file: SourceFile) -> str:
    truncated = "Yes" if source_file.truncated else "No"
    content = source_file.content or ""

    return "\n".join(
        [
            f"===== FILE START: {source_file.gcs_uri} =====",
            f"Relative path: {source_file.relative_path}",
            f"Folder: {source_file.folder_relative_path}",
            f"Lines included: 1-{source_file.lines_read}",
            f"Truncated after configured line limit: {truncated}",
            "",
            content,
            f"===== FILE END: {source_file.gcs_uri} =====",
        ]
    )


def build_combined_text(source_files: Iterable[SourceFile]) -> str:
    return "\n\n".join(build_file_section(source_file) for source_file in source_files)


def split_text_with_line_ranges(
    text: str,
    max_chars: int = DEFAULT_CHUNK_MAX_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> List[Tuple[str, str]]:
    if not text:
        return []

    lines = text.splitlines()
    chunks: List[Tuple[str, str]] = []

    current_lines: List[str] = []
    current_start_line = 1
    current_chars = 0

    for index, line in enumerate(lines, start=1):
        line_with_newline = line + "\n"
        line_len = len(line_with_newline)

        if current_lines and current_chars + line_len > max_chars:
            end_line = index - 1
            chunk_text = "".join(current_lines).strip()

            if chunk_text:
                chunks.append((chunk_text, f"{current_start_line}-{end_line}"))

            if overlap_chars > 0:
                overlap_lines: List[str] = []
                overlap_size = 0

                for previous_line in reversed(current_lines):
                    if overlap_size + len(previous_line) > overlap_chars:
                        break
                    overlap_lines.insert(0, previous_line)
                    overlap_size += len(previous_line)

                current_lines = overlap_lines
                current_start_line = max(1, end_line - len(current_lines) + 1)
                current_chars = sum(len(item) for item in current_lines)
            else:
                current_lines = []
                current_start_line = index
                current_chars = 0

        if not current_lines:
            current_start_line = index

        current_lines.append(line_with_newline)
        current_chars += line_len

    if current_lines:
        chunk_text = "".join(current_lines).strip()

        if chunk_text:
            chunks.append((chunk_text, f"{current_start_line}-{len(lines)}"))

    return chunks


def build_chunks_for_source_file(
    vm_name: str,
    folder_name: str,
    source_file: SourceFile,
    max_chars: int = DEFAULT_CHUNK_MAX_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> List[TextChunk]:
    chunks: List[TextChunk] = []

    for index, (chunk_text, line_range) in enumerate(
        split_text_with_line_ranges(
            source_file.content or "",
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        ),
        start=1,
    ):
        chunks.append(
            TextChunk(
                chunk_id=f"{vm_name}/{folder_name}/{source_file.relative_path}/chunk-{index}",
                vm_name=vm_name,
                folder_name=folder_name,
                source_uri=source_file.gcs_uri,
                relative_path=source_file.relative_path,
                line_range=line_range,
                text=chunk_text,
                metadata={
                    "filename": source_file.filename,
                    "folder_relative_path": source_file.folder_relative_path,
                    "lines_read": source_file.lines_read,
                    "truncated": source_file.truncated,
                },
            )
        )

    return chunks


def build_chunks_for_folder(
    folder_context: FolderContext,
    max_chars: int = DEFAULT_CHUNK_MAX_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> List[TextChunk]:
    chunks: List[TextChunk] = []

    for source_file in folder_context.included_files:
        chunks.extend(
            build_chunks_for_source_file(
                vm_name=folder_context.vm_name,
                folder_name=folder_context.folder_name,
                source_file=source_file,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
        )

    return chunks


def ingest_vm_folder(
    bucket_name: str,
    vm_name: str,
    vm_prefix: str,
    client: Optional[storage.Client] = None,
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    chunk_max_chars: int = DEFAULT_CHUNK_MAX_CHARS,
    chunk_overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
    progress_callback: ProgressCallback = None,
) -> VMContext:
    client = client or get_storage_client()
    bucket = client.bucket(bucket_name)

    emit_progress(progress_callback, f"{vm_name}: scanning files under gs://{bucket_name}/{vm_prefix}")

    blobs = list(client.list_blobs(bucket_or_name=bucket, prefix=vm_prefix))

    folder_files: Dict[str, List[SourceFile]] = {}
    folder_ignored: Dict[str, List[IgnoredFile]] = {}
    vm_ignored: List[IgnoredFile] = []

    total_files = 0

    for blob in blobs:
        blob_name = normalize_blob_name(blob.name)

        if is_directory_marker(blob_name):
            continue

        total_files += 1

        relative_path = get_relative_path(blob_name, vm_prefix)

        if not relative_path:
            continue

        filename = PurePosixPath(relative_path).name
        folder_name = get_folder_relative_path(relative_path)
        gcs_uri = blob_to_gcs_uri(bucket_name, blob_name)

        include, reason = should_include_file(
            filename=filename,
            folder_relative_path=folder_name,
        )

        if not include:
            ignored_file = IgnoredFile(
                gcs_uri=gcs_uri,
                relative_path=relative_path,
                reason=reason,
            )

            folder_ignored.setdefault(folder_name, []).append(ignored_file)
            vm_ignored.append(ignored_file)
            continue

        content, lines_read, truncated = read_first_n_lines_from_blob(
            blob=blob,
            max_lines=max_lines_per_file,
        )

        source_file = SourceFile(
            gcs_uri=gcs_uri,
            relative_path=relative_path,
            folder_relative_path=folder_name,
            filename=filename,
            lines_read=lines_read,
            truncated=truncated,
            content=content,
        )

        folder_files.setdefault(folder_name, []).append(source_file)

    folder_contexts: List[FolderContext] = []

    for folder_name in sorted(folder_files.keys()):
        source_files = sorted(
            folder_files[folder_name],
            key=lambda item: item.relative_path.lower(),
        )

        folder_context = FolderContext(
            vm_name=vm_name,
            folder_name=folder_name,
            folder_gcs_prefix=build_folder_gcs_prefix(vm_prefix, folder_name),
            included_files=source_files,
            ignored_files=folder_ignored.get(folder_name, []),
            chunks=[],
            combined_text=build_combined_text(source_files),
            truncated_file_count=sum(1 for item in source_files if item.truncated),
        )

        folder_context.chunks = build_chunks_for_folder(
            folder_context,
            max_chars=chunk_max_chars,
            overlap_chars=chunk_overlap_chars,
        )

        folder_contexts.append(folder_context)

    vm_context = VMContext(
        vm_name=vm_name,
        vm_gcs_prefix=vm_prefix,
        folders=folder_contexts,
        ignored_files=vm_ignored,
    )

    emit_progress(
        progress_callback,
        (
            f"{vm_name}: scan completed. "
            f"Total files found: {total_files}. "
            f"Included files: {vm_context.included_file_count}. "
            f"Ignored files: {vm_context.ignored_file_count}. "
            f"Folders with included files: {vm_context.folder_count}. "
            f"Truncated files: {vm_context.truncated_file_count}."
        ),
    )

    return vm_context


def ingest_gcs_root(
    gcs_bucket_path: str,
    client: Optional[storage.Client] = None,
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    chunk_max_chars: int = DEFAULT_CHUNK_MAX_CHARS,
    chunk_overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
    progress_callback: ProgressCallback = None,
) -> List[VMContext]:
    client = client or get_storage_client()
    gcs_path = validate_gcs_root_path(
        gcs_bucket_path,
        client=client,
        progress_callback=progress_callback,
    )

    vm_prefixes = list_vm_prefixes(
        gcs_bucket_path,
        client=client,
        progress_callback=progress_callback,
    )

    vm_contexts: List[VMContext] = []

    for index, (vm_name, vm_prefix) in enumerate(vm_prefixes, start=1):
        emit_progress(
            progress_callback,
            f"Processing VM {index}/{len(vm_prefixes)}: {vm_name}",
        )

        vm_contexts.append(
            ingest_vm_folder(
                bucket_name=gcs_path.bucket_name,
                vm_name=vm_name,
                vm_prefix=vm_prefix,
                client=client,
                max_lines_per_file=max_lines_per_file,
                chunk_max_chars=chunk_max_chars,
                chunk_overlap_chars=chunk_overlap_chars,
                progress_callback=progress_callback,
            )
        )

    emit_progress(progress_callback, f"Ingestion completed for {len(vm_contexts)} VM folder(s)")

    return vm_contexts


def flatten_folder_contexts(vm_contexts: Iterable[VMContext]) -> List[FolderContext]:
    folders: List[FolderContext] = []

    for vm_context in vm_contexts:
        folders.extend(vm_context.folders)

    return folders


def summarize_vm_context(vm_context: VMContext) -> Dict[str, int | str]:
    return {
        "vm_name": vm_context.vm_name,
        "folder_count": vm_context.folder_count,
        "included_file_count": vm_context.included_file_count,
        "ignored_file_count": vm_context.ignored_file_count,
        "truncated_file_count": vm_context.truncated_file_count,
    }


def summarize_ingestion(vm_contexts: Iterable[VMContext]) -> List[Dict[str, int | str]]:
    return [summarize_vm_context(vm_context) for vm_context in vm_contexts]
