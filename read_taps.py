#!/usr/bin/env python3
"""Print a day's synced taps. Usage: python3 read_taps.py [YYYY-MM-DD] [--json]"""
import sys,json,urllib.request,datetime
KEYF="/cursor/stores/store-c216b2f0-d5d7-421f-8b57-038751d79c25/trainer-dashboard/sync_key.txt"
import os
key=os.environ.get("TAPS_KEY") or open(KEYF).read().strip()
args=[a for a in sys.argv[1:] if not a.startswith("--")]
day=args[0] if args else datetime.date.today().isoformat()
req=urllib.request.Request(f"https://textdb.dev/api/data/{key}",headers={"User-Agent":"read_taps"})
t=urllib.request.urlopen(req,timeout=20).read().decode().strip()
S=json.loads(t) if t else {}
data=json.load(open(__file__.rsplit("/",1)[0]+"/data.json"))
items=data.get("items",[]) if data.get("date")==day else []
work=S.get("work:"+day) or {}; stack=S.get("stack:"+day) or {}; water=(S.get("water") or {}).get(day,0)
DR=S.get("drops:"+day); lemon=bool(DR.get("0")) if DR else water>0; matcha=bool(DR.get("7")) if DR else water>=8; others=sum(1 for k,v in DR.items() if v and k not in ("0","7")) if DR else min(max(water-1,0),6)
wpts_water=round((5 if lemon else 0)+(5 if matcha else 0)+5*others/6)
BLOCKS={"Morning":["asp","cre","cit"],"First meal":["omg","qun","cen","evoo"],"Bedtime":["mag","ator"]}
checked=[items[int(i)] if int(i)<len(items) else f"item {i}" for i,v in sorted(work.items(),key=lambda x:int(x[0])) if v]
req_items=[i for i,t in enumerate(items) if "optional" not in t.lower()]
wpts=round(45*sum(1 for i in req_items if work.get(str(i)))/len(req_items)) if req_items else 0
blocks={b:all(stack.get(i) for i in ids) for b,ids in BLOCKS.items()}
out={"day":day,"workout_checked":checked,"workout_pts":wpts,"stack_blocks":blocks,"stack_pts":10*sum(blocks.values()),
     "water_glasses":water,"water_pts":wpts_water,"lemon_water":lemon,"matcha":matcha,"last_sync_ms":S.get("_ts"),"page_score":(S.get("scorehist") or {}).get(day)}
if "--json" in sys.argv: print(json.dumps(out,indent=1))
else:
  print(f"Day {day}  (WHOOP pts not included; add 10 if recovery present)")
  print("Workout checked:",", ".join(checked) or "none",f"→ {wpts}/45")
  print("Stack blocks:",", ".join(f"{b} {'✓' if v else '✗'}" for b,v in blocks.items()),f"→ {out['stack_pts']}/30")
  print(f"Water: {water}/8 (lemon {'✓' if lemon else '✗'}, matcha {'✓' if matcha else '✗'}) → {out['water_pts']}/15");print("Page score saved:",out["page_score"])
