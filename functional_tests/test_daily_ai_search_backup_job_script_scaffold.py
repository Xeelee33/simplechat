#!/usr/bin/env python3
"""
Functional test for daily Azure AI Search backup job setup script scaffold.
Version: 0.240.057
Implemented in: 0.240.057

This test ensures the Azure Government setup script and README include the
expected Container Apps Job scheduling and backup command markers.
"""

import os
import sys


def test_daily_backup_job_script_markers():
    """Validate core markers in setup_daily_ai_search_backup_job_azure_gov.ps1."""
    print("🔍 Testing daily backup job setup script markers...")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script_path = os.path.join(repo_root, "scripts", "setup_daily_ai_search_backup_job_azure_gov.ps1")

    if not os.path.exists(script_path):
        print(f"❌ Script not found: {script_path}")
        return False

    with open(script_path, "r", encoding="utf-8") as script_file:
        script_content = script_file.read()

    required_markers = [
        "az cloud set --name AzureUSGovernment",
        "az containerapp job create",
        "--trigger-type Schedule",
        "--cron-expression",
        "backup_ai_search_indexes.py",
        "--write-direct-to-blob",
        "Search Index Data Reader",
        "Storage Blob Data Contributor",
    ]

    missing_markers = [marker for marker in required_markers if marker not in script_content]
    if missing_markers:
        print(f"❌ Missing script markers: {missing_markers}")
        return False

    print("✅ Script marker checks passed")
    return True


def test_daily_backup_job_readme_exists():
    """Validate README exists and references the setup script."""
    print("🔍 Testing daily backup job README markers...")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    readme_path = os.path.join(repo_root, "scripts", "README_DAILY_AI_SEARCH_BACKUP_JOB_AZURE_GOV.md")

    if not os.path.exists(readme_path):
        print(f"❌ README not found: {readme_path}")
        return False

    with open(readme_path, "r", encoding="utf-8") as readme_file:
        readme_content = readme_file.read()

    required_markers = [
        "setup_daily_ai_search_backup_job_azure_gov.ps1",
        "az containerapp job start",
        "job-search-backup-daily",
    ]

    missing_markers = [marker for marker in required_markers if marker not in readme_content]
    if missing_markers:
        print(f"❌ Missing README markers: {missing_markers}")
        return False

    print("✅ README marker checks passed")
    return True


if __name__ == "__main__":
    tests = [
        test_daily_backup_job_script_markers,
        test_daily_backup_job_readme_exists,
    ]
    results = [test() for test in tests]
    success = all(results)
    sys.exit(0 if success else 1)
