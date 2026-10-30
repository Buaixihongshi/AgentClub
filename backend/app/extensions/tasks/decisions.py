"""Resolve only explicit answers to one visible pending decision.

No fuzzy approval, role routing, or assumption that silence means consent.
"""
import re


def match_answer(board, text):
    pending = [d for d in board.get('decisions', []) if d['status'] == 'pending']
    if len(pending) != 1:
        return None
    decision = pending[0]
    answer = text.strip().rstrip('。.!！')
    options = {o['id'].casefold(): o['id'] for o in decision['options']}
    for option in decision['options']:
        if answer == option['text']:
            return {'decision_id': decision['id'], 'option_id': option['id'], 'note': ''}
    if decision['kind'] == 'release' and answer in {'批准上线', '批准此版本上线'} and 'approve' in options:
        return {'decision_id': decision['id'], 'option_id': options['approve'], 'note': ''}
    match = re.fullmatch(r'(?:我选|选择|选|采用)?\s*([A-Za-z0-9_-]+)\s*(?:方案)?(?:[，,：:]\s*(.*))?', answer)
    if not match or match[1].casefold() not in options:
        return None
    return {'decision_id': decision['id'], 'option_id': options[match[1].casefold()], 'note': match[2] or ''}
