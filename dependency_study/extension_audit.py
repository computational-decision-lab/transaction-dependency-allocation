"""Offline byte/arrival/optimization checks, independent of experiment runner."""
import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import random

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from .independent import reconstruct, parse_block, validate_selection, require
from .reference_selectors import reference_select
from .analyze import interval


def optimum(rows,budget):
    if not rows:return 0
    ids={r['txid']:i for i,r in enumerate(rows)}
    a=[[r['weight'] for r in rows]];upper=[budget]
    for i,r in enumerate(rows):
        for p in r['parents']:
            if p in ids:
                x=np.zeros(len(rows));x[i]=1;x[ids[p]]=-1
                a.append(x);upper.append(0)
    fees=np.array([r['fee_sat'] for r in rows])
    result=milp(-fees.astype(float),integrality=np.ones(len(rows)),
        bounds=Bounds(np.zeros(len(rows)),np.ones(len(rows))),
        constraints=LinearConstraint(np.array(a),-np.inf,np.array(upper)),
        options={'mip_rel_gap':0.0})
    require(result.success and result.status==0,'MILP incomplete')
    decisions=np.rint(result.x)
    require(np.max(abs(result.x-decisions))<1e-7,'MILP noninteger')
    require(np.all(np.array(a)@decisions<=np.array(upper)),'MILP infeasible')
    fee=int(fees@decisions)
    require(abs(result.mip_dual_bound+fee)<1e-5,'MILP bound mismatch')
    return fee


def audit_fixture(d):
    rows=reconstruct(d);byid={r['txid']:r for r in rows}
    require([r['label'] for r in rows]==list(range(12)),'label order mismatch')
    graphs={'independent':{},'cpfp':{1:0},'chain':{1:0,2:1,3:2},
            'star':{1:0,2:0,3:0,4:0,5:0}}
    require(d['topology'] in graphs,'unknown topology')
    expected_graph=graphs[d['topology']]
    for r in rows:
        parents=[rows[expected_graph[r['label']]]['txid']] if r['label'] in expected_graph else []
        require(r['parents']==parents,'declared topology mismatch')
    require(sorted(t['policy'] for t in d['trajectories'])==['ancestor','cluster','scalar'],
            'selector inventory mismatch')
    arrivals={r['txid']:r['arrival_seconds'] for r in d['transactions']}
    rates=random.Random(d['seed']).sample(range(2,121),12)
    rates[0],rates[1]=1,160+d['seed']%19
    for r in d['transactions']:
        i=r['label']
        expected_arrival=(0 if d['arrival_mode']=='simultaneous' else
            600*int(1<=i<=5) if d['arrival_mode']=='children_late' else
            600*int(i in [3,4,5,9,10,11]))
        factor=[1,4,2,1,2,4,1,2,4,1,2,4][i] if d['size_mode']=='heterogeneous' else 1
        require(r['arrival_seconds']==expected_arrival,'declared arrival treatment mismatch')
        require(r['arrival_index']==i,'insertion rank mismatch')
        require(r['weight']==1304*factor and r['vsize']==326*factor,'declared size treatment mismatch')
        require(r['fee_sat']==326*rates[i]+i,'fee seed mismatch')
    budget=d['capacity']*1304;result=[]
    initial_optimum=optimum([r for r in rows if arrivals[r['txid']]<600],budget)
    for trajectory in d['trajectories']:
        require(trajectory['budget']==budget and trajectory['horizon_seconds']==4800,
                'budget or follow-up mismatch')
        require([b['step'] for b in trajectory['blocks']]==list(range(1,len(trajectory['blocks'])+1)),
                'nonconsecutive block schedule')
        confirmed={};inserted=set();remaining={};start=None
        for block in trajectory['blocks']:
            step=block['step']
            for x,r in byid.items():
                if x not in inserted and arrivals[x]<600*step:
                    inserted.add(x);remaining[x]=r
            require(set(block['before']['txids'])==set(remaining),'arrival/mempool mismatch')
            got=parse_block(block['raw_block'])
            require(got['hash']==block['hash'],'block hash mismatch')
            if start is None:start=got['time']-step*600
            require(got['time']==start+step*600,'mock schedule mismatch')
            chosen=[t['txid'] for t in got['transactions'][1:]]
            require(chosen==block['txids'],'raw block order mismatch')
            validate_selection(list(remaining.values()),chosen,budget)
            if trajectory['policy']!='cluster':
                require(chosen==reference_select(list(remaining.values()),budget,trajectory['policy']),
                        'independent selector mismatch')
            else:require(chosen==[t['txid'] for t in block['template']['transactions']],'template mismatch')
            fee=sum(byid[x]['fee_sat'] for x in chosen)
            weight=sum(byid[x]['weight'] for x in chosen)
            require(fee==block['fee_sat'] and weight==block['weight'],'selected accounting mismatch')
            if step==1:require(initial_optimum==block['exact_optimum']['fee_sat'],'initial MILP mismatch')
            for x in chosen:confirmed[x]=step*600;del remaining[x]
        last_step=trajectory['blocks'][-1]['step']
        require(last_step<=8+max(arrivals.values())//600,'excess follow-up')
        require(len(confirmed)==12 or last_step==8+max(arrivals.values())//600,
                'incomplete follow-up')
        burdens={x:min(4800,confirmed[x]-arrivals[x])/600 if x in confirmed else 8 for x in byid}
        events={e['txid']:e for e in trajectory['events']}
        require(len(events)==12,'terminal count mismatch')
        for x,e in events.items():
            require(e['time']/600==burdens[x],'waiting clock mismatch')
            cause='confirmed' if x in confirmed and confirmed[x]-arrivals[x]<=4800 else 'censored'
            require(e['cause']==cause and e['arrival']==arrivals[x],'terminal type mismatch')
        b=np.array(list(burdens.values()))
        mean=float(b.mean());g=float(np.abs(b[:,None]-b).sum()/(2*len(b)*b.sum()))
        unrelated=float(np.mean([v for x,v in burdens.items() if byid[x]['label']>=6]))
        np.testing.assert_allclose([mean,g,unrelated],[trajectory['outcomes']['mean_burden'],
            trajectory['outcomes']['gini'],trajectory['unrelated_outcomes']['mean_burden']],atol=1e-12)
        first=trajectory['blocks'][0];fstar=first['exact_optimum']['fee_sat']
        result.append(dict(family=d['family'],capacity=d['capacity'],size=d['size_mode'],
            arrival=d['arrival_mode'],seed=d['seed'],topology=d['topology'],policy=trajectory['policy'],
            mean_burden=mean,gini=g,unrelated_burden=unrelated,first_fee_sat=first['fee_sat'],
            first_utilization=first['weight']/budget,optimality_gap=1-first['fee_sat']/fstar if fstar else 0,
            initial_optimal_fee=fstar,blocks=len(trajectory['blocks'])))
    return result


def main():
    ap=argparse.ArgumentParser(__doc__);ap.add_argument('directory',type=Path)
    ap.add_argument('--baseline',type=Path,default=Path('results/dependency_study/regtest_20261103_n40'))
    ap.add_argument('--partial',action='store_true')
    args=ap.parse_args();output=args.directory/'analysis';output.mkdir(exist_ok=True)
    rows=[];matching={};files=sorted((args.directory/'fixtures').glob('*.json'));inventory={}
    expected=json.loads((args.directory/'design.json').read_text())['jobs']
    if not args.partial:require(len(files)==len(expected),'missing fixtures')
    dimensions=['family','capacity','size_mode','arrival_mode','seed','topology']
    expected_jobs={tuple(job[k] for k in dimensions) for job in expected}
    observed_jobs=set()
    for i,path in enumerate(files):
        d=json.loads(path.read_text());rows+=audit_fixture(d)
        job=tuple(d[k] for k in dimensions)
        require(job in expected_jobs and job not in observed_jobs,'unexpected or repeated treatment')
        observed_jobs.add(job)
        key=(d['family'],d['capacity'],d['size_mode'],d['arrival_mode'],d['seed'])
        sig=[(r['label'],r['fee_sat'],r['weight'],r['vsize'],r['arrival_seconds'],r['arrival_index']) for r in d['transactions']]
        if key in matching:require(matching[key]==sig,'label matching failed')
        else:matching[key]=sig
        inventory[path.name]=sha256(path.read_bytes()).hexdigest()
        if (i+1)%100==0:print(json.dumps(dict(checked=i+1)),flush=True)
    if not args.partial:require(observed_jobs==expected_jobs,'treatment matrix incomplete')
    # The first forty precision seeds are the immutable baseline, not new samples.
    cells={(r['capacity'],r['topology']) for r in rows if r['family']=='precision'}
    for cap,topology in cells:
        for seed in range(40):
            d=json.loads((args.baseline/'fixtures'/f'cap{cap}_seed{seed:03d}_{topology}.json').read_text())
            for t in d['trajectories']:
                first=t['blocks'][0];opt=first['exact_optimum']['fee_sat']
                rows.append(dict(family='precision',capacity=cap,size='equal',arrival='simultaneous',
                    seed=seed,topology=topology,policy=t['policy'],
                    mean_burden=t['outcomes']['mean_burden']/600,gini=t['outcomes']['gini'],
                    unrelated_burden=t['unrelated_outcomes']['mean_burden']/600,
                    first_fee_sat=first['fee_sat'],first_utilization=first['weight']/(1304*cap),
                    optimality_gap=1-first['fee_sat']/opt,initial_optimal_fee=opt,blocks=len(t['blocks'])))
    grouped=defaultdict(dict)
    for r in rows:
        key=tuple(r[k] for k in ['family','capacity','size','arrival','topology'])
        require((r['seed'],r['policy']) not in grouped[key],'duplicate seed-policy')
        grouped[key][(r['seed'],r['policy'])]=r
    metrics=['unrelated_burden','mean_burden','gini','first_fee_sat','first_utilization','optimality_gap']
    contrasts=[];core_cells=[]
    for key,cell in sorted(grouped.items()):
        seeds=sorted({s for s,p in cell})
        dimensions=dict(zip(['family','capacity','size','arrival','topology'],key))
        core_cells.append(dict(**dimensions,metrics={m:interval([cell[(s,'cluster')][m] for s in seeds]) for m in metrics}))
        for comparator in ['scalar','ancestor']:
            contrasts.append(dict(**dimensions,comparator=comparator,
                metrics={m:interval([cell[(s,'cluster')][m]-cell[(s,comparator)][m] for s in seeds]) for m in metrics}))
    (output/'recomputed.json').write_text(json.dumps(rows,indent=2)+'\n')
    report=dict(status='partial' if args.partial else 'passed',fixtures=len(files),new_trajectories=len(files)*3,
        signed_transactions=len(files)*12,matching_groups=len(matching),initial_milp_checks=len(files),
        source='raw transactions, prevouts and mined blocks; independent selectors and HiGHS',
        repeated_unit='fee seed; 10 per robustness cell, 100 per completed precision cell',
        core_cells=core_cells,contrasts=contrasts,artifact_sha256=inventory)
    (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ['core_cells','contrasts','artifact_sha256']},indent=2))


if __name__=='__main__':main()
