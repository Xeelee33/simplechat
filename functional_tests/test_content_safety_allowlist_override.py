#!/usr/bin/env python3
"""
Functional test for content safety false-positive allowlist override.
Version: 0.240.068
Implemented in: 0.240.068

This test ensures chat content safety logic includes a strict false-positive
allowlist override path for exact term matches (for example, "Jaws") while
still preserving blocklist and non-allowlisted category protections.
"""

import os
import sys


def test_content_safety_allowlist_override_markers():
    """Validate required allowlist override markers in chat route and settings."""
    print("🔍 Testing content safety allowlist override markers...")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    chats_path = os.path.join(repo_root, 'application', 'single_app', 'route_backend_chats.py')
    settings_path = os.path.join(repo_root, 'application', 'single_app', 'functions_settings.py')

    if not os.path.exists(chats_path):
        print(f"❌ route_backend_chats.py not found: {chats_path}")
        return False
    if not os.path.exists(settings_path):
        print(f"❌ functions_settings.py not found: {settings_path}")
        return False

    with open(chats_path, 'r', encoding='utf-8') as chats_file:
        chats_content = chats_file.read()

    with open(settings_path, 'r', encoding='utf-8') as settings_file:
        settings_content = settings_file.read()

    required_chat_markers = [
        'def get_content_safety_allowlist_override(',
        "content_safety_false_positive_allowlist",
        "content_safety_false_positive_allowlist_categories",
        'allowlist_matched_and_categories_allowed',
        "Content Safety false-positive allowlist override applied",
        'if blocklist_matches:',
        're.search(pattern, message_text, flags=re.IGNORECASE)',
    ]

    missing_chat_markers = [marker for marker in required_chat_markers if marker not in chats_content]
    if missing_chat_markers:
        print(f"❌ Missing chat-route markers: {missing_chat_markers}")
        return False

    required_settings_markers = [
        "'content_safety_false_positive_allowlist':",
        "'content_safety_false_positive_allowlist_categories':",
    ]
    missing_settings_markers = [marker for marker in required_settings_markers if marker not in settings_content]
    if missing_settings_markers:
        print(f"❌ Missing settings markers: {missing_settings_markers}")
        return False

    print("✅ Content safety allowlist override marker checks passed")
    return True


if __name__ == '__main__':
    success = test_content_safety_allowlist_override_markers()
    sys.exit(0 if success else 1)
