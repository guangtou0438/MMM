CASES = [
    {'id': 'residence', 'memory': 'The synthetic user lives in Melbourne, Victoria.', 'query': 'Where does the synthetic user live?', 'expected': 'Melbourne', 'kind': 'profile'},
    {'id': 'preference', 'memory': 'The synthetic user prefers indoor exercise when rain is likely.', 'query': 'What exercise does the synthetic user prefer when rain is likely?', 'expected': 'indoor', 'kind': 'preference'},
    {'id': 'episodic', 'memory': 'The synthetic user may have felt tired after the September workshop; this is uncertain.', 'query': 'How did the synthetic user feel after the workshop?', 'expected': 'uncertain', 'kind': 'episodic'},
    {'id': 'commitment', 'memory': 'The synthetic user still needs to finish the design assignment by 2026-09-12.', 'query': 'What unfinished commitment does the synthetic user have?', 'expected': '2026-09-12', 'kind': 'task'},
]

# Cross-language questions require semantic matching rather than shared words.
CASES += [dict(case, id=case['id']+'-zh', query=query) for case, query in zip(CASES, [
    '这位用户通常住在哪座城市？', '容易下雨的时候，这位用户更喜欢哪种锻炼？',
    '上次研讨会之后的感受是否确定？', '还没履行的作业承诺是什么时候到期？'])]

CRITERIA = {
    'residence': [('melbourne', '墨尔本')],
    'preference': [('indoor', '室内')],
    'episodic': [('tired', '疲惫', '疲倦', '劳累'), ('uncertain', 'may', '不确定', '可能', '也许')],
    'commitment': [('2026-09-12', 'september 12', '12 september', '9月12日', '9 月 12 日')]
}


def answer_matches(case, answer):
    text = answer.casefold()
    return all(any(alias in text for alias in alternatives) for alternatives in CRITERIA[case['id'].removesuffix('-zh')])
