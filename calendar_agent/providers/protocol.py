"""Normalize harmless provider formatting; never infer a write operation."""
import json


def decision_json(raw):
    value = raw.strip()
    if value.startswith('```') and value.endswith('```'):
        value = value.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    data = json.loads(value)
    if isinstance(data, dict):
        if set(data) == {'answer'}:
            data['action'] = 'answer'
        if data.get('action') in {'answer', 'ask'} and 'text' in data and 'answer' not in data:
            data['answer'] = data.pop('text')
        if data.get('action') in {'answer', 'ask'} and isinstance(data.get('citation_ids'), list):
            # Calendar numeric IDs are never memory references. Drop them rather
            # than coercing them into plausible-looking memory evidence.
            data['citation_ids'] = [item for item in data['citation_ids'] if not isinstance(item, (int, float))]
    return json.dumps(data, ensure_ascii=False)
