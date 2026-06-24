"""
file_filters.py

Deterministic file filtering rules for the SAP Health Check GCS ingestion flow.

This module decides which files should be included or ignored before log/config
content is read from GCS.

Rules covered:
1. General allowed file extensions:
   .log, .txt, .ini, .conf, .master, .smb, .misc, .net

2. Global ignore rules:
   - Ignore .err files from all folders.
   - Ignore .lst files from all folders.
   - Ignore files ending with _bak, .bak, _lst, .lst.

3. Special profile folder rules:
   - Include default.pfl.
   - Include files that do not end with numeric suffixes like .1, .2, .3.
   - Ignore trans.log.
   - Ignore all .log files inside profile folder.
   - Ignore .lst files.
   - Ignore files ending with _bak, .bak, _lst, .lst.
   - Ignore files ending with numeric suffixes like .1, .2, .3.

The filtering is case-insensitive.
"""

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
    """
    Result of applying file filtering rules.

    Attributes:
        include:
            True if file should be processed, False if it should be ignored.

        reason:
            Human-readable reason explaining why the file was included or ignored.

        rule_group:
            Which rule group was applied: "general" or "profile".
    """

    include: bool
    reason: str
    rule_group: str


def normalize_filename(filename: str) -> str:
    """Normalize filename for case-insensitive comparison."""

    return filename.strip().lower()


def normalize_folder_path(folder_relative_path: Optional[str]) -> str:
    """
    Normalize folder path.

    Examples:
        None -> "."
        "" -> "."
        "profile/" -> "profile"
        "HANA/Profile" -> "hana/profile"
    """

    if not folder_relative_path:
        return "."

    normalized = str(PurePosixPath(folder_relative_path.strip("/"))).lower()
    return normalized if normalized else "."


def is_profile_folder(folder_relative_path: Optional[str]) -> bool:
    """
    Check whether the file belongs to a folder named profile.

    This returns True for:
        profile
        profile/
        hana/profile
        hana/profile/
        vm-01/hana/profile
    """

    normalized_path = normalize_folder_path(folder_relative_path)
    path_parts = PurePosixPath(normalized_path).parts

    return any(part == "profile" for part in path_parts)


def get_file_extension(filename: str) -> str:
    """
    Return the final file extension.

    Examples:
        abc.log -> .log
        DEFAULT.PFL -> .pfl
        file -> ""
        abc.conf.bak -> .bak
    """

    lowered = normalize_filename(filename)
    return PurePosixPath(lowered).suffix


def ends_with_number_suffix(filename: str) -> bool:
    """
    Check whether filename ends with numeric suffix.

    Examples:
        DEFAULT.PFL.1 -> True
        abc.2 -> True
        abc.100 -> True
        abc.log -> False
    """

    lowered = normalize_filename(filename)
    return bool(re.search(r"\.\d+$", lowered))


def has_backup_or_lst_suffix(filename: str) -> bool:
    """
    Check whether filename ends with backup/list suffix.

    Matches:
        _bak
        .bak
        _lst
        .lst
    """

    lowered = normalize_filename(filename)

    return (
        lowered.endswith("_bak")
        or lowered.endswith(".bak")
        or lowered.endswith("_lst")
        or lowered.endswith(".lst")
    )


def is_global_ignored_file(filename: str) -> Tuple[bool, str]:
    """
    Apply global ignore rules.

    These rules apply to all folders, including profile.
    """

    lowered = normalize_filename(filename)
    extension = get_file_extension(lowered)

    if extension == ".err":
        return True, "Ignored because .err files are excluded globally."

    if extension == ".lst":
        return True, "Ignored because .lst files are excluded globally."

    if has_backup_or_lst_suffix(lowered):
        return True, "Ignored because backup/list suffix files are excluded globally."

    return False, ""


def should_include_general_file(filename: str) -> FileFilterDecision:
    """
    Apply general file filtering rules.

    General allowed extensions:
        .log, .txt, .ini, .conf, .master, .smb, .misc, .net

    General ignored files:
        .err, .lst, *_bak, *.bak, *_lst
    """

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
            "is not in the general allowed extension list."
        ),
        rule_group="general",
    )


def should_include_profile_file(filename: str) -> FileFilterDecision:
    """
    Apply special profile folder rules.

    Inside profile folder:
    - Include default.pfl.
    - Include files that do not match exclusion rules.
    - Ignore trans.log.
    - Ignore all .log files.
    - Ignore .lst files.
    - Ignore backup/list suffix files.
    - Ignore numeric suffix files like .1, .2, .3.
    """

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
            reason=(
                "Ignored because numeric suffix files like .1, .2, .3 "
                "are excluded inside profile folder."
            ),
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
        reason=(
            "Included because file is inside profile folder and does not match "
            "any profile exclusion rule."
        ),
        rule_group="profile",
    )


def should_include_file_detailed(
    filename: str,
    folder_relative_path: Optional[str],
) -> FileFilterDecision:
    """
    Decide whether a file should be included.

    Profile folder rules override general rules.

    Args:
        filename:
            File basename only, for example:
            "global.ini", "DEFAULT.PFL", "trans.log"

        folder_relative_path:
            Folder path relative to the VM folder, for example:
            "os", "hana/profile", "profile"

    Returns:
        FileFilterDecision
    """

    if not filename or not filename.strip():
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
    """
    Backward-compatible helper.

    Returns:
        (include, reason)

    This is useful for gcs_ingestion.py, where we only need the boolean
    decision and explanation.
    """

    decision = should_include_file_detailed(
        filename=filename,
        folder_relative_path=folder_relative_path,
    )
    return decision.include, decision.reason


def should_ignore_file(
    filename: str,
    folder_relative_path: Optional[str],
) -> Tuple[bool, str]:
    """
    Convenience helper.

    Returns:
        (ignore, reason)
    """

    decision = should_include_file_detailed(
        filename=filename,
        folder_relative_path=folder_relative_path,
    )

    return not decision.include, decision.reason


def is_allowed_general_extension(filename: str) -> bool:
    """Return True if the file extension is generally allowed."""

    return get_file_extension(filename) in GENERAL_ALLOWED_EXTENSIONS


def is_globally_ignored_extension(filename: str) -> bool:
    """Return True if the file extension is globally ignored."""

    return get_file_extension(filename) in GLOBAL_IGNORED_EXTENSIONS


def explain_file_filter_decision(
    filename: str,
    folder_relative_path: Optional[str],
) -> str:
    """
    Return a human-readable explanation of the filter decision.

    Useful for logs, progress callbacks, debugging, or final ingestion summary.
    """

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
