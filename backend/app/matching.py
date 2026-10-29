from collections import defaultdict
from datetime import date
from decimal import Decimal


def match_rules(txns, items, days=3, tolerance_aed='1.00'):
    """Conservative matching: ambiguous candidates remain for human review."""
    remaining = {t['id']: t for t in txns}
    available = {i['id']: i for i in items}
    matches = []

    def amount(t, i):
        return t['currency'] == i['currency'] and Decimal(t['amount']) == Decimal(i['amount'])

    def near(t, i):
        return abs((date.fromisoformat(t['post_date']) - date.fromisoformat(i['posting_date'])).days) <= days

    def save(ids, item, rule):
        matches.append({'id': f'{rule}-{item["id"]}', 'txn_ids': ids, 'sap_id': item['id'],
                        'method': 'rule', 'rule_id': rule, 'confidence': 1,
                        'reason': f'Deterministic rule {rule}', 'status': 'accepted'})
        for txn_id in ids:
            remaining.pop(txn_id)
        available.pop(item['id'])

    predicates = [
        ('R1', lambda t, i: amount(t, i) and t['reference'] in (i.get('assignment'), i.get('text'))),
        ('R2', lambda t, i: amount(t, i) and t['card_last4'] == i.get('card_last4') and near(t, i)),
    ]
    for rule, predicate in predicates:
        candidates = {tid: [i for i in available.values() if predicate(t, i)] for tid, t in remaining.items()}
        for tid, options in candidates.items():
            if len(options) == 1 and sum(options[0] in xs for xs in candidates.values()) == 1:
                save([tid], options[0], rule)
    groups = defaultdict(list)
    for t in remaining.values():
        groups[(t['reference'], t['currency'])].append(t)
    group_candidates = []
    for (ref, currency), group in groups.items():
        if len(group) < 2:
            continue
        candidates = [i for i in available.values() if i['currency'] == currency
                      and ref in (i.get('assignment'), i.get('text'))
                      and sum(Decimal(t['amount']) for t in group) == Decimal(i['amount'])]
        if len(candidates) == 1:
            group_candidates.append((group, candidates[0]))
    for group, item in group_candidates:
        if sum(i['id'] == item['id'] for _, i in group_candidates) == 1:
            save([t['id'] for t in group], item, 'R3')
    candidates = {tid: [i for i in available.values() if t['currency'] == i['currency'] == 'AED'
                       and t['card_last4'] == i.get('card_last4') and near(t, i)
                       and abs(Decimal(t['amount_aed']) - Decimal(i['amount'])) <= Decimal(tolerance_aed)]
                  for tid, t in remaining.items()}
    for tid, options in candidates.items():
        if len(options) == 1 and sum(options[0] in xs for xs in candidates.values()) == 1:
            save([tid], options[0], 'R4')
    return matches, list(remaining.values())
