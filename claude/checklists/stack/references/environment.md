# Environment variables

Two failures live here and they point in opposite directions.

**A variable the code needs and the environment does not have.** Set in production and
missing in preview turns every branch deployment into a broken one, and the failure
reads as the branch rather than the setting. The check compares every name the source
reads against what is set for development, preview and production.

**A secret the environment has and the browser can read.** `VITE_`, `NEXT_PUBLIC_`,
`PUBLIC_` and `REACT_APP_` are instructions to the bundler to inline the value into the
client. The prefix does not protect the value; it publishes it.

## Why the built bundle is read, not the source

A value reaches the client through a re-export, a config object spread into a client
component, or a logging line — none of which reads like a leak in review. So ENV-03
greps the build output for key *shapes* rather than names: `sk_live_`, `whsec_`, a JWT,
a PEM block. Shapes, because the next one to leak arrives under a name nobody thought
to list.

Anything found there has already shipped. Rotation is the fix; deleting the line is
not.

## The example file and the vault

`.env.example` is the only description of what a fresh clone needs. It lists every name
and no values. A stale one turns a five-minute setup into an afternoon of guessing.

A secret that exists only in a Vercel settings page is one deleted project away from
being gone. Every one has a copy in CryptoFort, filed with `credential_put`. Never
paste a value into a gate note — the note is quoted into a report that gets read.
