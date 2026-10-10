from .retrieval import retrieve
from ..agent.store import timestamp
import re


def assemble(service, goal):
    mode = getattr(service, 'retrieval_mode', 'hybrid')
    hits = [] if mode == 'none' else retrieve(service, goal, mode=mode)
    if mode != 'none' and re.search(r'天气|户外|跑步|徒步|骑行|weather|outdoor|running|hiking|cycling', goal, re.I):
        for memory in service.list('profile'):
            if memory['content'].startswith('location:') and memory['id'] not in {m['id'] for m in hits}:
                hits.append(memory)
        for memory in service.list('procedural'):
            if memory['enabled'] and memory['id'] not in {m['id'] for m in hits}:
                hits.append(memory)
    temporary = service.store.rows('SELECT content,expires,source_id FROM short_term_memories WHERE expires>? AND source_id IN (SELECT id FROM memory_sources WHERE forgotten=0)', (timestamp(),))
    return {'User Block': [m for m in hits if m['kind'] in {'profile', 'preference', 'episodic'}],
            'Task Block': [m for m in hits if m['kind'] == 'task'],
            'Agent Block': [m for m in hits if m['kind'] == 'procedural'], 'Temporary': temporary}
