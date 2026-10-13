# The profile

`~/.claude/profile/mtoa.json` is the one place your own names live. Every tool,
hook, host service and the voice server reads it through
`~/.claude/tools/mtoa_config.py`, and a key you leave empty turns off what needs it.

The file is looked for in this order, and the first that exists wins:

1. `$MTOA_CONFIG`, an explicit path
2. `~/.claude/profile/mtoa.json`
3. `~/.config/mtoa/mtoa.json`, for a host that holds no `~/.claude`

```bash
~/.claude/tools/mtoa_config.py get host.url
~/.claude/tools/mtoa_config.py show
```

## Keys

| Key | What it is for |
| :--- | :--- |
| `user.name`, `user.email` | Your name, used in prompts, and the only address routine mail goes to. |
| `user.pronouns` | `subject`, `object`, `possessive`, for prompts that talk about you. Default they/them/their. |
| `user.git_name`, `user.git_email` | The identity the host commits under when it fixes a ticket. |
| `assistant.name`, `assistant.wake_word` | What the voice assistant calls itself. The wake word model shipped here is trained on "Sunday"; another word needs its own microWakeWord model. |
| `github.owners` | The GitHub users or orgs whose repositories follow the `develop`/`main` flow. Anything else is left alone. |
| `github.mirror` | A private repository of yours that `config-sync.py` keeps `~/.claude` in step with. Empty means no sync. |
| `github.tickets` | The repository the host files error tickets in and the routines read them back from. |
| `github.elsewhere` | `{"owner/site": "other/build-repo"}` for a site whose deploys build from a different repository. |
| `host.ssh` | The ssh alias of your always-on machine. Empty means there is no host. |
| `host.url` | Its public URL, where `status.json` is served. |
| `host.user`, `host.home` | The account the services run under there, used to render the systemd units. |
| `host.lan_name`, `host.lan_ip`, `host.tailnet_ip` | How clients on your network and your tailnet reach the voice server. |
| `host.voice_port` | The voice server's port. Default 8794. |
| `host.status_name` | What the status page calls everything behind the sites. |
| `desk.ssh`, `desk.home` | Your own computer as the host reaches it, for the voice assistant's errands there. |
| `notify.mail_from`, `notify.mail_credential` | The sender routine mail goes out as, and the credential name of its Resend key. |
| `notify.slack_channel` | The default channel for the boards, if you use Slack. |
| `secrets.backend` | `env`, `keychain`, `1password` or `cryptofort`; see [credentials](credentials.md). |
| `secrets.env_file`, `secrets.op_vault`, `secrets.cryptofort_launcher` | Where each backend looks. |
| `agency.name`, `agency.domain`, `agency.url`, `agency.repo` | If you build sites for clients: your studio, its site and the checkout that holds it. |
| `agency.status_url`, `agency.status_feed` | Your public status page and its feed, which the stack checklist checks client sites link to. |
| `agency.bar` | The footer bar every client site carries: `name`, `marker`, `packages`, `components`, `component_name`, `accent_var`. Without it the bar gates do not apply. |
| `agency.vercel_preview_suffix` | The host suffix of your Vercel preview deployments, so the error reporter accepts reports from them. |
| `outreach.supabase_url` | A Supabase project whose outreach tables the attention board watches. |
| `desktop.keep_folders` | Folders `recent-folders-prune.py` never removes from the recent list. |
| `voice.pronounce` | Words the voice says wrong as written: `{"name", "pattern", "say", "example"}`, where the pattern's group `w` is the word. |
| `voice.home_ssid`, `voice.home_gateways` | Your home network, so the Mac ear knows when it is home and the cube has priority. |
| `routines.<name>` | Passages a routine's prompt pulls in by placeholder, for rules that are only yours. |

## Prompts

Prompts and gate texts carry placeholders that are filled from these keys:
`{user}`, `{assistant}`, `{he}`, `{him}`, `{his}`, `{git_name}`, `{git_email}`,
`{tickets}`, `{mirror}`, `{host_ssh}`, `{host_lan}`, `{host_url}`, `{host_home}`,
`{desk_ssh}`, `{agency}` and `{agency_domain}`. A capitalised placeholder gives a
capitalised value. See one filled in with:

```bash
~/.claude/tools/mtoa_config.py render ~/error-monitor/error-monitor.prompt.md error-monitor
```
