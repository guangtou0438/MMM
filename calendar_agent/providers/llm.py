"""Streaming OpenAI-compatible adapter with explicit provider selection."""
import json
import os
import threading
import httpx
from ..llm.settings import PROVIDERS, offline_mode

MODEL_SLOTS = threading.BoundedSemaphore(2)
MODELS = {'glm': 'zhipu', 'deepseek': 'deepseek'}


class FunctionCallProtocolError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class LLMProvider:
    def _stream(self, messages, model, max_tokens=None, tools=None):
        if offline_mode():
            raise RuntimeError('离线模式：未调用真实模型')
        spec = PROVIDERS[MODELS[model]]
        key = next((os.getenv(k) for k in spec.key_envs if os.getenv(k)), '')
        if not key:
            raise RuntimeError('所选模型未配置 API Key')
        base = next((os.getenv(k) for k in spec.base_url_envs if os.getenv(k)), spec.base_url)
        name = next((os.getenv(k) for k in spec.model_envs if os.getenv(k)), spec.default_model)
        body = {'model': name, 'messages': messages, 'stream': True,
                'stream_options': {'include_usage': True}}
        if tools:
            body.update(tools=tools, tool_choice='auto')
        else:
            body['response_format'] = {'type': 'json_object'}
        if max_tokens is not None:
            body['max_tokens'] = max_tokens
        with httpx.Client(timeout=httpx.Timeout(45, connect=10)) as client:
            with client.stream('POST', base.rstrip('/') + '/chat/completions', json=body,
                               headers={'Authorization': 'Bearer ' + key}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith('data: ') or line == 'data: [DONE]':
                        continue
                    data = json.loads(line[6:])
                    if data.get('usage'):
                        yield 'usage', data['usage'].get('total_tokens', 0)
                    for choice in data.get('choices', []):
                        delta = choice.get('delta', {})
                        yield 'delta', delta
                        if choice.get('finish_reason'):
                            yield 'finish', choice['finish_reason']

    def stream(self, messages, model, max_tokens=None):
        for kind, value in self._stream(messages, model, max_tokens):
            if kind == 'usage':
                yield kind, value
            elif kind == 'delta' and value.get('content'):
                yield 'content', value['content']

    def stream_functions(self, messages, model, tools, max_tokens=None):
        """Collect native streamed tool arguments. Never parse content as a call."""
        calls, reasoning, content, finish, emitted = {}, '', '', None, ''
        for kind, value in self._stream(messages, model, max_tokens, tools):
            if kind == 'usage':
                yield kind, value
            elif kind == 'finish':
                finish = value
            elif kind == 'delta':
                reasoning += value.get('reasoning_content') or ''
                content += value.get('content') or ''
                for part in value.get('tool_calls') or []:
                    index = part.get('index', 0)
                    call = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                    if part.get('id'):
                        call['id'] = part['id']
                    fn = part.get('function') or {}
                    for key in ('name', 'arguments'):
                        call['function'][key] += fn.get(key) or ''
                if len(content)+len(reasoning)+sum(len(c['function']['arguments']) for c in calls.values()) > 100000:
                    raise RuntimeError('模型工具响应超过上限')
                if len(calls) == 1:
                    current = next(iter(calls.values()))['function']
                    if current['name'] in {'complete_answer', 'request_clarification'}:
                        from ..agent.events import partial_answer
                        # The function name supplies the action; reuse the safe
                        # string-fragment decoder without asking the model for it.
                        answer = partial_answer('"action":"answer",'+current['arguments'])
                        if answer.startswith(emitted) and len(answer)>len(emitted):
                            yield 'answer.delta', answer[len(emitted):]
                            emitted = answer
        if finish not in {'tool_calls', 'stop'} or len(calls) != 1:
            raise FunctionCallProtocolError('function_call_incomplete', '模型必须返回一个完整的原生函数调用')
        call = next(iter(calls.values()))
        allowed = {tool['function']['name'] for tool in tools}
        if not call['id'] or call['function']['name'] not in allowed:
            raise FunctionCallProtocolError('function_call_unknown', '模型返回了未知函数或缺少调用 ID')
        try:
            arguments = json.loads(call['function']['arguments'])
        except json.JSONDecodeError as error:
            raise FunctionCallProtocolError('json_invalid', '原生函数参数不是完整 JSON') from error
        if not isinstance(arguments, dict):
            raise FunctionCallProtocolError('model_type', '函数参数必须是 JSON 对象')
        message = {'role': 'assistant', 'content': content or None, 'tool_calls': [call]}
        if reasoning:
            message['reasoning_content'] = reasoning
        yield 'function', {'name': call['function']['name'], 'arguments': arguments, 'message': message}

    def stream_structured(self, messages, model, name, schema, max_tokens=None):
        tools = [{'type': 'function', 'function': {'name': name,
                  'description': '提交结构化结果供应用校验；必须调用此函数一次。', 'parameters': schema}}]
        for kind, value in self.stream_functions(messages, model, tools, max_tokens):
            if kind == 'usage':
                yield kind, value
            elif kind == 'function':
                yield 'content', json.dumps(value['arguments'], ensure_ascii=False)
