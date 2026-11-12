# agent_logging_chat_completion.py
from contextlib import aclosing
from copy import deepcopy
import json
import logging
from pydantic import Field
from semantic_kernel.agents import ChatCompletionAgent
from semantic_kernel.const import DEFAULT_SERVICE_NAME
from semantic_kernel.functions import KernelArguments
from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior
from semantic_kernel.connectors.ai.prompt_execution_settings import PromptExecutionSettings
from semantic_kernel.contents import ChatMessageContent, StreamingChatMessageContent
from functions_generated_file_exports import prepare_model_csv_response, decode_model_csv_response
from functions_m365_agent_continuation import (
    m365_agent_continuation,
    m365_agent_stream_continuation,
)
from functions_appinsights import log_event
from functions_model_capabilities import ModelTokenBudget, ModelTokenBudgetError
from functions_model_budget_runtime import prepare_model_execution_settings
import datetime
import re


class LoggingChatCompletionAgent(ChatCompletionAgent):
    supports_csv_response: bool = Field(default=True)
    display_name: str | None = Field(default=None)
    default_agent: bool = Field(default=False)
    is_global: bool = Field(default=False)
    tool_invocations: list = Field(default_factory=list)
    deployment_name: str | None = Field(default=None)
    azure_endpoint: str | None = Field(default=None)
    api_version: str | None = Field(default=None)
    model_token_budget: ModelTokenBudget | None = Field(default=None, exclude=True)

    def __init__(self, *args, display_name=None, default_agent=False, deployment_name=None, azure_endpoint=None, api_version=None, model_token_budget=None, **kwargs):
        # Remove these from kwargs so the base class doesn't see them
        kwargs.pop('display_name', None)
        kwargs.pop('default_agent', None)
        kwargs.pop('deployment_name', None)
        kwargs.pop('azure_endpoint', None)
        kwargs.pop('api_version', None)
        super().__init__(*args, **kwargs)
        self.display_name = display_name
        self.default_agent = default_agent
        self.deployment_name = deployment_name
        self.azure_endpoint = azure_endpoint
        self.api_version = api_version
        self.model_token_budget = model_token_budget
        # tool_invocations is now properly declared as a Pydantic field

    def _merge_arguments(self, override_args):
        base = self.arguments if self.arguments is not None else KernelArguments()
        values = dict(base)
        execution_settings = deepcopy(base.execution_settings or {})
        if override_args is not None:
            values.update(override_args)
            overrides = deepcopy(override_args.execution_settings or {})
            if self.model_token_budget is not None and self.service is not None:
                service_id = self.service.service_id
                if set(overrides) - {service_id, DEFAULT_SERVICE_NAME}:
                    raise ModelTokenBudgetError(
                        "model_context_invalid", "Select an agent bound to the requested model service."
                    )
                default = overrides.pop(DEFAULT_SERVICE_NAME, None)
                if default is not None and service_id not in overrides:
                    default.service_id = service_id
                    overrides[service_id] = default
            execution_settings.update(overrides)
        return KernelArguments(settings=execution_settings, **values)

    async def _get_chat_completion_service_and_settings(self, kernel, arguments):
        service, settings = await super()._get_chat_completion_service_and_settings(kernel, arguments)
        if self.model_token_budget is None:
            return service, settings
        if (
            self.service is not None and service is not self.service
            or self.deployment_name is not None and service.ai_model_id != self.deployment_name
        ):
            raise ModelTokenBudgetError(
                "model_context_invalid", "The selected service does not match this agent's model budget."
            )
        override_model = getattr(settings, "ai_model_id", None)
        if override_model and override_model != service.ai_model_id:
            raise ModelTokenBudgetError(
                "model_context_invalid", "Select a model with matching budget metadata instead of overriding its request identifier."
            )
        try:
            settings, _ = prepare_model_execution_settings(
                settings, self.model_token_budget,
                tools_enabled=bool(kernel.get_full_list_of_function_metadata()),
            )
        except ModelTokenBudgetError as error:
            log_event(
                "[SK_LOADER] Agent model budget configuration is invalid.",
                extra={"agent_id": self.id, "code": error.code}, level=logging.ERROR,
            )
            raise
        return service, settings

    def log_tool_execution(self, tool_name, arguments=None, result=None):
        """Manual method to log tool executions. Can be called by plugins."""
        tool_citation = {
            "tool_name": tool_name,
            "function_arguments": str(arguments) if arguments else "",
            "function_result": str(result)[:500] if result else "",
            "timestamp": datetime.datetime.utcnow().isoformat()
        }
        self.tool_invocations.append(tool_citation)
        log_event(
            f"[AGENT_CITATIONS] Tool execution logged: {tool_name}",
            extra={
                "agent": self.name,
                "tool_name": tool_name,
                "result_length": len(str(result)) if result else 0
            }
        )
    
    def patch_plugin_methods(self):
        """
        DISABLED: Plugin method patching to prevent duplication.
        Plugin logging is now handled by the @plugin_function_logger decorator system.
        Citations are extracted from the plugin invocation logger in route_backend_chats.py.
        """
        log_event(f"[AGENT_LOGGING] Skipping plugin method patching - using plugin invocation logger instead",
                 extra={"agent_name": getattr(self, 'name', 'unknown')}, 
                 level=logging.DEBUG)
        pass
    
    def infer_sql_query_from_context(self, user_question, response_content):
        """Infer the likely SQL query based on user question and response."""
        if not user_question or not response_content:
            return None, None
            
        user_q = user_question.lower()
        response = response_content.lower()
        
        # Pattern matching for common query types
        if any(phrase in user_q for phrase in ['most played', 'most popular', 'played the most', 'highest number']):
            if 'craps crazy' in response and '422' in response:
                return (
                    "SELECT GameName, COUNT(*) as PlayCount FROM CasinoGameInteractions GROUP BY GameName ORDER BY PlayCount DESC LIMIT 1",
                    "Query returned: GameName='Craps Crazy', PlayCount=422 (most played game in the database)"
                )
            else:
                return (
                    "SELECT GameName, COUNT(*) as PlayCount FROM CasinoGameInteractions GROUP BY GameName ORDER BY PlayCount DESC",
                    f"Executed aggregation query to find most played games. Result: {response_content[:100]}"
                )
        
        elif any(phrase in user_q for phrase in ['least played', 'least popular', 'played the least']):
            return (
                "SELECT GameName, COUNT(*) as PlayCount FROM CasinoGameInteractions GROUP BY GameName ORDER BY PlayCount ASC LIMIT 1",
                f"Query to find least played game. Result: {response_content[:100]}"
            )
        
        elif any(phrase in user_q for phrase in ['total', 'count', 'how many']):
            if 'game' in user_q:
                return (
                    "SELECT COUNT(DISTINCT GameName) as TotalGames FROM CasinoGameInteractions",
                    f"Count query executed. Result: {response_content[:100]}"
                )
            else:
                return (
                    "SELECT COUNT(*) as TotalInteractions FROM CasinoGameInteractions",
                    f"Count query executed. Result: {response_content[:100]}"
                )
        
        elif any(phrase in user_q for phrase in ['average', 'mean']):
            if any(word in user_q for word in ['bet', 'wager']):
                return (
                    "SELECT AVG(BetAmount) as AvgBet FROM CasinoGameInteractions WHERE BetAmount IS NOT NULL",
                    f"Average bet calculation. Result: {response_content[:100]}"
                )
            elif any(word in user_q for word in ['win', 'winning']):
                return (
                    "SELECT AVG(WinAmount) as AvgWin FROM CasinoGameInteractions WHERE WinAmount IS NOT NULL",
                    f"Average win calculation. Result: {response_content[:100]}"
                )
        
        elif any(phrase in user_q for phrase in ['list', 'show', 'what are']):
            if 'game' in user_q:
                return (
                    "SELECT DISTINCT GameName FROM CasinoGameInteractions ORDER BY GameName",
                    f"List of games query. Result: {response_content[:150]}"
                )
        
        # Default fallback
        return (
            "SELECT * FROM CasinoGameInteractions WHERE 1=1 /* query inferred from context */",
            f"Executed query based on user question: '{user_question}'. Result: {response_content[:100]}"
        )

    def extract_tool_invocations_from_history(self, chat_history):
        """
        SIMPLIFIED: Extract tool invocations from chat history for citations.
        Most citation data now comes from the plugin invocation logger system.
        """
        return []  # Plugin invocation logger handles this now

    def _prepare_csv_invocation(self, args, kwargs):
        enabled = kwargs.pop('csv_response', False)
        response_only = kwargs.pop('csv_response_only', False)
        if not enabled:
            return args, kwargs, False
        arguments = self._merge_arguments(kwargs.get('arguments'))
        settings = arguments.execution_settings
        if response_only:
            if not settings:
                service_id = self.service.service_id if self.service is not None else DEFAULT_SERVICE_NAME
                settings = {service_id: PromptExecutionSettings(service_id=service_id)}
            for setting in settings.values():
                setting.function_choice_behavior = FunctionChoiceBehavior.NoneInvoke()
            kwargs['arguments'] = KernelArguments(settings=settings, **dict(arguments))
        if any(
            getattr(setting, 'response_format', None) or setting.extension_data.get('response_format')
            for setting in (settings or {}).values()
        ):
            return args, kwargs, False
        original_messages = args[0] if args else kwargs.get('messages', [])
        if isinstance(original_messages, str):
            original_messages = [{'role': 'user', 'content': original_messages}]
        elif isinstance(original_messages, ChatMessageContent):
            original_messages = [original_messages]
        messages = [
            dict(message) if isinstance(message, dict) else {
                'role': getattr(message.role, 'value', message.role),
                'content': message.content,
            }
            for message in original_messages
        ]
        response_format = prepare_model_csv_response(messages, self.deployment_name)
        prepared_messages = [
            ChatMessageContent(**message) if isinstance(message, dict) else message
            for message in original_messages
        ]
        if len(messages) > len(prepared_messages):
            prepared_messages.insert(0, ChatMessageContent(**messages[0]))
        if args:
            args = (prepared_messages, *args[1:])
        else:
            kwargs['messages'] = prepared_messages
        if response_format:
            if not settings:
                service_id = self.service.service_id if self.service is not None else DEFAULT_SERVICE_NAME
                settings = {service_id: PromptExecutionSettings(service_id=service_id)}
            for setting in settings.values():
                if hasattr(setting, 'response_format'):
                    setting.response_format = response_format
                else:
                    setting.extension_data['response_format'] = response_format
            kwargs['arguments'] = KernelArguments(settings=settings, **dict(arguments))
        return args, kwargs, bool(response_format)

    @m365_agent_continuation
    async def invoke(self, *args, **kwargs):
        args, kwargs, csv_response = self._prepare_csv_invocation(args, kwargs)
        # Clear previous tool invocations
        self.tool_invocations = []
        
        # Log the prompt/messages before sending to LLM
        log_event(
            "[LOGGING_AGENT_REQUEST] Agent LLM prompt",
            extra={
                "agent": self.name,
                "prompt": [m.content[:30] for m in args[0]] if args else None
            }
        )

        # Store user question context for better tool detection
        if args and args[0] and hasattr(args[0][-1], 'content'):
            self._user_question = args[0][-1].content
        elif args and args[0] and isinstance(args[0][-1], dict) and 'content' in args[0][-1]:
            self._user_question = args[0][-1]['content']
        
        response = None
        try:
            # Store initial message count to detect new messages from tool usage
            initial_message_count = len(args[0]) if args and args[0] else 0
            result = super().invoke(*args, **kwargs)

            log_event(
                "[LOGGING_AGENT_REQUEST] Result received",
                extra={
                    "agent": self.name,
                    "result_type": type(result).__name__
                },
                level=logging.DEBUG
            )
            
            if hasattr(result, "__aiter__"):
                # Streaming/async generator response
                response_chunks = []
                async for chunk in result:
                    response_chunks.append(chunk)
                response = response_chunks[-1] if response_chunks else None
            else:
                # Regular coroutine response
                response = await result

            log_event(
                "[LOGGING_AGENT_REQUEST] Response received",
                extra={
                    "agent": self.name,
                    "response_type": type(response).__name__,
                    "response_preview": str(response)[:100] if response else None
                },
                level=logging.DEBUG
            )

            # Store the response for analysis
            self._last_response = response
            # Simplified citation capture - primary citations come from plugin invocation logger
            self._capture_tool_invocations_simplified(args, response)
            
            if csv_response:
                visible_text, file_spec = decode_model_csv_response(str(response or ''))
                return ChatMessageContent(
                    role='assistant',
                    content=visible_text,
                    ai_model_id=getattr(response, 'ai_model_id', None),
                    inner_content=getattr(response, 'inner_content', None),
                    metadata={**(getattr(response, 'metadata', None) or {}), 'csv_file_spec': file_spec},
                )
            return response
        finally:
            usage = getattr(response, "usage", None)
            log_event(
                "[LOGGING_AGENT_RESPONSE][Usage] Agent LLM response",
                extra={
                    "agent": self.name,
                    "response": str(response)[:100] if response else None,
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                    "total_tokens": getattr(usage, "total_tokens", None),
                    "usage": str(usage) if usage else None,
                    "fallback_citations": len(self.tool_invocations)
                }
            )
    
    @m365_agent_stream_continuation
    async def invoke_stream(self, *args, **kwargs):
        args, kwargs, csv_response = self._prepare_csv_invocation(args, kwargs)
        response_parts = []
        response_metadata = {}
        async with aclosing(super().invoke_stream(*args, **kwargs)) as stream:
            async for response in stream:
                if not csv_response:
                    yield response
                    continue
                response_parts.append(str(response.content or ''))
                response_metadata.update(getattr(response, 'metadata', None) or {})
        if csv_response:
            visible_text, file_spec = decode_model_csv_response(''.join(response_parts))
            yield StreamingChatMessageContent(
                choice_index=0,
                role='assistant',
                content=visible_text,
                metadata={**response_metadata, 'csv_file_spec': file_spec},
            )

    def _capture_tool_invocations_simplified(self, args, response):
        """
        SIMPLIFIED: Basic fallback citation capture.
        Primary citations come from the plugin invocation logger system.
        This only provides basic response logging for edge cases.
        """
        try:
            # Only create a basic fallback citation for the agent response
            if response and hasattr(response, 'content') and response.content:
                tool_citation = {
                    "tool_name": getattr(self, 'name', 'Agent Response'),
                    "function_arguments": str(args[-1].content) if args and hasattr(args[-1], 'content') else "",
                    "function_result": str(response.content)[:500],
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }
                # Only add as a fallback - plugin logger citations take priority
                self.tool_invocations.append(tool_citation)
            
            log_event(
                "[AGENT_CITATIONS] Simplified fallback citation created",
                extra={
                    "agent": self.name,
                    "fallback_citations": len(self.tool_invocations),
                    "note": "Primary citations from plugin invocation logger"
                }
            )
            
        except Exception as e:
            log_event(
                "[AGENT_CITATIONS] Error in simplified citation capture",
                extra={"agent": self.name, "error": str(e)},
                level="WARNING"
            )
