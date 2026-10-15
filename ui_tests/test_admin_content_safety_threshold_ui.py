# test_admin_content_safety_threshold_ui.py
"""
UI test for Admin Settings Content Safety prompt threshold controls.

Version: 0.261.059
Implemented in: 0.261.058

This test ensures the Content Safety admin pane exposes a severity threshold
slider, describes its blocking behavior, keeps the live badge synchronized
through local SimpleChat JavaScript, and verifies Admin Settings range sliders
share the dark-mode contrast fix.
"""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ADMIN_PANES_DIR = REPO_ROOT / "application" / "single_app" / "templates" / "admin" / "_panes"
CONTENT_SAFETY_TEMPLATE = ADMIN_PANES_DIR / "content-safety.html"
ADMIN_JS = REPO_ROOT / "application" / "single_app" / "static" / "js" / "admin" / "admin_settings.js"
STYLES_CSS = REPO_ROOT / "application" / "single_app" / "static" / "css" / "styles.css"


def test_content_safety_threshold_slider_is_rendered():
    """Validate the admin threshold slider is present with Azure severity bounds."""
    template = CONTENT_SAFETY_TEMPLATE.read_text(encoding="utf-8")

    assert 'for="content_safety_block_threshold"' in template
    assert 'id="content_safety_block_threshold_value"' in template
    assert 'type="range"' in template
    assert 'class="form-range"' in template
    assert 'id="content_safety_block_threshold"' in template
    assert 'name="content_safety_block_threshold"' in template
    assert 'min="2"' in template
    assert 'max="7"' in template
    assert 'step="1"' in template
    assert 'value="{{ settings.content_safety_block_threshold | default(4) }}"' in template
    assert 'Lower values are more restrictive; default is 4.' in template


def test_content_safety_threshold_badge_uses_text_content():
    """Validate the slider badge is updated without HTML injection sinks."""
    admin_js = ADMIN_JS.read_text(encoding="utf-8")

    assert "const contentSafetyBlockThreshold = document.getElementById('content_safety_block_threshold');" in admin_js
    assert "const contentSafetyBlockThresholdValue = document.getElementById('content_safety_block_threshold_value');" in admin_js
    assert 'contentSafetyBlockThresholdValue.textContent = contentSafetyBlockThreshold.value;' in admin_js
    assert "contentSafetyBlockThreshold.addEventListener('input', updateContentSafetyBlockThresholdValue);" in admin_js
    assert "contentSafetyBlockThreshold.addEventListener('change', markFormAsModified);" in admin_js


def test_admin_settings_range_sliders_use_switch_off_gray_track():
    """Validate every Admin Settings range slider receives the dark-mode contrast fix."""
    styles_css = STYLES_CSS.read_text(encoding="utf-8")
    pane_markup = "\n".join(
        path.read_text(encoding="utf-8") for path in ADMIN_PANES_DIR.glob("*.html")
    )

    assert 'id="landing_page_logo_scale_percent"' in pane_markup
    assert 'type="range"' in pane_markup
    assert '[data-bs-theme="dark"] .form-range::-webkit-slider-runnable-track' in styles_css
    assert '[data-bs-theme="dark"] .form-range::-moz-range-track' in styles_css
    assert 'background-color: #6c757d;' in styles_css
