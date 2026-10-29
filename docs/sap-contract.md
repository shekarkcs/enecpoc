# SAP Gateway contract for the ABAP team

Publish `ZCCR_RECON_SRV` on NetWeaver 7.40+ Gateway and expose through private APIM. Authorize the API managed identity at APIM and restrict the backend technical user to the two services, company code and document type. Preserve session cookies and `X-CSRF-Token` headers; never log request bodies or credentials.

`GET OpenItemSet` supports `$filter` on CompanyCode, Account and inclusive PostingDate, plus `$top`/`$skip`. Return OData V2 `d.results` rows with CompanyCode, DocumentNo, FiscalYear, LineItem, Account, PostingDate, Amount, Currency, Assignment, ItemText and optional CardLast4. Read BSIS or BSIK according to the finance-approved clearing-account configuration. The adapter currently expects ISO dates; adapt the metadata mapping if Gateway emits `/Date(milliseconds)/`.

`GET JournalEntrySet?$filter=CompanyCode eq '1000' and ClientReference eq '…'` returns only posted documents, including SapDocumentNo. It is required for timeout recovery.

`POST JournalEntrySet` is a deep insert with ClientReference, CompanyCode, DocDate, PostingDate, DocType, HeaderText, SimulateOnly and ToItems. Each line has ItemNo, GlAccount, CostCenter, signed Amount and Currency. Map to DOCUMENTHEADER, ACCOUNTGL/ACCOUNTPAYABLE and CURRENCYAMOUNT. Return SapDocumentNo and Messages (Type, Id, Number, Message). Type E/A must fail the OData operation with no commit.

For simulation call `BAPI_ACC_DOCUMENT_CHECK`. For posting acquire a lock keyed by company code and ClientReference, find any existing posted reference, return its original number if found, otherwise call `BAPI_ACC_DOCUMENT_POST` and `BAPI_TRANSACTION_COMMIT` atomically before releasing the lock. A lookup without locking is insufficient for concurrent requests. Store ClientReference in BKPF-XBLNR (16 characters maximum). The POC generates 16-character references.

Run contract tests against known test documents before enabling live mode: anonymous APIM call denied; read and simulation pass; invalid GL rejected; double post and concurrent post return one document; lost-response retry is reconciled by reference. No SAP service is installed by the Python code.
