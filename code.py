def build_deterministic_vm_markdown(
    vm_name: str,
    vm_gcs_prefix: str,
    folder_recommendations: List[FolderRecommendation],
    folder_errors: Optional[List[str]] = None,
) -> str:
    lines: List[str] = [
        "Hello, I have completed the comprehensive analysis of your VM parameter data.",
        "Here are my findings, separated by folder-level recommendations.",
        "",
        f"VM Name: {vm_name}",
        f"VM GCS Prefix: {vm_gcs_prefix}",
        "",
    ]

    if not folder_recommendations:
        lines.extend(
            [
                "Individual Parameter Recommendations in below table format:",
                "",
                "| Original Parameter | Recommendation | Reasoning & Justification | Citations |",
                "|---|---|---|---|",
                "| N/A | No recommendation issued based on the available evidence. | No folder-level recommendation was generated for this VM. | N/A |",
                "",
                "Combined Pattern Recommendations in below table format:",
                "",
                "| Original Parameters | Recommendation | Reasoning & Justification | Citations |",
                "|---|---|---|---|",
                "| N/A | No recommendation issued based on the available evidence. | No folder-level combined evidence was available. | N/A |",
                "",
                "Compliance & Checklist Report in below table format:",
                "",
                "| Rule / Check | Parameter Found | Observed Value | Expected Value | Status | Reasoning | Citations |",
                "|---|---|---|---|---|---|---|",
                "| N/A | No | N/A | N/A | Not Checked | No folder-level recommendation was generated. | N/A |",
                "",
            ]
        )
    else:
        for index, folder_recommendation in enumerate(folder_recommendations, start=1):
            lines.extend(
                [
                    "---",
                    "",
                    f"## Folder {index}: {folder_recommendation.folder_name}",
                    "",
                    folder_recommendation.markdown.strip(),
                    "",
                ]
            )

    if folder_errors:
        lines.extend(
            [
                "---",
                "",
                "## Folder Processing Errors",
                "",
            ]
        )

        for error in folder_errors:
            lines.append(f"- {error}")

        lines.append("")

    lines.append(
        "I hope these recommendations are helpful. Please let me know if you have any questions or require further clarification on any of these points."
    )

    return "\n".join(lines).strip()


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

emit_event(
    callback=progress_callback,
    event="vm_consolidation_started",
    message=f"{vm_name}: deterministic VM-level markdown assembly started.",
    vm_name=vm_name,
    data={
        "folder_recommendation_count": len(folder_recommendations),
        "folder_error_count": len(folder_errors),
    },
)

vm_recommendation = recommendation_service.consolidate_vm(
    request=request,
    vm_name=vm_name,
    vm_gcs_prefix=vm_prefix,
    folder_recommendations=folder_recommendations,
)

vm_recommendation.warnings.extend(folder_errors)

if folder_errors:
    vm_recommendation.markdown = build_deterministic_vm_markdown(
        vm_name=vm_name,
        vm_gcs_prefix=vm_prefix,
        folder_recommendations=folder_recommendations,
        folder_errors=folder_errors,
    )

emit_event(
    callback=progress_callback,
    event="vm_consolidation_completed",
    message=f"{vm_name}: deterministic VM-level markdown assembly completed.",
    vm_name=vm_name,
    data={
        "folder_recommendation_count": len(folder_recommendations),
        "warning_count": len(vm_recommendation.warnings),
        "markdown_char_count": len(vm_recommendation.markdown),
    },
)


from .recommendation_service import (
    create_recommendation_service,
    build_deterministic_vm_markdown,
)
