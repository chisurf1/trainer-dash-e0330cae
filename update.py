#!/usr/bin/env python3
"""Rewrite data.json. Example:
  update.py --date 2026-10-14 --item "30-min walk" --item "2x15 pushups" --date 2026-10-14 --challenge "..." --recovery 61 --sleep 80 --strain 9.4 --constraint "" --notes ""
Omitted WHOOP values are set to null (never carried over)."""
import argparse, json, os, datetime, zoneinfo
p=os.path.join(os.path.dirname(os.path.abspath(__file__)),"data.json")
a=argparse.ArgumentParser()
a.add_argument("--date",default=datetime.datetime.now(zoneinfo.ZoneInfo("America/Detroit")).date().isoformat())
a.add_argument("--challenge");a.add_argument("--constraint");a.add_argument("--notes")
for k in("recovery","sleep","strain"):a.add_argument("--"+k,type=float)
a.add_argument("--item",action="append",help="repeatable; replaces the day's checklist");a.add_argument("--keep-items",action="store_true")
a.add_argument("--keto",nargs=2,action="append",metavar=("DATE","on|off|fast"))
x=a.parse_args();d=json.load(open(p))
d["date"]=x.date
for k in("challenge","constraint","notes"):
    if getattr(x,k) is not None:d[k]=getattr(x,k)
d["items"]=x.item or (d.get("items",[]) if x.keep_items else [])
d["whoop"]={k:getattr(x,k) for k in("recovery","sleep","strain")}
for dt,v in x.keto or []:d.setdefault("keto_seed",{})[dt]=v
json.dump(d,open(p,"w"),ensure_ascii=False,indent=2);print("wrote",p)
