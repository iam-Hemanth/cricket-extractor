#!/usr/bin/env python3
"""Rebuild the authenticated, frozen CricStats snapshot in a new private PG18 cluster."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / 'engine'
MANIFEST = ROOT / 'snapshots/verified-subset-manifest.json'
SCHEMAS = ('cricket_player_read_v1', 'cricket_matchup_read_v1',
           'cricket_partnership_read_v1', 'cricket_opposition_read_v1', 'cricket_team_read_v1')
EXPECTED_VIEWS = {
    'cricket_player_read_v1.player_batting', 'cricket_player_read_v1.player_bowling',
    'cricket_matchup_read_v1.batter_vs_bowler', 'cricket_partnership_read_v1.partnerships',
    'cricket_opposition_read_v1.player_vs_team', 'cricket_team_read_v1.team_vs_team',
    'cricket_team_read_v1.team_vs_team_seasons', 'cricket_team_read_v1.team_recent_matches',
    'cricket_team_read_v1.venue_stats', 'cricket_team_read_v1.stat_cards',
}


def digest(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular file: ' + path.name)
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_lock():
    lock = json.loads((ROOT / 'snapshot-lock.json').read_text())
    if lock.get('version') != 'cricstats_rebuild_snapshot_v1':
        raise ValueError('Unsupported snapshot lock')
    return lock


def verify_engine(lock):
    if digest(MANIFEST) != lock['manifest_sha256']:
        raise ValueError('Frozen manifest differs from snapshot lock')
    for name, expected in lock['engine_sha256'].items():
        path = ENGINE / name
        if not path.resolve().is_relative_to(ENGINE) or digest(path) != expected:
            raise ValueError('Packaged backend implementation differs: ' + name)


def verify_payload(directory, lock):
    for name, expected in lock['payload_sha256'].items():
        path = directory / name
        if digest(path) != expected:
            raise ValueError('Input digest differs: ' + name)
        if path.stat().st_size != lock['payload_bytes'][name]:
            raise ValueError('Input size differs: ' + name)


def unpack_archive(archive, output, expected_hashes, expected_sizes):
    if output.exists() or output.is_symlink():
        raise FileExistsError('Use a fresh payload output directory')
    output.mkdir(mode=0o700, parents=False)
    seen = set()
    try:
        with tarfile.open(archive, 'r|gz') as tar:
            for member in tar:
                if (member.name not in expected_hashes or member.name in seen or not member.isfile()
                        or member.issym() or member.islnk() or '/' in member.name
                        or member.size != expected_sizes[member.name]):
                    raise ValueError('Unexpected, unsafe or incorrectly sized archive member')
                seen.add(member.name)
                source = tar.extractfile(member)
                if source is None:
                    raise ValueError('Missing archive content')
                h = hashlib.sha256()
                with (output / member.name).open('xb') as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                        h.update(chunk)
                if h.hexdigest() != expected_hashes[member.name]:
                    raise ValueError('Archive payload digest differs: ' + member.name)
        if seen != set(expected_hashes):
            raise ValueError('Incomplete archive')
    except Exception:
        shutil.rmtree(output)
        raise


def fetch_payload(output, archive=None):
    lock = load_lock()
    verify_engine(lock)
    if output.exists() or output.is_symlink():
        raise FileExistsError('Use a fresh payload output directory')
    temporary = output.parent / ('.' + output.name + '.download')
    downloaded = archive is None
    owns_temporary = False
    try:
        if downloaded:
            url = lock['archive_url']
            if not url or not url.startswith('https://github.com/iam-Hemanth/cricket-extractor/releases/download/'):
                raise ValueError('No approved snapshot download URL in lock')
            with temporary.open('xb') as target:
                owns_temporary = True
                with urllib.request.urlopen(url, timeout=60) as response:
                    copied = 0
                    while chunk := response.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > lock['archive_bytes']:
                            raise ValueError('Downloaded archive exceeds pinned size')
                        target.write(chunk)
            archive = temporary
        if archive.stat().st_size != lock['archive_bytes'] or digest(archive) != lock['archive_sha256']:
            raise ValueError('Snapshot archive digest or size differs')
        unpack_archive(archive, output, lock['payload_sha256'], lock['payload_bytes'])
        return {'snapshot': lock['snapshot'], 'payload_dir': str(output), 'all_input_hashes_verified': True}
    finally:
        if owns_temporary and temporary.exists():
            temporary.unlink()


def size_flags(size, lock):
    return {'maximum_database_bytes': lock['maximum_database_bytes'],
            'preferred_database_bytes': lock['preferred_database_bytes'],
            'under_budget': size < lock['maximum_database_bytes'],
            'under_preferred_budget': size < lock['preferred_database_bytes']}


def private_json(path, data):
    with path.open('x') as stream:
        json.dump(data, stream, indent=2, default=str)
        stream.write('\n')
    path.chmod(0o600)


def postgres_bin(value):
    if value:
        directory = Path(value).resolve()
    else:
        pg_config = shutil.which('pg_config')
        if not pg_config:
            raise ValueError('Provide --pg-bin for PostgreSQL 18 executables')
        directory = Path(subprocess.check_output([pg_config, '--bindir'], text=True).strip()).resolve()
    for name in ('postgres', 'initdb', 'pg_ctl', 'createdb', 'pg_dump'):
        if not (directory / name).is_file():
            raise ValueError('Missing PostgreSQL executable: ' + name)
    version = subprocess.check_output([directory / 'postgres', '--version'], text=True)
    if 'PostgreSQL) 18.' not in version:
        raise ValueError('This verified build requires PostgreSQL 18')
    return directory


def run_logged(command, root):
    with (root / 'postgres-build.log').open('a') as stream:
        subprocess.run([str(x) for x in command], stdout=stream, stderr=subprocess.STDOUT, check=True)


def create_cluster(root, pg_bin, port):
    if root.exists() or root.is_symlink():
        raise FileExistsError('Work directory must not already exist; existing databases are never overwritten')
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError('Work directory must be an absolute canonical path')
    if not 1024 < port < 65536 or port == 5432:
        raise ValueError('Use a non-default proof port between 1025 and 65535')
    if len(str(root / 'socket')) > 85:
        raise ValueError('Use a shorter work directory for the Unix socket, e.g. /private/tmp/cricstats-build on macOS')
    root.mkdir(mode=0o700)
    socket = root / 'socket'
    socket.mkdir(mode=0o700)
    run_id = str(uuid.uuid4())
    user = pwd.getpwuid(os.getuid()).pw_name
    database = 'cricstats_d3_full_' + run_id[:8]
    private_json(root / 'proof-marker.json', {'version': 'd3_cluster_v1', 'run_id': run_id, 'databases': [database]})
    private_json(root / 'control.json', {'pg_bin': str(pg_bin)})
    run_logged([pg_bin / 'initdb', '-D', root / 'data', '-U', user, '-A', 'trust', '--encoding=UTF8', '--no-locale'], root)
    descriptor = {'version': 'd3_descriptor_v1', 'run_id': run_id, 'root': str(root),
                  'data_dir': str(root / 'data'), 'socket_dir': str(socket),
                  'port': port, 'user': user, 'database': database}
    private_json(root / 'full.json', descriptor)
    options = shlex.join(['-k', str(socket), '-p', str(port), '-c', 'listen_addresses=', '-c', 'max_connections=12'])
    run_logged([pg_bin / 'pg_ctl', '-D', root / 'data', '-l', root / 'server.log', '-o', options, '-w', 'start'], root)
    run_logged([pg_bin / 'createdb', '-h', socket, '-p', port, '-U', user, database], root)
    return root / 'full.json'


def connect(descriptor):
    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))
    from db.source_connection import connect_proof
    return connect_proof(descriptor)


def lifecycle(root, command):
    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))
    from db.source_connection import validate_descriptor
    descriptor = root / 'full.json'
    data = validate_descriptor(descriptor)
    pg = postgres_bin(json.loads((root / 'control.json').read_text())['pg_bin'])
    if command == 'start':
        state = subprocess.run([pg / 'pg_ctl', '-D', data['data_dir'], 'status'], capture_output=True)
        if state.returncode != 0:
            options = shlex.join(['-k', data['socket_dir'], '-p', str(data['port']), '-c', 'listen_addresses=', '-c', 'max_connections=12'])
            run_logged([pg / 'pg_ctl', '-D', data['data_dir'], '-l', root / 'server.log', '-o', options, '-w', 'start'], root)
        session = connect(descriptor)
        session.close()
    else:
        session = connect(descriptor)
        session.close()
        run_logged([pg / 'pg_ctl', '-D', data['data_dir'], '-w', '-m', 'fast', 'stop'], root)
    return {'database': data['database'], 'socket': data['socket_dir'], 'port': data['port'], 'state': command}


def pack_snapshot_indexes(session):
    """Pack existing B-trees for this immutable snapshot without removing indexes."""
    from db.source_connection import assert_proof_session
    from psycopg2 import sql
    assert_proof_session(session)
    with session.connection.cursor() as cur:
        cur.execute("SELECT n.nspname,i.relname FROM pg_class i JOIN pg_namespace n ON n.oid=i.relnamespace JOIN pg_am a ON a.oid=i.relam WHERE i.relkind='i' AND a.amname='btree' AND n.nspname=ANY(%s) ORDER BY 1,2", (list(SCHEMAS) + ['cricket_compact_v1'],))
        indexes = cur.fetchall()
        for schema, name in indexes:
            target = sql.SQL('{}.{}').format(sql.Identifier(schema), sql.Identifier(name))
            cur.execute(sql.SQL('ALTER INDEX {} SET (fillfactor=100)').format(target))
            cur.execute(sql.SQL('REINDEX INDEX {}').format(target))
    session.connection.commit()
    return {'existing_btree_indexes_repacked': len(indexes), 'fillfactor': 100,
            'scope': 'immutable verified snapshot; no indexes or source records removed'}


def build_database(args):
    lock = load_lock()
    verify_engine(lock)
    verify_payload(args.payload_dir, lock)
    pg = postgres_bin(args.pg_bin)
    root = args.work_dir
    if root.exists() or root.is_symlink():
        raise FileExistsError('Work directory already exists')
    descriptor = None
    started = time.monotonic()
    report = {'snapshot': lock['snapshot'], 'source_full_ready': False, 'historical_database_bytes': lock['historical_database_bytes']}
    try:
        descriptor = create_cluster(root, pg, args.port)
        if str(ENGINE) not in sys.path:
            sys.path.insert(0, str(ENGINE))
        from ingestion.compact_store import import_compact_subset
        from db.compact_verify import verify_compact_database
        print('Importing authenticated compact snapshot...', flush=True)
        report['import'] = import_compact_subset(descriptor, MANIFEST, args.payload_dir, root / 'staging')
        print('Verifying every delivery and source relation...', flush=True)
        report['base_verification'] = verify_compact_database(descriptor, MANIFEST, args.payload_dir, root / 'base-verification.json')
        from db.player_summary import install_player_summaries
        from db.matchup_read import install_matchup_summaries
        from db.partnership_read import install_partnership_summaries
        from db.opposition_read import install_opposition_summaries
        from db.team_summary import install_team_summaries
        installers = [('player', install_player_summaries), ('matchup', install_matchup_summaries),
                      ('partnership', install_partnership_summaries), ('opposition', install_opposition_summaries), ('team', install_team_summaries)]
        report['installations'] = {}
        session = connect(descriptor)
        try:
            for name, install in installers:
                print('Installing final ' + name + ' summaries...', flush=True)
                report['installations'][name] = install(session)
                session.connection.commit()
            print('Packing all existing snapshot indexes...', flush=True)
            report['index_packing'] = pack_snapshot_indexes(session)
        finally:
            session.connection.rollback()
            session.close()
        from scripts.player_read_proof import verify_player_summaries
        from scripts.profile_detail_proof import verify_profile_detail
        from scripts.opposition_proof import verify_opposition
        from scripts.team_read_proof import verify_team_summaries
        report['oracles'] = {}
        for name, oracle in [('player', verify_player_summaries), ('profile_partnership', verify_profile_detail),
                             ('matchup_opposition', verify_opposition), ('team_venue_cards', verify_team_summaries)]:
            print('Independent full-data oracle: ' + name + '...', flush=True)
            session = connect(descriptor)
            try:
                # connect_proof authenticates the server with a SELECT transaction.
                session.connection.rollback()
                session.connection.set_session(readonly=True, isolation_level='REPEATABLE READ')
                report['oracles'][name] = oracle(session)
            finally:
                session.connection.rollback()
                session.close()
        session = connect(descriptor)
        try:
            with session.connection.cursor() as cur:
                cur.execute('SELECT schemaname,matviewname,ispopulated FROM pg_matviews WHERE schemaname=ANY(%s)', (list(SCHEMAS),))
                views = cur.fetchall()
                if {s + '.' + n for s, n, _ in views} != EXPECTED_VIEWS or not all(p for _, _, p in views):
                    raise ValueError('All ten final materializations must exist and be populated')
                cur.execute('SELECT COUNT(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY(%s) AND (NOT i.indisvalid OR NOT i.indisready)', (list(SCHEMAS) + ['cricket_compact_v1'],))
                if cur.fetchone()[0]:
                    raise ValueError('Invalid or unready index')
                counts = {}
                for name in sorted(EXPECTED_VIEWS):
                    cur.execute('SELECT COUNT(*) FROM ' + name)
                    counts[name] = cur.fetchone()[0]
                cur.execute('SELECT pg_database_size(current_database())')
                size = cur.fetchone()[0]
        finally:
            session.close()
        report.update(database_bytes=size, **size_flags(size, lock), materialized_views=counts,
                      all_ten_populated=True, indexes_valid=True, elapsed_seconds=round(time.monotonic() - started, 3))
        if not report['under_budget']:
            raise ValueError('Fresh build exceeds the verified storage budget; do not deploy it')
        if args.export_dump:
            data = json.loads(descriptor.read_text())
            run_logged([pg / 'pg_dump', '-h', data['socket_dir'], '-p', data['port'], '-U', data['user'],
                        '-d', data['database'], '-Fc', '--no-owner', '--no-acl', '-f', root / 'database.dump'], root)
            report['dump_sha256'] = digest(root / 'database.dump')
        report['status'] = 'passed'
        private_json(root / 'build-report.json', report)
        return {k: report[k] for k in ['status', 'snapshot', 'database_bytes', 'under_budget', 'all_ten_populated', 'source_full_ready', 'elapsed_seconds']}
    except Exception as exc:
        report.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        if root.is_dir() and not (root / 'build-report.json').exists():
            private_json(root / 'build-report.json', report)
        raise
    finally:
        if descriptor is not None and (not args.leave_running or report.get('status') != 'passed'):
            lifecycle(root, 'stop')
        elif descriptor is None and (root / 'data/postmaster.pid').is_file():
            # This path was absent on entry and created only by this invocation.
            # Bootstrap may have started the server before createdb failed.
            run_logged([pg / 'pg_ctl', '-D', root / 'data', '-w', '-m', 'fast', 'stop'], root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    fetch = sub.add_parser('fetch', help='Download and authenticate the frozen input snapshot')
    fetch.add_argument('--output', type=Path, required=True)
    fetch.add_argument('--archive', type=Path, help='Use an already downloaded authenticated snapshot archive')
    build = sub.add_parser('build', help='Fresh compact import, final ten summaries, independent proof and size receipt')
    build.add_argument('--payload-dir', type=Path, required=True)
    build.add_argument('--work-dir', type=Path, required=True)
    build.add_argument('--pg-bin')
    build.add_argument('--port', type=int, default=55439)
    build.add_argument('--leave-running', action='store_true')
    build.add_argument('--export-dump', action='store_true')
    for name in ('start', 'stop'):
        p = sub.add_parser(name)
        p.add_argument('--work-dir', type=Path, required=True)
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error('Use Python 3.11 or newer')
    try:
        if args.command == 'fetch':
            result = fetch_payload(args.output, args.archive)
        elif args.command == 'build':
            result = build_database(args)
        else:
            verify_engine(load_lock())
            result = lifecycle(args.work_dir, args.command)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, type(exc).__name__ + ': ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
