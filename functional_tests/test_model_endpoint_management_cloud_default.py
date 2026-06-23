# test_model_endpoint_management_cloud_default.py
"""
Functional test for model endpoint management cloud normalization.
Version: 0.242.072
Implemented in: 0.242.072

This test ensures model endpoint normalization enforces management_cloud from
AZURE_ENVIRONMENT when cloud selection is not user-editable in the admin UI.
"""

import os


def read_file_text(file_path):
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read()


def test_management_cloud_enforced_for_non_editable_paths():
    """Ensure hidden cloud selector paths do not persist incorrect defaults."""
    print("🔍 Validating management_cloud enforcement for hidden selector paths...")

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    settings_path = os.path.join(
        repo_root,
        "application",
        "single_app",
        "functions_settings.py",
    )
    source = read_file_text(settings_path)

    assert "def _is_management_cloud_user_editable" in source
    assert "provider in (\"aifoundry\", \"new_foundry\") and auth_type == \"service_principal\"" in source
    assert "if (not cloud_user_editable and management_cloud != default_management_cloud) or not management_cloud:" in source

    print("✅ Management cloud normalization enforcement is wired.")


if __name__ == "__main__":
    test_management_cloud_enforced_for_non_editable_paths()
