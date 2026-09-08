"""Freeze the user-requested public subset without selecting on model results."""
import collections
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEED = 'priority-20260908-v1'
QUOTAS = {'conv-26':129,'conv-41':106,'conv-30':72,'conv-42':65,
          'conv-43':47,'conv-44':45,'conv-47':16,'conv-48':14,'conv-49':6}
CATEGORIES = {'1':167,'2':167,'4':166}
OPERATIONS = {'A01': ['reflect', 'remember', 'update'],
 'A02': ['forget', 'reflect', 'remember', 'update'],
 'A04': ['forget', 'reflect', 'remember'],
 'A06': ['forget', 'reflect', 'remember', 'update'],
 'A07': ['forget', 'reflect', 'remember'],
 'A14': ['forget', 'reflect', 'update'],
 'A16': ['reflect', 'remember', 'update'],
 'A26': ['forget', 'reflect', 'remember', 'update'],
 'A27': ['reflect'],
 'A28': ['forget', 'reflect', 'remember', 'update'],
 'A29': ['forget', 'reflect', 'remember', 'update'],
 'A30': ['forget', 'reflect', 'update'],
 'B01': ['forget', 'reflect', 'remember', 'update'],
 'B03': ['reflect', 'remember', 'update'],
 'B04': ['forget', 'reflect', 'remember', 'update'],
 'B05': ['forget', 'reflect', 'remember', 'update'],
 'B06': ['reflect', 'update'],
 'B10': ['reflect', 'remember', 'update'],
 'B11': ['forget', 'reflect', 'remember', 'update'],
 'B14': ['forget', 'reflect', 'remember'],
 'B21': ['forget', 'reflect', 'update'],
 'B22': ['forget', 'reflect', 'remember', 'update'],
 'B23': ['forget', 'reflect', 'remember', 'update'],
 'B24': ['forget'],
 'B25': ['forget'],
 'B26': ['forget', 'remember'],
 'B27': ['reflect', 'update'],
 'B29': ['forget', 'remember', 'update'],
 'B30': ['forget', 'reflect', 'update'],
 'C01': ['forget', 'reflect', 'update'],
 'C02': ['reflect', 'remember', 'update']}


def digest(data):
    return hashlib.sha256(data).hexdigest()

def stable(q):
    return digest(f'{SEED}:{q["qid"]}'.encode())

def allocate(samples):
    """Integer max flow preserves both conversation and category marginals."""
    capacity = collections.defaultdict(dict)
    def edge(a,b,n):
        capacity[a][b] = n
        capacity[b].setdefault(a,0)
    for sid,n in QUOTAS.items():
        edge('source',sid,n)
        sample = next(s for s in samples if s['sample_id']==sid)
        counts = collections.Counter(q['category'] for q in sample['questions'])
        for cat in CATEGORIES:
            edge(sid,'cat-'+cat,counts[cat])
    for cat,n in CATEGORIES.items():
        edge('cat-'+cat,'sink',n)
    flow = 0
    while True:
        parents = {'source':None}
        queue = collections.deque(['source'])
        while queue and 'sink' not in parents:
            a = queue.popleft()
            for b,n in capacity[a].items():
                if n > 0 and b not in parents:
                    parents[b] = a
                    queue.append(b)
        if 'sink' not in parents:
            break
        b = 'sink'; amount = 500
        while parents[b] is not None:
            a = parents[b]; amount = min(amount,capacity[a][b]); b = a
        b = 'sink'
        while parents[b] is not None:
            a = parents[b]; capacity[a][b] -= amount; capacity[b][a] += amount; b = a
        flow += amount
    assert flow == 500, f'Infeasible user quotas: {flow}/500'
    return {(sid,cat):capacity['cat-'+cat][sid] for sid in QUOTAS for cat in CATEGORIES}

def main():
    out = ROOT/'eval/.data/priority-20260908-v1'
    out.mkdir(parents=True,exist_ok=True)
    manifest = {'version':SEED,'official_selection':False,
        'policy':'Public reconstruction; hash-ranked questions, no score filtering; full sessions retained. Run this priority suite before wider regressions.',
        'category_names':{'1':'multi_hop','2':'temporal','4':'single_hop'},
        'memops_policy':'All longitudinal questions for explicitly listed agent/operation pairs. Do not invent missing questions to reach 500.',
        'known_discrepancies':['User MemOps table totals 470, not 500. Pinned sources contain 472 matching questions: C02 has 15 instead of the listed 13.',
            'Original official qid list unavailable; LoCoMo preserves numeric category quotas and conversation quotas, with corrected category names.'],
        'datasets':{}}
    for benchmark in ['locomo','memops']:
        source = ROOT/'eval/.data'/f'{benchmark}-normalized.json'
        raw = source.read_bytes(); samples = json.loads(raw)
        selected = []
        if benchmark == 'locomo':
            allocation = allocate(samples)
            for sid in QUOTAS:
                s = next(s for s in samples if s['sample_id']==sid)
                qs = []
                for cat in CATEGORIES:
                    qs.extend(sorted((q for q in s['questions'] if q['category']==cat),key=stable)[:allocation[sid,cat]])
                selected.append({**s,'questions':sorted(qs,key=lambda q:q['qid'])})
            assert collections.Counter(q['category'] for s in selected for q in s['questions']) == CATEGORIES
            assert {s['sample_id']:len(s['questions']) for s in selected} == QUOTAS
        else:
            for s in samples:
                agent = s['group_id']; op = s['sample_id'].split('_',1)[1]
                if agent in OPERATIONS and op in OPERATIONS[agent]:
                    qs = [q for q in s['questions'] if '#longitudinal_operation#' in q['qid']]
                    if qs: selected.append({**s,'questions':qs})
            assert len({s['group_id'] for s in selected}) == 31
            assert sum(len(s['questions']) for s in selected) == 472
        qids = [q['qid'] for s in selected for q in s['questions']]
        assert len(qids)==len(set(qids))
        originals = {s['sample_id']:s for s in samples}
        for s in selected:
            assert s['sessions']==originals[s['sample_id']]['sessions']
            assert all(q in originals[s['sample_id']]['questions'] for q in s['questions'])
        payload = (json.dumps(selected,ensure_ascii=False,indent=2)+'\n').encode()
        path = out/f'{benchmark}.json'
        if path.exists() and path.read_bytes()!=payload:
            raise ValueError(f'Refusing to replace changed frozen dataset: {path}')
        path.write_bytes(payload)
        # First pass covers each capability with complete source histories.
        smoke = []; covered = set()
        for s in selected:
            qs = []
            for q in sorted(s['questions'],key=stable):
                family = q['category'].split('/')[0]
                if family not in covered:
                    covered.add(family); qs.append(q)
            if qs: smoke.append({**s,'questions':qs})
        smoke_path = out/f'{benchmark}-smoke.json'
        smoke_bytes = (json.dumps(smoke,ensure_ascii=False,indent=2)+'\n').encode()
        smoke_path.write_bytes(smoke_bytes)
        manifest['datasets'][benchmark] = {'source':str(source.relative_to(ROOT)),
            'source_sha256':digest(raw),'path':str(path.relative_to(ROOT)),
            'sha256':digest(payload),'questions':len(qids),'samples':len(selected),
            'groups':dict(collections.Counter(s['group_id'] for s in selected)),
            'questions_by_group':dict(collections.Counter(s['group_id'] for s in selected for q in s['questions'])),
            'categories':dict(collections.Counter(q['category'] for s in selected for q in s['questions'])),
            'qids':qids,'smoke_path':str(smoke_path.relative_to(ROOT)),
            'smoke_sha256':digest(smoke_bytes),'smoke_qids':[q['qid'] for s in smoke for q in s['questions']]}
    target = ROOT/'configs/priority-benchmarks-20260908.json'
    target.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({b:{k:d[k] for k in ['questions','samples','smoke_qids']} for b,d in manifest['datasets'].items()},indent=2))

if __name__ == '__main__':
    main()
