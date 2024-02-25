import httpx
from typing import Optional, List, Iterator, Dict, Any, Union, Tuple

from phi.llm.base import LLM
from phi.llm.message import Message
from phi.tools.function import FunctionCall
from phi.utils.log import logger
from phi.utils.timer import Timer
from phi.utils.functions import get_function_call
from phi.utils.tools import get_function_call_for_tool_call

try:
    from openai import OpenAI as OpenAIClient
    from openai.types.completion_usage import CompletionUsage
    from openai.types.chat.chat_completion import ChatCompletion
    from openai.types.chat.chat_completion_chunk import (
        ChatCompletionChunk,
        ChoiceDelta,
        ChoiceDeltaFunctionCall,
        ChoiceDeltaToolCall,
    )
    from openai.types.chat.chat_completion_message import (
        ChatCompletionMessage,
        FunctionCall as ChatCompletionFunctionCall,
    )
    from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall
except ImportError:
    logger.error("`openai` not installed")
    raise


class Gemini(LLM):
    name: str = "OpenAIChat"
    model: str = "gpt-4-1106-preview"
    seed: Optional[int] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    response_format: Optional[Dict[str, Any]] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    stop: Optional[Union[str, List[str]]] = None
    user: Optional[str] = None
    top_p: Optional[float] = None
    logit_bias: Optional[Any] = None
    headers: Optional[Dict[str, Any]] = None
    api_key: Optional[str] = None
    organization: Optional[str] = None
    base_url: Optional[Union[str, httpx.URL]] = None
    client_kwargs: Optional[Dict[str, Any]] = None
    openai_client: Optional[OpenAIClient] = None

    @property
    def client(self) -> OpenAIClient:
        if self.openai_client:
            return self.openai_client

        _openai_params: Dict[str, Any] = {}
        if self.api_key:
            _openai_params["api_key"] = self.api_key
        if self.organization:
            _openai_params["organization"] = self.organization
        if self.base_url:
            _openai_params["base_url"] = self.base_url
        if self.client_kwargs:
            _openai_params.update(self.client_kwargs)
        return OpenAIClient(**_openai_params)

    @property
    def api_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if self.seed:
            kwargs["seed"] = self.seed
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens
        if self.temperature:
            kwargs["temperature"] = self.temperature
        if self.response_format:
            kwargs["response_format"] = self.response_format
        if self.frequency_penalty:
            kwargs["frequency_penalty"] = self.frequency_penalty
        if self.presence_penalty:
            kwargs["presence_penalty"] = self.presence_penalty
        if self.stop:
            kwargs["stop"] = self.stop
        if self.user:
            kwargs["user"] = self.user
        if self.top_p:
            kwargs["top_p"] = self.top_p
        if self.logit_bias:
            kwargs["logit_bias"] = self.logit_bias
        if self.headers:
            kwargs["headers"] = self.headers
        if self.tools:
            kwargs["tools"] = self.get_tools_for_api()
            if self.tool_choice is None:
                kwargs["tool_choice"] = "auto"
            else:
                kwargs["tool_choice"] = self.tool_choice
        return kwargs

    def to_dict(self) -> Dict[str, Any]:
        _dict = super().to_dict()
        if self.seed:
            _dict["seed"] = self.seed
        if self.max_tokens:
            _dict["max_tokens"] = self.max_tokens
        if self.temperature:
            _dict["temperature"] = self.temperature
        if self.response_format:
            _dict["response_format"] = self.response_format
        if self.frequency_penalty:
            _dict["frequency_penalty"] = self.frequency_penalty
        if self.presence_penalty:
            _dict["presence_penalty"] = self.presence_penalty
        if self.stop:
            _dict["stop"] = self.stop
        if self.user:
            _dict["user"] = self.user
        if self.top_p:
            _dict["top_p"] = self.top_p
        if self.logit_bias:
            _dict["logit_bias"] = self.logit_bias
        return _dict

    def invoke(self, messages: List[Message]) -> ChatCompletion:
        return self.client.chat.completions.create(
            model=self.model,
            messages=[m.to_dict() for m in messages],  # type: ignore
            **self.api_kwargs,
        )

    def invoke_stream(self, messages: List[Message]) -> Iterator[ChatCompletionChunk]:
        yield from self.client.chat.completions.create(
            model=self.model,
            messages=[m.to_dict() for m in messages],  # type: ignore
            stream=True,
            **self.api_kwargs,
        )  # type: ignore

    def run_function(self, function_call: Dict[str, Any]) -> Tuple[Message, Optional[FunctionCall]]:
        _function_name = function_call.get("name")
        _function_arguments_str = function_call.get("arguments")
        if _function_name is not None:
            # Get function call
            _function_call = get_function_call(
                name=_function_name, arguments=_function_arguments_str, functions=self.functions
            )
            if _function_call is None:
                return Message(role="function", content="Could not find function to call."), None
            if _function_call.error is not None:
                return Message(role="function", content=_function_call.error), _function_call

            if self.function_call_stack is None:
                self.function_call_stack = []

            # -*- Check function call limit
            if len(self.function_call_stack) > self.function_call_limit:
                self.tool_choice = "none"
                return Message(
                    role="function", content=f"Function call limit ({self.function_call_limit}) exceeded."
                ), _function_call

            # -*- Run function call
            self.function_call_stack.append(_function_call)
            _function_call_timer = Timer()
            _function_call_timer.start()
            _function_call.execute()
            _function_call_timer.stop()
            _function_call_message = Message(
                role="function",
                name=_function_call.function.name,
                content=_function_call.result,
                metrics={"time": _function_call_timer.elapsed},
            )
            if "function_call_times" not in self.metrics:
                self.metrics["function_call_times"] = {}
            if _function_call.function.name not in self.metrics["function_call_times"]:
                self.metrics["function_call_times"][_function_call.function.name] = []
            self.metrics["function_call_times"][_function_call.function.name].append(_function_call_timer.elapsed)
            return _function_call_message, _function_call
        return Message(role="function", content="Function name is None."), None

    def response(self, messages: List[Message]) -> str:
        logger.debug("---------- OpenAI Response Start ----------")
        # -*- Log messages for debugging
        for m in messages:
            m.log()

        response_timer = Timer()
        response_timer.start()
        response: ChatCompletion = self.invoke(messages=messages)
        response_timer.stop()
        logger.debug(f"Time to generate response: {response_timer.elapsed:.4f}s")
        # logger.debug(f"OpenAI response type: {type(response)}")
        # logger.debug(f"OpenAI response: {response}")

        # -*- Parse response
        response_message: ChatCompletionMessage = response.choices[0].message
        response_role = response_message.role
        response_content: Optional[str] = response_message.content
        response_function_call: Optional[ChatCompletionFunctionCall] = response_message.function_call
        response_tool_calls: Optional[List[ChatCompletionMessageToolCall]] = response_message.tool_calls

        # -*- Create assistant message
        assistant_message = Message(
            role=response_role or "assistant",
            content=response_content,
        )
        if response_function_call is not None:
            assistant_message.function_call = response_function_call.model_dump()
        if response_tool_calls is not None:
            assistant_message.tool_calls = [t.model_dump() for t in response_tool_calls]

        # -*- Update usage metrics
        # Add response time to metrics
        assistant_message.metrics["time"] = response_timer.elapsed
        if "response_times" not in self.metrics:
            self.metrics["response_times"] = []
        self.metrics["response_times"].append(response_timer.elapsed)

        # Add token usage to metrics
        response_usage: Optional[CompletionUsage] = response.usage
        prompt_tokens = response_usage.prompt_tokens if response_usage is not None else None
        if prompt_tokens is not None:
            assistant_message.metrics["prompt_tokens"] = prompt_tokens
            if "prompt_tokens" not in self.metrics:
                self.metrics["prompt_tokens"] = prompt_tokens
            else:
                self.metrics["prompt_tokens"] += prompt_tokens
        completion_tokens = response_usage.completion_tokens if response_usage is not None else None
        if completion_tokens is not None:
            assistant_message.metrics["completion_tokens"] = completion_tokens
            if "completion_tokens" not in self.metrics:
                self.metrics["completion_tokens"] = completion_tokens
            else:
                self.metrics["completion_tokens"] += completion_tokens
        total_tokens = response_usage.total_tokens if response_usage is not None else None
        if total_tokens is not None:
            assistant_message.metrics["total_tokens"] = total_tokens
            if "total_tokens" not in self.metrics:
                self.metrics["total_tokens"] = total_tokens
            else:
                self.metrics["total_tokens"] += total_tokens

        # -*- Add assistant message to messages
        messages.append(assistant_message)
        assistant_message.log()

        # -*- Parse and run function call
        need_to_run_functions = assistant_message.function_call is not None or assistant_message.tool_calls is not None
        if need_to_run_functions and self.run_tools:
            if assistant_message.function_call is not None:
                function_call_message, function_call = self.run_function(function_call=assistant_message.function_call)
                messages.append(function_call_message)
                # -*- Get new response using result of function call
                final_response = ""
                if self.show_tool_calls and function_call is not None:
                    final_response += f"\n - Running: {function_call.get_call_str()}\n\n"
                final_response += self.response(messages=messages)
                return final_response
            elif assistant_message.tool_calls is not None:
                final_response = ""
                function_calls_to_run: List[FunctionCall] = []
                for tool_call in assistant_message.tool_calls:
                    _tool_call_id = tool_call.get("id")
                    _function_call = get_function_call_for_tool_call(tool_call, self.functions)
                    if _function_call is None:
                        messages.append(
                            Message(role="tool", tool_call_id=_tool_call_id, content="Could not find function to call.")
                        )
                        continue
                    if _function_call.error is not None:
                        messages.append(Message(role="tool", tool_call_id=_tool_call_id, content=_function_call.error))
                        continue
                    function_calls_to_run.append(_function_call)

                if self.show_tool_calls:
                    if len(function_calls_to_run) == 1:
                        final_response += f"\n - Running: {function_calls_to_run[0].get_call_str()}\n\n"
                    elif len(function_calls_to_run) > 1:
                        final_response += "\nRunning:"
                        for _f in function_calls_to_run:
                            final_response += f"\n - {_f.get_call_str()}"
                        final_response += "\n\n"

                function_call_results = self.run_function_calls(function_calls_to_run)
                if len(function_call_results) > 0:
                    messages.extend(function_call_results)
                # -*- Get new response using result of tool call
                final_response += self.response(messages=messages)
                return final_response
        logger.debug("---------- OpenAI Response End ----------")
        # -*- Return content if no function calls are present
        if assistant_message.content is not None:
            return assistant_message.get_content_string()
        return "Something went wrong, please try again."

    def generate(self, messages: List[Message]) -> Dict:
        logger.debug("---------- OpenAI Response Start ----------")
        # -*- Log messages for debugging
        for m in messages:
            m.log()

        response_timer = Timer()
        response_timer.start()
        response: ChatCompletion = self.invoke(messages=messages)
        response_timer.stop()
        logger.debug(f"Time to generate response: {response_timer.elapsed:.4f}s")
        # logger.debug(f"OpenAI response type: {type(response)}")
        # logger.debug(f"OpenAI response: {response}")

        # -*- Parse response
        response_message: ChatCompletionMessage = response.choices[0].message
        response_role = response_message.role
        response_content: Optional[str] = response_message.content
        response_function_call: Optional[ChatCompletionFunctionCall] = response_message.function_call
        response_tool_calls: Optional[List[ChatCompletionMessageToolCall]] = response_message.tool_calls

        # -*- Create assistant message
        assistant_message = Message(
            role=response_role or "assistant",
            content=response_content,
        )
        if response_function_call is not None:
            assistant_message.function_call = response_function_call.model_dump()
        if response_tool_calls is not None:
            assistant_message.tool_calls = [t.model_dump() for t in response_tool_calls]

        # -*- Update usage metrics
        # Add response time to metrics
        assistant_message.metrics["time"] = response_timer.elapsed
        if "response_times" not in self.metrics:
            self.metrics["response_times"] = []
        self.metrics["response_times"].append(response_timer.elapsed)

        # Add token usage to metrics
        response_usage: Optional[CompletionUsage] = response.usage
        prompt_tokens = response_usage.prompt_tokens if response_usage is not None else None
        if prompt_tokens is not None:
            assistant_message.metrics["prompt_tokens"] = prompt_tokens
            if "prompt_tokens" not in self.metrics:
                self.metrics["prompt_tokens"] = prompt_tokens
            else:
                self.metrics["prompt_tokens"] += prompt_tokens
        completion_tokens = response_usage.completion_tokens if response_usage is not None else None
        if completion_tokens is not None:
            assistant_message.metrics["completion_tokens"] = completion_tokens
            if "completion_tokens" not in self.metrics:
                self.metrics["completion_tokens"] = completion_tokens
            else:
                self.metrics["completion_tokens"] += completion_tokens
        total_tokens = response_usage.total_tokens if response_usage is not None else None
        if total_tokens is not None:
            assistant_message.metrics["total_tokens"] = total_tokens
            if "total_tokens" not in self.metrics:
                self.metrics["total_tokens"] = total_tokens
            else:
                self.metrics["total_tokens"] += total_tokens

        # -*- Add assistant message to messages
        messages.append(assistant_message)
        assistant_message.log()

        # -*- Return response
        response_message_dict = response_message.model_dump()
        logger.debug("---------- OpenAI Response End ----------")
        return response_message_dict

    def response_stream(self, messages: List[Message]) -> Iterator[str]:
        logger.debug("---------- OpenAI Response Start ----------")
        # -*- Log messages for debugging
        for m in messages:
            m.log()

        assistant_message_content = ""
        assistant_message_function_name = ""
        assistant_message_function_arguments_str = ""
        assistant_message_tool_calls: Optional[List[ChoiceDeltaToolCall]] = None
        completion_tokens = 0
        response_timer = Timer()
        response_timer.start()
        for response in self.invoke_stream(messages=messages):
            completion_tokens += 1

            # -*- Parse response
            response_delta: ChoiceDelta = response.choices[0].delta
            response_content: Optional[str] = response_delta.content
            response_function_call: Optional[ChoiceDeltaFunctionCall] = response_delta.function_call
            response_tool_calls: Optional[List[ChoiceDeltaToolCall]] = response_delta.tool_calls

            # -*- Return content if present, otherwise get function call
            if response_content is not None:
                assistant_message_content += response_content
                yield response_content

            # -*- Parse function call
            if response_function_call is not None:
                _function_name_stream = response_function_call.name
                if _function_name_stream is not None:
                    assistant_message_function_name += _function_name_stream
                _function_args_stream = response_function_call.arguments
                if _function_args_stream is not None:
                    assistant_message_function_arguments_str += _function_args_stream

            # -*- Parse tool calls
            if response_tool_calls is not None:
                if assistant_message_tool_calls is None:
                    assistant_message_tool_calls = []
                assistant_message_tool_calls.extend(response_tool_calls)

        response_timer.stop()
        logger.debug(f"Time to generate response: {response_timer.elapsed:.4f}s")

        # -*- Create assistant message
        assistant_message = Message(role="assistant")
        # -*- Add content to assistant message
        if assistant_message_content != "":
            assistant_message.content = assistant_message_content
        # -*- Add function call to assistant message
        if assistant_message_function_name != "":
            assistant_message.function_call = {
                "name": assistant_message_function_name,
                "arguments": assistant_message_function_arguments_str,
            }
        # -*- Add tool calls to assistant message
        if assistant_message_tool_calls is not None:
            # Build tool calls
            tool_calls: List[Dict[str, Any]] = []
            for _tool_call in assistant_message_tool_calls:
                _index = _tool_call.index
                _tool_call_id = _tool_call.id
                _tool_call_type = _tool_call.type
                _tool_call_function_name = _tool_call.function.name if _tool_call.function is not None else None
                _tool_call_function_arguments_str = (
                    _tool_call.function.arguments if _tool_call.function is not None else None
                )

                tool_call_at_index = tool_calls[_index] if len(tool_calls) > _index else None
                if tool_call_at_index is None:
                    tool_call_at_index_function_dict = (
                        {
                            "name": _tool_call_function_name,
                            "arguments": _tool_call_function_arguments_str,
                        }
                        if _tool_call_function_name is not None or _tool_call_function_arguments_str is not None
                        else None
                    )
                    tool_call_at_index_dict = {
                        "id": _tool_call.id,
                        "type": _tool_call_type,
                        "function": tool_call_at_index_function_dict,
                    }
                    tool_calls.insert(_index, tool_call_at_index_dict)
                else:
                    if _tool_call_function_name is not None:
                        tool_call_at_index["function"]["name"] += _tool_call_function_name
                    if _tool_call_function_arguments_str is not None:
                        tool_call_at_index["function"]["arguments"] += _tool_call_function_arguments_str
                    if _tool_call_id is not None:
                        tool_call_at_index["id"] = _tool_call_id
                    if _tool_call_type is not None:
                        tool_call_at_index["type"] = _tool_call_type
            assistant_message.tool_calls = tool_calls

        # -*- Update usage metrics
        # Add response time to metrics
        assistant_message.metrics["time"] = response_timer.elapsed
        if "response_times" not in self.metrics:
            self.metrics["response_times"] = []
        self.metrics["response_times"].append(response_timer.elapsed)

        # Add token usage to metrics
        # TODO: compute prompt tokens
        prompt_tokens = 0
        assistant_message.metrics["prompt_tokens"] = prompt_tokens
        if "prompt_tokens" not in self.metrics:
            self.metrics["prompt_tokens"] = prompt_tokens
        else:
            self.metrics["prompt_tokens"] += prompt_tokens
        logger.debug(f"Estimated completion tokens: {completion_tokens}")
        assistant_message.metrics["completion_tokens"] = completion_tokens
        if "completion_tokens" not in self.metrics:
            self.metrics["completion_tokens"] = completion_tokens
        else:
            self.metrics["completion_tokens"] += completion_tokens
        total_tokens = prompt_tokens + completion_tokens
        assistant_message.metrics["total_tokens"] = total_tokens
        if "total_tokens" not in self.metrics:
            self.metrics["total_tokens"] = total_tokens
        else:
            self.metrics["total_tokens"] += total_tokens

        # -*- Add assistant message to messages
        messages.append(assistant_message)
        assistant_message.log()

        # -*- Parse and run function call
        need_to_run_functions = assistant_message.function_call is not None or assistant_message.tool_calls is not None
        if need_to_run_functions and self.run_tools:
            if assistant_message.function_call is not None:
                function_call_message, function_call = self.run_function(function_call=assistant_message.function_call)
                messages.append(function_call_message)
                if self.show_tool_calls and function_call is not None:
                    yield f"\n - Running: {function_call.get_call_str()}\n\n"
                # -*- Yield new response using result of function call
                yield from self.response_stream(messages=messages)
            elif assistant_message.tool_calls is not None:
                function_calls_to_run: List[FunctionCall] = []
                for tool_call in assistant_message.tool_calls:
                    _tool_call_id = tool_call.get("id")
                    _function_call = get_function_call_for_tool_call(tool_call, self.functions)
                    if _function_call is None:
                        messages.append(
                            Message(role="tool", tool_call_id=_tool_call_id, content="Could not find function to call.")
                        )
                        continue
                    if _function_call.error is not None:
                        messages.append(Message(role="tool", tool_call_id=_tool_call_id, content=_function_call.error))
                        continue
                    function_calls_to_run.append(_function_call)

                if self.show_tool_calls:
                    if len(function_calls_to_run) == 1:
                        yield f"\n - Running: {function_calls_to_run[0].get_call_str()}\n\n"
                    elif len(function_calls_to_run) > 1:
                        yield "\nRunning:"
                        for _f in function_calls_to_run:
                            yield f"\n - {_f.get_call_str()}"
                        yield "\n\n"

                function_call_results = self.run_function_calls(function_calls_to_run)
                if len(function_call_results) > 0:
                    messages.extend(function_call_results)
                # -*- Yield new response using results of tool calls
                yield from self.response_stream(messages=messages)
        logger.debug("---------- OpenAI Response End ----------")

    def generate_stream(self, messages: List[Message]) -> Iterator[Dict]:
        logger.debug("---------- OpenAI Response Start ----------")
        # -*- Log messages for debugging
        for m in messages:
            m.log()

        assistant_message_content = ""
        assistant_message_function_name = ""
        assistant_message_function_arguments_str = ""
        assistant_message_tool_calls: Optional[List[ChoiceDeltaToolCall]] = None
        completion_tokens = 0
        response_timer = Timer()
        response_timer.start()
        for response in self.invoke_stream(messages=messages):
            # logger.debug(f"OpenAI response type: {type(response)}")
            # logger.debug(f"OpenAI response: {response}")
            completion_tokens += 1

            # -*- Parse response
            response_delta: ChoiceDelta = response.choices[0].delta

            # -*- Read content
            response_content: Optional[str] = response_delta.content
            if response_content is not None:
                assistant_message_content += response_content

            # -*- Parse function call
            response_function_call: Optional[ChoiceDeltaFunctionCall] = response_delta.function_call
            if response_function_call is not None:
                _function_name_stream = response_function_call.name
                if _function_name_stream is not None:
                    assistant_message_function_name += _function_name_stream
                _function_args_stream = response_function_call.arguments
                if _function_args_stream is not None:
                    assistant_message_function_arguments_str += _function_args_stream

            # -*- Parse tool calls
            response_tool_calls: Optional[List[ChoiceDeltaToolCall]] = response_delta.tool_calls
            if response_tool_calls is not None:
                if assistant_message_tool_calls is None:
                    assistant_message_tool_calls = []
                assistant_message_tool_calls.extend(response_tool_calls)

            yield response_delta.model_dump()

        response_timer.stop()
        logger.debug(f"Time to generate response: {response_timer.elapsed:.4f}s")

        # -*- Create assistant message
        assistant_message = Message(role="assistant")
        # -*- Add content to assistant message
        if assistant_message_content != "":
            assistant_message.content = assistant_message_content
        # -*- Add function call to assistant message
        if assistant_message_function_name != "":
            assistant_message.function_call = {
                "name": assistant_message_function_name,
                "arguments": assistant_message_function_arguments_str,
            }
        # -*- Add tool calls to assistant message
        if assistant_message_tool_calls is not None:
            # Build tool calls
            tool_calls: List[Dict[str, Any]] = []
            for tool_call in assistant_message_tool_calls:
                _index = tool_call.index
                _tool_call_id = tool_call.id
                _tool_call_type = tool_call.type
                _tool_call_function_name = tool_call.function.name if tool_call.function is not None else None
                _tool_call_function_arguments_str = (
                    tool_call.function.arguments if tool_call.function is not None else None
                )

                tool_call_at_index = tool_calls[_index] if len(tool_calls) > _index else None
                if tool_call_at_index is None:
                    tool_call_at_index_function_dict = (
                        {
                            "name": _tool_call_function_name,
                            "arguments": _tool_call_function_arguments_str,
                        }
                        if _tool_call_function_name is not None or _tool_call_function_arguments_str is not None
                        else None
                    )
                    tool_call_at_index_dict = {
                        "id": tool_call.id,
                        "type": _tool_call_type,
                        "function": tool_call_at_index_function_dict,
                    }
                    tool_calls.insert(_index, tool_call_at_index_dict)
                else:
                    if _tool_call_function_name is not None:
                        tool_call_at_index["function"]["name"] += _tool_call_function_name
                    if _tool_call_function_arguments_str is not None:
                        tool_call_at_index["function"]["arguments"] += _tool_call_function_arguments_str
                    if _tool_call_id is not None:
                        tool_call_at_index["id"] = _tool_call_id
                    if _tool_call_type is not None:
                        tool_call_at_index["type"] = _tool_call_type
            assistant_message.tool_calls = tool_calls

        # -*- Update usage metrics
        # Add response time to metrics
        assistant_message.metrics["time"] = response_timer.elapsed
        if "response_times" not in self.metrics:
            self.metrics["response_times"] = []
        self.metrics["response_times"].append(response_timer.elapsed)

        # Add token usage to metrics
        # TODO: compute prompt tokens
        prompt_tokens = 0
        assistant_message.metrics["prompt_tokens"] = prompt_tokens
        if "prompt_tokens" not in self.metrics:
            self.metrics["prompt_tokens"] = prompt_tokens
        else:
            self.metrics["prompt_tokens"] += prompt_tokens
        logger.debug(f"Estimated completion tokens: {completion_tokens}")
        assistant_message.metrics["completion_tokens"] = completion_tokens
        if "completion_tokens" not in self.metrics:
            self.metrics["completion_tokens"] = completion_tokens
        else:
            self.metrics["completion_tokens"] += completion_tokens

        total_tokens = prompt_tokens + completion_tokens
        assistant_message.metrics["total_tokens"] = total_tokens
        if "total_tokens" not in self.metrics:
            self.metrics["total_tokens"] = total_tokens
        else:
            self.metrics["total_tokens"] += total_tokens

        # -*- Add assistant message to messages
        messages.append(assistant_message)
        assistant_message.log()
        logger.debug("---------- OpenAI Response End ----------")