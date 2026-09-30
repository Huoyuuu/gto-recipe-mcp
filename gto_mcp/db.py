"""JSON -> SQLite index and lookups.

Runtime dump (analysis/data/runtime/*.json[l]) wins; otherwise the static CFR index
(analysis/data/recipe_index.json + machine_index.json). The SQLite cache is rebuilt
whenever any source file is newer than it.
"""
import gzip
import json
import os
import re
import sqlite3
from functools import cached_property
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]   # standalone repo keeps data/ next to gto_mcp/; workspace uses analysis/data
DATA = Path(os.environ.get("GTO_DATA_DIR") or (_ROOT / "data" if (_ROOT / "data").is_dir() else _ROOT.parents[1] / "analysis" / "data"))
RT = DATA / "runtime"
CACHE = Path(__file__).resolve().parents[1] / ".cache"
FILES = {"runtime": [RT / f for f in ("recipes.jsonl.gz", "machines.json", "materials.json", "lang_zh.json", "meta.json")],
         "static": [DATA / "recipe_index.json", DATA / "machine_index.json"]}
TIERS = "ULV LV MV HV EV IV LuV ZPM UV UHV UEV UIV UXV OpV MAX".split()

SCHEMA = """
CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE recipes(id TEXT PRIMARY KEY, type TEXT, eut INT, dur INT, j TEXT);
CREATE TABLE io(rid TEXT, dir TEXT, key TEXT);
CREATE TABLE names(name TEXT, key TEXT, prio INT);
CREATE TABLE labels(key TEXT PRIMARY KEY, zh TEXT);
CREATE TABLE machines(id TEXT PRIMARY KEY, zh TEXT, multi INT, tier INT, ntypes INT, j TEXT);
CREATE TABLE mtypes(mid TEXT, type TEXT);
CREATE TABLE materials(name TEXT PRIMARY KEY, zh TEXT, j TEXT);
"""
INDEXES = """
CREATE INDEX io_k ON io(key, dir); CREATE INDEX io_r ON io(rid); CREATE INDEX names_n ON names(name);
CREATE INDEX mt_t ON mtypes(type); CREATE INDEX mt_m ON mtypes(mid); CREATE INDEX r_t ON recipes(type);
"""


def tier_of(eut):
    for i in range(len(TIERS)):
        if (eut or 0) <= 8 * 4 ** i:
            return i
    return len(TIERS) - 1


def tpath(t):
    """'gtceu:digestion_treatment' / 'DIGESTION_TREATMENT_RECIPES' -> 'digestion_treatment'."""
    return t.split(":")[-1].lower().removesuffix("_recipes")


def _alias(rows, name, key, prio):
    n = name.strip().lower()
    rows.add((n, key, prio))
    if n.isascii() and "_" in n:
        rows.add((n.replace("_", ""), key, prio))


def _build_runtime(con):
    static_ids = set()
    if (DATA / "recipe_index.json").exists():
        static_ids = {r["id"] for r in json.loads((DATA / "recipe_index.json").read_text(encoding="utf-8"))}
    names, labels = set(), {}
    recipes, io = [], []
    with gzip.open(RT / "recipes.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            rid = r["id"]
            # heuristic: builder-chain ids appear in the CFR index, generated ones (decomposition/ore/...) don't
            r["gen"] = bool(static_ids) and rid.rsplit("/", 1)[-1] not in static_ids
            recipes.append((rid, tpath(r["type"]), r.get("eut"), r.get("duration"), json.dumps(r, ensure_ascii=False)))
            for s in r["in_items"] + r["in_fluids"]:
                io.append((rid, "in", s["key"]))
                io.extend((rid, "in", "item:" + a) for a in s.get("alternatives", [])[1:])
                if s.get("tag"):
                    names.add((s["tag"].lower(), s["key"], 0))
            io.extend((rid, "nc", s["key"]) for s in r["not_consumable"])
            io.extend((rid, "out", s["key"]) for s in r["out_items"] + r["out_fluids"])
    lang = json.loads((RT / "lang_zh.json").read_text(encoding="utf-8"))
    for k, zh in lang.items():
        if k.startswith("recipe_type:"):
            labels["type:" + tpath(k[12:])] = zh
            continue
        labels[k] = zh
        if ":flowing_" in k:           # flowing fluid variants share the zh name; never resolve to them
            continue
        _alias(names, zh, k, 0)
        _alias(names, k, k, 0)
        _alias(names, k.split(":")[-1], k, 0)
    for m in json.loads((RT / "materials.json").read_text(encoding="utf-8")):
        dust = m["forms"].get("dust")
        for p, item in m["forms"].items():
            _alias(names, f"{p}:{m['name']}", "item:" + item, 0)
        if m.get("fluid"):
            _alias(names, f"fluid:{m['name']}", "fluid:" + m["fluid"], 0)
        for n in (m["name"], m.get("zh") or m["name"]):          # bare material name -> dust and/or fluid
            if dust: _alias(names, n, "item:" + dust, 1)
            if m.get("fluid"): _alias(names, n, "fluid:" + m["fluid"], 1)
        con.execute("INSERT OR REPLACE INTO materials VALUES(?,?,?)", (m["name"], m.get("zh"), json.dumps(m, ensure_ascii=False)))
    static_m = {}
    if (DATA / "machine_index.json").exists():
        static_m = {m["id"]: m for m in json.loads((DATA / "machine_index.json").read_text(encoding="utf-8"))}
    ms = json.loads((RT / "machines.json").read_text(encoding="utf-8"))
    for m in ms["machines"]:
        s = static_m.get(m["id"].split(":")[-1], {})
        m.update({"cls": s.get("cls"), "par": s.get("par", []), "abil_static": s.get("abil", [])})
        types = [tpath(t) for t in m["recipe_types"]]
        con.execute("INSERT OR REPLACE INTO machines VALUES(?,?,?,?,?,?)",
                    (m["id"], m["zh"], m["multiblock"], m["tier"], len(types), json.dumps(m, ensure_ascii=False)))
        con.executemany("INSERT INTO mtypes VALUES(?,?)", [(m["id"], t) for t in types])
    meta = json.loads((RT / "meta.json").read_text(encoding="utf-8"))
    return recipes, io, names, labels, {"meta": meta, "me_parts": ms["me_parts"]}


def _build_static(con):
    names, labels, recipes, io = set(), {}, [], []
    for r in json.loads((DATA / "recipe_index.json").read_text(encoding="utf-8")):
        t = tpath(r["type"])
        rid = f"gtceu:{t}/{r['id']}"

        def st(s, fluid, out=False, chanced=False):
            key = ("fluid:" if fluid else "item:") + s["id"]
            _alias(names, s["id"], key, 0)
            if not fluid and s["id"].startswith("dust:"):
                _alias(names, s["id"][5:], key, 1)
            e = {"key": key, "n": s.get("n") or 1}
            if chanced and "chance" not in s:        # (stack, chance) form parsed as n
                e["n"], s = 1, {**s, "chance": s.get("n")}
            if out:
                e.update(chance=int(s.get("chance") or 10000), boost=int(s.get("boost") or 0))
            else:
                e["consume"] = True
            return e

        rec = {"id": rid, "type": "gtceu:" + t, "eut": r.get("eut"), "tier": tier_of(r.get("eut")), "duration": r.get("dur"),
               "circuit": r.get("circuit"), "data": {"ebf_temp": r["temp"]} if "temp" in r else {},
               "in_items": [st(s, False) for s in r["in"]], "in_fluids": [st(s, True) for s in r["fin"]],
               "out_items": [st(s, False, True) for s in r["out"]] + [st(s, False, True, True) for s in r["chance"]],
               "out_fluids": [st(s, True, True) for s in r["fout"]],
               "not_consumable": [{"key": "item:" + x, "n": 1} for x in r["nc"]], "src": f"{r['file']}:{r['line']}"}
        recipes.append((rid, t, rec["eut"], rec["duration"], json.dumps(rec, ensure_ascii=False)))
        io.extend((rid, "in", s["key"]) for s in rec["in_items"] + rec["in_fluids"])
        io.extend((rid, "out", s["key"]) for s in rec["out_items"] + rec["out_fluids"])
    for m in json.loads((DATA / "machine_index.json").read_text(encoding="utf-8")):
        types = [tpath(t) for t in m["types"]]
        j = {"id": m["id"], "zh": m["zh"], "multiblock": True, "tier": None, "recipe_types": types, "cls": m["cls"],
             "par": m["par"], "abil_static": m.get("abil", []), "src": m["file"]}
        con.execute("INSERT OR REPLACE INTO machines VALUES(?,?,?,?,?,?)", (m["id"], m["zh"], 1, None, len(types), json.dumps(j, ensure_ascii=False)))
        con.executemany("INSERT INTO mtypes VALUES(?,?)", [(m["id"], t) for t in types])
    return recipes, io, names, labels, {"meta": {"source": "CFR static index"}, "me_parts": []}


def build(source, path):
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    recipes, io, names, labels, kv = (_build_runtime if source == "runtime" else _build_static)(con)
    con.executemany("INSERT OR IGNORE INTO recipes VALUES(?,?,?,?,?)", recipes)
    con.executemany("INSERT INTO io VALUES(?,?,?)", set(io))
    con.executemany("INSERT INTO names VALUES(?,?,?)", names)
    con.executemany("INSERT OR REPLACE INTO labels VALUES(?,?)", labels.items())
    con.executemany("INSERT INTO kv VALUES(?,?)", [(k, json.dumps(v, ensure_ascii=False)) for k, v in kv.items()])
    con.executescript(INDEXES)
    con.commit()
    con.close()
    os.replace(tmp, path)


class DB:
    def __init__(self, source=None):
        source = source or os.environ.get("GTO_MCP_SOURCE") or ("runtime" if FILES["runtime"][0].exists() else "static")
        self.source = source
        CACHE.mkdir(exist_ok=True)
        path = CACHE / f"gto-{source}.sqlite"
        newest = max(p.stat().st_mtime for p in FILES[source] if p.exists())
        if not path.exists() or path.stat().st_mtime < newest:
            build(source, path)
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row

    def q(self, sql, *args):
        return self.con.execute(sql, args).fetchall()

    def kv(self, k):
        row = self.q("SELECT v FROM kv WHERE k=?", k)
        return json.loads(row[0]["v"]) if row else None

    def label(self, key):
        row = self.q("SELECT zh FROM labels WHERE key=?", key)
        return row[0]["zh"] if row else key.split(":", 1)[-1]

    @cached_property
    def type_labels(self):
        """type path -> zh: recipe-type lang entry, else the most dedicated multiblock that runs it."""
        out = {}
        for r in self.q("SELECT t.type, m.zh FROM mtypes t JOIN machines m ON m.id=t.mid ORDER BY m.multi DESC, m.ntypes, (m.id NOT LIKE '%' || t.type), m.id"):
            out.setdefault(r["type"], r["zh"])
        for r in self.q("SELECT key, zh FROM labels WHERE key LIKE 'type:%'"):
            out[r["key"][5:]] = r["zh"]
        return out

    def resolve(self, text):
        """Item/fluid text -> list of keys (1 = resolved, >1 = candidates, 0 = not found)."""
        n = text.strip().lower()
        for name in dict.fromkeys((n, n.replace("_", ""), re.sub(r"\s+", "_", n))):
            rows = self.q("SELECT key, MIN(prio) p FROM names WHERE name=? GROUP BY key ORDER BY p, key", name)
            if rows:
                return [r["key"] for r in rows if r["p"] == rows[0]["p"]]
        rows = self.q("SELECT key, MIN(prio*100 + length(name)) s FROM names WHERE name LIKE ? GROUP BY key ORDER BY s, key LIMIT 15", f"%{n}%")
        return [r["key"] for r in rows]

    def find_recipe(self, text):
        t = text.strip()
        rows = (self.q("SELECT id FROM recipes WHERE id=?", t) or self.q("SELECT id FROM recipes WHERE id LIKE ? LIMIT 20", "%:" + t)
                or self.q("SELECT id FROM recipes WHERE id LIKE ? LIMIT 20", "%/" + t))
        return [r["id"] for r in rows]

    def recipe(self, rid):
        row = self.q("SELECT j FROM recipes WHERE id=?", rid)
        return json.loads(row[0]["j"]) if row else None
