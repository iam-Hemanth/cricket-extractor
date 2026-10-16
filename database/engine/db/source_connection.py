"""Explicit, guarded access to private source-proof databases. No env fallback."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
from types import MappingProxyType
import uuid

import psycopg2

SCHEMA = 'cricket_source_v1'
SCHEMA_PATH = Path(__file__).with_name('source_schema.sql')


def _private(path: Path, directory=False):
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise ValueError('Proof paths must be explicit canonical paths without symlinks')
    stat = path.stat()
    if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise ValueError('Proof paths must be private to the current user')
    if directory and not path.is_dir():
        raise ValueError('Expected private proof directory')


def validate_descriptor(path: Path) -> dict:
    _private(path)
    data = json.loads(path.read_text())
    keys = {'version','run_id','root','data_dir','socket_dir','port','user','database'}
    if set(data) != keys or data['version'] != 'd3_descriptor_v1':
        raise ValueError('Invalid proof descriptor')
    uuid.UUID(data['run_id'])
    root, directory, socket = (Path(data[k]) for k in ('root','data_dir','socket_dir'))
    for p in (root, directory, socket):
        _private(p, directory=True)
    if path.parent != root or directory != root/'data' or socket != root/'socket':
        raise ValueError('Target paths are outside the marked cluster')
    marker = root/'proof-marker.json'
    _private(marker)
    authority = json.loads(marker.read_text())
    if authority['version'] != 'd3_cluster_v1' or authority['run_id'] != data['run_id']:
        raise ValueError('Cluster marker mismatch')
    if (not re.fullmatch(r'cricstats_d3_(cohort|full)_[a-f0-9]{8}',data['database'])
            or data['database'] not in authority['databases']):
        raise ValueError('Not a proof database')
    if type(data['port']) is not int or not 1024 < data['port'] < 65536 or data['port'] == 5432:
        raise ValueError('Unsafe proof port')
    if data['user'] != pwd.getpwuid(os.getuid()).pw_name:
        raise ValueError('Proof user mismatch')
    return data


@dataclass(frozen=True)
class ProofSession:
    connection: object
    descriptor: object
    descriptor_path: Path | None = None

    def close(self):
        self.connection.close()


def assert_proof_session(session: ProofSession):
    if not isinstance(session, ProofSession):
        raise ValueError('A guarded proof session is required')
    if session.descriptor_path is None:
        raise ValueError('Session has no independent descriptor authority')
    data = validate_descriptor(session.descriptor_path)
    if data != dict(session.descriptor):
        raise ValueError('Session descriptor changed after connection')
    with session.connection.cursor() as cur:
        cur.execute("SELECT current_database(),current_user,current_setting('data_directory'),current_setting('listen_addresses'),current_setting('port'),inet_server_addr()")
        name,user,directory,listeners,port,address = cur.fetchone()
    if (name != data['database'] or user != data['user'] or Path(directory).resolve() != Path(data['data_dir'])
            or listeners != '' or int(port) != data['port'] or address is not None):
        raise ValueError('Actual server is not the marked socket-only proof cluster')


def connect_proof(path: Path) -> ProofSession:
    data = validate_descriptor(path)
    conn = psycopg2.connect(host=data['socket_dir'],port=data['port'],dbname=data['database'],
                            user=data['user'],connect_timeout=5,application_name='cricstats-d3-proof')
    session = ProofSession(conn,MappingProxyType(data),path)
    try:
        assert_proof_session(session)
    except Exception:
        conn.close()
        raise
    return session


def apply_schema(session: ProofSession):
    assert_proof_session(session)
    raw = SCHEMA_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    with session.connection.cursor() as cur:
        cur.execute('SELECT to_regnamespace(%s)',(SCHEMA,))
        if cur.fetchone()[0] is not None:
            cur.execute(f'SELECT schema_sha256,cluster_run_id FROM {SCHEMA}.proof_environment')
            if cur.fetchone() != (digest,session.descriptor['run_id']):
                raise ValueError('Existing proof schema differs;use a fresh database')
            return
        cur.execute(raw.decode())
        cur.execute(f'INSERT INTO {SCHEMA}.proof_environment(schema_sha256,cluster_run_id) VALUES(%s,%s)',
                    (digest,session.descriptor['run_id']))
    session.connection.commit()
