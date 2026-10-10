def recall_at_k(expected_ids, recalled_ids):
    return len(set(expected_ids) & set(recalled_ids))/len(set(expected_ids)) if expected_ids else 1.


def mean(values):
    present = [v for v in values if v is not None]
    return sum(present)/len(present) if present else None


def tool_accuracy(expected, calls):
    """Required tool coverage, penalized for failed or unrelated selections.

    Reading calendar context is permitted in either workflow.
    """
    selected = {call['tool'] for call in calls if call['status']=='completed'}
    wrong = sum(call['status']!='completed' or call['tool'] not in set(expected)|{'read_calendar'} for call in calls)
    return len(set(expected)&selected)/(len(expected)+wrong)


def aggregate(rows):
    return {key: mean([row.get(key) for row in rows]) for key in
            ('success', 'recall_at_8', 'forgotten_recall', 'steps', 'provider_tokens', 'budget_units', 'first_token_seconds', 'total_seconds', 'citation_coverage', 'citation_correctness')}
