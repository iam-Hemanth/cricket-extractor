#!/usr/bin/env python3
"""Verify admitted source matches offline and write a fresh, explicit pilot receipt."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ingestion.source_identity import build_identity_index, verify_source_bytes
from ingestion.source_adapter import normalize_match
from ingestion.source_checks import build_match_checks
from ingestion.source_attribution import EVIDENCE_PATH, EVIDENCE_SHA256, load_attribution_evidence

POLICY_ID = 'curated_launch_20261008_v1'
FROZEN_SELECTION_SHA256 = '18156a9c5ad4b53c33ebc961872fd6db2837d73a7d6f9ba59601af0adc09db75'


def _sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


CODE_PATHS = ('ingestion/source_identity.py', 'ingestion/source_adapter.py', 'ingestion/source_checks.py', 'ingestion/source_attribution.py', 'scripts/verify_source_adapter.py')
LOADED_CODE_SHA256 = {name: _sha(ROOT / name) for name in CODE_PATHS}


def _selected_ids(receipt, match_ids):
    selected = receipt['admitted_candidate_ids']
    hashes = receipt['candidate_sha256']
    if receipt.get('policy_id') != POLICY_ID:
        raise ValueError('Unexpected admission policy')
    if (not isinstance(selected, list) or any(not isinstance(mid, str) or not re.fullmatch(r'[0-9]+', mid) for mid in selected)
            or len(set(selected)) != len(selected) or set(hashes) != set(selected)
            or type(receipt['counts']['selected']) is not int or receipt['counts']['selected'] != len(selected)):
        raise ValueError('Inconsistent selected ID/hash/count receipt')
    if any(receipt['rows'][mid]['status'] != 'selected' for mid in selected):
        raise ValueError('Receipt admits a held/excluded match')
    if any(not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest) for digest in hashes.values()):
        raise ValueError('Invalid source hash receipt')
    requested = selected if match_ids is None else match_ids
    if (not isinstance(requested, list) or not requested or len(set(requested)) != len(requested)
            or any(mid not in hashes for mid in requested)):
        raise ValueError('Requested cohort is not a unique admitted subset')
    return requested


def verify_snapshot(selection_report: Path, input_dir: Path, people_csv: Path,
                    output_dir: Path, match_ids: list[str] | None = None) -> dict:
    if output_dir.exists():
        raise FileExistsError('Output must be a fresh directory')
    receipt_bytes = selection_report.read_bytes()
    if hashlib.sha256(receipt_bytes).hexdigest() != FROZEN_SELECTION_SHA256:
        raise ValueError('Admission receipt differs from independently pinned SHA256')
    receipt = json.loads(receipt_bytes)
    requested = _selected_ids(receipt, match_ids)
    people_bytes = people_csv.read_bytes()
    registry_rows = list(csv.DictReader(io.StringIO(people_bytes.decode('utf-8'), newline='')))
    build_identity_index(registry_rows, [])  # Validate all registry keys once.
    registry = {row['identifier']: row for row in registry_rows}
    load_attribution_evidence()
    output_dir.mkdir(parents=True, exist_ok=False)
    global_index = {'players': {}, 'aliases': {}}
    errors, processed = [], 0
    metrics = {'deliveries': 0, 'actual_legal_balls': 0, 'unknown_non_striker_deliveries': 0,
               'unknown_phase_deliveries': 0, 'separate_penalty_innings': 0, 'source_only_timed_out_wickets': 0,
               'hundred_matches': 0, 'matches_with_unconfirmed_xi': 0}
    with (output_dir / 'matches.jsonl').open('w') as matches, (output_dir / 'checks.jsonl').open('w') as checks:
        for mid in requested:
            try:
                path = input_dir / f'{mid}.json'
                if path.is_symlink() or not path.is_file():
                    raise ValueError('Source path is not a regular match file')
                source = verify_source_bytes(path.read_bytes(), mid, receipt['candidate_sha256'][mid])
                current = list(source['players'].values())
                prior = [global_index['players'][p['player_id']] for p in current if p['player_id'] in global_index['players']]
                relevant_cids = {p['cricsheet_id'] for p in current + prior if p.get('cricsheet_id')}
                index = build_identity_index([registry[cid] for cid in relevant_cids if cid in registry], prior + current)
                for cid, pid in index['aliases'].items():
                    if cid in global_index['aliases'] and global_index['aliases'][cid] != pid:
                        raise ValueError('Cross-match alias collision')
                envelope = normalize_match(source, receipt['candidate_sha256'][mid], index)
                result = build_match_checks(envelope)
                checks.write(json.dumps(result, separators=(',', ':'), allow_nan=False) + '\n')
                if result['errors']:
                    errors.append({'match_id': mid, 'errors': result['errors']})
                    continue
                match_line = json.dumps(envelope, separators=(',', ':'), allow_nan=False) + '\n'
                match_metrics = dict.fromkeys(metrics, 0)
                match_metrics['hundred_matches'] = source['meta'].get('playing_format') == 'hundred_ball'
                match_metrics['matches_with_unconfirmed_xi'] = any(v != 'confirmed' for v in source['meta'].get('playing_xi_status', {}).values())
                for row in result['innings']:
                    match_metrics['deliveries'] += row['delivered_balls']
                    match_metrics['actual_legal_balls'] += row['actual_legal_balls']
                    match_metrics['unknown_non_striker_deliveries'] += row['unknown_non_striker_deliveries']
                    match_metrics['unknown_phase_deliveries'] += row['phase_counts'].get('unknown', {}).get('deliveries', 0)
                    match_metrics['separate_penalty_innings'] += row['innings_penalty_runs'] > 0
                    match_metrics['source_only_timed_out_wickets'] += row['source_only_timed_out_count'] or 0
                matches.write(match_line)
                global_index['players'].update(index['players'])
                global_index['aliases'].update(index['aliases'])
                processed += 1
                for key, value in match_metrics.items():
                    metrics[key] += value
            except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
                errors.append({'match_id': mid, 'errors': [f'{type(exc).__name__}: {exc}']})
    identity_path = output_dir / 'identity-crosswalk.json'
    identity_path.write_text(json.dumps(global_index, indent=2, allow_nan=False) + '\n')
    if any(_sha(ROOT / name) != digest for name, digest in LOADED_CODE_SHA256.items()):
        errors.append({'scope': 'code', 'errors': ['Source code changed after verifier import; output is not ready']})
    if _sha(EVIDENCE_PATH) != EVIDENCE_SHA256:
        errors.append({'scope': 'attribution', 'errors': ['Attribution evidence changed; output is not ready']})
    result = {
        'adapter_contract': 'd3_offline_v1', 'policy_id': POLICY_ID,
        'selection_report_sha256': hashlib.sha256(receipt_bytes).hexdigest(),
        'trusted_selection_sha256': FROZEN_SELECTION_SHA256,
        'attribution_evidence_sha256': EVIDENCE_SHA256,
        'people_csv_sha256': hashlib.sha256(people_bytes).hexdigest(),
        'mode': 'snapshot' if match_ids is None else 'cohort', 'admitted_snapshot_matches': len(receipt['admitted_candidate_ids']),
        'expected_matches': len(requested), 'attempted_matches': len(requested), 'processed_matches': processed,
        'ready': not errors and processed == len(requested), 'errors': errors, 'metrics': metrics,
        'canonical_players': len(global_index['players']), 'cricsheet_aliases': len(global_index['aliases']),
        'source_only_players': sum(not p.get('cricsheet_id') for p in global_index['players'].values()),
        'source_code_sha256': dict(LOADED_CODE_SHA256),
        'output_sha256': {name: _sha(output_dir / name) for name in ('matches.jsonl', 'checks.jsonl', 'identity-crosswalk.json')},
        'limits': ['Offline transformation only; SQL/API/page compatibility unproved',
                   'Absent award metadata and source-only dismissal attribution are not filled',
                   'Tactical presence is not independent accuracy; held source fixtures remain unavailable'],
    }
    (output_dir / 'verification.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('selection-report', 'input-dir', 'people-csv', 'output-dir'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--match-ids', help='Explicit comma-separated admitted cohort; otherwise full selected snapshot')
    args = parser.parse_args()
    try:
        result = verify_snapshot(args.selection_report, args.input_dir, args.people_csv, args.output_dir,
                                 None if args.match_ids is None else args.match_ids.split(','))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f'{type(exc).__name__}: {exc}\n')
    print(json.dumps({key: result[key] for key in ('ready', 'mode', 'expected_matches', 'processed_matches', 'canonical_players', 'errors')}, indent=2))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    sys.exit(main())
