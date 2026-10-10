"""Native function definitions mapped to the existing authorized executor."""
from copy import deepcopy


def decision_tools(schemas, skills, chat=False):
    answer_schema = {'type': 'object', 'properties': {
        'answer': {'type': 'string', 'minLength': 1, 'maxLength': 16000},
        'citation_ids': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 12}},
        'required': ['answer', 'citation_ids'], 'additionalProperties': False}
    tools, dispatch = [], {}
    for name, action in [('complete_answer', 'answer'), ('request_clarification', 'ask')]:
        tools.append({'type': 'function', 'function': {'name': name,
            'description': '向用户提交最终回答及记忆引用。' if action=='answer' else '缺少必要信息时向用户询问并等待。',
            'parameters': deepcopy(answer_schema)}})
        dispatch[name] = {'action': action}
    if not chat:
        for skill in skills:
            for tool in skill['tools']:
                if tool not in schemas:
                    continue
                schema = deepcopy(schemas[tool])
                props = schema.setdefault('properties', {})
                props.pop('request_id', None)  # Idempotency keys are server-owned.
                schema['required'] = [key for key in schema.get('required', []) if key!='request_id']
                props['expected_version'] = {'type': 'integer', 'minimum': 1,
                                             'description': '修改已有任务时必须传入读取到的当前版本。'}
                if tool in {'update_task', 'complete_task', 'archive_task', 'restore_task'}:
                    schema['required'].append('expected_version')
                name = skill['id']+'__'+tool
                tools.append({'type': 'function', 'function': {'name': name,
                    'description': skill['instructions'], 'parameters': schema}})
                dispatch[name] = {'action': 'tool', 'skill': skill['id'], 'tool': tool}
    return tools, dispatch


def resolve_call(call, dispatch):
    """Resolve one validated provider call to its local execution target.

    This keeps the provider's native function call as the conversation
    protocol. Runtime consumes the returned fields directly; it does not
    serialize them into the legacy Decision JSON format and parse them again.
    """
    target = dispatch[call['name']]
    arguments = dict(call['arguments'])
    if target['action'] in {'answer', 'ask'}:
        if set(arguments)-{'answer','citation_ids'}:
            raise ValueError('回答函数包含未定义参数')
        if 'answer' not in arguments or 'citation_ids' not in arguments:
            raise ValueError('回答函数缺少必填参数')
        return target | arguments
    version = arguments.pop('expected_version', None)
    return target | {'arguments': arguments, 'expected_version': version}
