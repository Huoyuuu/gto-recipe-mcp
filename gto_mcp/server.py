"""gto-recipes MCP: read-only, offline GregTech Odyssey recipe queries (stdio)."""
import json

from mcp.server.fastmcp import FastMCP

from .balance import balance as _balance
from .db import DB, TIERS, tier_of, tpath

CAP = 4000
STATIC_HINT = "（静态索引：不含运行时生成配方——材料分解/矿石处理/TagPrefix 生成等；需要运行时导出 analysis/runtime-exporter/run_export.py）"
mcp = FastMCP("gto-recipes", instructions="GregTech Odyssey 配方/机器/材料只读查询。物品参数接受 registry id、dust:Bastnasite、中文名、英文材料名或模糊词。")
db = None


def D():
    global db
    if db is None:
        db = DB()
    return db


# ------------------------------------------------------------------ formatting
def num(x):
    return f"{x:g}" if isinstance(x, float) else str(x)


def stack(s, fluid, out=False):
    n = s["n"]
    ch = s.get("chance", 10000)
    txt = f"{num(n)}mB {D().label(s['key'])}" if fluid else f"{num(n)}×{D().label(s['key'])}"
    if ch != 10000:
        boost = s.get("boost", 0)
        txt += f"@{ch / 100:g}%" + (f"(+{boost / 100:g}%/档)" if boost else "")
        if out:
            txt += f"≈{n * ch / 10000:.3g}"
    return txt


def rid_short(rid):
    return rid.split(":", 1)[-1]


def fmt(r):
    d = r.get("data", {})
    t = tpath(r["type"])
    head = f"[{D().type_labels.get(t, t)} {t}] {rid_short(r['id'])}  {r.get('eut')}EU/t({TIERS[r.get('tier') or tier_of(r.get('eut'))]}) {r.get('duration')}t"
    if d.get("ebf_temp"): head += f" {d['ebf_temp']}K"
    if r.get("circuit") is not None: head += f" c{r['circuit']}"
    if d.get("cleanroom"): head += f" 洁净:{d['cleanroom']}"
    for c in d.get("conditions", []):
        if c.get("type") != "CleanroomCondition": head += f" {c.get('text') or c['type']}"
    if r.get("gen"): head += " [生成]"
    lines = [head,
             "  IN  " + " | ".join(filter(None, [", ".join(stack(s, False) for s in r["in_items"]), ", ".join(stack(s, True) for s in r["in_fluids"])])),
             "  OUT " + " | ".join(filter(None, [", ".join(stack(s, False, True) for s in r["out_items"]), ", ".join(stack(s, True, True) for s in r["out_fluids"])]))]
    if r.get("not_consumable"):
        lines.append("  NC  " + ", ".join(D().label(s["key"]) for s in r["not_consumable"]))
    return "\n".join(lines)


def page(blocks, head="", offset=0, total=None, fmt_="text", objs=None):
    """Join blocks under the ~4 KB cap; report where to continue."""
    if fmt_ == "json":
        return json.dumps({"source": D().source, "total": total, "offset": offset, "items": objs if objs is not None else blocks}, ensure_ascii=False)
    out, size = [], len(head.encode())
    for b in blocks:
        if out and size + len(b.encode()) > CAP:
            break
        out.append(b)
        size += len(b.encode()) + 1
    total = len(blocks) + offset if total is None else total
    tail = f"\n…已截断，共 {total} 条，用 offset={offset + len(out)} 继续" if offset + len(out) < total else ""
    return "\n".join([f"source={D().source} {head}".rstrip()] + out) + tail


def resolve_one(item):
    keys = D().resolve(item)
    if len(keys) == 1:
        return keys[0], None
    if not keys:
        return None, f"source={D().source} 找不到「{item}」"
    return None, f"source={D().source} 「{item}」有多个候选，请用 registry id 指定：\n" + "\n".join(f"  {k}  {D().label(k)}" for k in keys)


# ------------------------------------------------------------------ tools
@mcp.tool()
def search(q: str, kind: str = "item", limit: int = 20, offset: int = 0, format: str = "text") -> str:
    """模糊搜索。kind = item | recipe | machine | material。"""
    like = f"%{q.strip().lower()}%"
    if kind == "recipe":
        rows = D().q("SELECT id, type, eut FROM recipes WHERE lower(id) LIKE ? ORDER BY eut IS NULL, eut, id", like)
        items = [f"{rid_short(r['id'])}  {r['eut']}EU/t" for r in rows]
    elif kind == "machine":
        types = {t for t, zh in D().type_labels.items() if q in zh or q.lower() in t}
        rows = [r for r in D().q("SELECT id, zh, multi FROM machines ORDER BY multi DESC, ntypes, id")
                if q in (r["zh"] or "") or q.lower() in r["id"].lower()
                or types & {t["type"] for t in D().q("SELECT type FROM mtypes WHERE mid=?", r["id"])}]
        items = [f"{r['id']}  {r['zh']}{'  [多方块]' if r['multi'] else ''}" for r in rows]
    elif kind == "material":
        rows = D().q("SELECT name, zh, j FROM materials WHERE lower(name) LIKE ? OR zh LIKE ? ORDER BY length(name)", like, like)
        items = [f"{r['name']}  {r['zh']}  {json.loads(r['j']).get('formula', '')}" for r in rows]
        if not rows and D().source == "static":
            items = ["（材料表只在运行时导出中提供）"]
    else:
        rows = D().q("SELECT key, MIN(prio*100+length(name)) s FROM names WHERE name LIKE ? GROUP BY key ORDER BY s, key", like)
        items = [f"{r['key']}  {D().label(r['key'])}" for r in rows]
    return page(items[offset:offset + limit], f"search {kind} 「{q}」 {len(items)} 条", offset, len(items), format)


@mcp.tool()
def recipe(id: str, format: str = "text") -> str:
    """单条配方全文。id 可为完整 id、type/tail 或唯一的 tail。"""
    ids = D().find_recipe(id)
    if len(ids) != 1:
        return f"source={D().source} " + ("找不到配方 " + id if not ids else "多个候选：\n" + "\n".join("  " + rid_short(i) for i in ids))
    r = D().recipe(ids[0])
    return json.dumps({"source": D().source, **r}, ensure_ascii=False) if format == "json" else f"source={D().source}\n{fmt(r)}"


def _io(item, direction, limit, offset, max_eut, format):
    key, err = resolve_one(item)
    if err:
        return err
    sql = "SELECT DISTINCT r.id, r.j, r.eut, r.dur FROM io JOIN recipes r ON r.id=io.rid WHERE io.key=? AND io.dir=?"
    args = [key, direction]
    if max_eut is not None:
        sql += " AND r.eut<=?"
        args.append(max_eut)
    rows = D().q(sql + " ORDER BY r.eut IS NULL, r.eut, r.dur, r.id", *args)
    rs = [json.loads(r["j"]) for r in rows]
    head = f"{'产出' if direction == 'out' else '消耗'} {D().label(key)} ({key}) 共 {len(rs)} 条"
    if D().source == "static":
        head += "\n" + STATIC_HINT
    sl = rs[offset:offset + limit]
    return page([fmt(r) for r in sl], head, offset, len(rs), format, sl)


@mcp.tool()
def producers(item: str, limit: int = 10, max_eut: int | None = None, offset: int = 0, format: str = "text") -> str:
    """产出该物品/流体的配方，按 EU/t 升序；概率产物显示 @概率 与期望。"""
    return _io(item, "out", limit, offset, max_eut, format)


@mcp.tool()
def consumers(item: str, limit: int = 10, offset: int = 0, format: str = "text") -> str:
    """消耗该物品/流体的配方，按 EU/t 升序（不含不可消耗输入）。"""
    return _io(item, "in", limit, offset, None, format)


def _machine_line(m):
    j = json.loads(m["j"])
    abil = j.get("part_abilities") or []
    extra = []
    if j.get("par"): extra.append("并行:" + ",".join(j["par"]))
    tips = [t for t in j.get("tooltips_zh", []) if "并行" in t or "超频" in t]
    if tips: extra.append(" / ".join(tips[:2]))
    if abil: extra.append(f"仓室{len(abil)}种")
    elif j.get("abil_static"): extra.append("仓室(静态):" + ",".join(j["abil_static"]))
    return f"  {m['id']}  {m['zh']}{'  [多方块]' if m['multi'] else ''}  {len(j['recipe_types'])}类配方  " + "  ".join(extra)


@mcp.tool()
def machines_for(recipe_type_or_id: str, format: str = "text") -> str:
    """能跑该配方类型（或该配方所属类型）的全部机器，多方块优先。"""
    t = recipe_type_or_id.strip()
    ids = [] if ":" in t and "/" not in t else D().find_recipe(t)
    t = D().q("SELECT type FROM recipes WHERE id=?", ids[0])[0]["type"] if len(ids) == 1 else tpath(t)
    rows = D().q("SELECT m.* FROM mtypes t JOIN machines m ON m.id=t.mid WHERE t.type=? ORDER BY m.multi DESC, m.ntypes, m.tier, m.id", t)
    head = f"配方类型 {t}（{D().type_labels.get(t, '?')}）可用机器 {len(rows)} 台"
    return page([_machine_line(m) for m in rows], head, 0, len(rows), format, [json.loads(m["j"]) for m in rows])


@mcp.tool()
def machine(id: str, format: str = "text") -> str:
    """机器定义：配方类型、仓室能力（PartAbility）、tooltip。"""
    q = id.strip()
    rows = D().q("SELECT * FROM machines WHERE id=? OR id LIKE ? OR zh=?", q, "%:" + q, q) or \
        D().q("SELECT * FROM machines WHERE id LIKE ? OR zh LIKE ? LIMIT 15", f"%{q}%", f"%{q}%")
    if len(rows) != 1:
        return f"source={D().source} " + ("找不到机器 " + q if not rows else "多个候选：\n" + "\n".join(f"  {r['id']}  {r['zh']}" for r in rows))
    j = json.loads(rows[0]["j"])
    if format == "json":
        return json.dumps({"source": D().source, **j}, ensure_ascii=False)
    lines = [f"{j['id']}  {j['zh']}  {'多方块' if j['multiblock'] else '单方块'}  tier={j.get('tier')}  {j.get('cls') or ''}",
             "配方类型: " + ", ".join(f"{tpath(t)}({D().type_labels.get(tpath(t), '')})" for t in j["recipe_types"])]
    if j.get("par"): lines.append("并行特征(静态): " + ", ".join(j["par"]))
    if j.get("part_abilities"): lines.append("仓室(运行时 pattern): " + ", ".join(j["part_abilities"]))
    elif j.get("multiblock"): lines.append("仓室(运行时): 未知 (GTOLib 结构文件读取器脱离世界不可用)")
    if j.get("abil_static"): lines.append("仓室(静态源码): " + ", ".join(j["abil_static"]) + ("  AUTO=按配方类型自动 IO+能源" if "AUTO" in j["abil_static"] else ""))
    lines += ["tooltip:"] + ["  " + t for t in j.get("tooltips_zh", [])]
    return page(lines)


@mcp.tool()
def material(name: str, format: str = "text") -> str:
    """材料：化学式、组分、flags、形态，以及会不会被自动分解（及对应分解配方）。"""
    if D().source == "static":
        return f"source=static 材料表只在运行时导出中提供。{STATIC_HINT}"
    q = name.strip().split(":")[-1]          # accept dust:Fluorite / gtocore:fluorite
    low = q.lower().replace(" ", "_")
    rows = D().q("SELECT * FROM materials WHERE name=? OR replace(name,'_','')=? OR zh=?", low, low.replace("_", ""), q) or \
        D().q("SELECT * FROM materials WHERE name LIKE ? OR zh LIKE ? ORDER BY length(name) LIMIT 15", f"%{low}%", f"%{q}%")
    if len(rows) != 1:
        return "source=runtime " + ("找不到材料 " + q if not rows else "多个候选：\n" + "\n".join(f"  {r['name']}  {r['zh']}" for r in rows))
    m = json.loads(rows[0]["j"])
    dec = D().q("SELECT id FROM recipes WHERE id LIKE ?", f"%/decomposition_%_{m['name']}")
    if format == "json":
        return json.dumps({"source": "runtime", **m, "decomposition_recipes": [r["id"] for r in dec]}, ensure_ascii=False)
    fl = set(m["flags"])
    how = ("禁止分解 (DISABLE_DECOMPOSITION)" if "DISABLE_DECOMPOSITION" in fl else
           "电解分解" if "DECOMPOSITION_BY_ELECTROLYZING" in fl else "离心分解" if "DECOMPOSITION_BY_CENTRIFUGING" in fl else
           "无分解 flag（按组分是否可还原由 GTCEu 自动决定）")
    lines = [f"{m['name']}  {m['zh']}  {m.get('formula', '')}  ({m['id']})",
             "组分: " + (", ".join(f"{c['n']}×{c['m']}" for c in m["components"]) or "-"),
             "flags: " + (", ".join(m["flags"]) or "-"), f"分解: {how}",
             "分解配方: " + (", ".join(rid_short(r["id"]) for r in dec) or "无"),
             "形态: " + ", ".join(f"{p}={i}" for p, i in m["forms"].items()), "流体: " + (m.get("fluid") or "-")]
    return page(lines, "")


@mcp.tool()
def trace(item: str, direction: str = "up", depth: int = 3, per_level: int = 3, exclude_types: list[str] | None = None) -> str:
    """文本树展开上游(up=谁产它、它要什么)/下游(down=谁吃它、产出什么)，每层取 EU/t 最低的 per_level 条。"""
    key, err = resolve_one(item)
    if err:
        return err
    ex = {tpath(t) for t in exclude_types or []}
    d = "out" if direction == "up" else "in"
    seen, lines, arrow = set(), [], "←" if direction == "up" else "→"

    def walk(k, lvl):
        pad = "  " * lvl
        if k in seen:
            lines.append(f"{pad}{D().label(k)} (见上)")
            return
        seen.add(k)
        rows = [r for r in D().q("SELECT DISTINCT r.id, r.type, r.eut, r.j FROM io JOIN recipes r ON r.id=io.rid WHERE io.key=? AND io.dir=? ORDER BY r.eut IS NULL, r.eut, r.dur", k, d)
                if r["type"] not in ex][:per_level]
        lines.append(f"{pad}{D().label(k)}" + ("" if rows else "  (无配方/原料)"))
        for row in rows:
            r = json.loads(row["j"])
            nxt = r["in_items"] + r["in_fluids"] if direction == "up" else r["out_items"] + r["out_fluids"]
            lines.append(f"{pad}  {arrow} [{D().type_labels.get(row['type'], row['type'])}] {rid_short(r['id'])} {r.get('eut')}EU/t "
                         + ", ".join(stack(s, s["key"].startswith("fluid:"), direction == "down") for s in nxt))
            if lvl + 1 < depth:
                for s in nxt:
                    if s["key"] != k:
                        walk(s["key"], lvl + 2)

    walk(key, 0)
    return page(lines, f"trace {direction} depth={depth}" + ("\n" + STATIC_HINT if D().source == "static" else ""))


@mcp.tool()
def balance(runs: list[dict], tier: int | str | None = None, format: str = "text") -> str:
    """物料平衡。runs=[{recipe_id, runs}] 或 [{"<id>": runs}]；tier(数字或 EV/IV…) 叠加概率 boost（GTCEu 默认公式，未核实 GTO）。"""
    if isinstance(tier, str):
        tier = TIERS.index(tier) if tier in TIERS else int(tier)
    pairs = []
    for e in runs:
        rid, n = (e["recipe_id"], e.get("runs", 1)) if "recipe_id" in e else next(iter(e.items()))
        ids = D().find_recipe(rid)
        if len(ids) != 1:
            return f"source={D().source} 配方 {rid} " + ("不存在" if not ids else "有多个候选: " + ", ".join(rid_short(i) for i in ids))
        pairs.append((D().recipe(ids[0]), float(n)))
    b = _balance(pairs, tier)
    if format == "json":
        return json.dumps({"source": D().source, **{k: v for k, v in b.items()}}, ensure_ascii=False)
    sec = lambda d: [f"  {num(round(v, 4))}{'mB ' if k.startswith('fluid:') else '×'}{D().label(k)}" for k, v in sorted(d.items(), key=lambda x: -x[1])] or ["  -"]
    lines = (["净需求（外部补料）:"] + sec(b["need"]) + ["净产出:"] + sec(b["out"]) + ["内部抵消:"] + sec(b["internal"])
             + [f"合计 {b['eu']:,.0f} EU，机器时间 {b['seconds']:.4g} s（按配方基础时长，未计超频/并行）"]
             + [f"  {rid_short(i)} ×{num(n)}: {e:,.0f} EU, {t:.4g} s" for i, n, e, t in b["per_recipe"]])
    return page(lines, f"balance tier={tier}")


@mcp.tool()
def me_parts() -> str:
    """ME 相关机器部件清单（id、中文名、是否可放进多方块）。"""
    rows = D().kv("me_parts") or []
    if not rows:
        return f"source={D().source} ME 部件清单只在运行时导出中提供。"
    return page([f"  {p['id']}  {p['zh']}  {'可入多方块' if p['multiblock_part'] else '独立方块'}" for p in rows], f"ME 部件 {len(rows)} 个")


def main():
    mcp.run()


if __name__ == "__main__":
    main()
