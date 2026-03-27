#!/usr/bin/env python3
"""
Functional test for Azure AI Search backup script scaffold.
Version: 0.240.007
Implemented in: 0.240.007

This test ensures that the backup script dry-run mode creates the expected
backup folder structure and manifest without requiring Azure connectivity.
"""

import json
import os
import subprocess
import sys
import tempfile


def test_backup_script_dry_run_scaffold():
    """Validate dry-run output structure for backup script."""
    print("🔍 Testing Azure AI Search backup script dry-run scaffold...")

    script_path = os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "scripts",
            "backup_ai_search_indexes.py",
        )
    )

    if not os.path.exists(script_path):
        print(f"❌ Backup script not found: {script_path}")
        return False

    with tempfile.TemporaryDirectory() as temp_output_root:
        cmd = [
            sys.executable,
            script_path,
            "--endpoint",
            "https://example.search.windows.net",
            "--dry-run",
            "--output-root",
            temp_output_root,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            print("❌ Script returned non-zero exit code")
            print(f"stdout: {result.stdout}")
            print(f"stderr: {result.stderr}")
            return False

        backup_dirs = [
            name
            for name in os.listdir(temp_output_root)
            if os.path.isdir(os.path.join(temp_output_root, name))
        ]

        if len(backup_dirs) != 1:
            print(f"❌ Expected one backup directory, found: {backup_dirs}")
            return False

        backup_root = os.path.join(temp_output_root, backup_dirs[0])
        manifest_path = os.path.join(backup_root, "manifest.json")

        if not os.path.exists(manifest_path):
            print("❌ manifest.json not found in backup root")
            return False

        with open(manifest_path, "r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)

        if not manifest.get("settings", {}).get("dry_run"):
            print("❌ Manifest does not indicate dry_run=true")
            return False

        results = manifest.get("results", [])
        if len(results) != 3:
            print(f"❌ Expected 3 index results in dry-run, found {len(results)}")
            return False

        for entry in results:
            if not entry.get("success"):
                print(f"❌ Dry-run result marked unsuccessful: {entry}")
                return False

            index_name = entry.get("name")
            index_dir = os.path.join(backup_root, "indexes", index_name)
            if not os.path.isdir(index_dir):
                print(f"❌ Missing dry-run index directory: {index_dir}")
                return False

        print("✅ Dry-run scaffold test passed")
        return True


def test_backup_script_blob_upload_flags_present():
    """Validate blob-upload CLI flags are present in backup script."""
    print("🔍 Testing Azure AI Search backup script blob upload CLI flags...")

    script_path = os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "scripts",
            "backup_ai_search_indexes.py",
        )
    )

    if not os.path.exists(script_path):
        print(f"❌ Backup script not found: {script_path}")
        return False

    with open(script_path, "r", encoding="utf-8") as script_file:
        script_content = script_file.read()

    required_markers = [
        '"--upload-to-blob"',
        '"--blob-container-url"',
        '"--blob-prefix"',
        '"--resume"',
        '"--backup-id"',
        '"--write-direct-to-blob"',
        'from azure.storage.blob import ContainerClient',
        'def upload_backup_to_blob(',
        'def backup_single_index_direct_to_blob(',
        'def upload_manifest_to_blob(',
        'build_blob_upload_root(',
        'def load_local_resume_state(',
        'def save_local_resume_state(',
        'def load_blob_resume_state(',
        'def save_blob_resume_state(',
        'create_append_blob',
        'append_block',
        'resume=args.resume',
        'backup-state.json',
        '--resume requires --backup-id',
    ]

    missing_markers = [marker for marker in required_markers if marker not in script_content]
    if missing_markers:
        print(f"❌ Missing blob upload markers: {missing_markers}")
        return False

    print("✅ Blob upload CLI flags test passed")
    return True


if __name__ == "__main__":
    tests = [
        test_backup_script_dry_run_scaffold,
        test_backup_script_blob_upload_flags_present,
    ]
    results = [test() for test in tests]
    success = all(results)
    sys.exit(0 if success else 1)
