import json
import os
import time
from pathlib import Path

import httpx
from ..db import connect, ident


class MockSAP:
    def open_items(self, date_from, date_to):
        path = Path(os.getenv('SAP_MOCK_ITEMS', '../examples/sap-items.json'))
        return [i for i in json.loads(path.read_text()) if date_from <= i['posting_date'] <= date_to]

    def lookup(self, reference):
        with connect() as db:
            row = db.execute('SELECT document FROM mock_postings WHERE reference=?', (reference,)).fetchone()
            return row['document'] if row else None

    def journal(self, reference, lines, simulate=True):
        if simulate:
            return {'valid': True, 'messages': []}
        with connect() as db:
            db.execute('INSERT OR IGNORE INTO mock_postings VALUES (?,?)', (reference, ident()[:10]))
        return {'valid': True, 'document': self.lookup(reference), 'messages': []}


class SapODataClient:
    """APIM access token callback must use the deployed managed identity."""
    def __init__(self, base_url, get_token, company_code, account):
        if not base_url.startswith('https://'):
            raise ValueError('SAP requires HTTPS')
        self.base = base_url.rstrip('/')
        self.get_token = get_token
        self.company = company_code
        self.account = account
        self.client = httpx.Client(timeout=30, follow_redirects=False)

    def headers(self, **extra):
        return {'Authorization': f'Bearer {self.get_token()}', 'Accept': 'application/json', **extra}

    def read(self, path, **kwargs):
        for attempt in range(3):
            try:
                response = self.client.get(self.base + path, headers=self.headers(), **kwargs)
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.HTTPStatusError) as error:
                if isinstance(error, httpx.HTTPStatusError) and error.response.status_code < 500:
                    raise
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

    @staticmethod
    def quote(value):
        return str(value).replace("'", "''")

    def open_items(self, date_from, date_to):
        from datetime import date
        date.fromisoformat(date_from)
        date.fromisoformat(date_to)
        flt = (f"CompanyCode eq '{self.quote(self.company)}' and Account eq '{self.quote(self.account)}' "
               f"and PostingDate ge datetime'{date_from}T00:00:00' and PostingDate le datetime'{date_to}T00:00:00'")
        results, skip = [], 0
        while True:
            page = self.read('/OpenItemSet', params={'$filter': flt, '$top': 500, '$skip': skip}).json()['d']['results']
            for i in page:
                results.append({'id': ':'.join(str(i[k]) for k in ('CompanyCode', 'DocumentNo', 'FiscalYear', 'LineItem')),
                                'amount': i['Amount'], 'currency': i['Currency'],
                                'posting_date': i['PostingDate'][:10], 'assignment': i.get('Assignment', ''),
                                'text': i.get('ItemText', ''), 'card_last4': i.get('CardLast4')})
            if len(page) < 500:
                return results
            skip += 500

    def lookup(self, reference):
        rows = self.read('/JournalEntrySet', params={'$filter':
            f"CompanyCode eq '{self.quote(self.company)}' and ClientReference eq '{self.quote(reference)}'"}).json()['d']['results']
        return rows[0].get('SapDocumentNo') if rows else None

    def journal(self, reference, lines, simulate=True):
        from datetime import date
        token = self.client.get(self.base + '/', headers=self.headers(**{'X-CSRF-Token': 'Fetch'}))
        token.raise_for_status()
        payload = {'ClientReference': reference, 'CompanyCode': self.company, 'DocType': 'SA',
                   'DocDate': date.today().isoformat(), 'PostingDate': date.today().isoformat(),
                   'HeaderText': 'Credit card reconciliation', 'SimulateOnly': simulate,
                   'ToItems': [{'ItemNo': str(n), 'GlAccount': l['gl_account'],
                                'CostCenter': l.get('cost_center') or '', 'Amount': str(l['amount']),
                                'Currency': l['currency']} for n, l in enumerate(lines, 1)]}
        # Never retry writes here. Recovery is by ClientReference, in the service.
        response = self.client.post(self.base + '/JournalEntrySet', json=payload,
                                    headers=self.headers(**{'X-CSRF-Token': token.headers['x-csrf-token']}), timeout=60)
        response.raise_for_status()
        result = response.json()['d']
        messages = result.get('Messages', [])
        if isinstance(messages, dict):
            messages = messages.get('results', [])
        return {'valid': not any(m.get('Type') in ('E', 'A') for m in messages),
                'messages': messages, 'document': result.get('SapDocumentNo')}


def sap_client():
    from .. import config
    if config.SAP_MODE == 'mock':
        return MockSAP()
    # Azure SDK is optional until live SAP mode is selected.
    from azure.identity import DefaultAzureCredential
    credential = DefaultAzureCredential()
    scope = os.environ['SAP_APIM_SCOPE']
    return SapODataClient(os.environ['SAP_BASE_URL'], lambda: credential.get_token(scope).token,
                          config.COMPANY_CODE, os.environ['SAP_ACCOUNT'])
