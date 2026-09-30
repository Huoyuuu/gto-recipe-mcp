"""v2 pure functions: standard overclock, machine multipliers from tooltips, chained planner (per-second rates)."""
import re
from collections import defaultdict

from .balance import boosted
from .db import TIERS

V = [8 * 4 ** i for i in range(len(TIERS))]


def vtier(v):
    if isinstance(v, int):
        return v
    s = str(v).strip()
    return int(s) if s.isdigit() else [t.lower() for t in TIERS].index(s.lower())


def overclock(eut, dur, voltage, perfect=False, eu_mult=1.0, dur_mult=1.0):
    """Multipliers first, then while eut*4 <= V[voltage]: EU x4, time /2 (/4 perfect), >= 1 tick."""
    eut, dur, n, cap = (eut or 0) * eu_mult, (dur or 0) * dur_mult, 0, V[vtier(voltage)]
    while eut > 0 and eut * 4 <= cap and dur > 1:
        eut, dur, n = eut * 4, max(1, dur / (4 if perfect else 2)), n + 1
    return eut, dur, n


def mults(machine):
    """(eu_mult, dur_mult, perfect) from '- 能量倍率 : 0.8' / '- 时间倍率 : 0.6' / '- 无损超频 : ✔' tooltip lines."""
    tips = [t.strip() for t in (machine or {}).get("tooltips_zh", [])]
    get = lambda k: next((float(m.group(1)) for t in tips if (m := re.fullmatch(rf"-\s*{k}\s*:\s*([\d.]+)", t))), 1.0)
    return get("能量倍率"), get("时间倍率"), any(re.fullmatch(r"-\s*无损超频\s*:\s*✔", t) for t in tips)


def vec(r, tier):
    """Per-run consumption / expected production (chance outputs boosted at `tier`)."""
    c, p = defaultdict(float), defaultdict(float)
    for s in r["in_items"] + r["in_fluids"]:
        if s.get("consume", True):
            c[s["key"]] += s["n"] * s.get("chance", 10000) / 10000
    for s in r["out_items"] + r["out_fluids"]:
        p[s["key"]] += s["n"] * boosted(s["chance"], s.get("boost", 0), r.get("tier") or 0, tier, r.get("chance_fn", "OVERCLOCK")) / 10000
    return c, p


def plan(feed, steps, voltage="IV"):
    """steps: [{recipe, machine, mode=use|make, share, drive, target, parallel, voltage, runs, perfect}]; all rates per second."""
    warn, S = [], []
    for st in steps:
        v = vtier(st.get("voltage") or voltage)
        c, p = vec(st["recipe"], v)
        mode = "fixed" if st.get("runs") is not None else st.get("mode") or "use"
        if mode not in ("use", "make", "fixed"):
            raise ValueError(f"{st['recipe']['id']} 的 mode 只能是 use / make")
        S.append({**st, "c": c, "p": p, "v": v, "mode": mode, "x": float(st.get("runs") or 0)})
    for s in S:
        if s["mode"] == "make":
            s["target"] = s.get("target") or next(iter(s["p"]), None)
            if s["target"] not in s["p"]:
                raise ValueError(f"{s['recipe']['id']} 不产出 target {s['target']}")
    targets = {s["target"] for s in S if s["mode"] == "make"}
    avail = set(feed) | {k for s in S if s["mode"] == "use" for k in s["p"]}
    for s in S:
        if s["mode"] != "use":
            continue
        order = [x["key"] for x in s["recipe"]["in_items"] + s["recipe"]["in_fluids"] if x.get("consume", True)]
        if s.get("drive"):
            if s["drive"] not in s["c"]:
                raise ValueError(f"{s['recipe']['id']} 不消耗 drive {s['drive']}")
        else:
            s["drive"] = next((k for k in order if k in avail and k not in targets), None)
            if not s["drive"]:
                raise ValueError(f"{s['recipe']['id']} 选不到驱动输入（没有来自进料或上游的输入），请给 drive 或 runs")
    groups = defaultdict(list)
    for s in S:
        if s["mode"] == "use":
            groups[s["drive"]].append(s)
    for d, g in groups.items():
        fixed = sum(s["share"] for s in g if s.get("share") is not None)
        free = [s for s in g if s.get("share") is None]
        if fixed > 1 + 1e-9:
            warn.append(f"{d} 的 share 合计 {fixed:g} > 1")
        for s in free:
            s["share"] = max(0.0, 1 - fixed) / len(free)
    for _ in range(1000):                                       # Gauss-Seidel to a fixed point
        delta = 0.0
        for s in S:
            if s["mode"] == "use":
                d = s["drive"]
                x = s["share"] * (feed.get(d, 0) + sum(o["p"].get(d, 0) * o["x"] for o in S if o is not s)) / s["c"][d]
            elif s["mode"] == "make":
                t = s["target"]
                gap = sum((o["c"].get(t, 0) - o["p"].get(t, 0)) * o["x"] for o in S if o is not s) - feed.get(t, 0)
                net = s["p"].get(t, 0) - s["c"].get(t, 0)
                x = max(0.0, gap / net) if net > 0 else 0.0
            else:
                continue
            delta, s["x"] = max(delta, abs(x - s["x"])), x
        if delta < 1e-12:
            break
    else:
        warn.append("迭代 1000 轮未收敛（检查回收环的产率）")
    rows, prod, cons, tot = [], defaultdict(float), defaultdict(float), 0.0
    for s in S:
        r, m = s["recipe"], s.get("machine")
        em, tm, perf = mults(m)
        eut, ticks, n = overclock(r.get("eut"), r.get("duration"), s["v"], s.get("perfect", perf), em, tm)
        par = float(s.get("parallel") or 1)
        low = (r.get("eut") or 0) * em > V[s["v"]]
        if low:
            warn.append(f"{r['id']} 需要 {(r.get('eut') or 0) * em:g} EU/t，{TIERS[s['v']]} 电压不足")
        avg = s["x"] * eut * ticks / 20
        tot += avg
        for k, v in s["c"].items():
            cons[k] += v * s["x"]
        for k, v in s["p"].items():
            prod[k] += v * s["x"]
        rows.append({"id": r["id"], "mode": s["mode"], "runs": s["x"], "machine": (m or {}).get("zh") or "?", "machine_id": (m or {}).get("id"),
                     "voltage": TIERS[s["v"]], "oc": n, "eut": eut, "ticks": ticks, "parallel": par, "machines": s["x"] * ticks / 20 / par,
                     "avg_eut": avg, "drive": s.get("drive"), "target": s.get("target"), "share": s.get("share"), "low_voltage": low})
    need, out, left, internal = {}, {}, {}, {}
    for k in set(prod) | set(cons) | set(feed):
        f, net = feed.get(k, 0.0), prod[k] + feed.get(k, 0.0) - cons[k]
        if net < -1e-9:
            need[k] = -net
        elif net > 1e-9:
            if f > 0:
                left[k] = min(f, net)
            if net - left.get(k, 0) > 1e-9:
                out[k] = net - left.get(k, 0)
        if min(prod[k], cons[k]) > 1e-9:
            internal[k] = min(prod[k], cons[k])
    return {"voltage": TIERS[vtier(voltage)], "steps": rows, "need": need, "out": out, "left": left, "internal": internal,
            "eut": tot, "warnings": warn}
