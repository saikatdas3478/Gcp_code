def normalize_markdown_output(markdown: str) -> str:
    cleaned = strip_markdown_code_fence(markdown)
    cleaned = clean_folder_markdown(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned

def generate_markdown_response(
    prompt: str,
    client: Optional[genai.Client] = None,
    runtime_config: Optional[RuntimeConfig] = None,
) -> str:
    runtime_config = runtime_config or get_default_runtime_config()
    client = client or get_genai_client(runtime_config)

    last_error = None

    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model=runtime_config.llm.model_name,
                contents=prompt,
                config=build_generate_config(runtime_config),
            )

            text = getattr(response, "text", None)

            if text and text.strip():
                return normalize_markdown_output(text)

            last_error = "LLM returned an empty response."

        except Exception as exc:
            last_error = str(exc)

    return ""

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

if folder_recommendation.markdown.strip():
    folder_recommendations.append(folder_recommendation)

    emit_event(
        callback=progress_callback,
        event="folder_llm_completed",
        message=(
            f"{vm_name}/{folder_context.folder_name}: "
            f"folder-level recommendation generated."
        ),
        vm_name=vm_name,
        folder_name=folder_context.folder_name,
        data={
            "markdown_char_count": len(folder_recommendation.markdown),
            "included_in_report": True,
        },
    )
else:
    emit_event(
        callback=progress_callback,
        event="folder_llm_completed",
        message=(
            f"{vm_name}/{folder_context.folder_name}: "
            f"no evidence-backed recommendation rows found after cleanup, folder skipped."
        ),
        vm_name=vm_name,
        folder_name=folder_context.folder_name,
        data={
            "markdown_char_count": 0,
            "included_in_report": False,
        },
    )
