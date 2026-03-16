#!/usr/bin/env python3
"""
Functional test for Azure AI Search restore script scaffold.
Version: 0.239.014
Implemented in: 0.239.014

This test ensures that the restore script dry-run mode validates backup artifacts,
plans target index names, and writes a restore manifest without Azure connectivity.
"""

import json
import os
import subprocess
import sys
import tempfile


DEFAULT_INDEXES = [
    "simplechat-user-index",
    "simplechat-group-index",
    "simplechat-public-index",
]


def _create_dummy_backup(backup_root: str) -> None:
    indexes_root = os.path.join(backup_root, "indexes")
    os.makedirs(indexes_root, exist_ok=True)

    manifest = {
        "backup_id": "test-backup-id",
        "indexes": DEFAULT_INDEXES,
        "results": [],
    }

    for index_name in DEFAULT_INDEXES:
        index_dir = os.path.join(indexes_root, index_name)
        os.makedirs(index_dir, exist_ok=True)

        schema_payload = {
            "name": index_name,
            "fields": [
                {
                    "name": "id",
                    "type": "Edm.String",
                    "key": True,
                    "searchable": False,
                    "filterable": True,
                    "sortable": True,
                    "facetable": False,
                    "retrievable": True,
                }
            ],
        }

        schema_path = os.path.join(index_dir, "index-schema.json")
        with open(schema_path, "w", encoding="utf-8") as schema_file:
            json.dump(schema_payload, schema_file)

        documents_path = os.path.join(index_dir, "documents.jsonl")
        with open(documents_path, "w", encoding="utf-8") as documents_file:
            documents_file.write(json.dumps({"id": "doc-1"}) + "\n")

        manifest["results"].append(
            {
                "name": index_name,
                "success": True,
                "schema_file": schema_path,
                "documents_file": documents_path,
            }
        )

    manifest_path = os.path.join(backup_root, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file)


def test_restore_script_dry_run_scaffold():
    """Validate dry-run planning behavior for restore script scaffold."""
    print("🔍 Testing Azure AI Search restore script dry-run scaffold...")

    script_path = os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "scripts",
            "restore_ai_search_indexes.py",
        )
    )

    if not os.path.exists(script_path):
        print(f"❌ Restore script not found: {script_path}")
        return False

    with tempfile.TemporaryDirectory() as temp_dir:
        backup_root = os.path.join(temp_dir, "20260316T000000Z")
        os.makedirs(backup_root, exist_ok=True)
        _create_dummy_backup(backup_root)

        cmd = [
            sys.executable,
            script_path,
            "--endpoint",
            "https://example.search.windows.net",
            "--backup-path",
            backup_root,
            "--dry-run",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            print("❌ Script returned non-zero exit code")
            print(f"stdout: {result.stdout}")
            print(f"stderr: {result.stderr}")
            return False

        restore_manifests = [
            filename
            for filename in os.listdir(backup_root)
            if filename.startswith("restore_manifest_") and filename.endswith(".json")
        ]

        if len(restore_manifests) != 1:
            print(f"❌ Expected one restore manifest, found: {restore_manifests}")
            return False

        restore_manifest_path = os.path.join(backup_root, restore_manifests[0])
        with open(restore_manifest_path, "r", encoding="utf-8") as restore_manifest_file:
            restore_manifest = json.load(restore_manifest_file)

        if not restore_manifest.get("settings", {}).get("dry_run"):
            print("❌ Restore manifest does not indicate dry_run=true")
            return False

        results = restore_manifest.get("results", [])
        if len(results) != 3:
            print(f"❌ Expected 3 index restore results, found {len(results)}")
            return False

        for item in results:
            if not item.get("success"):
                print(f"❌ Dry-run restore result marked unsuccessful: {item}")
                return False

            source_index = item.get("source_index")
            expected_target = f"{source_index}-restore"
            if item.get("target_index") != expected_target:
                print(
                    f"❌ Unexpected target index. Expected {expected_target}, "
                    f"found {item.get('target_index')}"
                )
                return False

        print("✅ Restore dry-run scaffold test passed")
        return True


if __name__ == "__main__":
    success = test_restore_script_dry_run_scaffold()
    sys.exit(0 if success else 1)
