import json, glob, sys, pathlib
from collections import defaultdict, Counter
ROOT = pathlib.Path("/home/ubuntu/Desktop/quangnm/MAS-FailureAttribution")
sys.path.insert(0, str(ROOT))
import evaluation.attribution_strategies as A

GT = {"Intent / Ambiguity Error":"Intent","Schema Linking Error":"Schema",
      "Skeleton / Structural Error":"Skeleton"}
STAGES=["Intent","Schema","Skeleton","Execution"]

class NS:                      # duck-typed view over a MAC-SQL log dict
    def __init__(s,d): s._d=d or {}
    def __getattr__(s,k): return s._d.get(k)
    def __bool__(s): return bool(s._d)
class Tbl:
    def __init__(s,n): s.table_name=n
class Entry:
    def __init__(s,d,map_x3):
        s._d=d
        s.gold_sql=d.get("gold_sql")
        s.x1=NS(d.get("x1")); s.x4=NS(d.get("x4"))
        x3=dict(d.get("x3") or {})
        # MAC-SQL's x3 is the Decomposer's qa_pairs (SQL text), NOT a clarified question.
        x3["clarified_question"] = x3.get("qa_pairs") if map_x3 else None
        s.x3=NS(x3) if (map_x3 and x3.get("clarified_question")) else NS({} if map_x3 else {})
        x5=dict(d.get("x5") or {})
        x5["selected_tables"]=[Tbl(t) if isinstance(t,str) else Tbl(t.get("table_name",""))
                               for t in (x5.get("selected_tables") or [])]
        s.x5=NS(x5)
        x6=dict(d.get("x6") or {}); x6.setdefault("alignments",[]); s.x6=NS(x6)
        x7=dict(d.get("x7") or {}); x7.setdefault("uses_join",False); s.x7=NS(x7)
        x8=dict(d.get("x8") or {}); x8.setdefault("expected_clauses",[]); s.x8=NS(x8)
        s.x9=NS(d.get("x9")); s.x10=NS(d.get("x10")); s.x13=NS(d.get("x13"))

backbone=sys.argv[1]; sysname=sys.argv[2]; map_x3 = (sys.argv[3]=="map_x3")
files=sorted(glob.glob(f"output_logs/{sysname}_drspider/{backbone}/drspider_*.json"))
fails=[]
for f in files:
    d=json.load(open(f,encoding='utf-8'))
    if d.get("execution_match") is True: continue
    t=GT.get(d.get("target_error_type"))
    if t: fails.append((t,Entry(d,map_x3)))
print(f"### {sysname}/{backbone}  x3->clarified_question: {map_x3}")
print(f"### {len(files)} logs, {len(fails)} failures scored\n")

fire=Counter()
for _,e in fails:
    for st,fn in A.EVIDENCE.items():
        if fn(e)[0]: fire[st]+=1
print("detector fire-rate over failures:",
      {k:f"{fire[k]}/{len(fails)} ({fire[k]/len(fails)*100:.1f}%)" for k in STAGES}, "\n")

for name,fn in A.STRATEGIES.items():
    conf=defaultdict(Counter)
    for t,e in fails: conf[t][fn(e)[0]]+=1
    cols=[c for c in STAGES+["Unknown","Ambiguous"] if any(conf[t].get(c) for t in conf)]
    print("="*84); print(f"STRATEGY: {name}"); print("="*84)
    h=f"{'injected':<10}{'n':>5}"+"".join(f"{c:>12}" for c in cols)+f"{'recall':>10}"
    print(h); print("-"*len(h)); th=tn=0
    for t in ["Intent","Schema","Skeleton"]:
        r=conf[t]; n=sum(r.values()); hit=r.get(t,0); th+=hit; tn+=n
        print(f"{t:<10}{n:>5}"+"".join(f"{r.get(c,0):>12}" for c in cols)+f"{(hit/n*100 if n else 0):>9.1f}%")
    print("-"*len(h)); print(f"{'OVERALL':<10}{tn:>5} -> {th}/{tn} = {th/tn*100:.1f}%\n")
