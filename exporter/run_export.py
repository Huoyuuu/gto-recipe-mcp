"""Launch the GTO instance once (offline account, no HMCL auth), dump runtime data, clean up.

uv run python analysis/runtime-exporter/run_export.py [--instance DIR] [--timeout 1500]

Steps: copy dist jar into <instance>/mods -> launch with -Dgtorecipeexporter.exit=true ->
wait for gto-dump/meta.json -> remove jar from mods -> copy gto-dump/* to analysis/data/runtime/.
Uses the Java/libraries/natives HMCL already prepared; never reads HMCL accounts.
"""
import argparse, gzip, json, shutil, subprocess, sys, time, uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "data" / "runtime"
ap = argparse.ArgumentParser()
ap.add_argument("--instance", default=r"E:\Games\HMCL-3.5.8\.minecraft\versions\GregTech Odyssey")
ap.add_argument("--java", default=r"D:\Program Files\Zulu\zulu-25\bin\java.exe")
ap.add_argument("--timeout", type=int, default=1500)
a = ap.parse_args()

inst = Path(a.instance)
mc = inst.parent.parent
lib = mc / "libraries"
ver = json.loads((inst / f"{inst.name}.json").read_text(encoding="utf-8"))


def allowed(rules):
    ok = not rules
    for r in rules or []:
        if "features" in r: return False
        if "os" not in r or r["os"].get("name") == "windows": ok = r["action"] == "allow"
    return ok


cp = []
for l in ver["libraries"]:
    if not allowed(l.get("rules")) or "natives" in l: continue
    art = l.get("downloads", {}).get("artifact")
    if art: p = lib / art["path"]
    else:
        g, n, v, *c = l["name"].split(":")
        p = lib / g.replace(".", "/") / n / v / f"{n}-{v}{'-' + c[0] if c else ''}.jar"
    if p.exists() and str(p) not in cp: cp.append(str(p))
cp.append(str(inst / f"{inst.name}.jar"))

sub = {"natives_directory": str(inst / "natives-windows-x86_64"), "launcher_name": "HMCL", "launcher_version": "3.16.3",
       "classpath": ";".join(cp), "classpath_separator": ";", "library_directory": str(lib), "version_name": inst.name,
       "primary_jar_name": f"{inst.name}.jar", "auth_player_name": "Exporter", "game_directory": str(inst),
       "assets_root": str(mc / "assets"), "assets_index_name": ver["assetIndex"]["id"], "auth_uuid": uuid.uuid4().hex,
       "auth_access_token": "0", "clientid": "0", "auth_xuid": "0", "user_type": "legacy", "version_type": "HMCL"}
fill = lambda s: __import__("re").sub(r"\$\{(\w+)\}", lambda m: sub.get(m.group(1), m.group(0)), s)
args = lambda xs: [fill(x) for x in xs if isinstance(x, str)]
cmd = [a.java, "-Xmx8g", "-XX:+UseG1GC", "-Dfile.encoding=UTF-8", "-Dgtorecipeexporter.exit=true",
       *args(ver["arguments"]["jvm"]), ver["mainClass"], *args(ver["arguments"]["game"])]

mod = inst / "mods" / "gto-recipe-exporter-1.0.0.jar"
dump = inst / "gto-dump"
shutil.copy2(HERE / "dist" / "gto-recipe-exporter-1.0.0.jar", mod)
shutil.rmtree(dump, ignore_errors=True)
(HERE / "build").mkdir(exist_ok=True)
log = open(HERE / "build" / "run_export.log", "w", encoding="utf-8", errors="replace")
t0 = time.time()
try:
    proc = subprocess.Popen(cmd, cwd=inst, stdout=log, stderr=subprocess.STDOUT)
    while proc.poll() is None and time.time() - t0 < a.timeout and not (dump / "meta.json").exists():
        time.sleep(5)
    time.sleep(3)
    if proc.poll() is None: proc.kill()
finally:
    mod.unlink(missing_ok=True)   # never leave the exporter in the instance
    log.close()
if not (dump / "meta.json").exists(): sys.exit(f"no dump after {time.time() - t0:.0f}s, see {log.name}")
OUT.mkdir(parents=True, exist_ok=True)
for f in dump.iterdir():
    if f.name == "recipes.jsonl":        # ~24 MB -> ~2 MB
        with open(f, "rb") as src, gzip.open(OUT / "recipes.jsonl.gz", "wb") as dst: shutil.copyfileobj(src, dst)
    elif f.suffix == ".json": shutil.copy2(f, OUT / f.name)
print(f"{time.time() - t0:.0f}s", json.loads((OUT / "meta.json").read_text(encoding="utf-8"))["counts"])
