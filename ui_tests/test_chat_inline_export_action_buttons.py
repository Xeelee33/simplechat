# test_chat_inline_export_action_buttons.py
"""
UI test for assistant inline export action buttons.
Version: 0.241.179
Implemented in: 0.241.179

This test ensures assistant replies show inline export buttons when the latest
user prompt explicitly asks for a supported export format such as a
presentation, markdown document, or email, that the buttons persist when the
conversation history is reloaded, that inline create actions show a pending
label while the export is being prepared, and that PowerPoint exports prefer
attached generated Markdown artifacts when present. It also ensures streaming
assistant placeholders do not expose export/create actions before the final
message is available, and that generic email export buttons are suppressed when
Microsoft Graph already provides the real mail action or consent prompt. It also
verifies the consent prompt includes a test-access action.
"""

import json
import os
import time
from pathlib import Path

import pytest

try:
    from playwright.sync_api import expect
except ModuleNotFoundError:
    pytest.skip("Playwright is not installed in this environment.", allow_module_level=True)


BASE_URL = os.getenv("SIMPLECHAT_UI_BASE_URL", "").rstrip("/")
STORAGE_STATE = os.getenv("SIMPLECHAT_UI_STORAGE_STATE", "")


def _require_ui_env():
    if not BASE_URL:
        pytest.skip("Set SIMPLECHAT_UI_BASE_URL to run this UI test.")
    if not STORAGE_STATE or not Path(STORAGE_STATE).exists():
        pytest.skip("Set SIMPLECHAT_UI_STORAGE_STATE to a valid authenticated Playwright storage state file.")


def _fulfill_json(route, payload, status=200):
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(payload),
    )


@pytest.mark.ui
def test_word_quick_action_does_not_duplicate_a_generated_document(playwright):
    """Exercise the real quick-action renderer with and without a DOCX artifact."""
    source_path = (
        Path(__file__).resolve().parents[1]
        / "application" / "single_app" / "static" / "js" / "chat" / "chat-messages.js"
    )
    source = source_path.read_text(encoding="utf-8")
    constants = source[
        source.index("const INLINE_ASSISTANT_EXPORT_ACTIONS ="):
        source.index("const MAX_SUGGESTED_FOLLOW_UP_ACTIONS")
    ]
    prompt_helpers = source[
        source.index("  function getLatestUserPromptText()"):
        source.index("  function isStreamingAssistantPlaceholder(")
    ]
    artifact_helpers = source[
        source.index("  function buildInlineAssistantExportActionsHtml("):
        source.index("  function getGeneratedTabularOutputs(")
    ]

    browser = playwright.chromium.launch()
    page = browser.new_page()
    try:
        page.set_content(
            '<div id="chatbox"><div class="message user-message">'
            '<div class="message-text">Create a Word document and presentation.</div>'
            '</div></div><div id="actions"></div>'
        )
        page.add_script_tag(content=(
            "const chatbox = document.getElementById('chatbox');\n"
            + constants + prompt_helpers + artifact_helpers
            + "\nwindow.renderActions = (message, text = '') => { "
            "document.getElementById('actions').innerHTML = "
            "buildInlineAssistantExportActionsHtml('assistant-1', message, text); };"
        ))

        page.evaluate("window.renderActions({}, 'Before I generate the Word file, what font and size would you like?')")
        expect(page.locator("#actions .inline-export-word-btn")).to_have_count(0)
        expect(page.locator("#actions .inline-export-ppt-btn")).to_have_count(1)
        page.evaluate("window.renderActions({metadata: {pending_document_copy: {}}}, 'Please confirm your preference.')")
        expect(page.locator("#actions .inline-export-word-btn")).to_have_count(0)

        page.evaluate("window.renderActions({metadata: {generated_analysis_artifacts: ["
                      "{artifact_message_id: 'docx-1', output_format: 'docx', file_name: 'result.docx'}]}})")
        expect(page.locator("#actions .inline-export-word-btn")).to_have_count(0)
        expect(page.locator("#actions .inline-export-ppt-btn")).to_have_count(1)

        for metadata in [
            {},
            {"generated_analysis_artifacts": [{"artifact_message_id": "pdf-1", "output_format": "pdf"}]},
            {"generated_analysis_artifacts": [{"output_format": "docx"}]},
        ]:
            page.evaluate("metadata => window.renderActions({metadata})", metadata)
            expect(page.locator("#actions .inline-export-word-btn")).to_have_count(1)

        page.evaluate("window.renderActions({metadata: {generated_tabular_outputs: ["
                      "{artifact_message_id: 'docx-2', file_name: 'result.DOCX'}]}})")
        expect(page.locator("#actions .inline-export-word-btn")).to_have_count(0)
    finally:
        page.close()
        browser.close()


@pytest.mark.ui
def test_generated_word_download_replaces_quick_action_in_live_and_history(playwright):
    """Keep the saved document download, but not a misleading acknowledgement export."""
    _require_ui_env()
    browser = playwright.chromium.launch()
    context = browser.new_context(storage_state=STORAGE_STATE, accept_downloads=True)
    page = context.new_page()
    download_urls = []
    page.route(
        "**/api/user/settings",
        lambda route: _fulfill_json(route, {"selected_agent": None, "settings": {"enable_agents": False}}),
    )
    page.route("**/api/get_conversations", lambda route: _fulfill_json(route, {"conversations": []}))

    def handle_download(route):
        download_urls.append(route.request.url)
        route.fulfill(
            status=200,
            headers={
                "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "Content-Disposition": 'attachment; filename="generated_output.docx"',
            },
            body=b"test-generated-document",
        )

    page.route("**/api/chat_artifacts/download?**", handle_download)
    try:
        page.goto(f"{BASE_URL}/chats", wait_until="domcontentloaded")
        page.wait_for_selector("#chatbox")
        page.wait_for_function("() => window.chatMessages && typeof window.chatMessages.appendMessage === 'function'")
        page.evaluate(
            """
            async () => {
                const conversationId = 'word-export-actions-test';
                currentConversationId = conversationId;
                window.currentConversationId = conversationId;
                const messages = await import('/static/js/chat/chat-messages.js');
                for (const [suffix, isNew] of [['live', true], ['history', false]]) {
                    const prompt = 'Generate a Word doc with that list.';
                    messages.appendMessage('You', prompt, null, `user-${suffix}`, false, [], [], [],
                        null, null, { id: `user-${suffix}`, role: 'user', content: prompt }, isNew);
                    messages.appendMessage('AI', 'The document is ready.', null, `assistant-${suffix}`,
                        false, [], [], [], null, null, {
                            id: `assistant-${suffix}`,
                            role: 'assistant',
                            metadata: {
                                generated_analysis_artifacts: [{
                                    capability: 'file_export',
                                    artifact_message_id: `artifact-${suffix}`,
                                    conversation_id: conversationId,
                                    file_name: 'generated_output.docx',
                                    output_format: 'docx',
                                }],
                            },
                        }, isNew);
                }
            }
            """
        )

        for suffix in ("live", "history"):
            message = page.locator(f'[data-message-id="assistant-{suffix}"]')
            expect(message.locator(".inline-export-word-btn")).to_have_count(0)
            expect(message.locator(".dropdown-export-word-btn")).to_have_count(1)
            download_button = message.get_by_role("button", name="Download generated_output.docx")
            expect(download_button).to_be_visible()
            with page.expect_download() as download_info:
                download_button.click()
            assert download_info.value.suggested_filename == "generated_output.docx"
            assert f"message_id=artifact-{suffix}" in download_urls[-1]
    finally:
        context.close()
        browser.close()


@pytest.mark.ui
def test_assistant_inline_export_actions_follow_latest_user_request(playwright):
    """Validate inline export actions appear only for matching new assistant replies."""
    _require_ui_env()

    browser = playwright.chromium.launch()
    context = browser.new_context(
        storage_state=STORAGE_STATE,
        viewport={"width": 1440, "height": 900},
    )
    page = context.new_page()

    page.route(
        "**/api/user/settings",
        lambda route: _fulfill_json(route, {"selected_agent": None, "settings": {"enable_agents": False}}),
    )
    page.route("**/api/get_conversations", lambda route: _fulfill_json(route, {"conversations": []}))
    export_requests = []

    def handle_powerpoint_export(route):
        export_requests.append(route.request.post_data_json)
        time.sleep(0.25)
        route.fulfill(
            status=200,
            content_type=(
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            ),
            body=b"mock-pptx-download",
        )

    page.route("**/api/message/export-powerpoint", handle_powerpoint_export)

    try:
        page.goto(f"{BASE_URL}/chats", wait_until="domcontentloaded")
        page.wait_for_selector("#chatbox")
        page.wait_for_function("() => window.chatMessages && typeof window.chatMessages.extractSuggestedFollowUpPrompts === 'function'")

        page.evaluate(
            """
            async () => {
                const conversationId = 'inline-export-actions-test';
                currentConversationId = conversationId;
                window.currentConversationId = conversationId;

                const messagesModule = await import('/static/js/chat/chat-messages.js');

                messagesModule.appendMessage(
                    'You',
                    'Please create a presentation and send an email for this summary.',
                    null,
                    'user-presentation-request',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    {
                        id: 'user-presentation-request',
                        role: 'user',
                        content: 'Please create a presentation and send an email for this summary.',
                    },
                    true
                );

                messagesModule.appendMessage(
                    'AI',
                    'Here is the summary you requested.',
                    null,
                    'assistant-presentation-response',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    {
                        id: 'assistant-presentation-response',
                        role: 'assistant',
                        content: 'Here is the summary you requested.',
                        metadata: {
                            generated_analysis_artifacts: [
                                {
                                    capability: 'analyze',
                                    artifact_message_id: 'generated-markdown-presentation',
                                    conversation_id: 'inline-export-actions-test',
                                    storage_scope: 'chat',
                                    file_name: 'generated-presentation.md',
                                    output_format: 'md',
                                    summary: 'Saved the full presentation deck as Markdown.',
                                    preview_lines: [
                                        '# Generated Presentation',
                                        '## Slide 1 - Overview',
                                        'Deck content lives in the artifact.',
                                    ],
                                },
                            ],
                        },
                    },
                    true
                );

                messagesModule.appendMessage(
                    'You',
                    'Send an email to ada@example.com about this update.',
                    null,
                    'user-graph-email-request',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    {
                        id: 'user-graph-email-request',
                        role: 'user',
                        content: 'Send an email to ada@example.com about this update.',
                    },
                    true
                );

                messagesModule.appendMessage(
                    'AI',
                    'Done. I created the email draft and it is pending review.',
                    null,
                    'assistant-graph-pending-response',
                    false,
                    [],
                    [],
                    [
                        {
                            function_name: 'send_mail',
                            function_result: {
                                operation: 'send_mail',
                                pending_action: {
                                    id: 'pending-mail-1',
                                    type: 'msgraph_pending_action',
                                    operation: 'send_mail',
                                    graph_resource_type: 'mail',
                                    status: 'pending',
                                    action_mode: 'manual',
                                    subject: 'Update',
                                    can_send_now: true,
                                    can_cancel: true,
                                },
                            },
                        },
                    ],
                    'Executive Agent',
                    'executive_agent',
                    {
                        id: 'assistant-graph-pending-response',
                        role: 'assistant',
                        content: 'Done. I created the email draft and it is pending review.',
                    },
                    true
                );

                messagesModule.appendMessage(
                    'You',
                    'Send an email to grace@example.com about this update.',
                    null,
                    'user-graph-consent-request',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    {
                        id: 'user-graph-consent-request',
                        role: 'user',
                        content: 'Send an email to grace@example.com about this update.',
                    },
                    true
                );

                messagesModule.appendMessage(
                    'AI',
                    'Microsoft Graph needs permission before I can send that email.',
                    null,
                    'assistant-graph-consent-response',
                    false,
                    [],
                    [],
                    [
                        {
                            function_name: 'send_mail',
                            function_result: {
                                error: 'consent_required',
                                message: 'User consent is required to access Microsoft 365 resources like Outlook email, Calendar, OneDrive, or SharePoint.',
                                operation: 'send_mail',
                                consent_url: 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize?client_id=test&scope=Mail.Send',
                                scopes: ['Mail.Send'],
                            },
                        },
                    ],
                    'Executive Agent',
                    'executive_agent',
                    {
                        id: 'assistant-graph-consent-response',
                        role: 'assistant',
                        content: 'Microsoft Graph needs permission before I can send that email.',
                    },
                    true
                );

                messagesModule.appendMessage(
                    'You',
                    'Please create a word document for this saved summary.',
                    null,
                    'user-history-request',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    {
                        id: 'user-history-request',
                        role: 'user',
                        content: 'Please create a word document for this saved summary.',
                    },
                    true
                );

                messagesModule.appendMessage(
                    'AI',
                    'This reloaded assistant reply should keep its export action.',
                    null,
                    'assistant-historical-response',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    {
                        id: 'assistant-historical-response',
                        role: 'assistant',
                        content: 'This reloaded assistant reply should keep its export action.',
                    },
                    false
                );

                messagesModule.appendMessage(
                    'You',
                    'Please create a markdown document for this answer too.',
                    null,
                    'user-markdown-request',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    {
                        id: 'user-markdown-request',
                        role: 'user',
                        content: 'Please create a markdown document for this answer too.',
                    },
                    true
                );

                messagesModule.appendMessage(
                    'AI',
                    'Here is the markdown-ready answer.',
                    null,
                    'assistant-markdown-response',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    {
                        id: 'assistant-markdown-response',
                        role: 'assistant',
                        content: 'Here is the markdown-ready answer.',
                    },
                    true
                );

                messagesModule.appendMessage(
                    'You',
                    'Thanks for the summary.',
                    null,
                    'user-no-export-request',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    {
                        id: 'user-no-export-request',
                        role: 'user',
                        content: 'Thanks for the summary.',
                    },
                    true
                );

                messagesModule.appendMessage(
                    'AI',
                    'No quick export actions should render for this reply.',
                    null,
                    'assistant-no-export-response',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    {
                        id: 'assistant-no-export-response',
                        role: 'assistant',
                        content: 'No quick export actions should render for this reply.',
                    },
                    true
                );

                messagesModule.appendMessage(
                    'You',
                    'Please provide a shorter 5-slide executive deck.',
                    null,
                    'user-deck-request',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    {
                        id: 'user-deck-request',
                        role: 'user',
                        content: 'Please provide a shorter 5-slide executive deck.',
                    },
                    true
                );

                messagesModule.appendMessage(
                    'AI',
                    'Here is the deck-ready answer.',
                    null,
                    'assistant-deck-response',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    {
                        id: 'assistant-deck-response',
                        role: 'assistant',
                        content: 'Here is the deck-ready answer.',
                    },
                    true
                );
            }
            """
        )

        presentation_message = page.locator('[data-message-id="assistant-presentation-response"]')
        presentation_actions = presentation_message.locator('.inline-assistant-export-actions')
        expect(presentation_actions).to_be_visible()
        expect(presentation_actions.locator('button')).to_have_count(3)
        powerpoint_button = presentation_actions.locator('button', has_text='Create PowerPoint Presentation')
        expect(powerpoint_button).to_be_visible()
        expect(presentation_actions.locator('button', has_text='Create Word Document')).to_be_visible()

        email_button = presentation_actions.locator('button', has_text='Send an Email')
        expect(email_button).to_be_visible()
        expect(email_button).to_have_attribute('title', 'Opens Message in your default mail program')
        expect(presentation_actions.locator('button', has_text='Create Markdown Document')).to_have_count(0)

        graph_pending_message = page.locator('[data-message-id="assistant-graph-pending-response"]')
        expect(graph_pending_message.locator('.inline-open-email-btn')).to_have_count(0)
        expect(graph_pending_message.locator('.dropdown-open-email-btn')).to_have_count(0)
        expect(graph_pending_message.locator('.msgraph-pending-action-card')).to_be_visible()
        expect(graph_pending_message.locator('.msgraph-pending-send-btn')).to_be_visible()

        graph_consent_message = page.locator('[data-message-id="assistant-graph-consent-response"]')
        expect(graph_consent_message.locator('.inline-open-email-btn')).to_have_count(0)
        expect(graph_consent_message.locator('.dropdown-open-email-btn')).to_have_count(0)
        expect(graph_consent_message.locator('.msgraph-consent-action-card')).to_be_visible()
        expect(graph_consent_message.locator('.msgraph-consent-btn')).to_have_text('Grant access')
        expect(graph_consent_message.locator('.msgraph-test-access-btn')).to_have_text('Test access')

        powerpoint_button.click()
        expect(powerpoint_button).to_have_text('Creating PowerPoint Presentation...')
        expect(powerpoint_button).to_be_disabled()
        expect(powerpoint_button).to_have_text('Create PowerPoint Presentation')
        assert export_requests[0]["message_id"] == "assistant-presentation-response"
        assert export_requests[0]["conversation_id"] == "inline-export-actions-test"
        assert export_requests[0]["artifact_message_id"] == "generated-markdown-presentation"

        historical_message = page.locator('[data-message-id="assistant-historical-response"]')
        historical_actions = historical_message.locator('.inline-assistant-export-actions')
        expect(historical_actions).to_be_visible()
        expect(historical_actions.locator('button')).to_have_count(1)
        expect(historical_actions.locator('button', has_text='Create Word Document')).to_be_visible()
        expect(historical_actions.locator('button', has_text='Create PowerPoint Presentation')).to_have_count(0)

        markdown_message = page.locator('[data-message-id="assistant-markdown-response"]')
        markdown_actions = markdown_message.locator('.inline-assistant-export-actions')
        expect(markdown_actions).to_be_visible()
        expect(markdown_actions.locator('button')).to_have_count(1)
        expect(markdown_actions.locator('button', has_text='Create Markdown Document')).to_be_visible()

        deck_message = page.locator('[data-message-id="assistant-deck-response"]')
        deck_actions = deck_message.locator('.inline-assistant-export-actions')
        expect(deck_actions).to_be_visible()
        expect(deck_actions.locator('button')).to_have_count(1)
        expect(deck_actions.locator('button', has_text='Create PowerPoint Presentation')).to_be_visible()

        no_export_message = page.locator('[data-message-id="assistant-no-export-response"]')
        expect(no_export_message.locator('.inline-assistant-export-actions')).to_have_count(0)
    finally:
        context.close()
        browser.close()


@pytest.mark.ui
def test_streaming_assistant_reply_hides_export_actions_until_complete(playwright):
    """Validate streaming placeholders do not show create/export controls."""
    _require_ui_env()

    browser = playwright.chromium.launch()
    context = browser.new_context(
        storage_state=STORAGE_STATE,
        viewport={"width": 1440, "height": 900},
    )
    page = context.new_page()

    page.route(
        "**/api/user/settings",
        lambda route: _fulfill_json(route, {"selected_agent": None, "settings": {"enable_agents": False}}),
    )
    page.route("**/api/get_conversations", lambda route: _fulfill_json(route, {"conversations": []}))

    try:
        page.goto(f"{BASE_URL}/chats", wait_until="domcontentloaded")
        page.wait_for_selector("#chatbox")
        page.wait_for_function("() => window.chatMessages && typeof window.chatMessages.extractSuggestedFollowUpPrompts === 'function'")

        page.evaluate(
            """
            async () => {
                const conversationId = 'streaming-export-actions-test';
                currentConversationId = conversationId;
                window.currentConversationId = conversationId;

                const messagesModule = await import('/static/js/chat/chat-messages.js');
                messagesModule.appendMessage(
                    'You',
                    'Please create a PowerPoint deck for this analysis.',
                    null,
                    'user-streaming-export-request',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    {
                        id: 'user-streaming-export-request',
                        role: 'user',
                        content: 'Please create a PowerPoint deck for this analysis.',
                    },
                    true
                );

                messagesModule.appendMessage(
                    'AI',
                    '<span class="text-muted"><i class="bi bi-three-dots-vertical"></i> Streaming...</span>',
                    null,
                    'temp_ai_streaming-export-actions',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    null,
                    false
                );
            }
            """
        )

        streaming_message = page.locator('[data-message-id="temp_ai_streaming-export-actions"]')
        expect(streaming_message).to_be_visible()
        expect(streaming_message.locator('.inline-assistant-export-actions')).to_have_count(0)
        expect(streaming_message.locator('.dropdown-export-md-btn')).to_have_count(0)
        expect(streaming_message.locator('.dropdown-export-word-btn')).to_have_count(0)
        expect(streaming_message.locator('.dropdown-export-ppt-btn')).to_have_count(0)
        expect(streaming_message.locator('.dropdown-open-email-btn')).to_have_count(0)

        page.evaluate(
            """
            async () => {
                document.querySelector('[data-message-id="temp_ai_streaming-export-actions"]')?.remove();

                const messagesModule = await import('/static/js/chat/chat-messages.js');
                messagesModule.appendMessage(
                    'AI',
                    'The PowerPoint deck is ready.',
                    null,
                    'assistant-complete-export-response',
                    false,
                    [],
                    [],
                    [],
                    null,
                    null,
                    {
                        id: 'assistant-complete-export-response',
                        role: 'assistant',
                        content: 'The PowerPoint deck is ready.',
                    },
                    true
                );
            }
            """
        )

        completed_message = page.locator('[data-message-id="assistant-complete-export-response"]')
        expect(completed_message.locator('.inline-assistant-export-actions')).to_be_visible()
        expect(completed_message.locator('.inline-export-ppt-btn')).to_be_visible()
        expect(completed_message.locator('.dropdown-export-ppt-btn')).to_have_count(1)
    finally:
        context.close()
        browser.close()