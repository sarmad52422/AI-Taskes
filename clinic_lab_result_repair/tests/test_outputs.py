import copy
import importlib
import json
import sys
from pathlib import Path
sys.path.insert(0, "/app")

DATA = Path('/app/lab_data.json')

def write_doc(doc):
    DATA.write_text(json.dumps(copy.deepcopy(doc), indent=2))

def run_worker():
    import lab_worker
    importlib.reload(lab_worker)
    return lab_worker.run()

def base_doc():
    return {'jobs': [], 'samples': {'P1': {'CBC': {'value': 'normal', 'unit': 'flag', 'version': 1}, 'KFT': {'value': 'clear', 'unit': 'flag', 'version': 2}}}, 'result_rows': []}

def test_return_value_equals_persisted_json():
    doc = base_doc(); doc['jobs']=[{'job_id':'J1','patient_id':'P1','required_tests':['CBC'],'status':'queued','attempts':0,'max_attempts':2,'metadata':{}}]
    write_doc(doc); returned=run_worker(); persisted=json.loads(DATA.read_text())
    assert returned == persisted

def test_happy_path_completes_job_and_writes_rows():
    doc = base_doc(); doc['jobs']=[{'job_id':'J1','patient_id':'P1','required_tests':['CBC','KFT'],'status':'queued','attempts':0,'max_attempts':2,'metadata':{}}]
    write_doc(doc); out=run_worker()
    assert out['jobs'][0]['status']=='completed'
    assert out['jobs'][0]['attempts']==0
    assert {(r['test_code'], r['owner_job_id']) for r in out['result_rows']} == {('CBC','J1'),('KFT','J1')}

def test_missing_sample_retries_without_partial_writes():
    doc = base_doc(); doc['jobs']=[{'job_id':'J1','patient_id':'P1','required_tests':['CBC','LFT'],'status':'queued','attempts':0,'max_attempts':2,'metadata':{'note':'keep'}}]
    write_doc(doc); out=run_worker(); job=out['jobs'][0]
    assert job['status']=='retrying'; assert job['attempts']==1
    assert job['metadata']['retry_reason']=='missing_sample'; assert job['metadata']['note']=='keep'
    assert out['result_rows']==[]

def test_retrying_job_recovers_and_clears_stale_retry_metadata():
    doc = base_doc(); doc['samples']['P1']['LFT']={'value':'ok','unit':'flag','version':3}
    doc['jobs']=[{'job_id':'J1','patient_id':'P1','required_tests':['CBC','LFT'],'status':'retrying','attempts':1,'max_attempts':2,'metadata':{'retry_reason':'missing_sample','last_error':'late','note':'keep'}}]
    write_doc(doc); out=run_worker(); job=out['jobs'][0]
    assert job['status']=='completed'; assert job['attempts']==1
    assert job['metadata']=={'note':'keep'}
    assert {r['test_code'] for r in out['result_rows']} == {'CBC','LFT'}

def test_exhausted_missing_sample_moves_to_failed():
    doc = base_doc(); doc['jobs']=[{'job_id':'J1','patient_id':'P1','required_tests':['LFT'],'status':'retrying','attempts':1,'max_attempts':2,'metadata':{}}]
    write_doc(doc); out=run_worker(); job=out['jobs'][0]
    assert job['status']=='failed'; assert job['attempts']==2
    assert job['metadata']['retry_reason']=='missing_sample'

def test_terminal_jobs_remain_unchanged():
    doc = base_doc(); terminal={'job_id':'J1','patient_id':'P1','required_tests':['CBC'],'status':'completed','attempts':5,'max_attempts':5,'metadata':{'done':True}}
    doc['jobs']=[copy.deepcopy(terminal)]; doc['result_rows']=[{'patient_id':'P1','test_code':'CBC','value':'old','unit':'flag','source_version':0,'owner_job_id':'J1'}]
    before=copy.deepcopy(doc); write_doc(doc); out=run_worker()
    assert out == before

def test_repeated_runs_do_not_duplicate_rows_or_increment_attempts():
    doc = base_doc(); doc['jobs']=[{'job_id':'J1','patient_id':'P1','required_tests':['CBC'],'status':'queued','attempts':0,'max_attempts':2,'metadata':{}}]
    write_doc(doc); first=run_worker(); write_doc(first); second=run_worker()
    assert second['jobs'][0]['attempts']==0
    assert len([r for r in second['result_rows'] if r['owner_job_id']=='J1' and r['test_code']=='CBC']) == 1

def test_existing_partial_state_is_reused_and_topped_up():
    doc = base_doc(); doc['jobs']=[{'job_id':'J1','patient_id':'P1','required_tests':['CBC','KFT'],'status':'queued','attempts':0,'max_attempts':2,'metadata':{}}]
    doc['result_rows']=[{'patient_id':'P1','test_code':'CBC','value':'normal','unit':'flag','source_version':1,'owner_job_id':'J1'}]
    write_doc(doc); out=run_worker()
    assert len(out['result_rows'])==2
    assert {r['test_code'] for r in out['result_rows']}=={'CBC','KFT'}

def test_stale_same_owner_row_is_refreshed_and_removed_if_no_longer_required():
    doc = base_doc(); doc['jobs']=[{'job_id':'J1','patient_id':'P1','required_tests':['CBC'],'status':'queued','attempts':0,'max_attempts':2,'metadata':{}}]
    doc['result_rows']=[{'patient_id':'P1','test_code':'CBC','value':'old','unit':'flag','source_version':0,'owner_job_id':'J1'}, {'patient_id':'P1','test_code':'KFT','value':'clear','unit':'flag','source_version':2,'owner_job_id':'J1'}]
    write_doc(doc); out=run_worker()
    assert out['result_rows']==[{'patient_id':'P1','test_code':'CBC','value':'normal','unit':'flag','source_version':1,'owner_job_id':'J1'}]

def test_other_owner_conflict_retries_without_touching_protected_row():
    doc = base_doc(); doc['jobs']=[{'job_id':'OWNER','patient_id':'P1','required_tests':['CBC'],'status':'completed','attempts':0,'max_attempts':2,'metadata':{}}, {'job_id':'J2','patient_id':'P1','required_tests':['CBC'],'status':'queued','attempts':0,'max_attempts':2,'metadata':{}}]
    protected={'patient_id':'P1','test_code':'CBC','value':'normal','unit':'flag','source_version':1,'owner_job_id':'OWNER'}
    doc['result_rows']=[copy.deepcopy(protected)]
    write_doc(doc); out=run_worker(); job=out['jobs'][1]
    assert job['status']=='retrying'; assert job['attempts']==1
    assert job['metadata']['retry_reason']=='protected_result_conflict'
    assert out['result_rows']==[protected]

def test_exhausted_conflict_fails_without_touching_protected_record():
    doc = base_doc(); doc['jobs']=[{'job_id':'OWNER','patient_id':'P1','required_tests':['CBC'],'status':'completed','attempts':0,'max_attempts':2,'metadata':{}}, {'job_id':'J2','patient_id':'P1','required_tests':['CBC'],'status':'retrying','attempts':1,'max_attempts':2,'metadata':{}}]
    protected={'patient_id':'P1','test_code':'CBC','value':'normal','unit':'flag','source_version':1,'owner_job_id':'OWNER'}
    doc['result_rows']=[copy.deepcopy(protected)]
    write_doc(doc); out=run_worker(); job=out['jobs'][1]
    assert job['status']=='failed'; assert job['attempts']==2
    assert job['metadata']['retry_reason']=='protected_result_conflict'
    assert out['result_rows']==[protected]
