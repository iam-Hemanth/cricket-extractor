#!/usr/bin/env python3
"""Offline source-backed curated selection; no extraction, publication or deletion."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile

from consolidate import scan_and_consolidate
from src.match_filter import POLICY_ID,scope_decision,source_scope_facts


def build_source_index(archives):
    index={}
    for path in sorted(map(Path,archives)):
        with tarfile.open(path,'r|gz') as archive:
            for member in archive:
                if not member.isfile() or Path(member.name).name!='scorecard.json.gz':continue
                mid=next((p for p in Path(member.name).parts if p.isdigit() and len(p)>=3),None)
                if mid is None:continue
                body=archive.extractfile(member).read();details=json.loads(gzip.decompress(body))
                facts=source_scope_facts(details)
                public=(details.get('match') or {}).get('objectId')
                mismatch=public is not None and str(public)!=mid
                evidence={'archive':path.name,'member':member.name,'compressed_sha256':hashlib.sha256(body).hexdigest()}
                old=index.get(mid)
                if old:
                    old['source_conflict']|=old['facts']!=facts or mismatch
                    old['evidence'].append(evidence)
                else:index[mid]={'facts':facts,'source_conflict':mismatch,'evidence':[evidence]}
    return index


def prepare_launch(input_dir,source_index,output_dir):
    source=Path(input_dir);out=Path(output_dir)
    if out.exists():raise ValueError('Launch output must be a fresh directory')
    out.mkdir(parents=True);stage=out/'scope_inputs';stage.mkdir();matches=out/'matches'
    rows={};held={};scope_counts=Counter()
    candidate_ids={p.stem for p in source.glob('*.json') if p.stem.isdigit()}
    errors_file=source/'failed_matches.json';previous_errors=json.loads(errors_file.read_text()) if errors_file.exists() else {}
    for mid in sorted(set(source_index)|candidate_ids):
        record=source_index.get(mid)
        if record is None or record['source_conflict']:
            decision={'status':'held','reason':'source_metadata_unavailable_or_conflicting','policy_id':POLICY_ID}
        else:decision=scope_decision(record['facts'],mid,verified=True)
        row=dict(decision);row['source_evidence']=record['evidence'] if record else []
        if record:row['teams']=record['facts']['teams'];row['competition']=record['facts']['competition']
        rows[mid]=row
        if decision['status']=='excluded':continue
        if decision['status']=='held':held[mid]=decision['reason'];continue
        path=source/(mid+'.json')
        if not path.exists():
            row.update(status='held',reason='candidate_integrity_review')
            held[mid]=previous_errors.get(mid,'No accepted candidate for source-qualified match');continue
        try:
            m=json.loads(path.read_text());meta=m.get('meta') or {};facts=record['facts']
            keys=('source_classification','source_classification_presence','source_competition_trophy_id',
                  'record_class','format_bucket','gender','classification_status','record_stats_eligible')
            if m.get('match_id')!=mid or any(meta.get(k)!=facts.get(k) for k in keys):
                row.update(status='held',reason='candidate_source_identity_conflict');held[mid]=row['reason'];continue
        except (ValueError,AttributeError,TypeError):
            row.update(status='held',reason='unreadable_candidate');held[mid]=row['reason'];continue
        # Hard links preserve bytes and avoid duplicate staging storage. Final output
        # is separately validated/written; no rejected input can remain in matches.
        try:os.link(path,stage/path.name)
        except OSError:shutil.copy2(path,stage/path.name)
    (stage/'failed_matches.json').write_text(json.dumps(held,indent=2)+'\n')
    manifest=scan_and_consolidate([stage],matches,out/'lake_manifest.json',out/'curated_launch.tar.gz')
    accepted={p.stem for p in matches.glob('*.json') if p.stem.isdigit()}
    for mid,row in rows.items():
        if row['status']=='selected' and mid not in accepted:
            row.update(status='held',reason='candidate_integrity_review')
        scope_counts[row['status']]+=1
    report={'policy_id':POLICY_ID,'counts':dict(scope_counts),'source_matches':len(source_index),
            'admitted_candidate_ids':sorted(accepted),'rows':rows,'candidate_sha256':{
                mid:hashlib.sha256((matches/(mid+'.json')).read_bytes()).hexdigest() for mid in sorted(accepted)},
            'afghanistan':{mid:row for mid,row in rows.items() if 'Afghanistan' in row.get('teams',[])},
            'manifest':{'matches':manifest['total_matches'],'held':manifest['unreconciled_failures']['count']}}
    for key in ('selected','held','excluded'):report['counts'].setdefault(key,0)
    assert len(accepted)==manifest['total_matches']==report['counts']['selected']
    (out/'selection_report.json').write_text(json.dumps(report,indent=2)+'\n')
    shutil.rmtree(stage)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir',required=True)
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--raw-archives',type=str)
    parser.add_argument('--source-index',type=str)
    args=parser.parse_args()
    if bool(args.raw_archives)==bool(args.source_index):parser.error('Provide exactly one raw-archives or source-index input')
    index=(json.loads(Path(args.source_index).read_text()) if args.source_index else
           build_source_index(Path(args.raw_archives).glob('raw_batch_*.tar.gz')))
    report=prepare_launch(args.input_dir,index,args.output_dir)
    Path(args.output_dir,'source_scope_index.json').write_text(json.dumps(index,indent=2)+'\n')
    print(json.dumps({'policy_id':POLICY_ID,'counts':report['counts'],'afghanistan':dict(Counter(r['status'] for r in report['afghanistan'].values()))},indent=2))


if __name__=='__main__':main()
