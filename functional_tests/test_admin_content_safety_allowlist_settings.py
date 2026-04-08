#!/usr/bin/env python3
"""
Functional test for admin content safety allowlist settings.
Version: 0.240.068
Implemented in: 0.240.068

This test ensures the admin settings UI and save handler include configurable
content safety false-positive allowlist fields.
"""

import os
import sys


def test_admin_content_safety_allowlist_markers():
    """Validate template and admin route markers for content safety allowlist settings."""
    print("🔍 Testing admin content safety allowlist settings markers...")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_path = os.path.join(repo_root, 'application', 'single_app', 'templates', 'admin_settings.html')
    route_path = os.path.join(repo_root, 'application', 'single_app', 'route_frontend_admin_settings.py')

    if not os.path.exists(template_path):
        print(f"❌ Template not found: {template_path}")
        return False
    if not os.path.exists(route_path):
        print(f"❌ Route file not found: {route_path}")
        return False

    with open(template_path, 'r', encoding='utf-8') as template_file:
        template_content = template_file.read()
    with open(route_path, 'r', encoding='utf-8') as route_file:
        route_content = route_file.read()

    required_template_markers = [
        'id="content_safety_false_positive_allowlist"',
        'name="content_safety_false_positive_allowlist"',
        'id="content_safety_false_positive_allowlist_categories"',
        'name="content_safety_false_positive_allowlist_categories"',
    ]
    missing_template = [marker for marker in required_template_markers if marker not in template_content]
    if missing_template:
        print(f"❌ Missing template markers: {missing_template}")
        return False

    required_route_markers = [
        "content_safety_false_positive_allowlist = parse_admin_text_list",
        "content_safety_false_positive_allowlist_categories = parse_admin_text_list",
        "'content_safety_false_positive_allowlist': content_safety_false_positive_allowlist",
        "'content_safety_false_positive_allowlist_categories': content_safety_false_positive_allowlist_categories",
    ]
    missing_route = [marker for marker in required_route_markers if marker not in route_content]
    if missing_route:
        print(f"❌ Missing route markers: {missing_route}")
        return False

    print("✅ Admin content safety allowlist settings marker checks passed")
    return True


if __name__ == '__main__':
    success = test_admin_content_safety_allowlist_markers()
    sys.exit(0 if success else 1)
