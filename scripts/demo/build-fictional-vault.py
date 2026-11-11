"""Build a fictional LanceDB vault for README screenshots.

Every memory below is invented (a homelab and two side projects of a fictional
user). Usage: python build.py <plugin_dir> <db_path>
"""
import importlib.util
import sys
import types

plugin_dir, db_path = sys.argv[1], sys.argv[2]
pkg = types.ModuleType("demo_plugin")
pkg.__path__ = [plugin_dir]
sys.modules["demo_plugin"] = pkg
for name in ("memory_contract", "store"):
    spec = importlib.util.spec_from_file_location(f"demo_plugin.{name}", f"{plugin_dir}/{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"demo_plugin.{name}"] = mod
    spec.loader.exec_module(mod)
store_mod = sys.modules["demo_plugin.store"]
contract = sys.modules["demo_plugin.memory_contract"]

store = store_mod.LanceDBStore(db_path)
ids = {}


def add(domain, subject, facts, tier, category, relations=()):
    rels = []
    for rtype, target in relations:
        if target in ids:
            rels.append({"type": rtype, "target_id": ids[target]})
    memory = contract.MemoryWrite.from_mapping({
        "domain": domain, "subject": subject, "facts": list(facts),
        "tier": tier, "category": category, "relations": rels,
    })
    try:
        result = store.add_memory(memory)
    except Exception as error:
        print("refused", f"{domain}:{subject}", getattr(error, "code", error))
        return
    ids.setdefault(f"{domain}:{subject}", result.get("memory_id"))
    print(result.get("status"), f"{domain}:{subject}")


# Hubs
add("Homelab", "Overview", ["host=atlas (Ryzen 5 5600G, 32 GB)", "os=Debian 13", "services run in Docker Compose under /srv/compose"], 2, "project")
add("Orbit", "Overview", ["Orbit = static site generator written in Rust", "repo=~/code/orbit", "build=cargo build --release"], 2, "project")
add("Pantry", "Overview", ["Pantry = home inventory web app", "stack=SvelteKit + SQLite", "repo=~/code/pantry"], 2, "project")

# Homelab
add("Homelab", "Reverse_Proxy", ["proxy=Traefik 3", "config=/srv/compose/traefik/dynamic.yml", "tls=Let's Encrypt DNS challenge"], 2, "tech", [("part_of", "Homelab:Overview")])
add("Homelab", "Jellyfin", ["port=8096", "media=/mnt/tank/media", "hw_accel=VAAPI on the iGPU"], 2, "tech", [("part_of", "Homelab:Overview"), ("runs_on", "Homelab:Reverse_Proxy")])
add("Homelab", "Jellyfin_Transcode_Fix", ["symptom=4K HEVC playback stutters", "cause=render group missing in the container", "fix=group_add: [render] in compose"], 1, "correction", [("part_of", "Homelab:Jellyfin")])
add("Homelab", "Pihole", ["port=8053", "upstream=unbound on 127.0.0.1#5335", "blocklists refresh weekly"], 2, "tech", [("part_of", "Homelab:Overview")])
add("Homelab", "Backups", ["tool=restic", "target=b2:atlas-backups", "schedule=daily 03:30 via systemd timer"], 1, "decision", [("part_of", "Homelab:Overview")])
add("Homelab", "Backups_Retention", ["keep-daily=7", "keep-weekly=4", "keep-monthly=6"], 2, "decision", [("extends", "Homelab:Backups")])
add("Homelab", "ZFS_Pool", ["pool=tank (2x8 TB mirror)", "scrub=monthly", "snapshots=sanoid hourly"], 2, "tech", [("part_of", "Homelab:Overview")])
add("Homelab", "Grafana", ["port=3000", "datasource=Prometheus", "dashboard=node-exporter full"], 3, "tech", [("part_of", "Homelab:Overview"), ("depends", "Homelab:Prometheus")])
add("Homelab", "Prometheus", ["port=9090", "retention=30d", "scrape_interval=30s"], 3, "tech", [("part_of", "Homelab:Overview")])
add("Homelab", "Grafana", ["port=3001", "moved off 3000 to free it for Pantry dev"], 3, "tech", [("part_of", "Homelab:Overview")])
add("Homelab", "Router_DHCP", ["router=OpenWrt", "atlas static lease=192.168.1.20", "dns handed out=192.168.1.20"], 2, "fact", [("connects_to", "Homelab:Pihole")])
add("Homelab", "Docker_Log_Growth", ["symptom=/var/lib/docker grew 40 GB", "cause=json-file driver without max-size", "fix=log-opts max-size=10m max-file=3"], 1, "correction", [("part_of", "Homelab:Overview")])
add("Homelab", "Unbound", ["port=5335", "mode=recursive, no forwarders", "cache-min-ttl=300"], 3, "tech", [("part_of", "Homelab:Overview")])
add("Homelab", "Uptime_Kuma", ["port=3002", "checks every 60s", "alerts to a Matrix room"], 3, "tech", [("part_of", "Homelab:Overview")])

# Orbit
add("Orbit", "Markdown_Pipeline", ["parser=pulldown-cmark", "syntax highlight=syntect", "front matter=TOML"], 2, "tech", [("part_of", "Orbit:Overview"), ("uses", "Orbit:Overview")])
add("Orbit", "Incremental_Builds", ["cache=.orbit/cache keyed by content hash", "full rebuild when templates change", "cold build 1.8 s, warm 0.2 s on 400 pages"], 2, "decision", [("part_of", "Orbit:Overview")])
add("Orbit", "Template_Engine", ["engine=minijinja", "templates=themes/<name>/*.html", "partials loaded once per build"], 2, "tech", [("part_of", "Orbit:Overview")])
add("Orbit", "Watch_Mode_Bug", ["symptom=watch mode rebuilds twice per save", "cause=editor writes a temp file then renames", "fix=debounce 150 ms on notify events"], 1, "correction", [("part_of", "Orbit:Overview"), ("extends", "Orbit:Incremental_Builds")])
add("Orbit", "Release_Process", ["tag=vX.Y.Z", "ci=GitHub Actions builds linux/mac/windows", "publish=cargo publish after the tag build"], 2, "pattern", [("part_of", "Orbit:Overview")])
add("Orbit", "Image_Resizing", ["crate=image + fast_image_resize", "widths=480,960,1600", "format=webp, original kept"], 3, "tech", [("part_of", "Orbit:Overview")])
add("Orbit", "Why_Not_Hugo", ["Hugo templates were the pain point", "wanted Rust plugins compiled in", "decided 2026-03"], 3, "decision", [("part_of", "Orbit:Overview")])
add("Orbit", "Search_Index", ["index=pagefind at build end", "size=~120 KB for 400 pages"], 3, "tech", [("part_of", "Orbit:Overview"), ("depends", "Orbit:Incremental_Builds")])

# Pantry
add("Pantry", "Database", ["db=SQLite in WAL mode", "path=./data/pantry.db", "migrations=drizzle-kit"], 2, "tech", [("part_of", "Pantry:Overview")])
add("Pantry", "Barcode_Scanning", ["lib=zxing-wasm", "camera needs HTTPS on mobile", "fallback=manual EAN entry"], 2, "tech", [("part_of", "Pantry:Overview")])
add("Pantry", "Dev_Server", ["port=3000", "command=pnpm dev --host"], 3, "tech", [("part_of", "Pantry:Overview")])
add("Pantry", "Deploy", ["runs on atlas behind Traefik", "url=pantry.home.arpa", "image built by a Forgejo runner"], 2, "project", [("part_of", "Pantry:Overview"), ("runs_on", "Homelab:Overview"), ("requires", "Homelab:Reverse_Proxy")])
add("Pantry", "Expiry_Reminders", ["job runs daily 08:00", "notifies 3 days before best-before", "channel=ntfy topic pantry"], 3, "project", [("part_of", "Pantry:Overview")])
add("Pantry", "SQLite_Locking", ["symptom=SQLITE_BUSY under concurrent writes", "cause=default rollback journal", "fix=PRAGMA journal_mode=WAL; busy_timeout=5000"], 1, "correction", [("part_of", "Pantry:Database"), ("supersedes", "Pantry:Database")])
add("Pantry", "Auth", ["auth=passkeys only", "lib=SimpleWebAuthn", "session=httpOnly cookie, 30 days"], 2, "decision", [("part_of", "Pantry:Overview")])

# Preferences, references, insights, questions
add("User", "Code_Style", ["prefers small commits with imperative messages", "tabs in Go, spaces elsewhere", "no emoji in commit messages"], 1, "user_pref")
add("User", "Answer_Style", ["short answers first, details on request", "French in chat, English in code and docs"], 1, "user_pref")
add("Reference", "Rust_Async_Book", ["url=https://rust-lang.github.io/async-book/", "useful for the Orbit dev server"], 3, "reference", [("extends", "Orbit:Overview")])
add("Reference", "Traefik_Docs", ["url=https://doc.traefik.io/traefik/", "routers + middlewares page is the one that matters"], 3, "reference", [("extends", "Homelab:Reverse_Proxy")])
add("Insight", "Compose_Healthchecks", ["depends_on with condition: service_healthy avoids start races", "applies to every stack on atlas"], 2, "insight", [("part_of", "Homelab:Overview")])
add("Pattern", "Debounce_File_Watchers", ["editors save via temp file + rename", "debounce 100-200 ms before rebuilding", "seen in Orbit and in Pantry dev"], 2, "pattern", [("extends", "Orbit:Watch_Mode_Bug")])
add("Question", "Orbit_Plugins_ABI", ["open: stable plugin ABI or compile-time features only?", "blocked on wasm component model maturity"], 3, "question", [("part_of", "Orbit:Overview")])
add("Homelab", "Backup_Schedule", ["restic runs daily at 03:30 from a systemd timer", "target bucket=b2:atlas-backups"], 2, "fact", [("part_of", "Homelab:Overview")])
# A contradiction as it arrives through the Import path: accepted, and recorded
# in the conflict ledger for review (add_memory refuses it, as shown above).
print(store.import_records([{
    "content": "Homelab:Grafana port=3001 ::fact:: moved off 3000 to free it for Pantry dev [Tier=3]",
    "category": "tech", "tags": ["grafana"],
}]))
print(len(ids), "subjects")
