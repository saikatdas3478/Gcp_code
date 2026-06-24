def parse_gcs_file_uri(gcs_uri: str) -> tuple[str, str]:
    if not gcs_uri or not gcs_uri.startswith("gs://"):
        raise ReportWriterError("GCS URI must start with gs://")

    raw_path = gcs_uri.replace("gs://", "", 1).strip()

    if not raw_path:
        raise ReportWriterError("GCS URI must include a bucket name.")

    parts = raw_path.split("/", 1)
    bucket_name = parts[0].strip()

    if not bucket_name:
        raise ReportWriterError("GCS bucket name is empty.")

    if len(parts) == 1 or not parts[1].strip():
        raise ReportWriterError("GCS URI must include a file path.")

    blob_name = parts[1].lstrip("/")

    if blob_name.endswith("/"):
        raise ReportWriterError("GCS output URI must point to a file, not only a folder.")

    return bucket_name, blob_name

def resolve_output_gcs_uri(
    root_gcs_uri: str,
    output_gcs_uri: Optional[str] = None,
    filename: str = "sap_health_check_recommendations.md",
) -> str:
    if output_gcs_uri and output_gcs_uri.strip():
        cleaned = output_gcs_uri.strip()

        if not cleaned.startswith("gs://"):
            raise ReportWriterError("output_gcs_uri must start with gs://")

        if cleaned.endswith("/"):
            return cleaned + filename

        raw_path = cleaned.replace("gs://", "", 1)
        path_parts = raw_path.split("/", 1)

        if len(path_parts) == 1:
            return cleaned.rstrip("/") + "/" + filename

        blob_path = path_parts[1]
        suffix = PurePosixPath(blob_path).suffix.lower()

        if suffix in {".md", ".txt", ".json"}:
            return cleaned

        return cleaned.rstrip("/") + "/" + filename

    parsed_root = parse_gcs_uri(root_gcs_uri)
    base_prefix = parsed_root.prefix.rstrip("/")

    if base_prefix:
        return f"gs://{parsed_root.bucket_name}/{base_prefix}/sap_hc_output/{filename}"

    return f"gs://{parsed_root.bucket_name}/sap_hc_output/{filename}"

def upload_text_to_gcs(
    text: str,
    output_gcs_uri: str,
    content_type: str = "text/markdown; charset=utf-8",
    client: Optional[storage.Client] = None,
    progress_callback: ProgressCallback = None,
) -> str:
    bucket_name, blob_name = parse_gcs_file_uri(output_gcs_uri)

    client = client or storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    emit_progress(progress_callback, f"Writing report to {output_gcs_uri}")

    blob.upload_from_string(
        data=text,
        content_type=content_type,
    )

    emit_progress(progress_callback, f"Report written successfully to {output_gcs_uri}")

    return output_gcs_uri
