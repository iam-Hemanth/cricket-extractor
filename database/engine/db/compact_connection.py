"""Transactional compact schema installation on independently guarded proof sessions."""
import hashlib
from pathlib import Path

from db.source_connection import ProofSession, assert_proof_session

SCHEMA = 'cricket_compact_v1'
SCHEMA_PATH = Path(__file__).with_name('compact_schema.sql')
CODEC_PATH = Path(__file__).resolve().parents[1] / 'ingestion' / 'compact_codec.py'


def apply_compact_schema(session: ProofSession) -> None:
    """Create or authenticate the schema without committing the caller transaction."""
    assert_proof_session(session)
    raw = SCHEMA_PATH.read_bytes()
    identity = (hashlib.sha256(raw).hexdigest(),
                hashlib.sha256(CODEC_PATH.read_bytes()).hexdigest(),
                session.descriptor['run_id'])
    with session.connection.cursor() as cur:
        cur.execute('SELECT to_regnamespace(%s)', (SCHEMA,))
        if cur.fetchone()[0] is not None:
            cur.execute(f'SELECT schema_sha256,codec_sha256,cluster_run_id FROM {SCHEMA}.proof_environment')
            if cur.fetchall() != [identity]:
                raise ValueError('Existing compact schema or codec differs; use a fresh database')
            return
        cur.execute(raw.decode())
        cur.execute(f'INSERT INTO {SCHEMA}.proof_environment(schema_sha256,codec_sha256,cluster_run_id) VALUES(%s,%s,%s)', identity)
