#!/usr/bin/env python3
"""Store a credential in the vault from your own terminal, typed where no
session can see it.

A credential handed to a session is written into its transcript, and the
vault's own put tool can only be reached by a session, so a secret that has to
be seeded by hand comes in here instead: the value is read without echo (or
from stdin when it is piped) and goes straight to the vault over the
launcher's stdio, rendered nowhere.

    vault-put.py ms-graph-mail-secret
    vault-put.py ms-graph-mail-box --description "the mailbox rh-mail reads" < address.txt

Putting a name again replaces its value.
"""

import argparse
import getpass
import importlib.util
import sys
from pathlib import Path


def vault():
    """The with-credential.py vault client, so both talk to the launcher alike."""
    path = Path(__file__).resolve().parent / "with-credential.py"
    spec = importlib.util.spec_from_file_location("with_credential", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Vault()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name")
    parser.add_argument("--description", default="")
    parser.add_argument("--provider", default="")
    args = parser.parse_args()

    if sys.stdin.isatty():
        value = getpass.getpass("%s: " % args.name)
    else:
        value = sys.stdin.read().strip()
    if not value:
        sys.exit("nothing typed; %s left as it was" % args.name)

    store = vault()
    try:
        fields = {"name": args.name, "secret": value}
        if args.description:
            fields["description"] = args.description
        if args.provider:
            fields["provider"] = args.provider
        result = store._call("tools/call", {"name": "credential_put", "arguments": fields})
        if result.get("isError"):
            sys.exit("the vault refused %s" % args.name)
    finally:
        value = None
        store.close()
    print("%s stored" % args.name)


if __name__ == "__main__":
    main()
