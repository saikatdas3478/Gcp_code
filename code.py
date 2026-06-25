from __future__ import annotations

from typing import Dict, List


RULEBOOK_SECTION_REFERENCE_LINKS: Dict[str, List[str]] = {
    "os_details": [
        "https://docs.cloud.google.com/sap/docs/certifications-sap-apps",
    ],
    "corosync": [
        "https://docs.cloud.google.com/sap/docs/sap-hana-ha-config-sles#create_the_corosync_configuration_files",
        "https://cloud.google.com/solutions/sap/docs/sap-hana-ha-config-rhel#edit_the_corosyncconf_default_settings",
    ],
    "sap_agents": [
        "https://cloud.google.com/solutions/sap/docs/agent-for-sap/latest/install-config-on-vm",
    ],
    "ops_agent": [
        "https://docs.cloud.google.com/sap/docs/agent-for-sap/latest/install-config-on-vm#validate-agent4sap-installation",
    ],
    "vm_deletion_protection": [
        "https://docs.cloud.google.com/compute/docs/instances/preventing-accidental-vm-deletion",
    ],
    "google_cloud_sap_agent_permission_issue": [
        "https://docs.cloud.google.com/compute/docs/access/iam#compute.viewer",
        "https://docs.cloud.google.com/iam/docs/roles-permissions#monitoring.metricWriter",
    ],
    "hana_srhook": [
        "https://docs.cloud.google.com/sap/docs/sap-hana-ha-config-sles#enable-hana-hadr-provider-hook",
        "https://docs.cloud.google.com/sap/docs/sap-hana-ha-config-rhel",
    ],
    "google_sdk": [
        "https://docs.cloud.google.com/sdk/docs/install-sdk#installation_instructions",
    ],
    "hana_fast_restart": [
        "https://docs.cloud.google.com/sap/docs/sap-hana-ha-config-sles#enable_sap_hana_fast_restart",
    ],
    "hana_parameters": [
        "https://docs.cloud.google.com/sap/docs/sap-hana-planning-guide",
    ],
}


RULEBOOK_SECTION_SAP_NOTES: Dict[str, List[str]] = {
    "database_version_sp_level": [
        "2378962",
    ],
}


RULEBOOK_SECTION_KEYWORDS: Dict[str, List[str]] = {
    "os_details": [
        "os",
        "operating system",
        "rhel",
        "suse",
        "sles",
        "certification",
        "certified",
        "machine type",
        "sap certified",
    ],
    "corosync": [
        "corosync",
        "pacemaker",
        "cluster",
        "ha cluster",
        "totem",
        "nodelist",
        "quorum",
        "ring0",
        "ring1",
        "rhel ha",
        "sles ha",
    ],
    "sap_agents": [
        "google cloud agent for sap",
        "agent for sap",
        "sap agent",
        "google_cloud_sap_agent",
        "agent",
        "sap host agent",
    ],
    "ops_agent": [
        "ops agent",
        "google cloud ops agent",
        "monitoring agent",
        "logging agent",
        "agent validation",
        "validate agent",
    ],
    "vm_deletion_protection": [
        "deletion protection",
        "delete protection",
        "prevent accidental deletion",
        "vm deletion",
        "instance deletion",
    ],
    "google_cloud_sap_agent_permission_issue": [
        "compute viewer",
        "monitoring metric writer",
        "metric writer",
        "iam",
        "permission",
        "service account",
        "roles",
        "monitoring.metricWriter",
        "compute.viewer",
    ],
    "hana_srhook": [
        "srhook",
        "ha_dr_provider",
        "hadr provider hook",
        "hana hadr",
        "hana system replication hook",
        "susHanaSR",
        "SAPHanaSR",
    ],
    "google_sdk": [
        "google sdk",
        "gcloud",
        "cloud sdk",
        "google cloud cli",
        "sdk installation",
    ],
    "hana_fast_restart": [
        "fast restart",
        "hana fast restart",
        "tmpfs",
        "basepath_persistent_memory_volumes",
        "persistent memory",
    ],
    "hana_parameters": [
        "hana parameter",
        "global.ini",
        "indexserver.ini",
        "nameserver.ini",
        "daemon.ini",
        "preprocessor.ini",
        "statement_memory_limit",
        "global_allocation_limit",
        "savepoint",
        "log_mode",
    ],
}


def get_reference_links(section_key: str) -> List[str]:
    return RULEBOOK_SECTION_REFERENCE_LINKS.get(section_key, [])


def get_section_keywords(section_key: str) -> List[str]:
    return RULEBOOK_SECTION_KEYWORDS.get(section_key, [])


def get_sap_notes(section_key: str) -> List[str]:
    return RULEBOOK_SECTION_SAP_NOTES.get(section_key, [])


def get_all_reference_sections() -> List[str]:
    return sorted(RULEBOOK_SECTION_REFERENCE_LINKS.keys())


def get_all_reference_links() -> Dict[str, List[str]]:
    return RULEBOOK_SECTION_REFERENCE_LINKS


def get_all_section_keywords() -> Dict[str, List[str]]:
    return RULEBOOK_SECTION_KEYWORDS
