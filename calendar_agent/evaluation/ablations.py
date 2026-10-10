"""python -m calendar_agent.evaluation.ablations [--live] [--output path]."""
import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from fastapi.testclient import TestClient
from .datasets import CASES, answer_matches
from .metrics import aggregate, recall_at_k
from ..app import create_app
from ..memory.retrieval import retrieve, rebuild
from ..memory.consolidation import apply


class FixtureModel:
    """Deterministic fixture verifies data flow; it is not a model benchmark."""
    def complete(self, messages):
        prompt = messages[0]['content']
        for case in CASES:
            if case['query'] == messages[-1]['content'] and case['memory'] in prompt:
                return json.dumps({'action': 'answer', 'answer': case['memory']})
        return json.dumps({'action': 'answer', 'answer': 'No supporting memory available.'})


def evaluate(live=False, semantic=False):
    output = {'dataset': 'synthetic-calendar-v1', 'mode': 'live' if live else 'fixture',
              'warning': None if live else 'Fixture results validate plumbing, not GLM/DeepSeek quality.', 'groups': []}
    for model in ('glm', 'deepseek'):
        for group, mode in [('A', 'none'), ('B', 'keyword'), ('C', 'hybrid')]:
            details = []
            for case in CASES:
                with tempfile.TemporaryDirectory(prefix='mmm-eval-') as directory:
                    app = create_app(Path(directory)/'synthetic.db', None if live else FixtureModel(),
                                     lambda: datetime(2026, 9, 6, tzinfo=timezone.utc), scheduler_enabled=False)
                    with TestClient(app) as client:
                        memory = app.state.memory_service
                        memory.retrieval_mode = mode
                        if not semantic:
                            memory.embedding = None
                        for distractor in CASES[:4]:
                            if distractor['memory'] != case['memory']:
                                sid = memory.source(distractor['memory'])
                                memory.add(distractor['memory'], distractor['kind'], [sid])
                        source = memory.source(case['memory'])
                        consolidation = {'status': 'not_applicable'}
                        if group == 'C' and live:
                            from ..memory.consolidation import enqueue
                            from ..memory.jobs import process_one
                            enqueue(memory, source, 'explicit')
                            consolidation = process_one(memory, model=model)
                            mid = next(iter(consolidation.get('memory_ids', [])), 'not-consolidated')
                        elif group == 'C':
                            mid = apply(memory, {'operation': 'ADD', 'source_ids': [source], 'content': case['memory'], 'kind': case['kind']}, lambda op, evidence: True)[0]
                        else:
                            mid = memory.add(case['memory'], case['kind'], [source])
                        forgotten = memory.source('Synthetic forgotten fact: lives on Mars')
                        forgotten_id = memory.add('Synthetic forgotten fact: lives on Mars', 'profile', [forgotten])
                        memory.forget(forgotten_id)
                        if semantic and group == 'C':
                            rebuild(memory)
                        hits = [] if group == 'A' else retrieve(memory, case['query'], mode=mode)
                        started = time.monotonic()
                        run = client.post('/api/agent/runs', json={'content': case['query'], 'request_id': case['id'], 'model': model,
                                                                 'kind': 'chat' if group == 'A' else 'complex'}).json()
                        deadline = started+200
                        while time.monotonic() < deadline:
                            result = client.get('/api/agent/runs/'+run['id']).json()
                            if result['status'] in {'completed', 'failed', 'waiting_for_user', 'cancelled'}:
                                break
                            time.sleep(.02)
                        events = app.state.agent_store.rows("SELECT created FROM agent_events WHERE run_id=? AND type IN ('answer.delta','answer.snapshot') ORDER BY seq LIMIT 1", (run['id'],))
                        citations = json.loads(result['citations'])
                        if group == 'C':
                            consolidation['key_fact_preservation'] = int(answer_matches(case, ' '.join(m['content'] for m in memory.list() if m['id']==mid)))
                        details.append({'case': case['id'], 'success': int(result['status'] == 'completed' and answer_matches(case, result['answer'])),
                                        'recall_at_8': recall_at_k([mid], [m['id'] for m in hits]),
                                        'forgotten_recall': int(any(m['id'] == forgotten_id for m in hits)),
                                        'steps': result['calls'], 'provider_tokens': result['actual_tokens'], 'budget_units': result['tokens'],
                                        'first_token_seconds': events[0]['created']-run['created'] if events else None,
                                        'total_seconds': time.monotonic()-started, 'status': result['status'], 'error': result['error'],
                                        'answer': result['answer'], 'consolidation': consolidation, 'citation_coverage':int(bool(citations)),
                                        'citation_correctness':int(mid in citations) if citations else None})
            output['groups'].append({'model': model, 'group': group, 'semantic_enabled': semantic and group == 'C', 'metrics': aggregate(details), 'cases': details})
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--semantic', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('artifacts/evaluation-synthetic.json'))
    args = parser.parse_args()
    result = evaluate(args.live, args.semantic)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'mode': result['mode'], 'groups': len(result['groups'])}))


if __name__ == '__main__':
    main()
