"""Build/test/run the real frontend with fake credentials and loopback egress only."""
import os
import shutil
import sys
from pathlib import Path

from release_verification_context import load_context

root = Path(__file__).resolve().parents[1]
context = load_context()
app, mode = sys.argv[1:3]
if app not in {"admin", "site"} or mode not in {"dev", "start", "build", "test", "lint", "typecheck"}:
    raise SystemExit("Choose admin/site and dev/start/build/test/lint/typecheck")
if any(p.name not in {".env.example"} for p in (root / app).glob(".env*")):
    raise SystemExit("Refusing frontend dotenv files in the verification worktree")
port = str(context[f"{app}_port"])
env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
       "HOME": context["root"], "NEXT_TELEMETRY_DISABLED": "1",
       "ADMIN_SECRET_KEY": "test-admin-key", "ADMIN_SESSION_SECRET": "release-local-session-key-32-characters",
       "BFF_ACTOR_SECRET": "test-actor-key", "SITE_BFF_SECRET": "test-site-bff-key",
       "SITE_REVALIDATE_SECRET": "test-revalidate-key", "INDEXNOW_KEY": "release-indexnow-test-key",
       "BACKEND_URL": f"http://release-api.example.test:{context['api_port']}",
       "NEXT_PUBLIC_API_URL": f"http://release-api.example.test:{context['api_port']}/api/v1/public",
       "NEXT_PUBLIC_SITE_URL": "https://release-site.example.test"}
if mode in {"test", "lint", "typecheck"}:
    for key in ("BACKEND_URL", "NEXT_PUBLIC_API_URL", "NEXT_PUBLIC_SITE_URL"):
        env.pop(key, None)
command = ["npm", "run", mode]
if mode == "dev":
    command += ["--", "--hostname", "127.0.0.1", "--port", port]
if mode == "start":
    standalone = root / app / ".next/standalone"
    if not (standalone / "server.js").is_file():
        raise SystemExit("Run a production build before the standalone UI rehearsal")
    shutil.copytree(root / app / ".next/static", standalone / ".next/static", dirs_exist_ok=True)
    if (root / app / "public").is_dir():
        shutil.copytree(root / app / "public", standalone / "public", dirs_exist_ok=True)
    command = ["node", str(standalone / "server.js")]
    env.update(NODE_ENV="production", HOSTNAME="127.0.0.1", PORT=port)
# Node-level egress guard is inherited by compiler subprocesses.
env["NODE_OPTIONS"] = f"--require {root / 'scripts/release_frontend_network_guard.cjs'}"
if mode != "build":
    # Runtime also gets an OS-level rule covering native libraries.
    allowed = " ".join(f'(remote ip "localhost:{context[k]}")'
                       for k in ("admin_port", "site_port", "api_port"))
    profile = f"(version 1) (allow default) (deny network-outbound) (allow network-outbound {allowed})"
    command = ["/usr/bin/sandbox-exec", "-p", profile, *command]
os.chdir(root / app)
os.execvpe(command[0], command, env)
