# Credentials

No secret goes in the profile, in a file the setup tracks, or on a command line.
A tool that needs one is run through `with-credential.py`, which puts the value in
that one child process's environment and prints it nowhere:

```bash
~/.claude/tools/with-credential.py google-pagespeed-api-key=GOOGLE_PAGESPEED_API_KEY -- probe.py all
```

Each argument before `--` maps a credential's name to the variable the command
reads. A name the store does not hold is reported on stderr and left unset.

## Where they are kept

Set `secrets.backend` in the profile, or `MTOA_SECRETS_BACKEND` for one run.

**`env`** (the default): a file of `NAME=value` lines that only you can read.

```bash
mkdir -p ~/.config/mtoa
touch ~/.config/mtoa/secrets.env && chmod 600 ~/.config/mtoa/secrets.env
echo 'google-pagespeed-api-key=...' >> ~/.config/mtoa/secrets.env
```

A file anyone else can read is refused. `secrets.env_file` or
`MTOA_SECRETS_FILE` points somewhere else.

**`keychain`**: the macOS login Keychain, service `mtoa`, account the credential's name.

```bash
security add-generic-password -s mtoa -a google-pagespeed-api-key -w
```

**`1password`**: the 1Password CLI, read as `op://<vault>/<name>/credential`, where the
vault is `secrets.op_vault` (default `mtoa`).

**`cryptofort`**: the [CryptoFort](https://github.com/bradley-t-t/cryptofort) vault over
its MCP server, started by `secrets.cryptofort_launcher`
(default `~/.claude/cryptofort-mcp.sh`).

## Names the setup uses

| Name | Used by |
| :--- | :--- |
| `google-pagespeed-api-key` | The performance and Google checklists. |
| `resend-api-key` (or `notify.mail_credential`) | Routine mail from the host. |
| `elevenlabs-api-key` | The voice, when it speaks with ElevenLabs instead of the local model. |

The host's GitHub and Claude Code logins are their own: `gh auth login` and
`claude` signed in on the host.
