# Adding a site to error reporting

Seven steps. A site is only onboarded when the last one passes: a real error
thrown in a browser on the live site reaches Slack, opens a ticket, and shows
on the status page. Anything short of that is half-done, and a half-onboarded
site looks healthy while reporting nothing.

## What the run needs before starting

- The site's repository, and which file its `<head>` lives in — usually
  `index.html` at the root, `public/index.html` for a Create React App project.
- The domain the site actually serves on, and every other origin it answers
  from (apex and `www`, plus any second brand domain pointed at the same
  deployment).
- SSH to the Pi (`voice-host` on the LAN, `sunday` over Tailscale — see the `ssh`
  skill).

## One list behind the page and the board

The public status page and the site board in Slack both read the reporter's
`status.json`, which is built from the uptime monitor's site list. A site enters
that list once, in step 4, and appears on both within two minutes under the name
given there. There is no second place to name it and no second list to keep up,
which is what stops the two from drifting apart. Step 6 checks that they did not.

## 1. Put the snippet in the site

Copy [`snippet/reporter.html`](../snippet/reporter.html) into the site's
`<head>`, immediately before `</head>`, and replace `__SITE__` with the site's
display name. That name is the join key for everything downstream — the ticket
title, the status page row, the Slack messages — so it has to match the name
used in steps 3 and 4 exactly.

The snippet reports uncaught errors, unhandled promise rejections,
`console.error`, `console.warn`, `console.trace` and failed `console.assert`
calls, failed and 5xx `fetch` calls, and resource load failures. Each one is
reported and then swallowed, so the client opening the console on their own site
sees nothing. It caps itself at ten reports per page load, and it never reports
its own requests to the collector.

To work on the site with the console back, load it once with `?console=on`; that
browser keeps the setting until `?console=off`. Without the flag,
`__reporter.replay()` returns the same errors from the live page.

Do not bundle it, and do not move it into a module: it has to be inline and
first so that it is listening before the app's own code runs.

## 2. Land that change and get it live

The snippet does nothing until it is in production. Land it the way that
repository lands anything, then confirm the built page serves it:

```bash
curl -sL https://<domain>/ | grep -c "voice-host.example.ts.net/report"
```

A minified build renames the snippet's local variables, so grep for the
endpoint rather than for any identifier inside it.

## 3. Let the collector accept the site

The reporter refuses reports from an origin it does not know. Add every origin
the site serves on — apex, `www`, and any additional brand domain — to
`allowed_origins`, and map the display name to its repository in `repo_map` so
tickets say which repository to fix:

```bash
ssh voice-host "python3 - <<'PY'
import json
path = '/home/mtoa/error-reporter/config.json'
config = json.load(open(path))
config['allowed_origins'] += ['https://example.com', 'https://www.example.com']
config['repo_map']['Example Site'] = 'Acme/example-com'
json.dump(config, open(path, 'w'), indent=2)
PY
sudo systemctl restart error-reporter"
```

`repo_map` matters more than it looks: a ticket whose repository is unknown is
one an agent cannot act on, and it will be closed as unreachable rather than
fixed.

## 4. Watch the site from outside

This is where the site is registered. The monitor checks it from outside so a
site that stops answering raises an alert instead of waiting for a visitor to
notice, and the name and URL given here are what the status page and the Slack
site board both show:

```bash
ssh voice-host "python3 - <<'PY'
import json
path = '/home/mtoa/uptime-monitor/config.json'
config = json.load(open(path))
config['sites'].append({'name': 'Example Site', 'url': 'https://example.com/'})
json.dump(config, open(path, 'w'), indent=2)
PY"
```

Use the canonical URL — the one that answers `200` directly rather than
redirecting. If the site depends on a backend of its own, add that backend's
health endpoint as a second entry with the status it answers unauthenticated
requests with, so a paused backend is caught as an outage rather than surfacing
later as a login failure:

```json
{ "name": "Example Site Backend", "url": "https://<ref>.supabase.co/auth/v1/health", "accept": [401] }
```

Entries whose name ends in `Backend` are watched but kept off the page and the
board. A site that should stay off both — an internal tool, a staging
deployment — goes in `status_exclude` in the reporter's config. It is still
checked every minute, and it still raises an alert on the attention board when
it stops answering; it is only not published.

The expiry monitor keeps its own list, because it reads a domain rather than a
page and a backend health endpoint has no registration to read:

```bash
ssh voice-host "python3 - <<'PY'
import json
path = '/home/mtoa/expiry-monitor/config.json'
config = json.load(open(path))
config['sites'].append({'name': 'Example Site', 'url': 'https://example.com/'})
json.dump(config, open(path, 'w'), indent=2)
PY"
```

One entry per site the client actually owns a name for. The monitor takes the
certificate off the host in the URL and the registration off the registrable
name underneath it, so `https://www.example.com/` covers both without a second
line.

## 5. Refresh the boards

All three boards refresh on their own within two minutes, and
immediately on the next report. To see the new site right away:

```bash
ssh voice-host "sudo systemctl start status-board queue-board attention-board"
```

The attention board stays silent unless the new site brings something that needs
a person: an unresolved ticket, a site not answering, or a claim an agent left
without a verdict. Nothing to see there is the correct result.

## 6. Check the page and the board against the feed

Confirm the site is in the feed, with the expected name and a status of
`operational`:

```bash
curl -s "https://voice-host.example.ts.net/status.json?t=$(date +%s)" \
  | python3 -c 'import json,sys; print([s for s in json.load(sys.stdin)["sites"] if "Example" in s["name"]])'
```

Then confirm the site board in Slack is showing the same list. The check reads
the message the board actually posted and compares it against the feed, naming
any site on one and not the other:

```bash
ssh voice-host "python3 /home/mtoa/status-board/status_board.py --check"
```

It exits non-zero on any difference, so the exit code is the verdict rather than
the wording. A board short of a site the page carries has usually failed to post
rather than fallen out of step: `journalctl -u status-board -n 20` says which.

The status page itself needs no change — it renders whatever the feed carries,
and polls every thirty seconds.

## 7. Prove it end to end

Nothing above counts until a real error travels the whole path. Open the live
site in a browser and throw one:

```js
setTimeout(() => { throw new Error("Onboarding check for Example Site") }, 100)
```

The console stays empty while it travels, which is the other half of the check:
an error that shows up in Slack and also in the browser is only half caught.
`__reporter.replay()` on that page returns the thrown error, so an empty console
and an empty replay together mean the snippet never ran rather than that the
page is clean.

Then confirm, in order:

1. Slack shows the error.
2. A ticket exists in this repository's issues carrying the site's name.
3. The queue board lists it, and the status page shows the site amber within a
   minute. The site board turns amber with it; `--check` passing is what says
   the two agree.

Close the test ticket, so the queue stays honest.

## When a site is more than one deployment

A site whose pages are served from somewhere other than its own repository —
built from a released artifact, say, rather than from a checkout — cannot be
onboarded by editing a checkout alone. The snippet has to enter through whatever
produces the deployed pages, and gating it to the hosted domain keeps it out of
self-hosted copies. Treat that as its own piece of work rather than forcing it
through these steps.
