#!/usr/bin/env python3
"""
Functional test for content safety allowlist prompt-filter retry.
Version: 0.240.069
Implemented in: 0.240.069

This test ensures chat paths include one retry with disambiguation context when
Azure OpenAI prompt filtering occurs after a Content Safety allowlist override.
"""

import os
import sys


def test_allowlist_prompt_filter_retry_markers():
    """Validate retry markers in route_backend_chats.py for non-streaming and streaming paths."""
    print("🔍 Testing allowlist prompt-filter retry markers...")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    chats_path = os.path.join(repo_root, 'application', 'single_app', 'route_backend_chats.py')

    if not os.path.exists(chats_path):
        print(f"❌ route_backend_chats.py not found: {chats_path}")
        return False

    with open(chats_path, 'r', encoding='utf-8') as chats_file:
        content = chats_file.read()

    required_markers = [
        'def is_prompt_content_filter_error(',
        'def add_allowlist_disambiguation_to_messages(',
        'content_safety_allowlist_matched_terms = []',
        'Prompt content filter triggered after allowlist override; retrying with disambiguation context',
        'Prompt content filter triggered after allowlist override in streaming path; retrying with disambiguation context',
        'is_prompt_content_filter_error(e)',
        'add_allowlist_disambiguation_to_messages(',
    ]

    missing = [marker for marker in required_markers if marker not in content]
    if missing:
        print(f"❌ Missing retry markers: {missing}")
        return False

    print("✅ Allowlist prompt-filter retry marker checks passed")
    return True


if __name__ == '__main__':
    success = test_allowlist_prompt_filter_retry_markers()
    sys.exit(0 if success else 1)
