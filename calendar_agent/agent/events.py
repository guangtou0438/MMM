"""Expose answer string deltas without exposing private reasoning."""
import json
import re


def partial_answer(raw):
    if not re.search(r'"action"\s*:\s*"(?:answer|ask)"', raw):
        return ''
    match = re.search(r'"answer"\s*:\s*"', raw)
    if not match:
        return ''
    fragment = raw[match.end()-1:]
    try:
        return json.JSONDecoder().raw_decode(fragment)[0]
    except json.JSONDecodeError:
        # A provider chunk can stop inside a JSON escape or surrogate pair.
        for suffix in range(7):
            value = fragment[:len(fragment)-suffix] if suffix else fragment
            try:
                decoded = json.loads(value+'"')
                return decoded.rstrip(''.join(chr(i) for i in range(0xD800, 0xE000)))
            except (json.JSONDecodeError, ValueError):
                continue
    return ''
