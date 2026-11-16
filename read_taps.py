#!/usr/bin/env python3
"""Print a day's synced taps, or import blood pressure.

Usage:
  python3 read_taps.py [YYYY-MM-DD] [--json]
  python3 read_taps.py --bp [--json]
  python3 read_taps.py --bp-add --json-in FILE [--dry-run] [--json]
  python3 read_taps.py --bp-parse --json-in FILE [--json]

--json-in FILE is a JSON document (or '-' for stdin). Accepted shapes:
  1) Simple list: [{"sys": 118, "dia": 74, "date": "YYYY-MM-DD", "time": "HH:MM"}, ...]
     Aliases: s/systolic for sys, di/diastolic for dia. time is optional.
  2) Health Auto Export REST: data.metrics[] with name "blood_pressure",
     data[] entries with systolic, diastolic, and date like
     "2026-11-16 08:12:00 -0500". A top-level or bare metrics array is also OK.

--bp-add merges into the private textdb "bp" record: dedupe on date+time,
newest-first at read time, never deletes existing readings. --dry-run and
--bp-parse do not write.
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.request

KEYF = "/cursor/stores/store-c216b2f0-d5d7-421f-8b57-038751d79c25/trainer-dashboard/sync_key.txt"
UA = {"User-Agent": "read_taps"}
_ID_SEQ = 0


def _key():
    return os.environ.get("TAPS_KEY") or open(KEYF).read().strip()


def _url():
    return f"https://textdb.dev/api/data/{_key()}"


def load_state():
    req = urllib.request.Request(_url(), headers=UA)
    t = urllib.request.urlopen(req, timeout=20).read().decode().strip()
    return json.loads(t) if t else {}


def save_state(S):
    body = json.dumps(S, separators=(",", ":")).encode()
    req = urllib.request.Request(
        _url(), data=body, method="POST",
        headers={**UA, "Content-Type": "text/plain"},
    )
    urllib.request.urlopen(req, timeout=20).read()


def _qty(v):
    if isinstance(v, dict):
        for k in ("qty", "value", "amount"):
            if v.get(k) is not None:
                return _qty(v[k])
        return None
    return v


def _norm_time(t):
    if t is None or t == "":
        return ""
    t = str(t).strip()
    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?\s*$", t)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?\s*([ap]m)$", t, re.I)
    if m:
        h, mi, ap = int(m.group(1)), m.group(2), m.group(3).lower()
        if ap == "pm" and h != 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mi}"
    return t


def _parse_date_time(raw, time_field=None):
    if raw is None or raw == "":
        if time_field:
            return None, _norm_time(time_field)
        return None, ""
    if isinstance(raw, (int, float)):
        # seconds or milliseconds epoch
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).astimezone()
        return dt.date().isoformat(), dt.strftime("%H:%M")
    s = str(raw).strip()
    m = re.match(
        r"(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(?:\s*([+-]\d{2}:?\d{2}|Z))?",
        s,
    )
    if m:
        return m.group(1), f"{int(m.group(2)):02d}:{m.group(3)}"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s, _norm_time(time_field)
    return None, _norm_time(time_field)


def _as_int(v):
    v = _qty(v)
    if v is None or v == "":
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def valid_reading(r):
    s, di, d = r.get("sys"), r.get("dia"), r.get("date")
    if not d or not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return False
    if s is None or di is None:
        return False
    if not (70 <= s <= 250 and 40 <= di <= 150 and s > di):
        return False
    t = r.get("time") or ""
    if t and not re.match(r"^\d{2}:\d{2}$", t):
        return False
    return True


def _entry_to_reading(e):
    if not isinstance(e, dict):
        return None
    s = _as_int(e.get("systolic") if e.get("systolic") is not None else (e.get("sys") if e.get("sys") is not None else e.get("s")))
    di = _as_int(e.get("diastolic") if e.get("diastolic") is not None else (e.get("dia") if e.get("dia") is not None else e.get("di")))
    raw = e.get("date") or e.get("start") or e.get("startDate") or e.get("start_date")
    d, t = _parse_date_time(raw, e.get("time") or e.get("t"))
    if not d and isinstance(e.get("date"), str) and re.match(r"^\d{4}-\d{2}-\d{2}$", e["date"]):
        d, t = e["date"], _norm_time(e.get("time") or e.get("t"))
    r = {"sys": s, "dia": di, "date": d, "time": t or ""}
    return r if s is not None and di is not None and d else None


def _is_bp_metric_name(name):
    n = re.sub(r"[\s-]+", "_", str(name or "").strip().lower())
    return n in ("blood_pressure", "bloodpressure", "bp")


def _from_metric(m):
    out = []
    if not isinstance(m, dict):
        return out
    if not _is_bp_metric_name(m.get("name") or m.get("metric") or m.get("type")):
        return out
    for e in m.get("data") or []:
        r = _entry_to_reading(e)
        if r:
            out.append(r)
    return out


def parse_payload(obj):
    """Return a list of {sys, dia, date, time} from either accepted payload shape."""
    readings = []
    if obj is None:
        return readings
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and _looks_like_reading(obj[0]):
            for e in obj:
                r = _entry_to_reading(e)
                if r:
                    readings.append(r)
            return readings
        for item in obj:
            readings.extend(parse_payload(item))
        return readings
    if not isinstance(obj, dict):
        return readings
    # Health Auto Export: {data: {metrics: [...]}} or {metrics: [...]}
    data = obj.get("data") if isinstance(obj.get("data"), dict) else obj
    metrics = None
    if isinstance(data, dict) and isinstance(data.get("metrics"), list):
        metrics = data["metrics"]
    elif isinstance(obj.get("metrics"), list):
        metrics = obj["metrics"]
    if metrics is not None:
        for m in metrics:
            readings.extend(_from_metric(m))
        return readings
    if isinstance(obj.get("data"), list):
        # {name: blood_pressure, data: [...]} or {data: [readings]}
        if _is_bp_metric_name(obj.get("name") or obj.get("metric") or obj.get("type")):
            return _from_metric(obj)
        for e in obj["data"]:
            r = _entry_to_reading(e) if _looks_like_reading(e) else None
            if r:
                readings.append(r)
            else:
                readings.extend(parse_payload(e))
        return readings
    if isinstance(obj.get("readings"), list):
        return parse_payload(obj["readings"])
    r = _entry_to_reading(obj)
    return [r] if r else []


def _looks_like_reading(e):
    if not isinstance(e, dict):
        return False
    keys = set(e)
    return bool(keys & {"sys", "s", "systolic", "dia", "di", "diastolic"})


def _new_id():
    global _ID_SEQ
    ms = int(time.time() * 1000) + _ID_SEQ
    _ID_SEQ += 1
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    n, s = ms, ""
    while n:
        n, r = divmod(n, 36)
        s = chars[r] + s
    return s or "0"


def merge_bp(existing, new_readings):
    """Merge readings into the bp dict. Never deletes. Dedupe on date+time.

    Returns (merged_dict, added, skipped) where skipped items are
    {reading, reason}.
    """
    out = dict(existing or {})
    seen = set()
    for v in out.values():
        if isinstance(v, dict) and v.get("s") and v.get("d"):
            seen.add((v["d"], v.get("t") or ""))
    added, skipped = [], []
    for r in new_readings:
        if not valid_reading(r):
            skipped.append({"reading": r, "reason": "invalid"})
            continue
        key = (r["date"], r.get("time") or "")
        if key in seen:
            skipped.append({"reading": r, "reason": "duplicate"})
            continue
        out[_new_id()] = {"s": r["sys"], "di": r["dia"], "d": r["date"], "t": r.get("time") or ""}
        seen.add(key)
        added.append(r)
    return out, added, skipped


def bp_list(S):
    return sorted(
        [dict(id=k, **v) for k, v in (S.get("bp") or {}).items() if isinstance(v, dict) and v.get("s")],
        key=lambda r: r["d"] + (r.get("t") or ""),
        reverse=True,
    )


def _print_bp(bp, as_json):
    if as_json:
        print(json.dumps(bp, indent=1))
    else:
        if not bp:
            print("No BP readings")
        else:
            for r in bp:
                print(r["d"], r.get("t") or "", f"{r['s']}/{r['di']}")


def _load_json_in(path):
    raw = sys.stdin.read() if path == "-" else open(path).read()
    return json.loads(raw) if raw.strip() else None


def _flag(name):
    return name in sys.argv


def _opt(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def main():
    as_json = _flag("--json")
    if _flag("--bp-parse") or _flag("--bp-add"):
        path = _opt("--json-in")
        if not path:
            sys.exit("Need --json-in FILE (or --json-in - for stdin)")
        payload = _load_json_in(path)
        parsed = parse_payload(payload)
        if _flag("--bp-parse") and not _flag("--bp-add"):
            if as_json:
                print(json.dumps(parsed, indent=1))
            else:
                if not parsed:
                    print("No blood pressure readings in payload")
                else:
                    for r in parsed:
                        print(r["date"], r.get("time") or "", f"{r['sys']}/{r['dia']}")
            return
        # --bp-add (optional --dry-run)
        S = load_state()
        merged, added, skipped = merge_bp(S.get("bp") or {}, parsed)
        result = {
            "parsed": len(parsed),
            "added": added,
            "skipped": skipped,
            "total": len([v for v in merged.values() if isinstance(v, dict) and v.get("s")]),
            "wrote": False,
        }
        if _flag("--dry-run") or not added:
            result["wrote"] = False
            if as_json:
                print(json.dumps(result, indent=1))
            else:
                print(f"parsed {result['parsed']}, would add {len(added)}, skipped {len(skipped)}, total {result['total']}"
                      + (" (dry-run, not written)" if _flag("--dry-run") else " (nothing new, not written)"))
                for r in added:
                    print("  add", r["date"], r.get("time") or "", f"{r['sys']}/{r['dia']}")
                for s in skipped:
                    r = s["reading"]
                    print("  skip", s["reason"], r.get("date") or "?", r.get("time") or "", f"{r.get('sys')}/{r.get('dia')}")
            return
        # Refetch so we don't clobber a concurrent tap update, then merge again.
        S2 = load_state()
        merged, added, skipped = merge_bp(S2.get("bp") or {}, parsed)
        if not added:
            result.update(added=added, skipped=skipped, total=len([v for v in merged.values() if isinstance(v, dict) and v.get("s")]), wrote=False)
        else:
            S2["bp"] = merged
            S2["_ts"] = int(time.time() * 1000)
            save_state(S2)
            result.update(added=added, skipped=skipped, total=len([v for v in merged.values() if isinstance(v, dict) and v.get("s")]), wrote=True)
        if as_json:
            print(json.dumps(result, indent=1))
        else:
            print(f"parsed {result['parsed']}, added {len(result['added'])}, skipped {len(result['skipped'])}, total {result['total']}"
                  + (" (written)" if result["wrote"] else " (nothing new, not written)"))
            for r in result["added"]:
                print("  add", r["date"], r.get("time") or "", f"{r['sys']}/{r['dia']}")
        return

    if _flag("--bp"):
        _print_bp(bp_list(load_state()), as_json)
        return

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    day = args[0] if args else datetime.date.today().isoformat()
    S = load_state()
    data = json.load(open(__file__.rsplit("/", 1)[0] + "/data.json"))
    items = data.get("items", []) if data.get("date") == day else []
    work = S.get("work:" + day) or {}
    stack = S.get("stack:" + day) or {}
    water = (S.get("water") or {}).get(day, 0)
    DR = S.get("drops:" + day)
    lemon = bool(DR.get("0")) if DR else water > 0
    matcha = bool(DR.get("7")) if DR else water >= 8
    others = sum(1 for k, v in DR.items() if v and k not in ("0", "7")) if DR else min(max(water - 1, 0), 6)
    wpts_water = round((5 if lemon else 0) + (5 if matcha else 0) + 5 * others / 6)
    BLOCKS = {"Morning": ["asp", "cre", "cit"], "First meal": ["omg", "qun", "cen", "evoo", "cit2"], "Bedtime": ["mag", "ator"]}
    checked = [items[int(i)] if int(i) < len(items) else f"item {i}" for i, v in sorted(work.items(), key=lambda x: int(x[0])) if v]
    req_items = [i for i, t in enumerate(items) if "optional" not in t.lower()]
    wpts = round(45 * sum(1 for i in req_items if work.get(str(i))) / len(req_items)) if req_items else 0
    blocks = {b: all(stack.get(i) for i in ids) for b, ids in BLOCKS.items()}
    out = {
        "day": day,
        "workout_checked": checked,
        "workout_pts": wpts,
        "stack_blocks": blocks,
        "stack_pts": 10 * sum(blocks.values()),
        "water_glasses": water,
        "water_pts": wpts_water,
        "lemon_water": lemon,
        "matcha": matcha,
        "last_sync_ms": S.get("_ts"),
        "page_score": (S.get("scorehist") or {}).get(day),
    }
    if as_json:
        print(json.dumps(out, indent=1))
    else:
        print(f"Day {day}  (WHOOP pts not included; add 10 if recovery present)")
        print("Workout checked:", ", ".join(checked) or "none", f"→ {wpts}/45")
        print("Stack blocks:", ", ".join(f"{b} {'✓' if v else '✗'}" for b, v in blocks.items()), f"→ {out['stack_pts']}/30")
        print(f"Water: {water}/8 (lemon {'✓' if lemon else '✗'}, matcha {'✓' if matcha else '✗'}) → {out['water_pts']}/15")
        print("Page score saved:", out["page_score"])


if __name__ == "__main__":
    main()
