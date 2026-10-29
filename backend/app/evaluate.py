"""Operator-run trusted evaluation; admins cannot submit a self-reported score."""
import argparse
import json
from .db import init, connect, audit
from .services import row_or_404
from .integrations.llm import Provider, chat, check_result


def evaluate(provider_id, dataset):
    cases = json.loads(dataset.read_text())
    if len(cases) < 100:
        raise ValueError('At least 100 finance-labelled cases are required')
    with connect() as db:
        p = Provider.model_validate_json(row_or_404(db, 'providers', provider_id)['payload'])
    correct = total = 0
    for case in cases:
        result, _ = chat(p, case['input'])
        check_result(result, case['input']['transactions'], case['input']['sap_items'])
        for item in result.items:
            expected = case['expected'][item.txn_id]
            correct += (item.outcome == expected['outcome'] and item.sap_id == expected.get('sap_id')
                        and item.category == expected.get('category'))
            total += 1
    if total < 100:
        raise ValueError('Evaluation requires at least 100 outcomes')
    accuracy = correct / total
    with connect() as db:
        db.execute('UPDATE providers SET accuracy=? WHERE id=?', (accuracy, provider_id))
        audit(db, provider_id, 'evaluation-operator', 'provider.evaluated', {'correct': correct, 'total': total, 'accuracy': accuracy})
    return accuracy


if __name__ == '__main__':
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument('provider_id')
    parser.add_argument('dataset', type=Path)
    args = parser.parse_args()
    init()
    print(evaluate(args.provider_id, args.dataset))
