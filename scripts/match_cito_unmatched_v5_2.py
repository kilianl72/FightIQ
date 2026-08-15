import json, os, re, unicodedata
from difflib import SequenceMatcher
from supabase import create_client

def norm(v):
    if not v: return ""
    v = unicodedata.normalize("NFKD", str(v))
    v = "".join(c for c in v if not unicodedata.combining(c))
    v = re.sub(r"[^a-z0-9]+", " ", v.lower()).strip()
    return re.sub(r"\s+", " ", v)

def fetch_all(sb, table, fields):
    out=[]; start=0; size=1000
    while True:
        batch=(sb.table(table).select(fields).range(start,start+size-1).execute()).data or []
        out += batch
        if len(batch) < size: break
        start += size
    return out

def cm_to_in(v):
    try: return float(v)/2.54
    except: return None

def kg_to_lb(v):
    try: return float(v)/0.45359237
    except: return None

def closenum(a,b,t):
    try: return abs(float(a)-float(b)) <= t
    except: return False

def fighter_names(f):
    full=" ".join(x for x in [f.get("first_name"),f.get("last_name")] if x)
    return {x for x in [norm(f.get("display_name")),norm(full),norm(f.get("nickname"))] if x}

def score(c,f):
    c_names={x for x in [norm(c.get("name")),norm(" ".join(x for x in [c.get("first_name"),c.get("last_name")] if x))] if x}
    f_names=fighter_names(f)
    ratio=max((SequenceMatcher(None,a,b).ratio() for a in c_names for b in f_names), default=0)
    s=ratio*70; reasons=[f"name={ratio:.3f}"]
    if any(a in f_names for a in c_names):
        s+=25; reasons.append("exact_name")
    if c.get("birth_date") and f.get("date_of_birth"):
        if str(c["birth_date"])[:10]==str(f["date_of_birth"])[:10]:
            s+=30; reasons.append("dob_match")
        else:
            s-=35; reasons.append("dob_conflict")
    hi=cm_to_in(f.get("height_cm"))
    if c.get("height_inches") is not None and hi is not None:
        if closenum(c["height_inches"],hi,1.0): s+=7; reasons.append("height_match")
        elif abs(float(c["height_inches"])-hi)>3: s-=8; reasons.append("height_conflict")
    ri=cm_to_in(f.get("reach_cm"))
    if c.get("reach_inches") is not None and ri is not None and closenum(c["reach_inches"],ri,1.5):
        s+=6; reasons.append("reach_match")
    wl=kg_to_lb(f.get("current_weight_kg"))
    if c.get("weight_lbs") is not None and wl is not None and closenum(c["weight_lbs"],wl,8):
        s+=4; reasons.append("weight_match")
    if c.get("stance") and f.get("stance") and norm(c["stance"])==norm(f["stance"]):
        s+=3; reasons.append("stance_match")
    return round(s,2),reasons

def main():
    url=os.environ["SUPABASE_URL"]; key=os.environ["SUPABASE_SECRET_KEY"]
    sb=create_client(url,key)

    unmatched=fetch_all(sb,"cito_unmatched_fighters",
        "cito_id,name,first_name,last_name,nickname,slug,division,status,is_active,record_text,place_of_birth,height_inches,weight_lbs,reach_inches,stance,birth_date,photo_url,profile_url,stats_available")
    fighters=fetch_all(sb,"fighters",
        "id,fightiq_id,ufcstats_id,display_name,first_name,last_name,nickname,date_of_birth,height_cm,reach_cm,current_weight_kg,stance,current_division,is_active,cito_id")

    print("===== V5.2 MATCH CITO UNMATCHED =====")
    print(f"Cito unmatched rows: {len(unmatched)}")
    print(f"FightIQ fighters: {len(fighters)}")

    buckets={"AUTO_SAFE":0,"VERY_LIKELY":0,"REVIEW":0,"NO_MATCH":0}
    results=[]

    for c in unmatched:
        ranked=[]
        for f in fighters:
            s,r=score(c,f); ranked.append((s,f,r))
        ranked.sort(key=lambda x:x[0],reverse=True)
        top=ranked[:3]
        best_s,best,best_r=top[0]
        second=top[1][0] if len(top)>1 else 0
        gap=best_s-second

        if best_s>=105 and gap>=20 and ("exact_name" in best_r or "dob_match" in best_r):
            bucket="AUTO_SAFE"
        elif best_s>=92 and gap>=10:
            bucket="VERY_LIKELY"
        elif best_s>=75:
            bucket="REVIEW"
        else:
            bucket="NO_MATCH"

        buckets[bucket]+=1
        results.append({
            "bucket":bucket,
            "cito":{"cito_id":c.get("cito_id"),"name":c.get("name"),"nickname":c.get("nickname"),
                    "birth_date":c.get("birth_date"),"division":c.get("division"),"is_active":c.get("is_active")},
            "best_match":{"score":best_s,"gap_to_second":round(gap,2),"reasons":best_r,
                          "fightiq_id":best.get("fightiq_id"),"ufcstats_id":best.get("ufcstats_id"),
                          "display_name":best.get("display_name"),"date_of_birth":best.get("date_of_birth")},
            "top3":[{"score":s,"display_name":f.get("display_name"),"ufcstats_id":f.get("ufcstats_id"),
                     "date_of_birth":f.get("date_of_birth"),"reasons":r} for s,f,r in top]
        })

    print("\n===== SUMMARY =====")
    for k,v in buckets.items(): print(f"{k}: {v}")

    for title in ["AUTO_SAFE","VERY_LIKELY","REVIEW"]:
        print(f"\n===== {title} SAMPLE =====")
        for r in [x for x in results if x["bucket"]==title][:40]:
            print(f'{r["cito"]["name"]} -> {r["best_match"]["display_name"]} score={r["best_match"]["score"]} gap={r["best_match"]["gap_to_second"]}')

    with open("cito_unmatched_matching_v5_2_report.json","w",encoding="utf-8") as fh:
        json.dump({"summary":buckets,"results":results},fh,ensure_ascii=False,indent=2)

    print("\nAUDIT ONLY: no fighter links were changed.")

if __name__=="__main__":
    main()
