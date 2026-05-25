import copy, json, sys
from pathlib import Path
sys.path.insert(0, '/app')
import budget_worker
STATE = Path('/app/ad_state.json')

def write_doc(doc): STATE.write_text(json.dumps(doc, indent=2), encoding='utf-8')
def read_doc(): return json.loads(STATE.read_text(encoding='utf-8'))

def base_doc():
    return {
        'campaigns': {'C1': {'account_id':'A1','daily_budget_cents':10000,'status':'active'}, 'PAUSED': {'account_id':'A1','daily_budget_cents':5000,'status':'paused'}},
        'spend_events': {'E1': {'campaign_id':'C1','account_id':'A1','window':'2026-05-25','amount_cents':1200,'version':3}, 'E2': {'campaign_id':'C1','account_id':'A1','window':'2026-05-25','amount_cents':1800,'version':1}, 'E3': {'campaign_id':'C1','account_id':'A1','window':'2026-05-26','amount_cents':900,'version':1}, 'E4': {'campaign_id':'PAUSED','account_id':'A1','window':'2026-05-25','amount_cents':600,'version':1}},
        'budget_ledger': {}, 'manual_holds': {'H1': {'campaign_id':'C1','account_id':'A1','window':'2026-05-25','amount_cents':500,'reason':'manager_hold'}},
        'jobs': [{'id':'JOB1','campaign_id':'C1','account_id':'A1','window':'2026-05-25','expected_event_ids':['E1','E2','E1'],'status':'pending','attempts':0,'max_attempts':2,'metadata':{'feed':'adx'}}],
    }

def run_doc(doc):
    write_doc(copy.deepcopy(doc)); result=budget_worker.run(); persisted=read_doc(); assert result==persisted; return persisted

def test_happy_path_deduplicates_events_and_computes_budget_summary():
    out=run_doc(base_doc()); job=out['jobs'][0]
    assert job['status']=='completed'; assert job['processed_event_ids']==['E1','E2']; assert job['summary']=={'event_count':2,'spend_cents':3000,'remaining_budget_cents':7000}; assert out['budget_ledger']['E1']['kind']=='exchange_spend'; assert out['manual_holds']==base_doc()['manual_holds']

def test_terminal_completed_job_is_not_rewritten():
    doc=base_doc(); terminal={'id':'DONE','campaign_id':'C1','account_id':'A1','window':'2026-05-25','expected_event_ids':['E1'],'status':'completed','attempts':8,'max_attempts':8,'summary':{'event_count':99},'metadata':{'keep':True}}
    doc['jobs']=[terminal]; out=run_doc(doc); assert out['jobs'][0]==terminal; assert out['budget_ledger']=={}

def test_failed_and_cancelled_jobs_are_terminal():
    doc=base_doc(); failed={'id':'F','campaign_id':'C1','account_id':'A1','window':'2026-05-25','expected_event_ids':['E1'],'status':'failed','attempts':2,'max_attempts':2,'last_error':'old'}; cancelled={'id':'X','campaign_id':'C1','account_id':'A1','window':'2026-05-25','expected_event_ids':['E2'],'status':'cancelled','attempts':0,'max_attempts':1}
    doc['jobs']=[failed,cancelled]; out=run_doc(doc); assert out['jobs']==[failed,cancelled]; assert out['budget_ledger']=={}

def test_missing_event_retries_without_partial_ledger_writes():
    doc=base_doc(); doc['jobs'][0]['expected_event_ids']=['E1','MISSING']; out=run_doc(doc); job=out['jobs'][0]
    assert job['status']=='retrying'; assert job['attempts']==1; assert job['retry_reason']=='source_unavailable'; assert 'MISSING' in job['last_error']; assert out['budget_ledger']=={}; assert out['manual_holds']==doc['manual_holds']

def test_window_mismatch_retries_without_partial_writes():
    doc=base_doc(); doc['jobs'][0]['expected_event_ids']=['E1','E3']; out=run_doc(doc); assert out['jobs'][0]['status']=='retrying'; assert out['jobs'][0]['retry_reason']=='source_unavailable'; assert out['budget_ledger']=={}

def test_inactive_campaign_retries_without_writing_ledger():
    doc=base_doc(); doc['jobs'][0].update({'campaign_id':'PAUSED','expected_event_ids':['E4']}); out=run_doc(doc); assert out['jobs'][0]['status']=='retrying'; assert out['budget_ledger']=={}

def test_missing_event_exhausts_to_failed():
    doc=base_doc(); doc['jobs'][0].update({'expected_event_ids':['MISSING'],'status':'retrying','attempts':1,'max_attempts':2}); out=run_doc(doc); assert out['jobs'][0]['status']=='failed'; assert out['jobs'][0]['attempts']==2; assert out['budget_ledger']=={}

def test_retrying_job_recovers_and_clears_retry_fields_but_keeps_metadata():
    doc=base_doc(); doc['jobs'][0].update({'status':'retrying','attempts':1,'last_error':'old','retry_reason':'source_unavailable','metadata':{'feed':'adx','keep':'yes'}}); out=run_doc(doc); job=out['jobs'][0]
    assert job['status']=='completed'; assert 'last_error' not in job; assert 'retry_reason' not in job; assert job['metadata']=={'feed':'adx','keep':'yes'}

def test_same_owner_fresh_ledger_row_is_reused_with_extra_fields_preserved():
    doc=base_doc(); doc['budget_ledger']['E1']={'event_id':'E1','campaign_id':'C1','account_id':'A1','window':'2026-05-25','amount_cents':1200,'owner_job_id':'JOB1','source_version':3,'kind':'exchange_spend','note':'keep'}; out=run_doc(doc)
    assert out['budget_ledger']['E1']['note']=='keep'; assert out['budget_ledger']['E2']['amount_cents']==1800

def test_same_owner_stale_ledger_row_is_refreshed():
    doc=base_doc(); doc['budget_ledger']['E1']={'event_id':'E1','campaign_id':'C1','account_id':'A1','window':'2026-05-25','amount_cents':99,'owner_job_id':'JOB1','source_version':1,'kind':'exchange_spend'}; out=run_doc(doc)
    assert out['budget_ledger']['E1']['amount_cents']==1200; assert out['budget_ledger']['E1']['source_version']==3

def test_other_owner_conflict_retries_and_preserves_protected_ledger_and_holds():
    doc=base_doc(); protected={'event_id':'E1','campaign_id':'C1','account_id':'A1','window':'2026-05-25','amount_cents':1200,'owner_job_id':'OTHER','source_version':3,'kind':'exchange_spend'}; doc['budget_ledger']['E1']=protected; doc['jobs'].append({'id':'OTHER','campaign_id':'C1','account_id':'A1','window':'2026-05-25','expected_event_ids':['E1'],'status':'retrying','attempts':0,'max_attempts':4})
    out=run_doc(doc); assert out['budget_ledger']['E1']==protected; assert out['manual_holds']==doc['manual_holds']; assert out['jobs'][0]['status']=='retrying'; assert out['jobs'][0]['retry_reason']=='ledger_conflict'

def test_conflict_exhausts_without_touching_protected_ledger():
    doc=base_doc(); protected={'event_id':'E1','campaign_id':'C1','account_id':'A1','window':'2026-05-25','amount_cents':1200,'owner_job_id':'OTHER','source_version':3,'kind':'exchange_spend'}; doc['budget_ledger']['E1']=protected; doc['jobs'][0].update({'status':'retrying','attempts':1,'max_attempts':2}); doc['jobs'].append({'id':'OTHER','campaign_id':'C1','account_id':'A1','window':'2026-05-25','expected_event_ids':['E1'],'status':'pending','attempts':0,'max_attempts':3})
    out=run_doc(doc); assert out['jobs'][0]['status']=='failed'; assert out['jobs'][0]['retry_reason']=='ledger_conflict'; assert out['budget_ledger']['E1']==protected

def test_ledger_owned_by_terminal_job_can_be_reclaimed():
    doc=base_doc(); doc['budget_ledger']['E1']={'event_id':'E1','campaign_id':'C1','account_id':'A1','window':'2026-05-25','amount_cents':50,'owner_job_id':'OLD','source_version':1,'kind':'exchange_spend'}; doc['jobs'].append({'id':'OLD','campaign_id':'C1','account_id':'A1','window':'2026-05-25','expected_event_ids':['E1'],'status':'completed','attempts':1,'max_attempts':1})
    out=run_doc(doc); assert out['budget_ledger']['E1']['owner_job_id']=='JOB1'; assert out['budget_ledger']['E1']['amount_cents']==1200

def test_manual_holds_are_preserved_exactly_even_on_success():
    doc=base_doc(); doc['manual_holds']['H2']={'campaign_id':'C1','account_id':'A1','window':'2026-05-25','amount_cents':777,'reason':'finance_lock','extra':{'x':1}}; out=run_doc(doc); assert out['manual_holds']==doc['manual_holds']

def test_repeated_runs_after_completion_are_stable():
    first=run_doc(base_doc()); second=run_doc(first); assert second==first
