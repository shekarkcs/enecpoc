import csv
import hashlib
import io
import json
import sqlite3
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException
from . import config
from .db import audit, connect, dumps, ident, now
from .matching import match_rules
from .models import Transaction
from .integrations.sap_odata import sap_client


def row_or_404(db, table, id):
    if table not in {'batches', 'jvs', 'jobs', 'providers'}:
        raise ValueError('Invalid table')
    row = db.execute(f'SELECT * FROM {table} WHERE id=?', (id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Not found')
    return dict(row)


def upload(content, filename, actor, total):
    if not filename.lower().endswith('.csv'):
        raise HTTPException(422, 'This POC accepts CSV only; PDF/MT940 adapters are not enabled')
    batch, job = ident(), ident()
    sha = hashlib.sha256(content).hexdigest()
    path = config.DATA_DIR / 'statements-in'
    path.mkdir(parents=True, exist_ok=True)
    try:
        with connect() as db:
            db.execute('INSERT INTO batches VALUES (?,?,?,?,?,?,NULL)',
                       (batch, sha, filename[:200], actor, 'uploaded', dumps({'total': str(total)})))
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,NULL,?)', (job, batch, 'parse', 'queued', now()))
            audit(db, batch, actor, 'statement.uploaded', {'hash': sha})
            (path / f'{batch}.csv').write_bytes(content)
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'Statement already uploaded') from None
    return {'batch_id': batch, 'job_id': job}


def process_parse(db, job):
    batch = row_or_404(db, 'batches', job['batch_id'])
    content = (config.DATA_DIR / 'statements-in' / f'{batch["id"]}.csv').read_text(encoding='utf-8-sig')
    txns = []
    for row in csv.DictReader(io.StringIO(content)):
        row['id'] = ident()
        txns.append(Transaction.model_validate(row).model_dump(mode='json'))
        if len(txns) > 10000:
            raise ValueError('Statement exceeds 10000 rows')
    if not txns:
        raise ValueError('Statement has no rows')
    if sum(Decimal(t['amount_aed']) for t in txns) != Decimal(json.loads(batch['payload'])['total']):
        raise ValueError('Billed AED total does not agree with the supplied statement total')
    for t in txns:
        fingerprint = hashlib.sha256(dumps([t[k] for k in ('card_last4', 'txn_date', 'amount', 'currency', 'reference')]).encode()).hexdigest()
        db.execute('INSERT INTO transactions VALUES (?,?,?,?)', (t['id'], batch['id'], fingerprint, dumps(t)))
    db.execute('UPDATE batches SET status=?,payload=? WHERE id=?', ('parsed', dumps({'transactions': txns}), batch['id']))
    db.execute('INSERT INTO jobs VALUES (?,?,?,?,NULL,?)', (ident(), batch['id'], 'match', 'queued', now()))
    audit(db, batch['id'], 'worker', 'statement.parsed', {'rows': len(txns)})


def process_match(db, job):
    batch = row_or_404(db, 'batches', job['batch_id'])
    payload = json.loads(batch['payload'])
    txns = payload['transactions']
    rules = json.loads(db.execute("SELECT payload FROM settings WHERE key='rules'").fetchone()[0])
    lo = min(date.fromisoformat(t['post_date']) for t in txns) - timedelta(days=rules['days'])
    hi = max(date.fromisoformat(t['post_date']) for t in txns) + timedelta(days=rules['days'])
    items = sap_client().open_items(lo.isoformat(), hi.isoformat())
    claimed = {r[0] for r in db.execute('SELECT sap_id FROM sap_claims')}
    items = [i for i in items if i['id'] not in claimed]
    matches, unmatched = match_rules(txns, items, rules['days'], rules['tolerance_aed'])
    for m in matches:
        db.execute('INSERT INTO sap_claims VALUES (?,?)', (m['sap_id'], batch['id']))
    # No invented AI output when no governed provider is configured.
    exceptions = [{'txn_id': t['id'], 'category': 'other', 'reason': 'No unique rule match; manual review required',
                   'proposed_action': 'Investigate and prepare a journal entry if required', 'status': 'open'} for t in unmatched]
    assignment = db.execute("SELECT payload FROM settings WHERE key='llm-assignment'").fetchone()
    if assignment and unmatched:
        from .integrations.llm import Provider, chat, check_result
        assignment = json.loads(assignment[0])
        available = [i for i in items if i['id'] not in {m['sap_id'] for m in matches}]
        exceptions = []
        for offset in range(0, len(unmatched), 20):
            chunk = unmatched[offset:offset + 20]
            result = None
            for provider_id in filter(None, [assignment['primary'], assignment.get('fallback')]):
                provider_row = row_or_404(db, 'providers', provider_id)
                if provider_row['status'] != 'active':
                    continue
                provider = Provider.model_validate_json(provider_row['payload'])
                for attempt in range(2):
                    try:
                        result, usage = chat(provider, {'transactions': chunk, 'sap_items': available,
                                                     'master_data': {'accounts': sorted(config.ACCOUNTS), 'cost_centres': sorted(config.COST_CENTRES)}})
                        check_result(result, chunk, available)
                        audit(db, batch['id'], 'worker', 'llm.usage', {'provider_id': provider_id, 'usage': usage})
                        break
                    except Exception:
                        result = None
                        audit(db, batch['id'], 'worker', 'llm.failed', {'provider_id': provider_id, 'attempt': attempt + 1})
                if result:
                    break
            if result is None:
                exceptions.extend({'txn_id': t['id'], 'category': 'other', 'reason': 'Model unavailable or invalid output; manual review required', 'status': 'open'} for t in chunk)
                continue
            for outcome in result.items:
                if outcome.outcome == 'match':
                    matches.append({'id': ident(), 'txn_ids': [outcome.txn_id], 'sap_id': outcome.sap_id,
                                    'method': 'ai', 'status': 'suggested', 'confidence': outcome.confidence, 'reason': outcome.reason})
                    available = [i for i in available if i['id'] != outcome.sap_id]
                else:
                    exceptions.append({'txn_id': outcome.txn_id, 'category': outcome.category,
                                       'reason': outcome.reason, 'confidence': outcome.confidence, 'status': 'open'})
                    if outcome.jv:
                        jv_id = ident()
                        proposal = outcome.jv.model_dump(mode='json')
                        validation = sap_client().journal(jv_id, proposal['lines'], simulate=True)
                        proposal['validation'] = validation
                        db.execute('INSERT INTO jvs(id,batch_id,prepared_by,status,payload) VALUES (?,?,?,?,?)',
                                   (jv_id, batch['id'], batch['actor'], 'validated' if validation['valid'] else 'proposed', dumps(proposal)))
                        audit(db, jv_id, 'worker', 'jv.ai_proposed', proposal)
    payload.update(matches=matches, exceptions=exceptions, sap_items=items)
    db.execute('UPDATE batches SET status=?,payload=? WHERE id=?', ('review', dumps(payload), batch['id']))
    audit(db, batch['id'], 'worker', 'matching.completed', {'matched': len(matches), 'manual': len(unmatched)})


def work_once():
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        job = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not job:
            return False
        job = dict(job)
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
        # Claim and effects share one transaction; rollback makes interrupted local jobs retryable.
        db.execute('SAVEPOINT job_effects')
        try:
            if job['kind'] == 'parse':
                process_parse(db, job)
            else:
                process_match(db, job)
            db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (job['id'],))
        except Exception as exc:
            db.execute('ROLLBACK TO job_effects')
            error = f'{type(exc).__name__}: statement or integration validation failed'
            if isinstance(exc, ValueError):
                error = str(exc)[:500]
            db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?", (error, job['id']))
            db.execute("UPDATE batches SET status='failed',error=? WHERE id=?", (error, job['batch_id']))
            audit(db, job['batch_id'], 'worker', 'job.failed', {'error': error})
    return True


def validate_master(proposal):
    if any(l.gl_account not in config.ACCOUNTS or (l.cost_center and l.cost_center not in config.COST_CENTRES)
           for l in proposal.lines):
        raise HTTPException(422, 'Account or cost centre is outside the allowed master data')


def post_jv(id, actor):
    # Claim before I/O. Concurrent requests cannot both perform the post.
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        jv = row_or_404(db, 'jvs', id)
        if jv['sap_doc_no']:
            return {'document': jv['sap_doc_no'], 'status': 'posted'}
        if jv['status'] != 'approved':
            raise HTTPException(409, 'Posting requires an approved JV; uncertain postings require recovery')
        approvals = json.loads(jv['approvals'])
        if not approvals or jv['prepared_by'] in approvals:
            raise HTTPException(409, 'Invalid segregation of duties')
        db.execute("UPDATE jvs SET status='posting' WHERE id=?", (id,))
        audit(db, id, actor, 'jv.posting')
    sap = sap_client()
    try:
        existing = sap.lookup(id)
        result = {'document': existing, 'valid': True} if existing else sap.journal(id, json.loads(jv['payload'])['lines'], simulate=False)
        if not result.get('valid') or not result.get('document'):
            raise ValueError('SAP did not confirm a document')
        with connect() as db:
            db.execute("UPDATE jvs SET status='posted',sap_doc_no=?,error=NULL WHERE id=?", (result['document'], id))
            audit(db, id, actor, 'jv.posted', {'document': result['document']})
        return {'status': 'posted', 'document': result['document']}
    except Exception:
        # A network failure may mean SAP committed. Never clear the posting claim automatically.
        with connect() as db:
            db.execute('UPDATE jvs SET error=? WHERE id=?', ('Outcome uncertain; recover by ClientReference before retrying', id))
            audit(db, id, actor, 'jv.posting_uncertain')
        raise HTTPException(502, 'Posting outcome uncertain; use recovery') from None
