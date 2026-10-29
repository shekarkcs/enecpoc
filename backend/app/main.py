import json
import sqlite3
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from pydantic import Field

from .auth import require
from .db import audit, connect, dumps, ident, init
from .models import Decision, Proposal, StrictModel
from .services import post_jv, row_or_404, upload, validate_master
from .integrations.sap_odata import sap_client


@asynccontextmanager
async def lifespan(app):
    init()
    yield


app = FastAPI(title='Credit Card Reconciliation', version='0.1.0', lifespan=lifespan)
from .provider_api import router as provider_router
app.include_router(provider_router)
READ = require('Preparer', 'Approver', 'Admin', 'Auditor')


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.get('/ready')
def ready():
    with connect() as db:
        db.execute('SELECT count(*) FROM jobs').fetchone()
    return {'database': 'ready'}


@app.post('/statements', status_code=202)
async def statements(file: UploadFile = File(), total_aed: Decimal = Form(), user=Depends(require('Preparer'))):
    if not total_aed.is_finite():
        raise HTTPException(422, 'Total must be finite')
    content = await file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, 'Maximum statement size is 5 MiB')
    return upload(content, file.filename or '', user.id, total_aed)


@app.get('/batches')
def batches(user=Depends(READ)):
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT id,filename,status,error,actor FROM batches ORDER BY rowid DESC')]


@app.get('/batches/{id}/results')
def results(id: str, user=Depends(READ)):
    with connect() as db:
        batch = row_or_404(db, 'batches', id)
        batch['payload'] = json.loads(batch['payload'])
        batch['jvs'] = [dict(r) for r in db.execute('SELECT * FROM jvs WHERE batch_id=?', (id,))]
        for jv in batch['jvs']:
            jv['payload'] = json.loads(jv['payload'])
            jv['approvals'] = json.loads(jv['approvals'])
        return batch


@app.post('/batches/{id}/match', status_code=202)
def rematch(id: str, user=Depends(require('Preparer'))):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        batch = row_or_404(db, 'batches', id)
        if batch['status'] != 'parsed':
            raise HTTPException(409, 'Only a parsed batch can be matched')
        if db.execute("SELECT 1 FROM jobs WHERE batch_id=? AND kind='match' AND status IN ('queued','running')", (id,)).fetchone():
            raise HTTPException(409, 'Match already queued')
        from .db import now
        job = ident()
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,NULL,?)', (job, id, 'match', 'queued', now()))
        audit(db, id, user.id, 'matching.requested')
        return {'job_id': job}


@app.get('/jobs/{id}')
def job_status(id: str, user=Depends(READ)):
    with connect() as db:
        return row_or_404(db, 'jobs', id)


@app.post('/batches/{id}/jvs', status_code=201)
def propose(id: str, proposal: Proposal, user=Depends(require('Preparer'))):
    validate_master(proposal)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        batch = row_or_404(db, 'batches', id)
        if batch['status'] != 'review':
            raise HTTPException(409, 'Batch is not in review')
        payload = json.loads(batch['payload'])
        unresolved = {e['txn_id'] for e in payload['exceptions'] if e['status'] == 'open'}
        if not set(proposal.txn_ids) <= unresolved:
            raise HTTPException(422, 'JV must reference open exceptions in this batch')
        for row in db.execute("SELECT payload FROM jvs WHERE batch_id=? AND status!='rejected'", (id,)):
            if set(json.loads(row['payload'])['txn_ids']) & set(proposal.txn_ids):
                raise HTTPException(409, 'A transaction already has an active JV')
        jv_id = ident()
        validation = sap_client().journal(jv_id, proposal.model_dump(mode='json')['lines'], simulate=True)
        if not validation.get('valid'):
            raise HTTPException(422, {'message': 'SAP simulation failed', 'validation': validation})
        p = proposal.model_dump(mode='json')
        p['validation'] = validation
        db.execute('INSERT INTO jvs(id,batch_id,prepared_by,status,payload) VALUES (?,?,?,?,?)',
                   (jv_id, id, user.id, 'validated', dumps(p)))
        audit(db, jv_id, user.id, 'jv.validated', p)
        return {'id': jv_id, 'status': 'validated', 'validation': validation}


@app.post('/jv/{id}/decision')
def decision(id: str, request: Decision, user=Depends(require('Approver'))):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        jv = row_or_404(db, 'jvs', id)
        if jv['prepared_by'] == user.id:
            raise HTTPException(403, 'Preparers cannot approve or reject their own JV')
        if jv['status'] != 'validated':
            raise HTTPException(409, 'JV is not awaiting approval')
        approvals = json.loads(jv['approvals'])
        if user.id in approvals:
            raise HTTPException(409, 'This approver has already decided')
        payload = json.loads(jv['payload'])
        rules = json.loads(db.execute("SELECT payload FROM settings WHERE key='rules'").fetchone()[0])
        amount = sum(max(Decimal(l['amount']), 0) for l in payload['lines'])
        required = 2 if amount > Decimal(rules['second_approval_aed']) else 1
        if request.decision == 'reject':
            status = 'rejected'
        else:
            approvals.append(user.id)
            status = 'approved' if len(approvals) >= required else 'validated'
        db.execute('UPDATE jvs SET status=?,approvals=? WHERE id=?', (status, dumps(approvals), id))
        audit(db, id, user.id, 'jv.' + request.decision, request.model_dump())
        return {'status': status, 'approvals': len(approvals), 'required': required}


@app.post('/jv/{id}/post')
def post(id: str, user=Depends(require('Approver'))):
    return post_jv(id, user.id)


@app.post('/jv/{id}/recover')
def recover(id: str, user=Depends(require('Approver'))):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        jv = row_or_404(db, 'jvs', id)
        if jv['status'] != 'posting':
            raise HTTPException(409, 'Only uncertain postings need recovery')
        doc = sap_client().lookup(id)
        if not doc:
            # Absence may reflect an in-flight SAP request; do not permit a blind retry.
            raise HTTPException(409, 'No document found. SAP operator must confirm the original request ended before retry')
        db.execute("UPDATE jvs SET status='posted',sap_doc_no=?,error=NULL WHERE id=?", (doc, id))
        audit(db, id, user.id, 'jv.recovered', {'document': doc})
        return {'status': 'posted', 'document': doc}


class Resolution(StrictModel):
    comment: str = Field(min_length=1, max_length=2000)


@app.post('/batches/{id}/exceptions/{txn_id}/resolve')
def resolve(id: str, txn_id: str, request: Resolution, user=Depends(require('Preparer'))):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        batch = row_or_404(db, 'batches', id)
        if batch['status'] != 'review':
            raise HTTPException(409, 'Batch is not in review')
        payload = json.loads(batch['payload'])
        exception = next((e for e in payload['exceptions'] if e['txn_id'] == txn_id), None)
        if not exception:
            raise HTTPException(404, 'Exception not found')
        for row in db.execute("SELECT status,payload FROM jvs WHERE batch_id=?", (id,)):
            if txn_id in json.loads(row['payload'])['txn_ids'] and row['status'] not in ('posted', 'rejected'):
                raise HTTPException(409, 'Finish the associated JV first')
        exception.update(status='resolved', resolved_by=user.id, comment=request.comment)
        db.execute('UPDATE batches SET payload=? WHERE id=?', (dumps(payload), id))
        audit(db, txn_id, user.id, 'exception.resolved', request.model_dump())
        return exception


@app.post('/batches/{id}/close')
def close_batch(id: str, user=Depends(require('Preparer'))):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        batch = row_or_404(db, 'batches', id)
        if batch['status'] != 'review':
            raise HTTPException(409, 'Batch is not in review')
        if any(e['status'] != 'resolved' for e in json.loads(batch['payload'])['exceptions']):
            raise HTTPException(409, 'Resolve every exception before closing')
        if any(m['status'] == 'suggested' for m in json.loads(batch['payload'])['matches']):
            raise HTTPException(409, 'Review every AI suggestion before closing')
        if db.execute("SELECT 1 FROM jvs WHERE batch_id=? AND status NOT IN ('posted','rejected')", (id,)).fetchone():
            raise HTTPException(409, 'Finish every JV before closing')
        db.execute("UPDATE batches SET status='completed' WHERE id=?", (id,))
        audit(db, id, user.id, 'batch.completed')
        return {'status': 'completed'}


@app.post('/batches/{id}/matches/{match_id}/decision')
def match_decision(id: str, match_id: str, decision: Decision, user=Depends(require('Preparer'))):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        batch = row_or_404(db, 'batches', id)
        if batch['status'] != 'review':
            raise HTTPException(409, 'Batch is not in review')
        payload = json.loads(batch['payload'])
        m = next((m for m in payload['matches'] if m['id'] == match_id), None)
        if not m or m['status'] != 'suggested':
            raise HTTPException(409, 'Match is not awaiting a decision')
        if decision.decision == 'approve':
            try:
                db.execute('INSERT INTO sap_claims VALUES (?,?)', (m['sap_id'], id))
            except sqlite3.IntegrityError:
                raise HTTPException(409, 'SAP item was already matched') from None
            m['status'] = 'accepted'
        else:
            m['status'] = 'rejected'
            payload['exceptions'].extend({'txn_id': tid, 'category': 'other', 'reason': decision.comment,
                                          'status': 'open'} for tid in m['txn_ids'])
        m['decided_by'] = user.id
        db.execute('UPDATE batches SET payload=? WHERE id=?', (dumps(payload), id))
        audit(db, match_id, user.id, 'match.' + decision.decision, decision.model_dump())
        return m


@app.get('/audit')
def history(entity_id: str | None = None, user=Depends(READ)):
    with connect() as db:
        rows = db.execute('SELECT * FROM audit WHERE (? IS NULL OR entity_id=?) ORDER BY at DESC LIMIT 1000',
                          (entity_id, entity_id))
        return [dict(r) for r in rows]


class Rules(StrictModel):
    days: int = Field(ge=0, le=30)
    tolerance_aed: Decimal = Field(ge=0, le=100, allow_inf_nan=False)
    second_approval_aed: Decimal = Field(gt=0, allow_inf_nan=False)


@app.get('/admin/rules')
def get_rules(user=Depends(require('Admin'))):
    with connect() as db:
        return json.loads(db.execute("SELECT payload FROM settings WHERE key='rules'").fetchone()[0])


@app.put('/admin/rules')
def set_rules(rules: Rules, user=Depends(require('Admin'))):
    with connect() as db:
        db.execute("UPDATE settings SET payload=? WHERE key='rules'", (rules.model_dump_json(),))
        audit(db, 'rules', user.id, 'rules.updated', rules.model_dump(mode='json'))
        return rules


@app.get('/master-data')
def master_data(user=Depends(READ)):
    from . import config
    return {'accounts': sorted(config.ACCOUNTS), 'cost_centres': sorted(config.COST_CENTRES)}
