import json
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .auth import require
from .db import audit, connect, dumps, ident
from .integrations.llm import Provider, chat, check_result
from .models import StrictModel
from .services import row_or_404

router = APIRouter(prefix='/admin', dependencies=[Depends(require('Admin'))])


@router.get('/llm-providers')
def list_providers():
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM providers')]


@router.post('/llm-providers', status_code=201)
def create_provider(provider: Provider, user=Depends(require('Admin'))):
    id = ident()
    with connect() as db:
        db.execute('INSERT INTO providers VALUES (?,?,NULL,?,?,NULL)', (id, user.id, 'draft', provider.model_dump_json()))
        audit(db, id, user.id, 'provider.created', provider.model_dump())
    return {'id': id, 'status': 'draft'}


@router.post('/llm-providers/{id}/test')
def test_provider(id: str, user=Depends(require('Admin'))):
    with connect() as db:
        provider = Provider.model_validate_json(row_or_404(db, 'providers', id)['payload'])
    try:
        result, usage = chat(provider, {'transactions': [], 'sap_items': [], 'master_data': {}})
        check_result(result, [], [])
    except Exception:
        raise HTTPException(502, 'Provider test failed; check endpoint, identity, network and response schema') from None
    with connect() as db:
        audit(db, id, user.id, 'provider.tested', {'usage': usage})
    return {'status': 'passed', 'usage': usage}


class Assignment(StrictModel):
    primary: str
    fallback: str | None = None


@router.post('/llm-providers/{id}/approve')
def approve_provider(id: str, user=Depends(require('Admin'))):
    with connect() as db:
        row = row_or_404(db, 'providers', id)
        if row['created_by'] == user.id:
            raise HTTPException(403, 'A second admin must approve the provider')
        if row['accuracy'] is None or row['accuracy'] < 0.95:
            raise HTTPException(409, 'A trusted evaluation with at least 95% accuracy is required')
        p = Provider.model_validate_json(row['payload'])
        if p.hosting == 'internet' and not p.security_approved:
            raise HTTPException(409, 'Security approval is required')
        db.execute("UPDATE providers SET status='active',approved_by=? WHERE id=?", (user.id, id))
        audit(db, id, user.id, 'provider.approved')
    return {'status': 'active'}


@router.post('/llm-assignment-requests', status_code=201)
def assignment_request(request: Assignment, user=Depends(require('Admin'))):
    id = ident()
    with connect() as db:
        for p in filter(None, [request.primary, request.fallback]):
            if row_or_404(db, 'providers', p)['status'] != 'active':
                raise HTTPException(409, 'Assigned providers must be active')
        payload = {**request.model_dump(), 'requested_by': user.id}
        db.execute('INSERT INTO settings VALUES (?,?)', ('assignment-request:' + id, dumps(payload)))
        audit(db, id, user.id, 'assignment.requested', payload)
    return {'id': id}


@router.post('/llm-assignment-requests/{id}/approve')
def approve_assignment(id: str, user=Depends(require('Admin'))):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT payload FROM settings WHERE key=?', ('assignment-request:' + id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Request not found')
        payload = json.loads(row[0])
        if payload['requested_by'] == user.id:
            raise HTTPException(403, 'A second admin must approve assignment changes')
        db.execute("INSERT OR REPLACE INTO settings VALUES ('llm-assignment',?)", (dumps(payload),))
        db.execute('DELETE FROM settings WHERE key=?', ('assignment-request:' + id,))
        audit(db, id, user.id, 'assignment.approved', payload)
    return {'status': 'approved'}
