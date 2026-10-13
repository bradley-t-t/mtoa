#!/usr/bin/env python3
"""Reaches the services a project depends on, so the checklist answers to a request.

Everything here makes a real call. That is the whole point of the group it feeds:
source can say a project uses Supabase, and only a request can say the project is
not paused, the key still works, the webhook exists and production is serving the
commit main points at.

Credentials are read from the environment at the point of use and never written
anywhere. Pull them from CryptoFort into the call that needs them:

    SUPABASE_ACCESS_TOKEN=<pat> STRIPE_SECRET_KEY=<key> VERCEL_TOKEN=<token> \
      probe.py all --root . --url https://example.com --out /tmp/stack-probe.json

A probe that cannot be taken is filed as failing, with the reason. It is never
filed as passing and never quietly dropped: an unverified service and a verified
one have to look different, or the checklist is back to reading configuration.

    all         every probe the project's shape calls for
    supabase | clerk | stripe | vercel | repo | live | env | deps | observe | paint
                one family at a time

Output is {"probes": {name: {ok, detail, at}}}, which `stack-pass.py probe --file`
reads. Every detail is scrubbed of key shapes before it is written, because the
file is quoted into a report that gets read by people.
"""

import json
import os
import re
import subprocess
import tempfile
import time
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TIMEOUT = 20


def _profile():
    """The profile reader, tools/mtoa_config.py: the copy in the tree this file
    sits in first, then the installed one, else nothing."""
    import importlib.util
    here = Path(__file__).resolve()
    tree = [here.parents[3]] if len(here.parents) > 3 else []
    for root in tree + [Path.home() / ".claude"]:
        path = root / "tools" / "mtoa_config.py"
        if path.is_file():
            try:
                spec = importlib.util.spec_from_file_location("mtoa_config", str(path))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
            except Exception:
                continue
    return None


PROFILE = _profile()


def setting(key, default=None):
    """One value from the owner's profile by dotted key; unset without a reader."""
    return PROFILE.get(key, default) if PROFILE is not None else default

# Redacted out of every detail before it is written. The output file is quoted into
# a report, and a probe that leaks the key it used is worse than no probe.
SECRET_SHAPE = re.compile(
    r"sk_(?:live|test)_[A-Za-z0-9]{6,}|rk_(?:live|test)_[A-Za-z0-9]{6,}|whsec_[A-Za-z0-9]{6,}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|sb_(?:secret|publishable)_[A-Za-z0-9_-]{6,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def scrub(text):
    return SECRET_SHAPE.sub("<redacted>", str(text))[:400]


class Probes(dict):
    """The results, keyed by the name the gates ask for."""

    def ok(self, name, detail):
        self[name] = {"ok": True, "detail": scrub(detail), "at": now()}

    def bad(self, name, detail):
        self[name] = {"ok": False, "detail": scrub(detail), "at": now()}

    def need(self, name, variable, what):
        self.bad(name, "%s is not set, so %s. Read it from CryptoFort into this call rather "
                       "than into a file." % (variable, what))


def http(url, headers=None, method="GET", data=None, timeout=TIMEOUT):
    """One request. Returns (status, body-text). Never raises."""
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, method=method,
                                     headers={"Accept": "application/json",
                                              "User-Agent": "stack-checklist-probe",
                                              **({"Content-Type": "application/json"}
                                                 if data is not None else {}),
                                              **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(2000000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(200000).decode("utf-8", "replace")
    except Exception as exc:                      # DNS, TLS, timeout, refused
        return 0, str(exc)


def as_json(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


def env(*names):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return ""


def sh(*args, cwd=None, timeout=60):
    try:
        done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return done.returncode, done.stdout, done.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


# ---------------------------------------------------------------- supabase


def project_ref(url):
    match = re.search(r"https?://([a-z0-9]+)\.supabase\.(co|in)", url or "")
    return match.group(1) if match else env("SUPABASE_PROJECT_REF")


def management(ref, path, token, data=None, method="GET"):
    return http("https://api.supabase.com/v1/projects/%s%s" % (ref, path),
                headers={"Authorization": "Bearer %s" % token}, data=data, method=method)


def sql(ref, token, query):
    status, body = management(ref, "/database/query", token, data={"query": query}, method="POST")
    return status, as_json(body), body


def probe_supabase(probes, root, url=None):
    base = env("SUPABASE_URL", "VITE_SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL")
    key = env("SUPABASE_ANON_KEY", "SUPABASE_PUBLISHABLE_KEY", "VITE_SUPABASE_ANON_KEY",
              "NEXT_PUBLIC_SUPABASE_ANON_KEY")
    token = env("SUPABASE_ACCESS_TOKEN")
    ref = project_ref(base)

    if not base:
        for name in ("supabase.awake", "supabase.query"):
            probes.need(name, "SUPABASE_URL", "the project could not be reached")
    else:
        status, body = http("%s/auth/v1/settings" % base.rstrip("/"),
                            headers={"apikey": key} if key else None)
        if status in (200, 401, 403):
            probes.ok("supabase.awake", "%s answered %s, so the project is running" % (base, status))
        elif status in (503, 540, 0):
            probes.bad("supabase.awake", "%s did not answer (%s) - a paused project fails this "
                                         "way. Resume it." % (base, status or body[:80]))
        else:
            probes.bad("supabase.awake", "%s answered %s" % (base, status))
        if not key:
            probes.need("supabase.query", "SUPABASE_ANON_KEY", "no query was made")
        elif status == 200:
            probes.ok("supabase.query", "a request carrying the publishable key returned 200")
        elif status in (401, 403):
            probes.bad("supabase.query", "the publishable key was rejected (%s) - it is not the "
                                         "key for this project" % status)
        else:
            probes.bad("supabase.query", "the query returned %s" % status)

    if not token or not ref:
        for name in ("supabase.rls", "supabase.migrations", "supabase.functions"):
            probes.need(name, "SUPABASE_ACCESS_TOKEN and a project ref",
                        "the schema was never inspected")
    else:
        status, data, raw = sql(ref, token, """
            select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relkind = 'r' and c.relrowsecurity = false
            order by 1
        """)
        # A successful POST to /database/query is answered 201, not 200, so
        # keying this on 200 alone read every correct project as unreadable.
        if status in (200, 201) and isinstance(data, list):
            open_tables = [row.get("relname") for row in data]
            if open_tables:
                probes.bad("supabase.rls", "%d table(s) in public have RLS off: %s. The "
                                           "publishable key is in every visitor's browser."
                           % (len(open_tables), ", ".join(open_tables[:12])))
            else:
                probes.ok("supabase.rls", "every table in public has row level security on")
        else:
            probes.bad("supabase.rls", "the schema query returned %s: %s" % (status, raw[:160]))

        local = sorted(p.name for p in (Path(root) / "supabase" / "migrations").glob("*.sql")) \
            if (Path(root) / "supabase" / "migrations").is_dir() else []
        status, body = management(ref, "/database/migrations", token)
        remote = as_json(body)
        if status == 200 and isinstance(remote, list):
            versions = {str(row.get("version")) for row in remote}
            missing = [n for n in local if not any(n.startswith(v) for v in versions)]
            extra = sorted(versions - {n.split("_")[0] for n in local})
            if missing or extra:
                probes.bad("supabase.migrations",
                           "%d local migration(s) not applied (%s); %d applied on the remote and "
                           "not in the repository (%s)"
                           % (len(missing), ", ".join(missing[:5]) or "none",
                              len(extra), ", ".join(extra[:5]) or "none"))
            else:
                probes.ok("supabase.migrations", "%d migration(s) match between the repository "
                                                 "and the remote" % len(local))
        else:
            probes.bad("supabase.migrations", "the migration list returned %s" % status)

        want = sorted(p.name for p in (Path(root) / "supabase" / "functions").iterdir()
                      if p.is_dir()) if (Path(root) / "supabase" / "functions").is_dir() else []
        status, body = management(ref, "/functions", token)
        deployed = as_json(body)
        if status == 200 and isinstance(deployed, list):
            live = {row.get("slug") for row in deployed}
            missing = [n for n in want if n not in live]
            if missing:
                probes.bad("supabase.functions", "%d function(s) in the repository are not "
                                                 "deployed: %s" % (len(missing), ", ".join(missing)))
            else:
                probes.ok("supabase.functions", "%d function(s) deployed" % len(live))
        else:
            probes.bad("supabase.functions", "the function list returned %s" % status)

    service = env("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not service:
        probes.need("supabase.storage", "SUPABASE_SERVICE_ROLE_KEY", "no bucket was inspected")
    else:
        status, body = http("%s/storage/v1/bucket" % base.rstrip("/"),
                            headers={"apikey": service, "Authorization": "Bearer %s" % service})
        buckets = as_json(body)
        if status == 200 and isinstance(buckets, list):
            public = [b.get("name") for b in buckets if b.get("public")]
            if public:
                probes.bad("supabase.storage", "%d bucket(s) are public: %s. Say so deliberately "
                                               "or make them private." % (len(public), ", ".join(public)))
            else:
                probes.ok("supabase.storage", "%d bucket(s), none public" % len(buckets))
        else:
            probes.bad("supabase.storage", "the bucket list returned %s" % status)


# ---------------------------------------------------------------- clerk


def probe_clerk(probes, root, url=None):
    secret = env("CLERK_SECRET_KEY")
    publishable = env("CLERK_PUBLISHABLE_KEY", "VITE_CLERK_PUBLISHABLE_KEY",
                      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY")
    if not secret:
        for name in ("clerk.live", "clerk.origins", "clerk.webhook"):
            probes.need(name, "CLERK_SECRET_KEY", "the instance was never reached")
    else:
        headers = {"Authorization": "Bearer %s" % secret}
        status, body = http("https://api.clerk.com/v1/instance", headers=headers)
        instance = as_json(body)
        if status == 200:
            probes.ok("clerk.live", "the instance answered: environment %s"
                      % (instance or {}).get("environment_type", "unknown"))
        else:
            probes.bad("clerk.live", "api.clerk.com answered %s - the key or the instance is "
                                     "wrong for this environment" % status)
        if status == 200 and url:
            host = re.sub(r"^https?://", "", url).rstrip("/")
            status2, body2 = http("https://api.clerk.com/v1/domains", headers=headers)
            domains = as_json(body2) or []
            rows = domains.get("data", domains) if isinstance(domains, dict) else domains
            names = {str(row.get("name", "")) for row in rows} if isinstance(rows, list) else set()
            if any(host.endswith(n) or n.endswith(host) for n in names if n):
                probes.ok("clerk.origins", "%s is one of the instance's domains (%s)"
                          % (host, ", ".join(sorted(names)[:5])))
            else:
                probes.bad("clerk.origins", "%s is not among the instance's domains (%s) - the "
                                            "redirect back from sign-in breaks in production only"
                           % (host, ", ".join(sorted(names)[:5]) or "none returned"))
        elif status == 200:
            probes.bad("clerk.origins", "no --url given, so the deployed domain could not be "
                                        "compared against the instance's allowed origins")
        status3, body3 = http("https://api.clerk.com/v1/webhooks/svix", headers=headers,
                              method="GET")
        if status3 == 200:
            probes.ok("clerk.webhook", "a Svix application is attached to this instance")
        elif status3 in (404, 400):
            probes.bad("clerk.webhook", "no webhook application on this instance (%s) - an "
                                        "endpoint that is not registered receives nothing" % status3)
        else:
            probes.bad("clerk.webhook", "the webhook lookup returned %s" % status3)

    if not publishable:
        probes.need("clerk.keymode", "CLERK_PUBLISHABLE_KEY", "the key mode was never read")
    else:
        live = publishable.startswith("pk_live_") and secret.startswith("sk_live_") if secret \
            else publishable.startswith("pk_live_")
        test = publishable.startswith("pk_test_")
        if live:
            probes.ok("clerk.keymode", "production keys (pk_live/sk_live)")
        elif test:
            probes.bad("clerk.keymode", "this environment holds pk_test keys - sign-in succeeds "
                                        "and the users land in the development instance")
        else:
            probes.bad("clerk.keymode", "the publishable key is neither pk_live nor pk_test")


# ---------------------------------------------------------------- stripe


def probe_stripe(probes, root, url=None):
    secret = env("STRIPE_SECRET_KEY")
    if not secret:
        for name in ("stripe.keymode", "stripe.webhook", "stripe.prices"):
            probes.need(name, "STRIPE_SECRET_KEY", "the account was never reached")
        return
    headers = {"Authorization": "Bearer %s" % secret}
    mode = "live" if secret.startswith("sk_live_") else "test" if secret.startswith("sk_test_") \
        else "unknown"
    deployed = bool(url) and "localhost" not in (url or "")
    if mode == "unknown":
        probes.bad("stripe.keymode", "the key is neither sk_live nor sk_test")
    elif deployed and mode == "test":
        probes.bad("stripe.keymode", "a production URL was given and the key is sk_test - "
                                     "payments succeed and no money arrives")
    elif not deployed and mode == "live":
        probes.bad("stripe.keymode", "no production URL was given and the key is sk_live - a "
                                     "branch build would take real money")
    else:
        probes.ok("stripe.keymode", "%s keys for this environment" % mode)

    status, body = http("https://api.stripe.com/v1/webhook_endpoints?limit=100", headers=headers)
    data = (as_json(body) or {}).get("data") if status == 200 else None
    if data is None:
        probes.bad("stripe.webhook", "the endpoint list returned %s" % status)
    elif not data:
        probes.bad("stripe.webhook", "no webhook endpoint registered in %s mode - payment "
                                     "succeeds and the application never learns" % mode)
    else:
        enabled = [e for e in data if e.get("status") == "enabled"]
        matching = [e for e in enabled if url and re.sub(r"^https?://", "", url).rstrip("/")
                    in str(e.get("url", ""))] if url else enabled
        if matching:
            probes.ok("stripe.webhook", "%d enabled endpoint(s), one at %s"
                      % (len(enabled), matching[0].get("url", "")))
        else:
            probes.bad("stripe.webhook", "%d endpoint(s) registered, none pointing at %s"
                       % (len(enabled), url or "the deployed URL"))

    wanted = set()
    for path in Path(root).rglob("*"):
        if set(path.parts) & {"node_modules", ".git", "dist", "build"} or not path.is_file():
            continue
        if path.suffix.lower() not in {".ts", ".tsx", ".js", ".jsx", ".json", ".env", ".example"}:
            continue
        try:
            wanted.update(re.findall(r"\bprice_[A-Za-z0-9]{6,}\b",
                                     path.read_text(errors="replace")[:400000]))
        except OSError:
            continue
    if not wanted:
        probes.ok("stripe.prices", "no price identifier is written into the source")
    else:
        missing = []
        for price in sorted(wanted):
            code, _ = http("https://api.stripe.com/v1/prices/%s" % price, headers=headers)
            if code != 200:
                missing.append("%s (%s)" % (price, code))
        if missing:
            probes.bad("stripe.prices", "%d price id(s) in source do not resolve in %s mode: %s"
                       % (len(missing), mode, ", ".join(missing[:8])))
        else:
            probes.ok("stripe.prices", "all %d price id(s) resolve in %s mode" % (len(wanted), mode))


# ---------------------------------------------------------------- vercel


def vercel_api(path, token, team=None):
    join = "&" if "?" in path else "?"
    suffix = "%steamId=%s" % (join, team) if team else ""
    return http("https://api.vercel.com%s%s" % (path, suffix),
                headers={"Authorization": "Bearer %s" % token})


def vercel_link(root):
    """The project id, org id and name, from whichever file the CLI wrote.

    project.json is what a linked directory gets; repo.json is what a linked
    git repository gets, and it holds the same fields one level down under the
    entry whose directory is the root.
    """
    direct = Path(root) / ".vercel" / "project.json"
    if direct.is_file() and direct.stat().st_size:
        return json.loads(direct.read_text())
    repo = Path(root) / ".vercel" / "repo.json"
    if repo.is_file() and repo.stat().st_size:
        projects = (json.loads(repo.read_text()) or {}).get("projects") or []
        for entry in projects:
            if entry.get("directory") in (".", "", None):
                return {"projectId": entry.get("id"), "orgId": entry.get("orgId"),
                        "projectName": entry.get("name")}
        if projects:
            first = projects[0]
            return {"projectId": first.get("id"), "orgId": first.get("orgId"),
                    "projectName": first.get("name")}
    return {}


def probe_vercel(probes, root, url=None):
    token = env("VERCEL_TOKEN")
    link = vercel_link(root)
    project = env("VERCEL_PROJECT_ID") or link.get("projectId", "")
    team = env("VERCEL_TEAM_ID") or link.get("orgId") or None
    names = ("vercel.domain", "vercel.protection", "vercel.current")
    if not token:
        for name in names:
            probes.need(name, "VERCEL_TOKEN", "the project was never read")
        return
    if not project:
        for name in names:
            probes.bad(name, "no project id - neither .vercel/project.json nor "
                             ".vercel/repo.json is present and VERCEL_PROJECT_ID is "
                             "not set, so there is nothing to look up")
        return

    status, body = vercel_api("/v9/projects/%s" % project, token, team)
    data = as_json(body) or {}
    if status != 200:
        for name in names:
            probes.bad(name, "the Vercel project lookup returned %s" % status)
        return

    domains = [d.get("name") for d in (data.get("alias") or []) if isinstance(d, dict)]
    status2, body2 = vercel_api("/v9/projects/%s/domains" % project, token, team)
    listed = [d.get("name") for d in ((as_json(body2) or {}).get("domains") or [])]
    all_domains = sorted({d for d in domains + listed if d})
    host = re.sub(r"^https?://", "", url or "").rstrip("/")
    if host and host in all_domains:
        code, _ = http(url)
        if code and code < 400:
            probes.ok("vercel.domain", "%s is attached and answered %s" % (host, code))
        else:
            probes.bad("vercel.domain", "%s is attached to the project and answered %s"
                       % (host, code or "nothing"))
    elif host:
        probes.bad("vercel.domain", "%s is not attached to project %s (attached: %s)"
                   % (host, data.get("name", project), ", ".join(all_domains[:6]) or "none"))
    else:
        probes.bad("vercel.domain", "no --url given, so no domain could be checked against the "
                                    "%d attached to %s" % (len(all_domains), data.get("name", project)))

    protection = data.get("passwordProtection") or data.get("ssoProtection")
    probes.ok("vercel.protection",
              "deployment protection is %s on %s - confirm this is deliberate"
              % ("on (%s)" % json.dumps(protection)[:80] if protection else "off",
                 data.get("name", project)))

    status3, body3 = vercel_api("/v6/deployments?projectId=%s&target=production&limit=1"
                                % project, token, team)
    deployments = (as_json(body3) or {}).get("deployments") or []
    if not deployments:
        probes.bad("vercel.current", "no production deployment found for %s"
                   % data.get("name", project))
    else:
        served = str((deployments[0].get("meta") or {}).get("githubCommitSha", ""))[:40]
        code, out, _ = sh("git", "rev-parse", "origin/main", cwd=root)
        tip = out.strip()
        if not tip:
            probes.bad("vercel.current", "production serves %s and origin/main could not be read "
                                         "to compare" % (served[:8] or "an unknown commit"))
        elif served and served == tip:
            probes.ok("vercel.current", "production serves %s, the tip of main" % served[:8])
        else:
            probes.bad("vercel.current", "production serves %s and main is at %s - a promotion "
                                         "that merged and never built leaves exactly this"
                       % (served[:8] or "an unknown commit", tip[:8]))


# ---------------------------------------------------------------- repository


STANDARD_CI = "~/.claude/checklists/stack/references/ci.yml"


def slug_of(root):
    code, out, _ = sh("git", "remote", "get-url", "origin", cwd=root)
    match = re.search(r"[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?$", out.strip())
    return "%s/%s" % (match.group(1), match.group(2)) if match else ""


def out_or(text):
    return text if text.strip() else "gh api user returned nothing"


def gh(path, slug):
    code, out, err = sh("gh", "api", path.replace("{repo}", slug))
    if code != 0:
        return None, (err or out).strip()[:200]
    return as_json(out), ""


def probe_repo(probes, root, url=None):
    names = ("repo.develop", "repo.protection", "repo.settings", "repo.workflow")
    slug = slug_of(root)
    if not slug:
        for name in names:
            probes.bad(name, "no origin remote, so there is no repository to read")
        return
    code, who, err = sh("gh", "api", "user", "--jq", ".login", timeout=30)
    if code != 0 or not who.strip():
        for name in names:
            probes.bad(name, "gh cannot reach the REST API (%s) - a run that finds gh broken says "
                             "so rather than skipping the repository gates"
                       % (err or out_or(who)).strip()[:100])
        return

    branches, error = gh("repos/{repo}/branches", slug)
    if branches is None:
        probes.bad("repo.develop", "could not list branches: %s" % error)
    else:
        found = {b.get("name") for b in branches}
        if "develop" in found:
            probes.ok("repo.develop", "develop exists on %s" % slug)
        else:
            probes.bad("repo.develop", "no develop branch on %s - every branch is cut from main, "
                                       "and a branch cut from main is missing what develop holds"
                       % slug)

    faults = []
    for branch in ("main", "develop"):
        rule, error = gh("repos/{repo}/branches/%s/protection" % branch, slug)
        if rule is None:
            faults.append("%s: %s" % (branch, error or "no protection rule"))
            continue
        contexts = ((rule.get("required_status_checks") or {}).get("contexts") or [])
        strict = (rule.get("required_status_checks") or {}).get("strict")
        if "check" not in contexts:
            faults.append("%s does not require the check context (has %s)"
                          % (branch, ", ".join(contexts) or "none"))
        if branch == "develop" and strict is not True:
            faults.append("develop's check is not strict, so a branch is not tested against what "
                          "it will land on")
        if branch == "main" and strict is True:
            faults.append("main's check is strict, which puts develop behind after every "
                          "merge-commit promotion")
        if (rule.get("allow_force_pushes") or {}).get("enabled"):
            faults.append("%s allows force pushes" % branch)
        if not (rule.get("required_conversation_resolution") or {}).get("enabled"):
            faults.append("%s does not require conversations resolved" % branch)
    if faults:
        probes.bad("repo.protection", "; ".join(faults[:6]))
    else:
        probes.ok("repo.protection", "main and develop both gate on check, with develop strict")

    repo, error = gh("repos/{repo}", slug)
    if repo is None:
        probes.bad("repo.settings", "could not read the repository: %s" % error)
    else:
        off = [k for k in ("allow_auto_merge", "delete_branch_on_merge") if not repo.get(k)]
        if off:
            probes.bad("repo.settings", "%s is off on %s" % (" and ".join(off), slug))
        else:
            probes.ok("repo.settings", "auto-merge and branch deletion are both on")

    standard = Path(STANDARD_CI).expanduser()
    want = standard.read_text() if standard.is_file() else ""
    seen = {}
    for branch in ("main", "develop"):
        code, out, _ = sh("gh", "api",
                          "repos/%s/contents/.github/workflows/ci.yml?ref=%s" % (slug, branch),
                          "--jq", ".content")
        if code != 0 or not out.strip():
            seen[branch] = None
            continue
        import base64
        try:
            seen[branch] = base64.b64decode(out.strip()).decode("utf-8", "replace")
        except Exception:
            seen[branch] = None
    missing = [b for b, text in seen.items() if text is None]
    if missing:
        probes.bad("repo.workflow", "no ci.yml on %s - the branch gates on a check that never "
                                    "runs there" % ", ".join(missing))
    elif want and any(text.strip() != want.strip() for text in seen.values()):
        drifted = [b for b, text in seen.items() if text.strip() != want.strip()]
        probes.bad("repo.workflow", "ci.yml on %s differs from the standard - a workflow that "
                                    "drifted gates nothing on that branch" % ", ".join(drifted))
    elif not want:
        probes.bad("repo.workflow", "ci.yml is on both branches and the standard copy is not "
                                    "installed at %s, so they could not be compared" % STANDARD_CI)
    else:
        probes.ok("repo.workflow", "ci.yml matches the standard on main and develop")


# ---------------------------------------------------------------- live page


CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/opt/pw-browsers/chromium", "/opt/pw-browsers/chrome-linux/chrome",
)

ASSET_TAG = re.compile(r"<(?:script|link|img|source)\b[^>]*?>", re.I)
ASSET_HREF = re.compile(r"""\b(?:src|href)\s*=\s*["']([^"']+)["']""", re.I)
HINT_REL = re.compile(r"""rel\s*=\s*["'][^"']*\b(?:preconnect|dns-prefetch|prefetch|
                          manifest|alternate|canonical)\b""", re.I | re.X)


def asset_refs(html):
    """Every resource the page actually fetches, hints excluded."""
    out = []
    for tag in ASSET_TAG.findall(html):
        if HINT_REL.search(tag):
            continue
        href = ASSET_HREF.search(tag)
        if href:
            out.append(href.group(1))
    return out


def absolute(href, base):
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("data:") or href.startswith("#") or href.startswith("mailto:"):
        return ""
    return base.rstrip("/") + "/" + href.lstrip("/")


def probe_live(probes, root, url=None):
    names = ("live.reachable", "live.console", "live.assets", "live.thirdparty", "chrome.links")
    if not url:
        for name in names:
            probes.bad(name, "no --url given, so the deployed page was never loaded")
        return
    status, html = http(url, timeout=30)
    if not status or status >= 400:
        for name in names:
            probes.bad(name, "%s answered %s" % (url, status or html[:100]))
        return
    probes.ok("live.reachable", "%s answered %s, %d bytes" % (url, status, len(html)))

    refs = [absolute(h, url) for h in asset_refs(html)]
    refs = [r for r in refs if r]
    host = re.sub(r"^https?://", "", url).split("/")[0]
    own = [r for r in refs if host in r]
    third = [r for r in refs if host not in r]
    broken = []
    for ref in own[:60]:
        code, _ = http(ref, method="HEAD", timeout=15)
        if not code or code >= 400:
            code, _ = http(ref, timeout=15)
        if not code or code >= 400:
            broken.append("%s (%s)" % (ref[len(url):][:70] or "/", code or "no answer"))
    if broken:
        probes.bad("live.assets", "%d of %d asset(s) the page asks for are missing: %s"
                   % (len(broken), len(own), "; ".join(broken[:6])))
    else:
        probes.ok("live.assets", "all %d same-origin asset(s) answered" % len(own))

    dead = []
    for ref in sorted(set(third))[:30]:
        code, _ = http(ref, method="HEAD", timeout=15)
        if not code or code >= 400:
            code, _ = http(ref, timeout=15)
        if not code or code >= 400:
            dead.append("%s (%s)" % (ref[:70], code or "no answer"))
    if dead:
        probes.bad("live.thirdparty", "%d third-party resource(s) do not answer: %s"
                   % (len(dead), "; ".join(dead[:5])))
    else:
        probes.ok("live.thirdparty", "all %d third-party resource(s) answered" % len(set(third)))

    # The bar's two links are the agency's site and its status page, as the
    # profile names them. A profile with no agency has no bar to ask about.
    links = [link for link in (setting("agency.url"), setting("agency.status_url")) if link]
    bad_links = []
    for link in links:
        code, _ = http(link, timeout=15)
        if not code or code >= 400:
            bad_links.append("%s (%s)" % (link, code or "no answer"))
    if not links:
        probes.bad("chrome.links", "the profile names no agency links (agency.url, "
                                   "agency.status_url), so the bar's links were never read")
    elif bad_links:
        probes.bad("chrome.links", "the bar's links do not resolve: %s" % "; ".join(bad_links))
    else:
        probes.ok("chrome.links", "both links the bar carries answered" if len(links) == 2
                  else "the link the bar carries answered")

    browser = next((p for p in CHROME_PATHS if Path(p).exists()), None)
    if not browser:
        probes.bad("live.console", "no Chromium binary on this machine, so the console was never "
                                   "read. Install one, run this from a machine that has one, or "
                                   "dispute the gate with what you observed in a browser.")
        return
    args = [browser, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--virtual-time-budget=8000", "--enable-logging=stderr", "--dump-dom"]
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        args.append("--proxy-server=%s" % proxy)
    code, out, err = sh(*args, url, timeout=90)
    if re.search(r"\bERR_[A-Z_]{4,}", out or ""):
        reason = re.search(r"\bERR_[A-Z_]{4,}", out).group(0)
        probes.bad("live.console", "the browser never loaded the page (%s), so the console was "
                                   "never read. A clean console cannot be claimed from a failed "
                                   "navigation - run this where the browser can reach the site."
                   % reason)
        return
    text = err or ""
    # Chromium logs the page's own console under an ERROR:CONSOLE tag. Every
    # other ERROR line on stderr is the browser process talking about itself -
    # dbus, udev, the sandbox, the GPU - and says nothing about the page.
    errors = [line for line in text.splitlines()
              if re.search(r":ERROR:CONSOLE|Uncaught", line)]
    if errors:
        probes.bad("live.console", "%d console error(s) on load: %s"
                   % (len(errors), " | ".join(e[-120:] for e in errors[:4])))
    else:
        probes.ok("live.console", "the page loaded with no console errors")


# ---------------------------------------------------------------- first paint


OUT_DIRS = ("dist", "build", "out", ".output/public", "public/build", "_site")
STYLESHEET_LINK = re.compile(r"<link\b[^>]*>", re.I)
HREF_ATTR = re.compile(r'href=["\']([^"\']+)["\']', re.I)
CLASS_ATTR = re.compile(r'class=["\']([^"\']*)["\']', re.I)
# A class selector, with Tailwind's escapes intact: `.min-h-\[380\%\]` is one name.
SELECTOR_CLASS = re.compile(r"\.((?:\\.|[A-Za-z0-9_-])+)")


def defined_classes(css):
    """Every class a stylesheet defines, read off its selectors.

    Declaration blocks come out first so a colour, a url() or a length can never
    be mistaken for a selector, and the escapes a bundler writes are undone so the
    name matches the one in the markup.
    """
    selectors = re.sub(r"\{[^{}]*\}", " ", css)
    return {re.sub(r"\\(.)", r"\1", m.group(1)) for m in SELECTOR_CLASS.finditer(selectors)}


def linked_sheets(html, page, out):
    """The stylesheet files a document links, as paths inside the output tree."""
    found = []
    for tag in STYLESHEET_LINK.finditer(html):
        if 'rel="stylesheet"' not in tag.group(0).lower().replace("'", '"'):
            continue
        href = HREF_ATTR.search(tag.group(0))
        if not href or "://" in href.group(1):
            continue
        target = href.group(1).split("?")[0]
        path = (out / target.lstrip("/")) if target.startswith("/") else (page.parent / target)
        found.append(path.resolve())
    return found


def markup_classes(html):
    names = set()
    for match in CLASS_ATTR.finditer(html):
        names.update(match.group(1).split())
    return names


def build_output(root):
    """The directory the project's build writes, rebuilt from the current source.

    A build sitting on disk answers for whatever the tree held when it ran, which
    is the one thing this probe cannot take on trust: a stale output reports a
    fixed page as broken and a broken one as fixed. So the build runs, unless the
    project has no build to run and serves HTML it keeps in the tree.
    """
    package = as_json((Path(root) / "package.json").read_text()) if (
        Path(root) / "package.json").is_file() else None
    buildable = bool((package or {}).get("scripts", {}).get("build"))
    if buildable:
        code, _, err = sh("npm", "run", "build", cwd=root, timeout=900)
        if code:
            tail = err.strip().splitlines()[-1] if err.strip() else "exit %d" % code
            return None, "the project's own build failed, so no HTML could be read: %s" % tail
    for name in OUT_DIRS:
        candidate = Path(root) / name
        if candidate.is_dir() and any(candidate.rglob("*.html")):
            return candidate, ""
    return None, ("the build wrote no HTML under %s" % ", ".join(OUT_DIRS) if buildable
                  else "no build script to run and no HTML under %s" % ", ".join(OUT_DIRS))


def probe_paint(probes, root, url=None):
    """Whether each built page links the stylesheets its own markup needs.

    A page rendered to HTML at build time paints before a line of JavaScript runs,
    so a stylesheet the bundler split into an async chunk dresses nothing until
    that chunk arrives. The markup is already on screen by then, undressed. The
    test is per page and needs no browser: a class in the delivered markup that
    some other sheet in the same build defines, and none of the sheets this page
    links does, is markup that paints bare.
    """
    out, why = build_output(root)
    if not out:
        probes.bad("paint.stylesheets", why)
        return
    sheets = {path.resolve(): defined_classes(path.read_text(errors="replace"))
              for path in out.rglob("*.css")}
    if not sheets:
        probes.ok("paint.stylesheets", "%s carries no stylesheets, so nothing can be "
                                       "left out of a page" % out.name)
        return
    pages = sorted(out.rglob("*.html"))[:400]
    undressed = []
    for page in pages:
        html = page.read_text(errors="replace")
        used = markup_classes(html)
        if not used:
            continue
        linked = [path for path in linked_sheets(html, page, out) if path in sheets]
        covered = set().union(*(sheets[path] for path in linked)) if linked else set()
        for path, defined in sheets.items():
            if path in linked:
                continue
            bare = (used & defined) - covered
            if bare:
                undressed.append((page.relative_to(out), path.name, sorted(bare)))
    if undressed:
        detail = "; ".join("/%s needs %s for %s" % (row[0].as_posix().replace("index.html", ""),
                                                    row[1], ", ".join(row[2][:3]))
                           for row in undressed[:4])
        probes.bad("paint.stylesheets", "%d of %d built page(s) paint markup no linked "
                                        "stylesheet dresses: %s"
                                        % (len(undressed), len(pages), detail))
        return
    probes.ok("paint.stylesheets", "all %d built page(s) link every stylesheet their own "
                                   "markup uses, across %d sheet(s)" % (len(pages), len(sheets)))


# ---------------------------------------------------------------- env, deps, observability


ENV_REF = re.compile(r"(?:process\.env|import\.meta\.env)\.([A-Z][A-Z0-9_]{2,})"
                     r"|(?:process\.env|import\.meta\.env)\[['\"]([A-Z][A-Z0-9_]{2,})['\"]\]")

SKIP = {"node_modules", ".git", "dist", "build", ".next", ".vercel", "__pycache__", ".venv"}


def referenced(root):
    names = set()
    for path in Path(root).rglob("*"):
        if set(path.parts) & SKIP or not path.is_file():
            continue
        if path.suffix.lower() not in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue",
                                       ".svelte", ".astro", ".html"}:
            continue
        try:
            text = path.read_text(errors="replace")[:400000]
        except OSError:
            continue
        for match in ENV_REF.finditer(text):
            names.add(match.group(1) or match.group(2))
    return sorted(names)


SYSTEM_ENV = re.compile(r"^(CI|VERCEL|VERCEL_(ENV|URL|TARGET_ENV|BRANCH_URL|REGION|DEPLOYMENT_ID"
                        r"|PROJECT_ID|PROJECT_PRODUCTION_URL|SKEW_PROTECTION_ENABLED"
                        r"|AUTOMATION_BYPASS_SECRET|OIDC_TOKEN|GIT_[A-Z_]+))$")


def probe_env(probes, root, url=None):
    token = env("VERCEL_TOKEN")
    link = vercel_link(root)
    project = env("VERCEL_PROJECT_ID") or link.get("projectId", "")
    team = env("VERCEL_TEAM_ID") or link.get("orgId") or None
    want = referenced(root)
    if not token or not project:
        probes.need("env.coverage", "VERCEL_TOKEN and a linked project",
                    "the %d variable(s) the code reads were never compared against what is set"
                    % len(want))
        return
    status, body = vercel_api("/v10/projects/%s/env?decrypt=false" % project, token, team)
    rows = (as_json(body) or {}).get("envs") if status == 200 else None
    if rows is None:
        probes.bad("env.coverage", "the environment listing returned %s" % status)
        return
    have = {}
    for row in rows:
        for target in (row.get("target") or []):
            have.setdefault(target, set()).add(row.get("key"))
    # Vercel's own system variables are never listed as project variables; they
    # arrive at build and run time whenever the project exposes them.
    pstatus, pbody = vercel_api("/v9/projects/%s" % project, token, team)
    if pstatus == 200 and (as_json(pbody) or {}).get("autoExposeSystemEnvs"):
        want = [name for name in want if not SYSTEM_ENV.match(name)]
    gaps = []
    for target in ("development", "preview", "production"):
        missing = sorted(set(want) - have.get(target, set()))
        if missing:
            gaps.append("%s is missing %d: %s" % (target, len(missing), ", ".join(missing[:8])))
    if gaps:
        probes.bad("env.coverage", "; ".join(gaps))
    else:
        probes.ok("env.coverage", "all %d referenced variable(s) are set for development, "
                                  "preview and production" % len(want))


def probe_deps(probes, root, url=None):
    if not (Path(root) / "package.json").is_file():
        for name in ("deps.audit", "deps.deprecated"):
            probes.bad(name, "no package.json at %s" % root)
        return
    code, out, err = sh("npm", "audit", "--json", cwd=root, timeout=180)
    report = as_json(out)
    if report is None:
        probes.bad("deps.audit", "npm audit did not return JSON: %s" % (err or out)[:150])
    else:
        counts = ((report.get("metadata") or {}).get("vulnerabilities") or {})
        high = int(counts.get("high", 0)) + int(counts.get("critical", 0))
        if high:
            worst = sorted((report.get("vulnerabilities") or {}).items(),
                           key=lambda kv: kv[0])[:6]
            probes.bad("deps.audit", "%d advisory(ies) at high or critical: %s"
                       % (high, ", ".join(name for name, _ in worst)))
        else:
            probes.ok("deps.audit", "npm audit reports nothing at high or critical across %s "
                                    "dependency(ies)" % counts.get("total", "the tree"))

    package = json.loads((Path(root) / "package.json").read_text())
    direct = dict(package.get("dependencies") or {})
    direct.update(package.get("devDependencies") or {})
    retired = []
    for name in sorted(direct)[:60]:
        status, body = http("https://registry.npmjs.org/%s" % name.replace("/", "%2f"),
                            timeout=15)
        data = as_json(body) if status == 200 else None
        if not data:
            continue
        latest = ((data.get("dist-tags") or {}).get("latest") or "")
        note = ((data.get("versions") or {}).get(latest) or {}).get("deprecated")
        if note:
            retired.append("%s (%s)" % (name, str(note)[:60]))
    if retired:
        probes.bad("deps.deprecated", "%d direct dependency(ies) are deprecated upstream: %s"
                   % (len(retired), "; ".join(retired[:5])))
    else:
        probes.ok("deps.deprecated", "none of the %d direct dependencies is deprecated upstream"
                  % len(direct))


# The uptime feed and the public board it backs, and the queue a report becomes
# a ticket in: the profile's agency.status_feed, agency.status_url and
# github.tickets.
STATUS_FEED = setting("agency.status_feed") or ""
STATUS_BOARD = setting("agency.status_url") or "the status board"
TICKETS = setting("github.tickets") or ""


def probe_observe(probes, root, url=None):
    host = re.sub(r"^https?://", "", url or "").split("/")[0]
    slug = slug_of(root)
    if not STATUS_FEED:
        for name in ("observe.collector", "observe.monitor", "observe.board"):
            probes.bad(name, "the profile names no status feed (agency.status_feed), so there "
                             "is no board to look for the site on")
    elif not host:
        for name in ("observe.collector", "observe.monitor", "observe.board"):
            probes.bad(name, "no --url given, so there is no site to look for on the boards")
    else:
        status, body = http(STATUS_FEED, timeout=25)
        feed = as_json(body) if status == 200 else None
        if feed is None:
            for name in ("observe.collector", "observe.monitor", "observe.board"):
                probes.bad(name, "the status feed answered %s, so nothing could be confirmed"
                           % status)
        else:
            text = json.dumps(feed)
            stem = host.replace("www.", "")
            if stem in text:
                probes.ok("observe.monitor", "%s has an entry in the uptime feed" % stem)
                probes.ok("observe.board", "%s is listed on %s" % (stem, STATUS_BOARD))
            else:
                probes.bad("observe.monitor", "%s is not in the uptime feed - nothing notices "
                                              "when it goes down between visits" % stem)
                probes.bad("observe.board", "%s does not appear on %s" % (stem, STATUS_BOARD))
            sites = feed.get("sites") if isinstance(feed, dict) else None
            known = [str(s.get("name", "")) for s in sites] if isinstance(sites, list) else []
            if stem in text or any(stem in n or n in stem for n in known if n):
                probes.ok("observe.collector", "the collector knows %s" % stem)
            else:
                probes.bad("observe.collector", "the collector has no entry for %s, so a report "
                                                "arriving from it is dropped rather than "
                                                "attributed" % stem)
    probes.bad("observe.roundtrip",
               "a report has to be made to travel the path; nothing here can make one. Throw an "
               "error in a browser on %s, follow it to Discord and to a ticket in "
               "%s, then answer this gate disputed with what arrived and "
               "when. A site is not onboarded until one has."
               % (url or "the deployed site", TICKETS or "the ticket queue"))


# ---------------------------------------------------------------- entry point


FAMILIES = {
    "supabase": probe_supabase, "clerk": probe_clerk, "stripe": probe_stripe,
    "vercel": probe_vercel, "repo": probe_repo, "live": probe_live, "env": probe_env,
    "deps": probe_deps, "observe": probe_observe, "paint": probe_paint,
}


def parse(argv):
    out, key = {}, None
    for token in argv:
        if token.startswith("--"):
            key = token[2:]
            out.setdefault(key, [])
        elif key:
            out[key].append(token)
    return out


# A reading written under a name another run is also writing is a reading two
# workers share: the second to arrive reads the first's numbers as its own. The
# window is wide enough to cover a slow sweep taking its own readings.
PROBE_COLLISION_SECONDS = 3600


def default_probe_out(kind):
    """A path no concurrent run holds."""
    return (Path(tempfile.gettempdir())
            / ("%s-probe-%d-%s.json" % (kind, os.getpid(), time.strftime("%H%M%S"))))


def guarded_probe_out(target, force=False):
    """The path to write, refusing one a recent reading already holds."""
    path = Path(target).expanduser()
    if path.exists() and not force:
        age = time.time() - path.stat().st_mtime
        if age < PROBE_COLLISION_SECONDS:
            raise SystemExit(
                "%s holds a reading written %d minute(s) ago, and overwriting it takes "
                "the numbers out from under whatever is reading it. Name a path of this "
                "run's own, or pass --force." % (path, int(age // 60)))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    verb, flags = sys.argv[1], parse(sys.argv[2:])
    root = str(Path((flags.get("root") or ["."])[0]).expanduser().resolve())
    url = (flags.get("url") or [""])[0]
    families = list(FAMILIES) if verb == "all" else [verb]
    unknown = [f for f in families if f not in FAMILIES]
    if unknown:
        print("unknown probe %r. One of: all, %s" % (unknown[0], ", ".join(sorted(FAMILIES))),
              file=sys.stderr)
        return 2
    probes = Probes()
    for name in families:
        try:
            FAMILIES[name](probes, root, url)
        except Exception as exc:                  # one family failing must not lose the rest
            probes.bad("%s.error" % name, "the %s probe raised %s" % (name, exc))
    payload = {"taken": now(), "root": root, "url": url, "probes": dict(probes)}
    text = json.dumps(payload, indent=2)
    target = guarded_probe_out((flags.get("out") or [""])[0]
                               or default_probe_out("stack"), "force" in flags)
    target.write_text(text)
    out = str(target)
    print("Filed %d probe result(s) to %s\n" % (len(probes), target))
    for name in sorted(probes):
        row = probes[name]
        print("  %-3s %-24s %s" % ("ok" if row["ok"] else "X", name, row["detail"][:110]))
    failing = [n for n, r in probes.items() if not r["ok"]]
    print("\n%d probed, %d failing." % (len(probes), len(failing)))
    if not out:
        print("\n%s" % text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
