"""Expected-value material balance over a set of (recipe, runs)."""
from collections import defaultdict


def boosted(chance, boost, recipe_tier, tier, fn="OVERCLOCK"):
    """GTCEu ChanceBoostFunction.OVERCLOCK (read from the gtceu-26.7.3 decompile, not verified for GTO overrides)."""
    if tier is None or fn != "OVERCLOCK":
        return chance
    diff = tier - recipe_tier
    if diff <= 0:
        return chance
    if recipe_tier == 0:
        diff -= 1
    return max(0, min(10000, chance + boost * diff))


def balance(recipes, tier=None):
    """recipes: [(recipe_dict, runs)] -> dict(need, out, internal, eu, seconds, per_recipe)."""
    prod, cons = defaultdict(float), defaultdict(float)
    eu = sec = 0.0
    per = []
    for r, runs in recipes:
        rt = r.get("tier") or 0
        for s in r["in_items"] + r["in_fluids"]:
            if s.get("consume", True):
                cons[s["key"]] += s["n"] * s.get("chance", 10000) / 10000 * runs
        for s in r["out_items"] + r["out_fluids"]:
            prod[s["key"]] += s["n"] * boosted(s["chance"], s["boost"], rt, tier, r.get("chance_fn", "OVERCLOCK")) / 10000 * runs
        e, t = (r.get("eut") or 0) * (r.get("duration") or 0) * runs, (r.get("duration") or 0) * runs / 20
        eu, sec = eu + e, sec + t
        per.append((r["id"], runs, e, t))
    keys = set(prod) | set(cons)
    need = {k: cons[k] - prod[k] for k in keys if cons[k] - prod[k] > 1e-9}
    out = {k: prod[k] - cons[k] for k in keys if prod[k] - cons[k] > 1e-9}
    internal = {k: min(prod[k], cons[k]) for k in keys if min(prod[k], cons[k]) > 1e-9}
    return {"need": need, "out": out, "internal": internal, "eu": eu, "seconds": sec, "per_recipe": per}
