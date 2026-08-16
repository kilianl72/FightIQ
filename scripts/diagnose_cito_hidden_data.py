import json, os, re
from collections import Counter, defaultdict
from urllib.request import Request, urlopen
from supabase import create_client

CITO_URL="https://api.citoapi.com/api/v1/ufc/fighters?page=1&limit=5000"
PAGE_SIZE=1000
KEYWORDS=("birth","dob","date","age","height","reach","weight","image","photo","picture","train","gym","camp","style","stance","record","win","loss","draw","division","status","champion")
DATE_PATTERNS=[re.compile(r"^\d{4}-\d{2}-\d{2}(?:T.*)?$"),re.compile(r"^[A-Z][a-z]{2,8}\.?\s+\d{1,2},\s+\d{4}$"),re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")]
TEST_NAMES={"alexander volkanovski","jon jones","conor mcgregor","charles oliveira","islam makhachev"}

def normalize(s): return re.sub(r"\s+"," ",(s or "").strip().lower())
def fetch_cito(api_key):
    req=Request(CITO_URL,headers={"x-api-key":api_key,"User-Agent":"FightIQ-cito-diagnostic/1.0","Accept":"application/json"})
    with urlopen(req,timeout=120) as r: payload=json.loads(r.read().decode("utf-8"))
    if not payload.get("success"): raise RuntimeError("Cito API returned success=false")
    return payload.get("data") or []
def fetch_all(sb,table,fields):
    rows=[]; start=0
    while True:
        batch=sb.table(table).select(fields).range(start,start+PAGE_SIZE-1).execute().data or []
        rows.extend(batch)
        if len(batch)<PAGE_SIZE: break
        start+=PAGE_SIZE
    return rows
def walk(obj,path="$"):
    if isinstance(obj,dict):
        for k,v in obj.items():
            child=f"{path}.{k}"; yield child,k,v; yield from walk(v,child)
    elif isinstance(obj,list):
        for i,v in enumerate(obj):
            child=f"{path}[{i}]"; yield child,str(i),v; yield from walk(v,child)
def scalar(v): return isinstance(v,(str,int,float,bool)) or v is None
def looks_like_date(v):
    return isinstance(v,str) and any(p.match(v.strip()) for p in DATE_PATTERNS)
def interesting_key(k):
    k=(k or "").lower(); return any(word in k for word in KEYWORDS)

def main():
    sb=create_client(os.environ["SUPABASE_URL"],os.environ["SUPABASE_SECRET_KEY"])
    fighters=fetch_all(sb,"fighters","fightiq_id,display_name,date_of_birth,cito_id,ufcstats_id")
    sources=fetch_all(sb,"fighter_source_ids","fightiq_id,source,source_id")
    cito_ids_by_fiq=defaultdict(list)
    for row in sources:
        if row.get("source")=="cito": cito_ids_by_fiq[row["fightiq_id"]].append(row["source_id"])
    for f in fighters:
        if f.get("cito_id") and f["cito_id"] not in cito_ids_by_fiq[f["fightiq_id"]]:
            cito_ids_by_fiq[f["fightiq_id"]].append(f["cito_id"])
    cito=fetch_cito(os.environ["CITO_API_KEY"])
    cito_by_id={x.get("id"):x for x in cito if x.get("id")}
    missing={f["fightiq_id"]:f for f in fighters if not f.get("date_of_birth")}
    path_counts=Counter(); value_examples=defaultdict(list); date_counts=Counter(); date_examples=defaultdict(list)
    with_cito=0; inspected=0
    for fiq,fighter in missing.items():
        cids=cito_ids_by_fiq.get(fiq,[])
        if not cids: continue
        with_cito+=1
        for cid in cids:
            item=cito_by_id.get(cid)
            if not item: continue
            inspected+=1
            for path,key_name,value in walk(item):
                if not scalar(value): continue
                if interesting_key(key_name):
                    path_counts[path]+=1
                    if len(value_examples[path])<3 and value not in (None,"",False):
                        value_examples[path].append({"fighter":fighter["display_name"],"value":value})
                if looks_like_date(value):
                    date_counts[path]+=1
                    if len(date_examples[path])<5:
                        date_examples[path].append({"fighter":fighter["display_name"],"value":value,"key":key_name})
    print("===== CITO RAW STRUCTURE DIAGNOSTIC =====")
    print(f"fighters_missing_dob: {len(missing)}")
    print(f"missing_dob_with_at_least_one_cito_id: {with_cito}")
    print(f"cito_profiles_inspected: {inspected}")
    print("\n===== MOST COMMON INTERESTING PATHS =====")
    for path,count in path_counts.most_common(120):
        print(json.dumps({"path":path,"count":count,"examples":value_examples[path]},ensure_ascii=False))
    print("\n===== DATE-LIKE VALUES ANYWHERE IN CITO JSON =====")
    for path,count in date_counts.most_common(120):
        print(json.dumps({"path":path,"count":count,"examples":date_examples[path]},ensure_ascii=False))
    print("\n===== TEST FIGHTERS RAW-DATE INSPECTION =====")
    for fighter in fighters:
        if normalize(fighter.get("display_name")) not in TEST_NAMES: continue
        print(f"\n--- {fighter['display_name']} ---")
        print(f"fightiq_id={fighter['fightiq_id']} dob_db={fighter.get('date_of_birth')}")
        for cid in cito_ids_by_fiq.get(fighter["fightiq_id"],[]):
            item=cito_by_id.get(cid)
            if not item: continue
            print(f"cito_id={cid}")
            for path,key_name,value in walk(item):
                if not scalar(value): continue
                if interesting_key(key_name) or looks_like_date(value) or (isinstance(value,str) and any(x in value.lower() for x in ["volkanovski","birth","born"])):
                    if value not in (None,"",False):
                        print(json.dumps({"path":path,"key":key_name,"value":value},ensure_ascii=False))
    print("\nDIAGNOSTIC COMPLETE - NO DATABASE WRITES")
if __name__=="__main__": main()
