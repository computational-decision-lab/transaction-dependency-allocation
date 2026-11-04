"""Legal size/arrival robustness and targeted additional fee seeds.

All treatments are fixed before this extension executes. They are exploratory
follow-ups to an already inspected baseline, not preregistered discovery tests.
"""
import argparse
import base64
from decimal import Decimal
import gzip
from hashlib import sha256
import http.client
import json
from pathlib import Path
import random
import socket
import subprocess
import time

from .runner import Node, COMMIT, TOPOLOGIES, dump, sat
from .independent import (parse_transaction, parse_block, reconstruct, require,
                          select, exact_max_fees, validate_selection, gini)


class StudyNode(Node):
    def call(self, method, *params, **kwargs):
        # A separate connection per request avoids stale HTTP keepalive sockets
        # when signing a larger fixture leaves another node idle.
        if self.connection:self.connection.close()
        self.connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=120)
        return super().call(method,*params,**kwargs)

    def start(self, budget, mocktime=None):
        self.directory.mkdir(parents=True,exist_ok=True)
        flags=['-regtest',f'-datadir={self.directory}','-listen=0','-connect=0',
               '-discover=0','-networkactive=0',f'-rpcport={self.port}',
               '-rpcbind=127.0.0.1','-rpcallowip=127.0.0.1','-persistmempool=0',
               '-fallbackfee=0.00001','-txindex=1','-blockreservedweight=2000',
               f'-blockmaxweight={2001+budget}','-blockmintxfee=0','-walletbroadcast=0',
               '-keypool=100','-dbcache=64','-par=1']
        if mocktime is not None:flags.append(f'-mocktime={mocktime}')
        self.stdout=(self.directory/'process.log').open('a')
        self.process=subprocess.Popen([str(self.binary),*flags],stdout=self.stdout,stderr=subprocess.STDOUT)
        for _ in range(200):
            require(self.process.poll() is None,'Core failed startup')
            cookie=self.directory/'regtest/.cookie'
            if cookie.exists():
                self.auth='Basic '+base64.b64encode(cookie.read_bytes().strip()).decode()
                try:
                    self.connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=120)
                    self.call('getblockcount');break
                except (OSError,RuntimeError,http.client.HTTPException):self.connection.close()
            time.sleep(.05)
        else:raise RuntimeError('Core startup timeout')
        if (self.directory/'regtest/wallets/study').exists():self.call('loadwallet','study')
        else:self.call('createwallet','study')
        self.wallet='study';self.flags=flags
        require(not self.call('getrawmempool'),'restart did not clear mempool')


def create(node,topology,seed,size_mode,arrival_mode):
    graph=TOPOLOGIES[topology]
    rates=random.Random(seed).sample(range(2,121),12)
    rates[0]=1;rates[1]=160+seed%19
    fees=[r*326+i for i,r in enumerate(rates)]
    factors=[1,4,2,1,2,4,1,2,4,1,2,4] if size_mode=='heterogeneous' else [1]*12
    arrivals=([0]*12 if arrival_mode=='simultaneous' else
              [0,600,600,600,600,600,0,0,0,0,0,0] if arrival_mode=='children_late' else
              [0,0,0,600,600,600,0,0,0,600,600,600])
    coins=node.call('listunspent',1,9999999,[],True,{'minimumAmount':'0.20000000'},wallet=True,log=False)
    coins.sort(key=lambda x:-sat(x['amount']))
    addresses=[node.call('getnewaddress','','bech32m',wallet=True) for _ in range(28)]
    rows,funding,root=[],{},0
    for i in range(12):
        if i in graph:
            parent,vout=graph[i]
            prev=dict(txid=rows[parent]['txid'],vout=vout,
                amount=float(Decimal(rows[parent]['outputs'][vout]['value_sat'])/100000000))
            parents=[prev['txid']]
            require(arrivals[parent]<=arrivals[i],'child arrives before parent')
        else:
            prev={k:coins[root][k] for k in ('txid','vout','amount')};root+=1
            funding[prev['txid']]=node.call('getrawtransaction',prev['txid'])
            parents=[]
        count={1:6,2:13,4:28}[factors[i]]
        total=sat(prev['amount'])-fees[i]
        require(total>=count*1000,'insufficient funding')
        values=[total//count]*count;values[0]+=total-sum(values)
        outs=[{a:format(Decimal(v)/100000000,'.8f')} for a,v in zip(addresses,values)]
        if factors[i]>1:outs.append({'data':'00'*{2:14,4:21}[factors[i]]})
        raw=node.call('createrawtransaction',[dict(txid=prev['txid'],vout=prev['vout'],sequence=4294967293)],outs,0,True)
        signed=node.call('signrawtransactionwithwallet',raw,wallet=True)
        require(signed['complete'],'unsigned fixture')
        tx=parse_transaction(signed['hex']);decoded=node.call('decoderawtransaction',signed['hex'])
        require(tx['weight']==1304*factors[i] and tx['vsize']==326*factors[i], 'size target mismatch')
        require(tx['weight']==decoded['weight'] and tx['txid']==decoded['txid'],'parser disagrees')
        row=dict(txid=tx['txid'],raw=signed['hex'],weight=tx['weight'],vsize=tx['vsize'],
                 fee_sat=fees[i],outputs=tx['outputs'],prevout=prev,parents=parents,
                 label=i,arrival_index=i,arrival_seconds=arrivals[i],size_factor=factors[i])
        row['acceptance']=node.call('testmempoolaccept',[row['raw']],0)[0]
        require(row['acceptance'].get('allowed'),'invalid constructed transaction')
        node.call('sendrawtransaction',row['raw'],0)
        rows.append(row)
    data=dict(topology=topology,seed=seed,size_mode=size_mode,arrival_mode=arrival_mode,
              transactions=rows,funding_transactions=funding,rejections=[],
              graph={str(k):list(v) for k,v in graph.items()},fee_assignment=fees)
    reconstruct(data)
    return data


def trial(node,data,policy,budget,address,start,horizon=8):
    parsed=reconstruct(data);byid={t['txid']:t for t in parsed}
    original={t['txid']:t for t in data['transactions']}
    arrivals={t['txid']:t['arrival_seconds'] for t in data['transactions']}
    remaining={};inserted=set();events=[];blocks=[]
    # Every label receives eight blocks of follow-up from its own arrival.
    last_step=horizon+max(arrivals.values())//600
    for step in range(1,last_step+1):
        insertion_time=(step-1)*600
        node.call('setmocktime',start+insertion_time)
        for row in data['transactions']:
            x=row['txid']
            if x not in inserted and arrivals[x]<=insertion_time:
                acceptance=node.call('testmempoolaccept',[row['raw']],0)[0]
                require(acceptance.get('allowed'),'arrival rejected')
                node.call('sendrawtransaction',row['raw'],0)
                inserted.add(x);remaining[x]=byid[x]
        before=node.call('getrawmempool',False,True)
        require(set(before['txids'])==set(remaining),'unexpected mempool removal')
        node.call('setmocktime',start+600*step)
        template=node.call('getblocktemplate',{'rules':['segwit']}) if policy=='cluster' else None
        chosen=[t['txid'] for t in template['transactions']] if template else select(list(remaining.values()),budget,policy)
        validate_selection(list(remaining.values()),chosen,budget)
        optimum=exact_max_fees(list(remaining.values()),budget)
        generated=node.call('generateblock',address,chosen)
        raw=node.call('getblock',generated['hash'],0)
        block=parse_block(raw)
        require([t['txid'] for t in block['transactions'][1:]]==chosen,'block differs')
        blocks.append(dict(step=step,time=600*step,hash=generated['hash'],raw_block=raw,
              txids=chosen,template=template,before=before,
              fee_sat=sum(byid[x]['fee_sat'] for x in chosen),
              weight=sum(byid[x]['weight'] for x in chosen),exact_optimum=optimum))
        for x in chosen:
            age=600*step-arrivals[x]
            events.append(dict(txid=x,label=byid[x]['label'],arrival=arrivals[x],
                calendar_time=600*step,time=min(age,600*horizon),
                cause='confirmed' if age<=600*horizon else 'censored'))
            del remaining[x]
        if not remaining and len(inserted)==12:break
    events += [dict(txid=x,label=byid[x]['label'],arrival=arrivals[x],time=600*horizon,
                    calendar_time=arrivals[x]+600*horizon,cause='censored') for x in remaining]
    require(len(events)==12,'incomplete follow-up')
    events.sort(key=lambda e:e['label'])
    def metrics(es):
        burdens=[e['time']/600 if e['cause']=='confirmed' else horizon for e in es]
        return dict(mean_burden=sum(burdens)/len(burdens),gini=gini(burdens),
                    confirmation_incidence=sum(e['cause']=='confirmed' for e in es)/len(es),burdens=burdens)
    return dict(policy=policy,budget=budget,horizon_seconds=600*horizon,blocks=blocks,events=events,
                outcomes=metrics(events),unrelated_outcomes=metrics([e for e in events if e['label']>=6]))


def matrix():
    jobs=[]
    # A compact 2 size x 3 arrival diagnostic, including topology-matched
    # independent controls. Ten seed units; uncertainty is reported as such.
    for cap in [4,6]:
        for size in ['equal','heterogeneous']:
            for arrival in ['simultaneous','children_late','two_batches']:
                for seed in range(10):
                    for topology in ['independent','cpfp','chain','star']:
                        jobs.append(dict(capacity=cap,size_mode=size,arrival_mode=arrival,
                            seed=seed,topology=topology,family='robustness'))
    for cap,topologies in [(4,['independent','chain','star']),(6,['independent','chain'])]:
        for seed in range(40,100):
            for topology in topologies:
                jobs.append(dict(capacity=cap,size_mode='equal',arrival_mode='simultaneous',
                    seed=seed,topology=topology,family='precision'))
    return jobs


def main():
    ap=argparse.ArgumentParser(__doc__)
    ap.add_argument('--binary',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--limit',type=int)
    ap.add_argument('--resume',action='store_true')
    args=ap.parse_args()
    jobs=matrix()
    args.output.mkdir(parents=True,exist_ok=True)
    if not (args.output/'design.json').exists():
        dump(args.output/'design.json',dict(created_unix=time.time(),jobs=jobs,
            status='fixed_before_execution',classification='exploratory_followup',source_commit=COMMIT,
            binary_sha256=sha256(args.binary.read_bytes()).hexdigest(),
            source_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
            fee_matching='label fees fixed across size, arrival, topology and selector treatments',
            arrival_rule='arrivals immediately after prior block; latency from own arrival',
            horizon='4800 seconds per transaction; extra calendar block for late arrivals',
            size_construction='Taproot output count 6/13/28; padding data payload 0/14/21 bytes; exact vsize 326/652/1304'))
        for name in ['extension_runner.py','runner.py','independent.py']:
            target=args.output/'source_snapshot'/name;target.parent.mkdir(exist_ok=True)
            target.write_bytes((Path(__file__).parent/name).read_bytes())
    if args.limit:jobs=[jobs[i] for i in [0,2,3,42,83,122,163,201]][:args.limit]
    runtime=Path('/dev/shm')/('pow_'+args.output.name)
    require(not runtime.exists() or args.resume,'use a fresh runtime directory')
    runtime.mkdir(exist_ok=args.resume)
    if args.resume:
        original=json.loads((args.output/'design.json').read_text())
        require(original['jobs']==matrix(),'resume treatment design differs')
        require(original['binary_sha256']==sha256(args.binary.read_bytes()).hexdigest(),'resume binary differs')
        dump(args.output/'resumption.json',dict(reason='Reconnect RPC requests after idle HTTP keepalive expiry; preserve completed fixtures and reset incomplete branch to last saved cleanup block',
            completed_before_resume=len(list((args.output/'fixtures').glob('*.json'))),
            revised_source_sha256=sha256(Path(__file__).read_bytes()).hexdigest()))
        (args.output/'source_snapshot/resumed_extension_runner.py').write_bytes(Path(__file__).read_bytes())
    nodes={};ledgers={}
    for name in ['generator','scalar','ancestor','cluster']:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        suffix='_resumed' if args.resume else ''
        ledgers[name]=gzip.open(args.output/f'{name}{suffix}_rpc.jsonl.gz','wt')
        nodes[name]=StudyNode(args.binary.resolve(),runtime/name,ledgers[name],port)
    mocktime=int(time.time())+100000
    count=0;old_budget=None;setup=None
    if args.resume:
        setup=json.loads((args.output/'setup_blocks.json').read_text())
        mocktime=max(mocktime,max(parse_block(json.loads(p.read_text())['cleanup_block'])['time']
            for p in (args.output/'fixtures').glob('*.json'))+6000)
    try:
        for job in jobs:
            ident='{family}_cap{capacity}_{size_mode}_{arrival_mode}_seed{seed:03d}_{topology}'.format(**job)
            target=args.output/'fixtures'/f'{ident}.json'
            if target.exists() and args.resume:continue
            require(not target.exists(),'fresh experiment directory required')
            budget=job['capacity']*1304;mocktime+=12000
            if budget!=old_budget:
                for node in nodes.values():
                    node.close();node.start(budget,mocktime)
                old_budget=budget
            generator=nodes['generator']
            generator.call('setmocktime',mocktime)
            mine_address=generator.call('getnewaddress','','bech32m',wallet=True)
            if setup is None:
                hashes=generator.call('generatetoaddress',201,mine_address)
                setup=[generator.call('getblock',h,0) for h in hashes]
                dump(args.output/'setup_blocks.json',setup)
                for policy in ['scalar','ancestor','cluster']:
                    for raw in setup:require(nodes[policy].call('submitblock',raw) is None,'setup rejected')
            data=create(generator,job['topology'],job['seed'],job['size_mode'],job['arrival_mode'])
            data.update(job);data['node_flags']=generator.flags
            generator.call('setmocktime',mocktime+6600)
            cleanup=generator.call('generateblock',mine_address,[t['txid'] for t in data['transactions']])
            data['cleanup_block']=generator.call('getblock',cleanup['hash'],0)
            trajectories=[]
            for policy in ['scalar','ancestor','cluster']:
                node=nodes[policy]
                require(not node.call('getrawmempool'),'fixture has leftover mempool')
                addr=generator.call('getnewaddress','','bech32m',wallet=True)
                trajectory=trial(node,data,policy,budget,addr,mocktime)
                trajectories.append(trajectory)
                node.call('invalidateblock',trajectory['blocks'][0]['hash'])
                node.call('setmocktime',mocktime+6601)
                require(node.call('submitblock',data['cleanup_block']) is None,'cleanup rejected')
                require(not node.call('getrawmempool'),'cleanup left mempool entries')
            data['trajectories']=trajectories
            dump(target,data);count+=1
            if count%10==0:print(json.dumps(dict(completed=count,total=len(jobs),fixture=ident)),flush=True)
        dump(args.output/'execution.json',dict(status='completed',fixtures=len(jobs),trajectories=len(jobs)*3,
             scope='pilot' if args.limit else 'full extension',runtime=str(runtime)))
    finally:
        for node in nodes.values():node.close()
        for ledger in ledgers.values():ledger.close()


if __name__=='__main__':main()
