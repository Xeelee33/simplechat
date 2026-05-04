#!/usr/bin/env python3
"""
Functional test for local daily Azure AI Search backup scheduler script.
Version: 0.241.008
Implemented in: 0.241.008

This test ensures the local scheduler wrapper script includes expected parameters,
backup invocation behavior, and direct-to-blob backup arguments.
"""

import os
import sys


def test_local_backup_scheduler_script_markers():
    """Validate local backup scheduler script markers."""
    print("🔍 Testing local daily AI Search backup scheduler script markers...")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script_path = os.path.join(repo_root, 'scripts', 'run_daily_ai_search_backup_local.ps1')

    if not os.path.exists(script_path):
        print(f"❌ Script not found: {script_path}")
        return False

    with open(script_path, 'r', encoding='utf-8') as script_file:
        content = script_file.read()

    required_markers = [
        'param(',
        '[string]$SearchEndpoint',
        '[string]$BlobContainerUrl',
        '--write-direct-to-blob',
        '--blob-container-url',
        '--blob-prefix',
        'Ensure-AzureLogin',
        'Backup completed successfully',
    ]

    missing_markers = [marker for marker in required_markers if marker not in content]
    if missing_markers:
        print(f"❌ Missing required markers: {missing_markers}")
        return False

    print("✅ Local daily backup scheduler script marker checks passed")
    return True


if __name__ == '__main__':
    success = test_local_backup_scheduler_script_markers()
    sys.exit(0 if success else 1)
