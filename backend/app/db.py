import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from . import config


def now():
    return datetime.now(timezone.utc).isoformat()


def ident():
    # Fits SAP BKPF-XBLNR (16 chars); unique constraint detects collisions.
    return uuid4().hex[:16]


def dumps(value):
    return json.dumps(value, default=str)


@contextmanager
def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.DATA_DIR / 'recon.db', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS batches(
          id TEXT PRIMARY KEY, hash TEXT UNIQUE NOT NULL, filename TEXT NOT NULL,
          actor TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, error TEXT);
        CREATE TABLE IF NOT EXISTS transactions(
          id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
          fingerprint TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
          kind TEXT NOT NULL, status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jvs(
          id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
          prepared_by TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
          approvals TEXT NOT NULL DEFAULT '[]', sap_doc_no TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS audit(
          id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, actor TEXT NOT NULL,
          action TEXT NOT NULL, payload TEXT NOT NULL, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS providers(
          id TEXT PRIMARY KEY, created_by TEXT NOT NULL, approved_by TEXT,
          status TEXT NOT NULL, payload TEXT NOT NULL, accuracy REAL);
        CREATE TABLE IF NOT EXISTS mock_postings(
          reference TEXT PRIMARY KEY, document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sap_claims(
          sap_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id));
        ''')
        db.execute("INSERT OR IGNORE INTO settings VALUES ('rules', ?)",
                   (dumps({'days': 3, 'tolerance_aed': '1.00', 'second_approval_aed': '10000.00'}),))


def audit(db, entity_id, actor, action, payload=None):
    db.execute('INSERT INTO audit VALUES (?,?,?,?,?,?)',
               (ident(), entity_id, actor, action, dumps(payload or {}), now()))
