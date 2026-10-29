import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

os.environ['AUTH_MODE'] = 'dev'
os.environ['SAP_MODE'] = 'mock'
from fastapi.testclient import TestClient
from app import config
from app.main import app
from app.db import connect
from app.services import work_once
from app.matching import match_rules
from app.integrations.llm import AgentResult, check_result

ROOT = Path(__file__).resolve().parents[2]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DATA_DIR = Path(self.tmp.name)
        config.AUTH_MODE = 'dev'
        os.environ['SAP_MOCK_ITEMS'] = str(ROOT / 'examples/sap-items.json')
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def h(self, role='preparer'):
        return {'Authorization': 'Bearer dev-' + role}

    def upload(self, total='350.00', content=None):
        return self.client.post('/statements', headers=self.h(), data={'total_aed': total},
                                files={'file': ('statement.csv', content or (ROOT / 'examples/statement.csv').read_bytes(), 'text/csv')})

    def ready_batch(self):
        response = self.upload()
        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(work_once())
        self.assertTrue(work_once())
        self.assertFalse(work_once())
        id = response.json()['batch_id']
        return self.client.get(f'/batches/{id}/results', headers=self.h()).json()

    def proposal(self, batch, amount='200.00'):
        return self.client.post(f'/batches/{batch["id"]}/jvs', headers=self.h(), json={
            'txn_ids': [batch['payload']['exceptions'][0]['txn_id']], 'reason': 'Finance adjustment',
            'lines': [{'gl_account': '400100', 'cost_center': 'CC100', 'amount': amount},
                      {'gl_account': '210000', 'amount': str(-Decimal(amount))}]})

    def test_end_to_end_and_idempotency(self):
        batch = self.ready_batch()
        self.assertEqual(batch['status'], 'review')
        self.assertEqual([m['rule_id'] for m in batch['payload']['matches']], ['R1', 'R2'])
        self.assertEqual(len(batch['payload']['exceptions']), 1)
        proposal = self.proposal(batch)
        self.assertEqual(proposal.status_code, 201, proposal.text)
        jv = proposal.json()['id']
        self.assertEqual(self.client.post(f'/jv/{jv}/post', headers=self.h('approver')).status_code, 409)
        decision = self.client.post(f'/jv/{jv}/decision', headers=self.h('approver'), json={'decision': 'approve'})
        self.assertEqual(decision.json()['status'], 'approved')
        first = self.client.post(f'/jv/{jv}/post', headers=self.h('approver'))
        second = self.client.post(f'/jv/{jv}/post', headers=self.h('approver'))
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), second.json())
        with connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM mock_postings').fetchone()[0], 1)
        txn = batch['payload']['exceptions'][0]['txn_id']
        self.assertEqual(self.client.post(f'/batches/{batch["id"]}/exceptions/{txn}/resolve',headers=self.h(),json={'comment':'Posted approved adjustment'}).status_code, 200)
        self.assertEqual(self.client.post(f'/batches/{batch["id"]}/close',headers=self.h()).json()['status'], 'completed')

    def test_auth_roles_and_self_approval(self):
        self.assertEqual(self.client.get('/batches').status_code, 401)
        self.assertEqual(self.client.post('/statements', headers=self.h('auditor')).status_code, 403)
        batch = self.ready_batch()
        jv = self.proposal(batch).json()['id']
        with connect() as db:
            db.execute("UPDATE jvs SET prepared_by='approver' WHERE id=?", (jv,))
        self.assertEqual(self.client.post(f'/jv/{jv}/decision',headers=self.h('approver'),json={'decision':'approve'}).status_code,403)
        self.assertEqual(self.client.post(f'/jv/{jv}/decision',headers=self.h('approver2'),json={'decision':'reject'}).status_code,422)

    def test_duplicate_file_and_bad_total(self):
        self.assertEqual(self.upload(total='1').status_code, 202)
        self.assertTrue(work_once())
        self.assertEqual(self.upload().status_code, 409)
        batches = self.client.get('/batches', headers=self.h()).json()
        self.assertEqual(batches[0]['status'], 'failed')
        with connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM transactions').fetchone()[0],0)

    def test_master_data_and_balance(self):
        batch = self.ready_batch()
        payload = {'txn_ids':[batch['payload']['exceptions'][0]['txn_id']], 'reason':'Bad entry',
                   'lines':[{'gl_account':'999999','amount':'10'}, {'gl_account':'210000','amount':'-10'}]}
        self.assertEqual(self.client.post(f'/batches/{batch["id"]}/jvs',headers=self.h(),json=payload).status_code,422)
        payload['lines'][0] = {'gl_account':'400100','amount':'11'}
        self.assertEqual(self.client.post(f'/batches/{batch["id"]}/jvs',headers=self.h(),json=payload).status_code,422)

    def test_large_jv_needs_two_distinct_approvers(self):
        batch = self.ready_batch()
        jv = self.proposal(batch, '10001.00').json()['id']
        first = self.client.post(f'/jv/{jv}/decision',headers=self.h('approver'),json={'decision':'approve'})
        self.assertEqual(first.json()['status'],'validated')
        self.assertEqual(self.client.post(f'/jv/{jv}/decision',headers=self.h('approver'),json={'decision':'approve'}).status_code,409)
        second = self.client.post(f'/jv/{jv}/decision',headers=self.h('approver2'),json={'decision':'approve'})
        self.assertEqual(second.json()['status'],'approved')

    def test_uncertain_post_never_blindly_retries(self):
        batch = self.ready_batch()
        jv = self.proposal(batch).json()['id']
        self.client.post(f'/jv/{jv}/decision',headers=self.h('approver'),json={'decision':'approve'})
        with patch('app.integrations.sap_odata.MockSAP.journal', side_effect=TimeoutError):
            self.assertEqual(self.client.post(f'/jv/{jv}/post',headers=self.h('approver')).status_code,502)
        self.assertEqual(self.client.post(f'/jv/{jv}/post',headers=self.h('approver')).status_code,409)
        self.assertEqual(self.client.post(f'/jv/{jv}/recover',headers=self.h('approver')).status_code,409)

    def test_model_hallucinated_ids_rejected(self):
        value = AgentResult.model_validate({'items':[{'txn_id':'T','outcome':'match','sap_id':'invented','confidence':0.99,'reason':'Trust me'}]})
        with self.assertRaises(ValueError):
            check_result(value, [{'id':'T'}], [])

    def test_timeout_after_commit_recovers_original_document(self):
        from app.integrations.sap_odata import MockSAP
        batch = self.ready_batch()
        jv = self.proposal(batch).json()['id']
        self.client.post(f'/jv/{jv}/decision',headers=self.h('approver'),json={'decision':'approve'})
        original = MockSAP.journal
        def committed_then_timeout(sap, reference, lines, simulate=True):
            original(sap, reference, lines, simulate)
            raise TimeoutError()
        with patch.object(MockSAP, 'journal', committed_then_timeout):
            self.assertEqual(self.client.post(f'/jv/{jv}/post',headers=self.h('approver')).status_code,502)
        recovered = self.client.post(f'/jv/{jv}/recover',headers=self.h('approver'))
        self.assertEqual(recovered.status_code,200)
        self.assertEqual(self.client.post(f'/jv/{jv}/post',headers=self.h('approver')).json(),recovered.json())
        with connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM mock_postings').fetchone()[0],1)

    def test_model_invalid_output_falls_back_to_manual_review(self):
        response = self.upload()
        work_once()
        with connect() as db:
            provider = {'name':'Mock','base_url':'https://models.example.com/v1','model':'recon',
                        'hosting':'internal','auth_type':'none'}
            db.execute('INSERT INTO providers VALUES (?,?,?,?,?,?)',('P','admin','admin2','active',json.dumps(provider),0.99))
            db.execute('INSERT INTO settings VALUES (?,?)',('llm-assignment',json.dumps({'primary':'P'})))
        os.environ['LLM_ALLOWED_HOSTS']='models.example.com'
        with patch('app.integrations.llm.chat',side_effect=ValueError('Invalid JSON')) as call:
            work_once()
            self.assertEqual(call.call_count,2)
        result=self.client.get(f'/batches/{response.json()["batch_id"]}/results',headers=self.h()).json()
        self.assertEqual(result['status'],'review')
        self.assertIn('manual review',result['payload']['exceptions'][0]['reason'])

    def test_provider_governance(self):
        os.environ['LLM_ALLOWED_HOSTS'] = 'models.example.com'
        p = self.client.post('/admin/llm-providers',headers=self.h('admin'),json={
            'name':'Internal','model':'recon','base_url':'https://models.example.com/v1',
            'hosting':'internal','auth_type':'none'}).json()['id']
        self.assertEqual(self.client.post(f'/admin/llm-providers/{p}/approve',headers=self.h('admin')).status_code,403)
        self.assertEqual(self.client.post(f'/admin/llm-providers/{p}/approve',headers=self.h('admin2')).status_code,409)
        with connect() as db:
            db.execute('UPDATE providers SET accuracy=0.96 WHERE id=?',(p,))
        self.assertEqual(self.client.post(f'/admin/llm-providers/{p}/approve',headers=self.h('admin2')).status_code,200)


class MatchingTests(unittest.TestCase):
    def t(self, id='T', amount='10', reference='R'):
        return {'id':id,'amount':amount,'amount_aed':amount,'currency':'AED','reference':reference,
                'card_last4':'1234','post_date':'2026-09-02'}

    def i(self, id='I', amount='10', assignment='R'):
        return {'id':id,'amount':amount,'currency':'AED','assignment':assignment,
                'card_last4':'1234','posting_date':'2026-09-02'}

    def test_split_and_rounding(self):
        matches, rest = match_rules([self.t('A','4'), self.t('B','6')],[self.i()])
        self.assertEqual(matches[0]['rule_id'],'R3')
        self.assertEqual(rest,[])
        matches, _ = match_rules([self.t(amount='10.50')],[self.i(assignment='X')])
        self.assertEqual(matches[0]['rule_id'],'R4')

    def test_ambiguous_items_are_not_consumed(self):
        matches, rest = match_rules([self.t()],[self.i('I1'), self.i('I2')])
        self.assertEqual(matches,[])
        self.assertEqual(len(rest),1)


if __name__ == '__main__':
    unittest.main()
