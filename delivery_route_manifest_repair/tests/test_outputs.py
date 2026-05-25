import copy, json, sys
from pathlib import Path
sys.path.insert(0, '/app')
import manifest_worker
STATE = Path('/app/route_state.json')

def write_doc(doc): STATE.write_text(json.dumps(doc, indent=2), encoding='utf-8')
def read_doc(): return json.loads(STATE.read_text(encoding='utf-8'))

def base_doc():
    return {
        'stops': {
            'A': {'route_id': 'R1', 'driver_id': 'D1', 'address': '10 Main', 'packages': ['P1','P2','P1'], 'scan_version': 4},
            'B': {'route_id': 'R1', 'driver_id': 'D1', 'address': '12 Main', 'packages': ['P3'], 'scan_version': 2},
            'X': {'route_id': 'R9', 'driver_id': 'D9', 'address': '99 Side', 'packages': ['PX'], 'scan_version': 1},
        },
        'manifests': {}, 'audit_log': [],
        'jobs': [{'id':'JOB1','route_id':'R1','driver_id':'D1','expected_stop_ids':['A','B','A'],'status':'pending','attempts':0,'max_attempts':2,'metadata':{'batch':'am'}}],
    }

def run_doc(doc):
    write_doc(copy.deepcopy(doc)); result = manifest_worker.run(); persisted = read_doc(); assert result == persisted; return persisted

def test_happy_path_deduplicates_packages_and_expected_stops():
    out=run_doc(base_doc()); job=out['jobs'][0]
    assert job['status']=='completed'; assert job['touched_stop_ids']==['A','B']
    assert job['summary']=={'stop_count':2,'package_count':3}
    assert out['manifests']['A']['package_ids']==['P1','P2']; assert len(out['audit_log'])==2

def test_terminal_jobs_are_byte_for_byte_preserved():
    doc=base_doc(); terminal={'id':'DONE','route_id':'R1','driver_id':'D1','expected_stop_ids':['A'],'status':'completed','attempts':9,'max_attempts':9,'summary':{'stop_count':99},'metadata':{'keep':True}}
    doc['jobs']=[terminal]; out=run_doc(doc); assert out['jobs'][0]==terminal; assert out['manifests']=={}; assert out['audit_log']==[]

def test_failed_and_cancelled_jobs_are_terminal():
    doc=base_doc(); failed={'id':'FAIL','route_id':'R1','driver_id':'D1','expected_stop_ids':['A'],'status':'failed','attempts':3,'max_attempts':3,'last_error':'old'}; cancelled={'id':'CXL','route_id':'R1','driver_id':'D1','expected_stop_ids':['B'],'status':'cancelled','attempts':0,'max_attempts':1}
    doc['jobs']=[failed,cancelled]; out=run_doc(doc); assert out['jobs']==[failed,cancelled]; assert out['manifests']=={}

def test_missing_stop_retries_without_partial_manifest_or_audit():
    doc=base_doc(); doc['jobs'][0]['expected_stop_ids']=['A','MISSING']; out=run_doc(doc); job=out['jobs'][0]
    assert job['status']=='retrying'; assert job['attempts']==1; assert job['retry_reason']=='source_unavailable'; assert 'MISSING' in job['last_error']; assert out['manifests']=={}; assert out['audit_log']==[]

def test_wrong_route_retries_without_partial_write():
    doc=base_doc(); doc['jobs'][0]['expected_stop_ids']=['A','X']; out=run_doc(doc)
    assert out['jobs'][0]['status']=='retrying'; assert out['jobs'][0]['retry_reason']=='source_unavailable'; assert out['manifests']=={}

def test_missing_stop_exhausts_to_failed():
    doc=base_doc(); doc['jobs'][0].update({'expected_stop_ids':['MISSING'],'status':'retrying','attempts':1,'max_attempts':2}); out=run_doc(doc)
    assert out['jobs'][0]['status']=='failed'; assert out['jobs'][0]['attempts']==2; assert out['manifests']=={}

def test_retrying_job_recovers_and_clears_only_stale_retry_fields():
    doc=base_doc(); doc['jobs'][0].update({'status':'retrying','attempts':1,'last_error':'old','retry_reason':'source_unavailable','metadata':{'batch':'am','note':'keep'}}); out=run_doc(doc); job=out['jobs'][0]
    assert job['status']=='completed'; assert 'last_error' not in job; assert 'retry_reason' not in job; assert job['metadata']=={'batch':'am','note':'keep'}

def test_same_owner_fresh_manifest_is_reused_and_audit_not_duplicated():
    doc=base_doc(); doc['manifests']['A']={'stop_id':'A','route_id':'R1','driver_id':'D1','package_ids':['P1','P2'],'owner_job_id':'JOB1','source_version':4,'status':'manifested','custom':'keep'}; doc['audit_log']=[{'job_id':'JOB1','stop_id':'A','event':'manifested','source_version':4}]
    out=run_doc(doc); assert out['manifests']['A']['custom']=='keep'; assert out['audit_log'].count({'job_id':'JOB1','stop_id':'A','event':'manifested','source_version':4})==1; assert len(out['audit_log'])==2

def test_same_owner_stale_manifest_is_refreshed():
    doc=base_doc(); doc['manifests']['A']={'stop_id':'A','route_id':'R1','driver_id':'D1','package_ids':['OLD'],'owner_job_id':'JOB1','source_version':1,'status':'manifested'}; out=run_doc(doc)
    assert out['manifests']['A']['package_ids']==['P1','P2']; assert out['manifests']['A']['source_version']==4

def test_other_owner_conflict_retries_without_touching_protected_row_or_audit():
    doc=base_doc(); protected={'stop_id':'A','route_id':'R1','driver_id':'D1','package_ids':['LOCKED'],'owner_job_id':'OTHER','source_version':4,'status':'manifested'}; doc['manifests']['A']=protected; doc['jobs'].append({'id':'OTHER','route_id':'R1','driver_id':'D1','expected_stop_ids':[],'status':'retrying','attempts':0,'max_attempts':3})
    out=run_doc(doc); assert out['manifests']['A']==protected; assert out['audit_log']==[]; assert out['jobs'][0]['status']=='retrying'; assert out['jobs'][0]['retry_reason']=='manifest_conflict'

def test_conflict_exhausts_without_touching_protected_row():
    doc=base_doc(); protected={'stop_id':'A','route_id':'R1','driver_id':'D1','package_ids':['LOCKED'],'owner_job_id':'OTHER','source_version':4,'status':'manifested'}; doc['manifests']['A']=protected; doc['jobs'][0].update({'status':'retrying','attempts':1,'max_attempts':2}); doc['jobs'].append({'id':'OTHER','route_id':'R1','driver_id':'D1','expected_stop_ids':[],'status':'pending','attempts':0,'max_attempts':2})
    out=run_doc(doc); assert out['jobs'][0]['status']=='failed'; assert out['jobs'][0]['retry_reason']=='manifest_conflict'; assert out['manifests']['A']==protected

def test_manifest_owned_by_terminal_job_can_be_reclaimed():
    doc=base_doc(); doc['manifests']['A']={'stop_id':'A','route_id':'R1','driver_id':'D1','package_ids':['OLD'],'owner_job_id':'OLDJOB','source_version':1,'status':'manifested'}; doc['jobs'].append({'id':'OLDJOB','route_id':'R1','driver_id':'D1','expected_stop_ids':['A'],'status':'completed','attempts':1,'max_attempts':1})
    out=run_doc(doc); assert out['manifests']['A']['owner_job_id']=='JOB1'; assert out['manifests']['A']['package_ids']==['P1','P2']

def test_repeated_runs_after_completion_are_stable():
    first=run_doc(base_doc()); second=run_doc(first); assert second==first
