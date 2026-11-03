#!/usr/bin/env python3
"""cf - CurseForge API CLI (stdlib only)."""
import argparse
import hashlib
import json
import os
import random
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

API = "https://api.curseforge.com/v1"
CONFIG = Path(os.environ.get("APPDATA") or Path.home()) / "cf-cli" / "config.json"
MINECRAFT = 432
LOADERS = {"forge": 1, "cauldron": 2, "liteloader": 3, "fabric": 4, "quilt": 5, "neoforge": 6}
SORTS = {"featured": 1, "popularity": 2, "lastupdated": 3, "name": 4, "author": 5, "totaldownloads": 6}
RELEASE = {1: "release", 2: "beta", 3: "alpha"}
VERSION_TYPES = {1: "release", 2: "snapshot", 3: "old_beta", 4: "old_alpha"}
MC_VERSION = re.compile(r"^\d+\.\d+")
PARALLEL = 8


def load_config():
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def api_key():
    key = os.environ.get("CURSEFORGE_API_KEY", "").strip()
    if not key:
        key = load_config().get("api_key", "")
    if not key:
        sys.exit("No API key. Get one at https://console.curseforge.com/ then run: cf auth <key>")
    return key


def profile_defaults():
    cfg = load_config()
    return (cfg.get("profiles") or {}).get(cfg.get("active_profile") or "") or {}


def get_profile(name):
    cfg = load_config()
    profiles = cfg.get("profiles") or {}
    if name not in profiles:
        sys.exit(f"No profile named {name!r}. See: cf profile list")
    return name, cfg, profiles[name]


def active_profile(args, required=True):
    name = getattr(args, "profile", None) or load_config().get("active_profile")
    if not name:
        if required:
            sys.exit("No active profile. Create one: cf profile create <name> --version 1.21.1 --loader fabric")
        return None, load_config(), {}
    return get_profile(name)


RETRY_CODES = (429, 500, 502, 503, 504)


def retry_delay(exc, attempt):
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return min(float(retry_after), 30)
        except ValueError:
            pass
    return min(2 ** attempt + random.uniform(0, 0.5), 30)


def api(path, params=None, body=None):
    url = API + path
    query = {k: v for k, v in (params or {}).items() if v is not None}
    if query:
        url += "?" + urllib.parse.urlencode(query)
    headers = {"x-api-key": api_key(), "Accept": "application/json", "User-Agent": "cf-cli/1.1"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    for attempt in range(4):
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code in RETRY_CODES and attempt < 3:
                exc.close()
                time.sleep(retry_delay(exc, attempt))
                continue
            detail = exc.read().decode("utf-8", "replace")[:300]
            sys.exit(f"HTTP {exc.code} for {url}\n{detail}")
        except urllib.error.URLError as exc:
            sys.exit(f"Network error: {exc.reason}")


def table(headers, rows):
    rows = [[str(c) for c in row] for row in rows]
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)))


def human(n):
    n = float(n or 0)
    for unit in ("", "K", "M", "B"):
        if n < 1000:
            return f"{n:.0f}" if unit == "" else f"{n:.1f}{unit}"
        n /= 1000
    return f"{n:.1f}T"


def date(s):
    return (s or "")[:10]


def loader_id(name):
    return LOADERS[name] if name else None


def resolve_mod(value, game=MINECRAFT):
    if str(value).isdigit():
        return int(value)
    res = api("/mods/search", {"gameId": game, "slug": str(value), "pageSize": 1})
    if not res["data"]:
        sys.exit(f"No mod with slug {value!r}")
    return res["data"][0]["id"]


def pick_file(mod_id, version=None, loader=None):
    # shortcut: newest of the first 50 files; paginate if a mod needs more
    files = api(f"/mods/{mod_id}/files", {
        "gameVersion": version,
        "modLoaderType": loader_id(loader),
        "pageSize": 50,
    })["data"]
    if not files:
        return None
    files.sort(key=lambda f: (f.get("releaseType") == 1, f.get("fileDate", "")), reverse=True)
    return files[0]


def sha1_of(path):
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_file(file_info, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    url = api(f"/mods/{file_info['modId']}/files/{file_info['id']}/download-url")["data"]
    if not url:
        sys.exit(f"No download URL for {file_info.get('displayName')} (author disabled third-party downloads).")
    dest = out / Path(file_info["fileName"]).name
    urllib.request.urlretrieve(urllib.request.Request(url, headers={"User-Agent": "cf-cli/1.1"}), dest)
    expected = next((h["value"].lower() for h in file_info.get("hashes") or [] if h.get("algo") == 1), None)
    if expected and sha1_of(dest) != expected:
        dest.unlink()
        sys.exit(f"Checksum mismatch for {dest.name} (expected sha1 {expected})")
    return dest


def default_mods_dir():
    return Path(os.environ.get("APPDATA") or Path.home()) / ".minecraft" / "mods"


def download_many(files, out, workers=PARALLEL):
    done, failed = [], []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(download_file, f, out): f for f in files}
        for future in as_completed(futures):
            f = futures[future]
            try:
                done.append(future.result())
                print(f"install  {f['fileName']}")
            except (Exception, SystemExit) as exc:
                failed.append(f["fileName"])
                print(f"FAILED   {f['fileName']}: {exc}")
    return done, failed


def clean_dir(folder, keep):
    old = folder / ".old"
    moved = 0
    for jar in sorted(folder.glob("*.jar")):
        if jar.name in keep:
            continue
        old.mkdir(exist_ok=True)
        target = old / jar.name
        if target.exists():
            target = old / f"{jar.stem}.{int(time.time())}{jar.suffix}"
        shutil.move(str(jar), str(target))
        print(f"archive  {jar.name} -> .old/")
        moved += 1
    return moved


def cf_fingerprint(data):
    """CurseForge fingerprint: murmur2 (seed 1) over bytes, skipping tab/LF/CR/space."""
    m = 0x5BD1E995
    clean = bytes(b for b in data if b not in (9, 10, 13, 32))
    h = (1 ^ len(clean)) & 0xFFFFFFFF
    i = 0
    while i + 4 <= len(clean):
        k = int.from_bytes(clean[i:i + 4], "little")
        k = (k * m) & 0xFFFFFFFF
        k = ((k ^ (k >> 24)) * m) & 0xFFFFFFFF
        h = ((h * m) & 0xFFFFFFFF) ^ k
        i += 4
    if i < len(clean):
        h = ((h ^ int.from_bytes(clean[i:], "little")) * m) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * m) & 0xFFFFFFFF
    return (h ^ (h >> 15)) & 0xFFFFFFFF


def match_fingerprints(game_id, fingerprints):
    result = {"exactMatches": [], "unmatchedFingerprints": [], "installedFingerprints": []}
    fingerprints = list(fingerprints)
    for i in range(0, len(fingerprints), 1000):
        data = api(f"/fingerprints/{game_id}", body={"fingerprints": fingerprints[i:i + 1000]})["data"]
        for key in result:
            result[key].extend(data.get(key) or [])
    return result


def file_context(f):
    versions = f.get("gameVersions") or []
    mc = next((v for v in versions if MC_VERSION.match(v)), None)
    loader = next((v.lower() for v in versions if v.lower() in LOADERS), None)
    return mc, loader


def cmd_auth(args):
    cfg = load_config()
    cfg["api_key"] = args.key
    save_config(cfg)
    if os.name != "nt":
        CONFIG.chmod(0o600)
    print(f"Saved API key to {CONFIG}")


def cmd_profile_create(args):
    cfg = load_config()
    profiles = cfg.setdefault("profiles", {})
    if args.name in profiles:
        sys.exit(f"Profile {args.name!r} already exists")
    profiles[args.name] = {
        "game": args.game,
        "version": args.version,
        "loader": args.loader,
        "dir": str(Path(args.dir or default_mods_dir()).resolve()),
        "mods": [],
    }
    if args.use or not cfg.get("active_profile"):
        cfg["active_profile"] = args.name
    save_config(cfg)
    print(f"Created profile {args.name!r} ({args.version or 'any version'} / {args.loader or 'any loader'})")
    if cfg.get("active_profile") == args.name:
        print(f"Active profile: {args.name}")


def cmd_profile_list(args):
    cfg = load_config()
    profiles = cfg.get("profiles") or {}
    if not profiles:
        print("No profiles. Create one: cf profile create <name> --version 1.21.1 --loader fabric")
        return
    table(["", "Name", "Version", "Loader", "Mods", "Directory"], [
        ["*" if name == cfg.get("active_profile") else "", name, p.get("version") or "-",
         p.get("loader") or "-", len(p.get("mods") or []), p.get("dir", "")]
        for name, p in profiles.items()
    ])


def cmd_profile_use(args):
    cfg = load_config()
    if args.name not in (cfg.get("profiles") or {}):
        sys.exit(f"No profile named {args.name!r}")
    cfg["active_profile"] = args.name
    save_config(cfg)
    print(f"Active profile: {args.name}")


def cmd_profile_show(args):
    name = args.name or load_config().get("active_profile")
    if not name:
        sys.exit("No active profile. See: cf profile list")
    name, _, prof = get_profile(name)
    print(f"{name} ({prof.get('version') or 'any version'} / {prof.get('loader') or 'any loader'})")
    print(f"Directory: {prof.get('dir')}")
    mods = prof.get("mods") or []
    table(["ID", "Name", "Slug"], [[m["id"], m.get("name", ""), m.get("slug", "")] for m in mods])
    print(f"\n{len(mods)} mods")


def cmd_profile_delete(args):
    cfg = load_config()
    profiles = cfg.get("profiles") or {}
    if args.name not in profiles:
        sys.exit(f"No profile named {args.name!r}")
    del profiles[args.name]
    if cfg.get("active_profile") == args.name:
        cfg["active_profile"] = next(iter(profiles), None)
    save_config(cfg)
    print(f"Deleted profile {args.name!r}")


def cmd_add(args):
    name, cfg, prof = active_profile(args)
    version, loader = prof.get("version"), prof.get("loader")
    mods = prof.setdefault("mods", [])
    added = 0
    for value in args.mods:
        mod_id = resolve_mod(value, prof.get("game", MINECRAFT))
        if any(m["id"] == mod_id for m in mods):
            print(f"have     {mod_id}")
            continue
        m = api(f"/mods/{mod_id}")["data"]
        if not args.force and not pick_file(mod_id, version, loader):
            print(f"skip     {m['name']}: no file for {version or 'any version'}/{loader or 'any loader'} (use --force)")
            continue
        mods.append({"id": mod_id, "name": m["name"], "slug": m.get("slug", "")})
        added += 1
        print(f"added    {m['name']}")
    save_config(cfg)
    print(f"\nRun 'cf upgrade' to download {added} new mod(s) into profile {name!r}.")


def cmd_remove(args):
    name, cfg, prof = active_profile(args)
    mods = prof.get("mods") or []
    removed = 0
    for value in args.mods:
        if value.isdigit():
            match = next((m for m in mods if m["id"] == int(value)), None)
        else:
            needle = value.lower()
            match = next((m for m in mods
                          if needle in (m.get("name", "").lower(), m.get("slug", "").lower())), None)
        if not match:
            print(f"not found: {value}")
            continue
        mods.remove(match)
        removed += 1
        print(f"removed  {match['name']}")
    prof["mods"] = mods
    save_config(cfg)
    print(f"\n{removed} removed from profile {name!r}")


def cmd_list(args):
    name, _, prof = active_profile(args)
    mods = prof.get("mods") or []
    table(["ID", "Name", "Slug"], [[m["id"], m.get("name", ""), m.get("slug", "")] for m in mods])
    print(f"\n{len(mods)} mods in profile {name!r} "
          f"({prof.get('version') or 'any version'} / {prof.get('loader') or 'any loader'})")


def cmd_upgrade(args):
    name, _, prof = active_profile(args)
    version, loader = prof.get("version"), prof.get("loader")
    out = Path(args.dir or prof.get("dir") or default_mods_dir())
    mods = prof.get("mods") or []
    if not mods:
        sys.exit(f"Profile {name!r} has no mods. Add some: cf add <mod>")

    queue = [m["id"] for m in mods]
    seen, plan, filenames = set(), [], set()
    while queue:
        mod_id = queue.pop(0)
        if mod_id in seen:
            continue
        seen.add(mod_id)
        f = pick_file(mod_id, version, loader)
        if not f:
            print(f"skip     {mod_id}: no file for {version or 'any version'}/{loader or 'any loader'}")
            continue
        if f["fileName"] in filenames:
            continue
        filenames.add(f["fileName"])
        plan.append(f)
        for dep in f.get("dependencies") or []:
            if dep.get("relationType") == 3 and dep["modId"] not in seen:
                queue.append(dep["modId"])

    out.mkdir(parents=True, exist_ok=True)
    missing = [f for f in plan if not (out / Path(f["fileName"]).name).exists()]
    print(f"{len(plan)} files resolved, {len(plan) - len(missing)} already present in {out}")
    for f in missing:
        print(f"  {f['fileName']}")
    if args.dry_run:
        print("\n(dry run, nothing downloaded)")
        return
    if missing:
        done, failed = download_many(missing, out, args.parallel)
        print(f"\n{len(done)} downloaded, {len(failed)} failed")
    if args.clean:
        moved = clean_dir(out, {Path(f["fileName"]).name for f in plan})
        print(f"{moved} stale jar(s) moved to {out / '.old'}")


def cmd_games(args):
    data = api("/games")["data"]
    table(["ID", "Name", "Slug"], [[g["id"], g["name"], g["slug"]] for g in data])


def cmd_game(args):
    g = api(f"/games/{args.game}")["data"]
    print(f"{g['name']} (id {g['id']}, slug {g['slug']})")
    print(f"Status: {g.get('status')}   API status: {g.get('apiStatus')}   Modified: {date(g.get('dateModified'))}")
    for key, url in (g.get("assets") or {}).items():
        if url:
            print(f"{key:9}: {url}")


def cmd_game_versions(args):
    if args.v2:
        print(json.dumps(api(f"/games/{args.game}/versions/v2")["data"], indent=2))
        return
    data = api(f"/games/{args.game}/versions")["data"]
    table(["ID", "Name", "Slug"], [[v["id"], v["name"], v.get("slug", "")] for v in data])


def cmd_version_types(args):
    data = api(f"/games/{args.game}/version-types")["data"]
    table(["ID", "Name", "Slug"], [[v["id"], v["name"], v.get("slug", "")] for v in data])


def cmd_categories(args):
    data = api("/categories", {"gameId": args.game, "classId": args.class_id})["data"]
    table(["ID", "Name", "Class"], [[c["id"], c["name"], c.get("classId")] for c in data])


def cmd_search(args):
    res = api("/mods/search", {
        "gameId": args.game,
        "searchFilter": " ".join(args.query) or None,
        "classId": args.class_id,
        "categoryId": args.category,
        "gameVersion": args.version,
        "modLoaderType": loader_id(args.loader),
        "sortField": SORTS[args.sort] if args.sort else None,
        "sortOrder": args.order,
        "index": (args.page - 1) * args.limit,
        "pageSize": args.limit,
    })
    if args.json:
        print(json.dumps(res, indent=2))
        return
    table(["ID", "Name", "Downloads", "Updated", "Slug"], [
        [m["id"], m["name"][:50], human(m.get("downloadCount")), date(m.get("dateModified")), m.get("slug", "")]
        for m in res["data"]
    ])
    p = res.get("pagination") or {}
    print(f"\n{p.get('totalCount', len(res['data']))} results (page {args.page}, {args.limit}/page)")


def cmd_mod(args):
    mod_id = resolve_mod(args.mod, args.game)
    m = api(f"/mods/{mod_id}")["data"]
    if args.json:
        print(json.dumps(m, indent=2))
        return
    print(f"{m['name']}  (id {m['id']}, slug {m.get('slug')})")
    print(f"Authors  : {', '.join(a['name'] for a in m.get('authors', []))}")
    print(f"Category : {', '.join(c['name'] for c in m.get('categories', []))}")
    print(f"Downloads: {human(m.get('downloadCount'))}   Updated: {date(m.get('dateModified'))}")
    print(f"Summary  : {m.get('summary')}")
    for k in ("websiteUrl", "sourceUrl", "issuesUrl"):
        if (m.get("links") or {}).get(k):
            print(f"{k:9}: {m['links'][k]}")


def cmd_mods(args):
    data = api("/mods", body={"modIds": args.ids})["data"]
    if args.json:
        print(json.dumps(data, indent=2))
        return
    table(["ID", "Name", "Downloads", "Slug"], [
        [m["id"], m["name"][:50], human(m.get("downloadCount")), m.get("slug", "")] for m in data
    ])


def cmd_featured(args):
    data = api("/mods/featured", body={
        "gameId": args.game,
        "excludedModIds": args.exclude or [],
        "gameVersionTypeId": args.version_type,
    })["data"]
    if args.json:
        print(json.dumps(data, indent=2))
        return
    for key in ("featured", "popular", "recentlyUpdated"):
        items = data.get(key) or []
        print(f"\n{key} ({len(items)})")
        table(["ID", "Name", "Downloads"], [
            [m["id"], m["name"][:55], human(m.get("downloadCount"))] for m in items[:args.limit]
        ])


def cmd_files(args):
    # shortcut: only the newest 50 files; paginate if a mod needs more
    files = api(f"/mods/{args.mod}/files", {
        "gameVersion": args.version,
        "modLoaderType": loader_id(args.loader),
        "pageSize": 50,
    })["data"]
    files.sort(key=lambda f: f.get("fileDate", ""), reverse=True)
    if args.release:
        files = [f for f in files if RELEASE.get(f.get("releaseType")) == args.release]
    files = files[:args.limit]
    if args.json:
        print(json.dumps(files, indent=2))
        return
    table(["FileID", "Name", "File", "Game versions", "Type", "Date"], [
        [f["id"], f["displayName"][:45], f.get("fileName", "")[:40],
         ",".join(f.get("gameVersions", []))[:30], RELEASE.get(f.get("releaseType"), "?"), date(f.get("fileDate"))]
        for f in files
    ])


def cmd_file_info(args):
    data = api("/mods/files", body={"fileIds": args.ids})["data"]
    if args.json:
        print(json.dumps(data, indent=2))
        return
    table(["FileID", "ModID", "Name", "File", "Game versions", "Type", "Date"], [
        [f["id"], f["modId"], f["displayName"][:40], f.get("fileName", "")[:35],
         ",".join(f.get("gameVersions", []))[:28], RELEASE.get(f.get("releaseType"), "?"), date(f.get("fileDate"))]
        for f in data
    ])


def cmd_file(args):
    print(json.dumps(api(f"/mods/{args.mod}/files/{args.file}")["data"], indent=2))


def cmd_desc(args):
    print(api(f"/mods/{args.mod}/description")["data"])


def cmd_changelog(args):
    print(api(f"/mods/{args.mod}/files/{args.file}/changelog")["data"])


def cmd_url(args):
    url = api(f"/mods/{args.mod}/files/{args.file}/download-url")["data"]
    if not url:
        sys.exit("No download URL (the author disabled third-party downloads).")
    print(url)


def cmd_download(args):
    if args.file == "latest":
        prof = profile_defaults()
        f = pick_file(args.mod, args.version or prof.get("version"), args.loader or prof.get("loader"))
        if not f:
            sys.exit("No files match those filters.")
    else:
        f = api(f"/mods/{args.mod}/files/{args.file}")["data"]
    print(f"Downloading {f['displayName']} ({human(f.get('fileLength'))}B)")
    print(f"Saved to {download_file(f, args.out)}")


def cmd_install(args):
    prof = profile_defaults()
    version = args.version or prof.get("version")
    loader = args.loader or prof.get("loader")
    out = Path(args.dir or prof.get("dir") or default_mods_dir())
    queue = [resolve_mod(args.mod, args.game)]
    seen = set()
    while queue:
        mod_id = queue.pop(0)
        if mod_id in seen:
            continue
        seen.add(mod_id)
        f = pick_file(mod_id, version, loader)
        if not f:
            print(f"skip     mod {mod_id}: no file for {version or 'any version'}/{loader or 'any loader'}")
            continue
        dest = out / Path(f["fileName"]).name
        if args.dry_run:
            print(f"would install {dest.name}")
        elif dest.exists():
            print(f"have     {dest.name}")
        else:
            download_file(f, out)
            print(f"install  {dest.name}")
        if not args.no_deps:
            for dep in f.get("dependencies") or []:
                if dep.get("relationType") == 3 and dep["modId"] not in seen:
                    queue.append(dep["modId"])
    if args.dry_run:
        print("\n(dry run, nothing downloaded)")
    print(f"\nMods dir: {out}")


def cmd_update(args):
    folder = Path(args.dir or profile_defaults().get("dir") or default_mods_dir())
    jars = sorted(folder.glob("*.jar"))
    if not jars:
        sys.exit(f"No .jar files in {folder}")
    fingerprints = {cf_fingerprint(j.read_bytes()): j for j in jars}
    res = match_fingerprints(args.game, fingerprints)
    print(f"{len(jars)} jars: {len(res['exactMatches'])} recognized, {len(res['unmatchedFingerprints'])} unknown")
    for m in res["exactMatches"]:
        local = fingerprints.get(m["id"])
        if not local:
            continue
        old = m["file"]
        mc, loader = file_context(old)
        newer = pick_file(old["modId"], args.version or mc, args.loader or loader)
        if not newer or newer["id"] == old["id"]:
            continue
        print(f"update   {local.name} -> {newer['fileName']}")
        if not args.dry_run:
            download_file(newer, folder)
    if args.dry_run:
        print("(dry run, nothing downloaded)")
    else:
        print("Old jars are kept; remove them yourself after verifying.")


def cmd_scan(args):
    target = Path(args.path)
    # shortcut: top-level .jar files only, no recursion
    jars = [target] if target.is_file() else sorted(target.glob("*.jar"))
    if not jars:
        sys.exit(f"No .jar files in {target}")
    fingerprints = {cf_fingerprint(j.read_bytes()): j for j in jars}
    if args.fuzzy:
        body = {"gameId": args.game, "fingerprints": [{"foldername": target.name, "fingerprints": list(fingerprints)}]}
        print(json.dumps(api(f"/fingerprints/fuzzy/{args.game}", body=body), indent=2))
        return
    res = match_fingerprints(args.game, fingerprints)
    rows = [[fingerprints[m["id"]].name, m["file"]["modId"], m["file"]["displayName"][:45], m["file"]["fileName"][:35]]
            for m in res["exactMatches"] if m["id"] in fingerprints]
    table(["Local file", "ModID", "Mod file", "Remote file"], rows)
    unknown = [fingerprints[fp].name for fp in res["unmatchedFingerprints"] if fp in fingerprints]
    print(f"\n{len(rows)} matched, {len(unknown)} unknown")
    for name in unknown:
        print(f"  ? {name}")


def install_pack(pack, dest=None):
    pack = Path(pack)
    try:
        with zipfile.ZipFile(pack) as z:
            manifest = json.loads(z.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError) as exc:
        sys.exit(f"Not a CurseForge modpack zip: {pack} ({exc})")
    minecraft = manifest.get("minecraft") or {}
    loaders = [l.get("id", "") for l in minecraft.get("modLoaders") or []]
    loader = loaders[0].split("-")[0] if loaders else "?"
    name = manifest.get("name") or pack.stem
    out = Path(dest or name)
    out.mkdir(parents=True, exist_ok=True)
    files = manifest.get("files") or []
    print(f"{name} {manifest.get('version', '')} - MC {minecraft.get('version', '?')} / {loader}, {len(files)} files")
    for i, entry in enumerate(files, 1):
        info = api(f"/mods/{entry['projectID']}/files/{entry['fileID']}")["data"]
        dest = download_file(info, out / "mods")
        print(f"[{i}/{len(files)}] {dest.name}")
    with zipfile.ZipFile(pack) as z:
        for member in z.namelist():
            if member.startswith("overrides/") and not member.endswith("/"):
                target = out / member[len("overrides/"):]
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    print(f"\nInstalled to {out.resolve()}")


def cmd_import(args):
    install_pack(args.pack, args.dir)


def cmd_pack(args):
    mod_id = resolve_mod(args.mod, args.game)
    f = pick_file(mod_id, args.version, args.loader)
    if not f:
        sys.exit("No files match those filters.")
    print(f"Downloading {f['displayName']} ({human(f.get('fileLength'))}B)")
    install_pack(download_file(f, args.out), args.dir)


def cmd_worlds(args):
    saves = Path(args.dir or (Path(os.environ.get("APPDATA") or Path.home()) / ".minecraft" / "saves"))
    if not saves.is_dir():
        sys.exit(f"No saves directory at {saves}")
    rows = []
    for world in sorted(saves.iterdir()):
        if world.is_dir():
            size = sum(f.stat().st_size for f in world.rglob("*") if f.is_file())
            rows.append([world.name, human(size) + "B", datetime.fromtimestamp(world.stat().st_mtime).strftime("%Y-%m-%d %H:%M")])
    table(["World", "Size", "Last modified"], rows)


def cmd_loaders(args):
    data = api("/minecraft/modloader", {"gameVersion": args.version})["data"]
    table(["Name", "Game version", "Latest", "Recommended"], [
        [m["name"], m.get("gameVersion"), m.get("latest"), m.get("recommended")] for m in data[:args.limit]
    ])


def cmd_loader(args):
    print(json.dumps(api(f"/minecraft/modloader/{urllib.parse.quote(args.name)}")["data"], indent=2))


def cmd_versions(args):
    data = api("/minecraft/version")["data"]
    table(["Version", "Type", "Released"], [
        [v["versionString"], VERSION_TYPES.get(v.get("type"), v.get("type")), date(v.get("dateString"))]
        for v in data[:args.limit]
    ])


def cmd_version(args):
    print(json.dumps(api(f"/minecraft/version/{urllib.parse.quote(args.version)}")["data"], indent=2))


def add_filters(sp):
    sp.add_argument("--game", type=int, default=MINECRAFT, help="game id (default 432 = Minecraft)")
    sp.add_argument("--version", help="game version, e.g. 1.21.1")
    sp.add_argument("--loader", choices=LOADERS, help="mod loader")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="cf", description="CurseForge API CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, func, help):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(func=func)
        return sp

    sp = add("auth", cmd_auth, "save your CurseForge API key")
    sp.add_argument("key")

    sp = add("profile", cmd_profile_list, "manage profiles")
    psub = sp.add_subparsers(dest="profile_cmd", required=True)

    s = psub.add_parser("create", help="create a profile")
    s.add_argument("name")
    s.add_argument("--version", help="game version, e.g. 1.21.1")
    s.add_argument("--loader", choices=LOADERS)
    s.add_argument("--dir", help="mods directory")
    s.add_argument("--game", type=int, default=MINECRAFT)
    s.add_argument("--use", action="store_true", help="make it the active profile")
    s.set_defaults(func=cmd_profile_create)

    s = psub.add_parser("list", help="list profiles")
    s.set_defaults(func=cmd_profile_list)

    s = psub.add_parser("use", help="set the active profile")
    s.add_argument("name")
    s.set_defaults(func=cmd_profile_use)

    s = psub.add_parser("show", help="show a profile")
    s.add_argument("name", nargs="?")
    s.set_defaults(func=cmd_profile_show)

    s = psub.add_parser("delete", help="delete a profile")
    s.add_argument("name")
    s.set_defaults(func=cmd_profile_delete)

    add("games", cmd_games, "list games")

    sp = add("game", cmd_game, "game details")
    sp.add_argument("game", type=int, nargs="?", default=MINECRAFT)

    sp = add("game-versions", cmd_game_versions, "list a game's versions")
    sp.add_argument("game", type=int, nargs="?", default=MINECRAFT)
    sp.add_argument("--v2", action="store_true", help="use the v2 endpoint (grouped by type)")

    sp = add("version-types", cmd_version_types, "list a game's version types")
    sp.add_argument("game", type=int, nargs="?", default=MINECRAFT)

    sp = add("categories", cmd_categories, "list categories")
    sp.add_argument("--game", type=int, default=MINECRAFT)
    sp.add_argument("--class-id", type=int)

    sp = add("search", cmd_search, "search mods")
    sp.add_argument("query", nargs="*")
    add_filters(sp)
    sp.add_argument("--class-id", type=int, help="project class, e.g. 6 = mods, 4471 = modpacks, 17 = worlds")
    sp.add_argument("--category", type=int, help="category id (see: cf categories)")
    sp.add_argument("--sort", choices=SORTS)
    sp.add_argument("--order", choices=["asc", "desc"], default="desc")
    sp.add_argument("--limit", type=int, default=20, help="results per page, max 50")
    sp.add_argument("--page", type=int, default=1)
    sp.add_argument("--json", action="store_true", help="raw JSON")
    sp.set_defaults(func=cmd_search)

    sp = add("mod", cmd_mod, "mod details (id or slug)")
    sp.add_argument("mod")
    sp.add_argument("--game", type=int, default=MINECRAFT)
    sp.add_argument("--json", action="store_true", help="raw JSON")
    sp.set_defaults(func=cmd_mod)

    sp = add("mods", cmd_mods, "mod details for several ids")
    sp.add_argument("ids", type=int, nargs="+")
    sp.add_argument("--json", action="store_true", help="raw JSON")

    sp = add("featured", cmd_featured, "featured / popular / recently updated mods")
    sp.add_argument("--game", type=int, default=MINECRAFT)
    sp.add_argument("--version-type", type=int, help="game version type id (see: cf version-types)")
    sp.add_argument("--exclude", type=int, nargs="*", help="mod ids to exclude")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--json", action="store_true", help="raw JSON")

    sp = add("files", cmd_files, "list a mod's files")
    sp.add_argument("mod", type=int)
    sp.add_argument("--version")
    sp.add_argument("--loader", choices=LOADERS)
    sp.add_argument("--release", choices=["release", "beta", "alpha"])
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--json", action="store_true", help="raw JSON")
    sp.set_defaults(func=cmd_files)

    sp = add("file", cmd_file, "single file details (raw JSON)")
    sp.add_argument("mod", type=int)
    sp.add_argument("file", type=int)

    sp = add("file-info", cmd_file_info, "file details for several ids")
    sp.add_argument("ids", type=int, nargs="+")
    sp.add_argument("--json", action="store_true", help="raw JSON")

    sp = add("desc", cmd_desc, "print a mod's description (HTML)")
    sp.add_argument("mod", type=int)

    sp = add("changelog", cmd_changelog, "print a file's changelog (HTML)")
    sp.add_argument("mod", type=int)
    sp.add_argument("file", type=int)

    sp = add("url", cmd_url, "print a file's download URL")
    sp.add_argument("mod", type=int)
    sp.add_argument("file", type=int)

    sp = add("download", cmd_download, "download a file (file id or 'latest')")
    sp.add_argument("mod", type=int)
    sp.add_argument("file", help="file id or 'latest'")
    sp.add_argument("--version")
    sp.add_argument("--loader", choices=LOADERS)
    sp.add_argument("--out", default="downloads", help="output directory")
    sp.set_defaults(func=cmd_download)

    sp = add("install", cmd_install, "install a mod and its required dependencies")
    sp.add_argument("mod", help="mod id or slug")
    sp.add_argument("--game", type=int, default=MINECRAFT)
    sp.add_argument("--version")
    sp.add_argument("--loader", choices=LOADERS)
    sp.add_argument("--dir", help="mods directory (default: %%APPDATA%%\\.minecraft\\mods)")
    sp.add_argument("--no-deps", action="store_true", help="skip required dependencies")
    sp.add_argument("--dry-run", action="store_true", help="show what would be installed")

    sp = add("update", cmd_update, "update installed jars via fingerprints")
    sp.add_argument("--game", type=int, default=MINECRAFT)
    sp.add_argument("--version")
    sp.add_argument("--loader", choices=LOADERS)
    sp.add_argument("--dir", help="mods directory (default: %%APPDATA%%\\.minecraft\\mods)")
    sp.add_argument("--dry-run", action="store_true", help="only report available updates")

    sp = add("add", cmd_add, "add mods to the active profile")
    sp.add_argument("mods", nargs="+", help="mod ids or slugs")
    sp.add_argument("--profile")
    sp.add_argument("--force", action="store_true", help="skip the compatibility check")

    sp = add("remove", cmd_remove, "remove mods from the active profile")
    sp.add_argument("mods", nargs="+", help="mod ids, names, or slugs")
    sp.add_argument("--profile")

    sp = add("list", cmd_list, "list mods in the active profile")
    sp.add_argument("--profile")

    sp = add("upgrade", cmd_upgrade, "download latest compatible versions for the profile")
    sp.add_argument("--profile")
    sp.add_argument("--dir", help="override the profile's mods directory")
    sp.add_argument("--parallel", type=int, default=PARALLEL, help="simultaneous downloads")
    sp.add_argument("--clean", action="store_true", help="move stale jars to .old/")
    sp.add_argument("--dry-run", action="store_true", help="show what would be downloaded")

    sp = add("scan", cmd_scan, "identify local jars by fingerprint")
    sp.add_argument("path", help="a .jar file or a folder of jars")
    sp.add_argument("--game", type=int, default=MINECRAFT)
    sp.add_argument("--fuzzy", action="store_true", help="fuzzy folder match (raw JSON)")

    sp = add("import", cmd_import, "install a CurseForge modpack zip")
    sp.add_argument("pack", help="modpack .zip")
    sp.add_argument("--dir", help="target folder (default: ./<pack name>)")

    sp = add("pack", cmd_pack, "download and install a CurseForge modpack")
    sp.add_argument("mod", help="modpack id or slug")
    sp.add_argument("--game", type=int, default=MINECRAFT)
    sp.add_argument("--version")
    sp.add_argument("--loader", choices=LOADERS)
    sp.add_argument("--out", default="downloads", help="where to save the pack zip")
    sp.add_argument("--dir", help="target folder (default: ./<pack name>)")

    sp = add("worlds", cmd_worlds, "list local Minecraft worlds")
    sp.add_argument("--dir", help="saves directory (default: %%APPDATA%%\\.minecraft\\saves)")

    sp = add("loaders", cmd_loaders, "list Minecraft mod loaders")
    sp.add_argument("--version", help="filter by game version")
    sp.add_argument("--limit", type=int, default=30)

    sp = add("loader", cmd_loader, "mod loader details")
    sp.add_argument("name", help="e.g. forge, fabric, neoforge")

    sp = add("versions", cmd_versions, "list Minecraft versions")
    sp.add_argument("--limit", type=int, default=30)

    sp = add("version", cmd_version, "Minecraft version details")
    sp.add_argument("version", help="e.g. 1.21.1")

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
