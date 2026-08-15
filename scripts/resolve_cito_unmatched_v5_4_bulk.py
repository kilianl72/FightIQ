import csv
import io
import json
import os
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from urllib.request import Request, urlopen

from supabase import create_client

DETAILS_URL = "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_fighter_details.csv"
TOTT_URL = "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_fighter_tott.csv"
FIGHTS_URL = "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_fight_results.csv"

MANUAL_ALIASES = {
    "kevin souza": "edimilson souza",
    "kimbo slice": "kevin ferguson",
    "rampage jackson": "quinton jackson",
    "mirko cro cop": "mirko filipovic",
    "tank abbott": "david abbott",
    "minotauro nogueira": "antonio rodrigo nogueira",
    "cris cyborg": "cristiane justino",
    "ulka sasaki": "yuta sasaki",
    "tiago trator": "tiago dos santos e silva",
    "zu anyanwu": "azunna anyanwu",
    "wendell oliveira": "wendell oliveira marques",
    "miguel angel torres": "miguel torres",
    "emily peters kagan": "emily kagan",
    "dan downes": "danny downes",
    "daniel bobish": "dan bobish",
    "alexandra albu": "aleksandra albu",
    "nariman abbassov": "nariman abbasov",
    "zach reese": "zachary reese",
    "max grishin": "maxim grishin",
    "manny gamburyan": "manvel gamburyan",
    "rob whiteford": "robert whiteford",
    "robbie peralta": "robert peralta",
    "yuri alcantara": "iuri alcantara",
}

PLACEHOLDER_PATTERNS = [
    r"^tbd(?: tbd)?$",
    r"^test(?:y)?(?: fighter)?\d*$",
    r"^test fighter\d*$",
]

def norm(v):
    if not v:
        return ""
    v = unicodedata.normalize("NFKD", str(v))
    v = "".join(c for c in v if not unicodedata.combining(c))
    v = re.sub(r"[^a-z0-9]+", " ", v.lower())
    return re.sub(r"\s+", " ", v).strip()

def extract_id(url):
    m = re.search(r"/fighter-details/([a-zA-Z0-9]+)", url or "")
    return m.group(1) if m else None

def download_csv(url):
    req = Request(url, headers={"User-Agent":"FightIQ-bulk-resolver/5.4"})
    with urlopen(req, timeout=120) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))

def fetch_all(sb, table, fields):
    rows=[]; start=0; size=1000
    while True:
        batch=(sb.table(table).select(fields).range(start,start+size-1).execute()).data or []
        rows.extend(batch)
        if len(batch) < size:
            break
        start += size
    return rows

def parse_tott(rows):
    out={}
    for r in rows:
        uid=extract_id(r.get("URL"))
        if uid:
            out[uid]=r
    return out

def parse_fighters(details, tott):
    out=[]
    by_name=defaultdict(list)
    for r in details:
        uid=extract_id(r.get("URL"))
        if not uid:
            continue
        full=" ".join(x for x in [r.get("FIRST","").strip(), r.get("LAST","").strip()] if x).strip()
        t=tott.get(uid,{})
        item={
            "ufcstats_id":uid,
            "name":full,
            "nickname":(r.get("NICKNAME") or "").strip(),
            "dob":(t.get("DOB") or "").strip(),
            "height":(t.get("HEIGHT") or "").strip(),
            "weight":(t.get("WEIGHT") or "").strip(),
            "reach":(t.get("REACH") or "").strip(),
            "stance":(t.get("STANCE") or "").strip(),
        }
        out.append(item)
        by_name[norm(full)].append(item)
        if item["nickname"]:
            by_name[norm(item["nickname"])].append(item)
    return out, by_name

def fight_fingerprint(rows):
    fp=defaultdict(set)
    for r in rows:
        bout=(r.get("BOUT") or "").strip()
        event=(r.get("EVENT") or "").strip()
        if " vs. " in bout:
            a,b=bout.split(" vs. ",1)
        elif " vs " in bout:
            a,b=bout.split(" vs ",1)
        else:
            continue
        a=a.strip(); b=b.strip()
        fp[norm(a)].add((norm(event), norm(b)))
        fp[norm(b)].add((norm(event), norm(a)))
    return fp

def inches_from_text(v):
    if v is None: return None
    s=str(v)
    m=re.search(r"(\d+)'\s*(\d+)",s)
    if m: return int(m.group(1))*12+int(m.group(2))
    m=re.search(r"([\d.]+)",s)
    return float(m.group(1)) if m else None

def pounds_from_text(v):
    if v is None: return None
    m=re.search(r"([\d.]+)",str(v))
    return float(m.group(1)) if m else None

def close(a,b,tol):
    try: return abs(float(a)-float(b)) <= tol
    except: return False

def cito_record(c):
    raw=c.get("raw_json") or {}
    w=raw.get("recordWins")
    l=raw.get("recordLosses")
    d=raw.get("recordDraws")
    if w is None:
        rec=raw.get("record") or {}
        w,l,d=rec.get("wins"),rec.get("losses"),rec.get("draws")
    try:
        return (int(w),int(l),int(d or 0)) if w is not None and l is not None else None
    except:
        return None

def is_placeholder(name):
    n=norm(name)
    return any(re.match(p,n) for p in PLACEHOLDER_PATTERNS)

def score(c, f, fingerprints):
    cn=norm(c.get("name"))
    fn=norm(f["name"])
    nn=norm(f.get("nickname"))
    ratios=[SequenceMatcher(None,cn,fn).ratio()]
    if nn: ratios.append(SequenceMatcher(None,cn,nn).ratio())
    best=max(ratios)
    s=best*60
    reasons=[f"name={best:.3f}"]

    if cn==fn:
        s+=35; reasons.append("exact_name")
    if cn and nn and cn==nn:
        s+=55; reasons.append("exact_nickname")

    target=MANUAL_ALIASES.get(cn)
    if target and fn==norm(target):
        s+=140; reasons.append("validated_alias")

    # Physical cross-check
    ch=c.get("height_inches")
    fh=inches_from_text(f.get("height"))
    if ch is not None and fh is not None:
        if close(ch,fh,1): s+=10; reasons.append("height_match")
        elif not close(ch,fh,3): s-=10; reasons.append("height_conflict")

    cr=c.get("reach_inches")
    fr=inches_from_text(f.get("reach"))
    if cr is not None and fr is not None and close(cr,fr,1.5):
        s+=8; reasons.append("reach_match")

    cw=c.get("weight_lbs")
    fw=pounds_from_text(f.get("weight"))
    if cw is not None and fw is not None and close(cw,fw,8):
        s+=5; reasons.append("weight_match")

    if c.get("stance") and f.get("stance") and norm(c["stance"])==norm(f["stance"]):
        s+=3; reasons.append("stance_match")

    # UFC.com/Cito raw DOB vs UFCStats DOB
    cb=c.get("birth_date")
    fd=f.get("dob")
    if cb and fd:
        # UFCStats is e.g. Sep 10, 1974, so use only if same ISO-like string unavailable.
        pass

    return round(s,2), reasons

def main():
    sb=create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])

    unmatched=fetch_all(
        sb,"cito_unmatched_fighters",
        "cito_id,name,first_name,last_name,nickname,slug,division,status,is_active,record_text,"
        "place_of_birth,height_inches,weight_lbs,reach_inches,stance,birth_date,photo_url,"
        "profile_url,stats_available,raw_json"
    )

    details=download_csv(DETAILS_URL)
    tott=parse_tott(download_csv(TOTT_URL))
    fights=download_csv(FIGHTS_URL)
    fighters,by_name=parse_fighters(details,tott)
    fingerprints=fight_fingerprint(fights)

    summary={"MATCH_READY":0,"PLACEHOLDER":0,"REVIEW":0,"UNRESOLVED":0}
    results=[]

    for c in unmatched:
        if is_placeholder(c.get("name")):
            bucket="PLACEHOLDER"
            summary[bucket]+=1
            results.append({"bucket":bucket,"cito":c,"best_match":None,"top5":[]})
            continue

        ranked=[]
        for f in fighters:
            sc,rs=score(c,f,fingerprints)
            ranked.append((sc,f,rs))
        ranked.sort(key=lambda x:x[0],reverse=True)
        top=ranked[:5]
        best_s,best,best_r=top[0]
        gap=best_s-(top[1][0] if len(top)>1 else 0)

        strong = any(x in best_r for x in ["validated_alias","exact_nickname","exact_name"])
        if "validated_alias" in best_r:
            bucket="MATCH_READY"
        elif best_s>=110 and gap>=18 and strong:
            bucket="MATCH_READY"
        elif best_s>=82:
            bucket="REVIEW"
        else:
            bucket="UNRESOLVED"

        summary[bucket]+=1
        results.append({
            "bucket":bucket,
            "cito":{
                "cito_id":c.get("cito_id"),
                "name":c.get("name"),
                "nickname":c.get("nickname"),
                "record_text":c.get("record_text"),
                "birth_date":c.get("birth_date"),
                "profile_url":c.get("profile_url"),
                "division":c.get("division"),
            },
            "best_match":{
                "score":best_s,
                "gap":round(gap,2),
                "reasons":best_r,
                **best,
            },
            "top5":[{"score":s,"reasons":r,**f} for s,f,r in top]
        })

    order={"MATCH_READY":0,"REVIEW":1,"UNRESOLVED":2,"PLACEHOLDER":3}
    results.sort(key=lambda x:(order[x["bucket"]], -(x["best_match"]["score"] if x["best_match"] else 0)))

    print("===== V5.4 BULK RESOLUTION =====")
    print(f"Cito unmatched: {len(unmatched)}")
    print(f"UFCStats fighters: {len(fighters)}")
    print(f"UFCStats fights: {len(fights)}")
    print(json.dumps(summary,ensure_ascii=False))

    for bucket in ["MATCH_READY","REVIEW","UNRESOLVED","PLACEHOLDER"]:
        print(f"\n===== {bucket} =====")
        for r in [x for x in results if x["bucket"]==bucket]:
            if r["best_match"]:
                print(f'{r["cito"]["name"]} -> {r["best_match"]["name"]} '
                      f'id={r["best_match"]["ufcstats_id"]} score={r["best_match"]["score"]} '
                      f'gap={r["best_match"]["gap"]} reasons={",".join(r["best_match"]["reasons"])}')
            else:
                print(r["cito"]["name"])

    with open("cito_bulk_resolution_v5_4_report.json","w",encoding="utf-8") as fh:
        json.dump({"summary":summary,"results":results},fh,ensure_ascii=False,indent=2)

    print("\nAUDIT ONLY: no Supabase fighter rows modified.")

if __name__=="__main__":
    main()
