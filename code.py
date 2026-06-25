from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Optional, Tuple


GENERAL_ALLOWED_EXTENSIONS = {
    ".log",
    ".txt",
    ".ini",
    ".conf",
    ".master",
    ".smb",
    ".misc",
    ".net",
}

GLOBAL_IGNORED_EXTENSIONS = {
    ".err",
    ".lst",
}

PROFILE_REQUIRED_EXACT_FILES = {
    "default.pfl",
}

PROFILE_IGNORED_EXACT_FILES = {
    "trans.log",
}


@dataclass(frozen=True)
class FileFilterDecision:
    include: bool
    reason: str
    rule_group: str


def normalize_filename(filename: str) -> str:
    return str(filename or "").strip().lower()


def normalize_folder_path(folder_relative_path: Optional[str]) -> str:
    if not folder_relative_path:
        return "."

    normalized = str(PurePosixPath(str(folder_relative_path).strip("/"))).lower()
    return normalized if normalized else "."


def get_file_extension(filename: str) -> str:
    return PurePosixPath(normalize_filename(filename)).suffix


def is_profile_folder(folder_relative_path: Optional[str]) -> bool:
    normalized_path = normalize_folder_path(folder_relative_path)
    path_parts = PurePosixPath(normalized_path).parts
    return any(part == "profile" for part in path_parts)


def ends_with_number_suffix(filename: str) -> bool:
    return bool(re.search(r"\.\d+$", normalize_filename(filename)))


def has_backup_or_lst_suffix(filename: str) -> bool:
    lowered = normalize_filename(filename)

    return (
        lowered.endswith("_bak")
        or lowered.endswith(".bak")
        or lowered.endswith("_lst")
        or lowered.endswith(".lst")
    )


def is_global_ignored_file(filename: str) -> Tuple[bool, str]:
    lowered = normalize_filename(filename)
    extension = get_file_extension(lowered)

    if extension == ".err":
        return True, "Ignored because .err files are excluded globally."

    if extension == ".lst":
        return True, "Ignored because .lst files are excluded globally."

    if lowered.endswith("_bak") or lowered.endswith(".bak"):
        return True, "Ignored because backup files are excluded globally."

    if lowered.endswith("_lst") or lowered.endswith(".lst"):
        return True, "Ignored because list suffix files are excluded globally."

    return False, ""


def should_include_general_file(filename: str) -> FileFilterDecision:
    lowered = normalize_filename(filename)
    extension = get_file_extension(lowered)

    ignored, reason = is_global_ignored_file(lowered)
    if ignored:
        return FileFilterDecision(
            include=False,
            reason=reason,
            rule_group="general",
        )

    if extension in GENERAL_ALLOWED_EXTENSIONS:
        return FileFilterDecision(
            include=True,
            reason=f"Included because extension {extension} is allowed.",
            rule_group="general",
        )

    return FileFilterDecision(
        include=False,
        reason=(
            f"Ignored because extension {extension or '[no extension]'} "
            "is not in the allowed general extension list."
        ),
        rule_group="general",
    )


def should_include_profile_file(filename: str) -> FileFilterDecision:
    lowered = normalize_filename(filename)
    extension = get_file_extension(lowered)

    ignored, reason = is_global_ignored_file(lowered)
    if ignored:
        return FileFilterDecision(
            include=False,
            reason=reason,
            rule_group="profile",
        )

    if lowered in PROFILE_IGNORED_EXACT_FILES:
        return FileFilterDecision(
            include=False,
            reason="Ignored because trans.log is excluded inside profile folder.",
            rule_group="profile",
        )

    if extension == ".log":
        return FileFilterDecision(
            include=False,
            reason="Ignored because .log files are excluded inside profile folder.",
            rule_group="profile",
        )

    if ends_with_number_suffix(lowered):
        return FileFilterDecision(
            include=False,
            reason="Ignored because numeric suffix files like .1, .2, .3 are excluded inside profile folder.",
            rule_group="profile",
        )

    if has_backup_or_lst_suffix(lowered):
        return FileFilterDecision(
            include=False,
            reason="Ignored because backup/list suffix files are excluded inside profile folder.",
            rule_group="profile",
        )

    if lowered in PROFILE_REQUIRED_EXACT_FILES:
        return FileFilterDecision(
            include=True,
            reason="Included because default.pfl is explicitly required inside profile folder.",
            rule_group="profile",
        )

    return FileFilterDecision(
        include=True,
        reason="Included because file is inside profile folder and does not match any profile exclusion rule.",
        rule_group="profile",
    )


def should_include_file_detailed(
    filename: str,
    folder_relative_path: Optional[str],
) -> FileFilterDecision:
    if not filename or not str(filename).strip():
        return FileFilterDecision(
            include=False,
            reason="Ignored because filename is empty.",
            rule_group="general",
        )

    if is_profile_folder(folder_relative_path):
        return should_include_profile_file(filename)

    return should_include_general_file(filename)


def should_include_file(
    filename: str,
    folder_relative_path: Optional[str],
) -> Tuple[bool, str]:
    decision = should_include_file_detailed(
        filename=filename,
        folder_relative_path=folder_relative_path,
    )
    return decision.include, decision.reason


def should_ignore_file(
    filename: str,
    folder_relative_path: Optional[str],
) -> Tuple[bool, str]:
    decision = should_include_file_detailed(
        filename=filename,
        folder_relative_path=folder_relative_path,
    )
    return not decision.include, decision.reason


def is_allowed_general_extension(filename: str) -> bool:
    return get_file_extension(filename) in GENERAL_ALLOWED_EXTENSIONS


def is_globally_ignored_extension(filename: str) -> bool:
    return get_file_extension(filename) in GLOBAL_IGNORED_EXTENSIONS


def explain_file_filter_decision(
    filename: str,
    folder_relative_path: Optional[str],
) -> str:
    decision = should_include_file_detailed(
        filename=filename,
        folder_relative_path=folder_relative_path,
    )

    action = "INCLUDED" if decision.include else "IGNORED"
    folder = normalize_folder_path(folder_relative_path)

    return (
        f"{action}: {filename} | "
        f"folder={folder} | "
        f"rule_group={decision.rule_group} | "
        f"reason={decision.reason}"
    )
