---
name: predator
description: Hunt catalogued AI-slop through a codebase and kill it - conditions sampled from a 1000-item catalog, swept by parallel agents, fixed, then validated by an independent agent working from Chrome screenshots, with anything that fails handed back to a new agent, and the site's own copy rewritten in a second pass by subagents carrying the humanizer skill and whichever marketing skills the repo has surfaces for. Use on /predator, or "hunt the slop", "find the AI tells in this site", "de-slop this project", "make this stop looking generated".
---

# Predator

A generated site does not fail. It reads as finished and is wrong in a hundred
small ways nobody writes a ticket for. This hunts those, a handful at a time,
and does not accept its own word that any of them was fixed.

The catalog at `references/catalog.md` holds 1000 numbered conditions, each one
observable: you can look at a screen or a file and say yes or no. A run samples
from it, sweeps the whole tree for real instances, repairs them, and then makes
a different agent prove the condition is gone. Proof is a Chrome screenshot for
anything a reader can see, and the diff plus a re-run of the search for
everything else. A fix that cannot be proved goes back into the loop with a
fresh agent and the reason it was rejected.

Then the words. The catalog cannot see a sentence that reads like a chatbot
wrote it, so a second lane takes the copy apart with `humanizer` and the
marketing skills the repo has surfaces for, on the same terms: find it, rewrite
it, and let an agent that did not write it prove the page still says what it
said.

**This is an instruction to change the project, not to survey it.** A condition
found and not fixed is reported unresolved, never quietly recorded as passing.

## What it will not do

It runs unattended and does not stop to ask. The boundary is not a question, it
is a fence: predator edits and commits inside its own worktree branch and goes
no further. It never pushes, never opens or merges a pull request, never
deploys, never touches the database, Stripe, or anything that sends mail.
Landing is `/commit`. Releasing is `/merge`. Those stay yours.

The marketing skills reach further than the catalog does, and the fence does not
move for them. A skill that knows how to send, post, schedule or publish stops
at the file: the voice pass changes text in the worktree, and a campaign nobody
asked for is not one of its outputs.

## 0. The pieces

```bash
PRED="$HOME/.claude/skills/predator"
SELECT="$PRED/bin/select.py"
FLOW="$PRED/workflow/predator.js"
```

## 1. Resolve the target

The repo is the argument, or the checkout the session is in, or `agency.repo`
in that order. Everything below is one repo; a run does not straddle two.

```bash
ROOT=<absolute path to the checkout>
PROJECT=$(basename "$ROOT")
```

## 2. Cut the worktree

Off `develop`, never `main`. The primary checkout stays on `develop` and stays
clean.

```bash
cd "$ROOT" && git fetch origin develop --quiet && git worktree add \
  "$HOME/Development/worktrees/$PROJECT-predator" -b claude/predator-<short-slug> origin/develop
```

Work in that worktree from here. `ROOT` for every step below is the worktree
path, not the primary checkout.

## 3. Start the dev server and learn the routes

Validators shoot a running site, and it has to be the one carrying the edits, so
it is local rather than deployed. Use `.claude/launch.json` if the repo has one.

```bash
cd "$WORKTREE" && npm install --silent && (npm run dev &) && sleep 4
curl -sf -o /dev/null http://localhost:<port>/ && echo "dev server up"
```

Then read the routes out of the router, the sitemap, or whatever the repo uses
to enumerate pages. Pass the real list - a validator sent to a route that does
not exist reports a fail it cannot explain.

## 4. Choose the targets

Five by default. State per project means a second run covers new ground rather
than re-treading the first.

```bash
"$SELECT" select --project "$PROJECT" --count 5
"$SELECT" select --project "$PROJECT" --section 12          # one catalog section
"$SELECT" select --project "$PROJECT" --items 27,199,464    # named numbers
"$SELECT" status --project "$PROJECT"                       # coverage so far
```

`/predator 10`, `/predator section 16`, `/predator 27,199,464` map onto those.

## 5. Run the workflow

Ten agents at most, enforced inside the script rather than trusted to the
runtime. Hunt fans out one agent per condition; fix runs one agent per file so
two agents never hold the same file; a single guard runs the build and tests;
validation runs the visual lane one at a time, because there is one Chrome, with
the code lane taking the remaining slots.

```
Workflow({
  scriptPath: "<expanded $HOME>/.claude/skills/predator/workflow/predator.js",
  args: {
    root:      "<worktree path>",
    baseUrl:   "http://localhost:<port>",
    baseRef:   "origin/develop",
    routes:    ["/", "..."],
    targets:   <the targets array from select, verbatim>,
    maxRounds: 3,
    verifyCmd: "npm run build"
  }
})
```

Pass `targets` as the actual JSON array. A stringified one reaches the script as
a single string and the hunt phase throws.

The workflow returns `passed`, `unresolved`, `clean` and `counts`. Read them;
they are the report.

## 6. The voice pass

The catalog is about what a generated site looks like. It says nothing about
what the site says, and the copy is the louder tell - nobody reads a class
string, everybody reads the hero. So a second lane goes over the repository's
own words with `humanizer` and whichever marketing skills the repo has surfaces
for.

This lane is subagents, not a workflow. Which skills apply is not known until
the tree has been read, so the set cannot be an argument to a script, and a
skill works when the agent applying it has read the whole thing rather than a
paraphrase of it somebody folded into a prompt string.

It runs after the workflow returns, never beside it. Both lanes shoot
screenshots and there is one Chrome.

### Which skills

`humanizer` always, over every string a reader can see. Everything else earns
an agent only if the repo holds the surface it governs. No surface, no agent: a
`pricing` agent on a repo with no prices has nothing to do but invent some.

| What is in the tree | Skill |
| :--- | :--- |
| Hero, landing, feature or about copy | `copywriting`, `copy-editing` |
| A page carrying a call to action | `cro` |
| Titles, meta descriptions, headings, canonicals | `seo-audit` |
| JSON-LD or other structured data | `schema` |
| `llms.txt`, docs, anything meant to be cited | `ai-seo` |
| A pricing page or a plan table | `pricing` |
| A quote, package or service offer | `offers` |
| Templated email or SMS bodies | `emails`, `sms` |
| Post copy committed to the repo | `social` |
| A blog, a docs tree, or twenty pages of content | `content-strategy`, `site-architecture` |

Six agents at most, `humanizer` among them. Past that they overlap and start
editing each other's sentences.

### Read

One subagent per chosen skill, all of them launched in a single message so they
run at once. Each reads its own skill first and reports findings only.

```
Agent({
  subagent_type: "general-purpose",
  description: "<skill> sweep",
  prompt: "Invoke the <skill> skill with the Skill tool and read it in full before you look at anything else.
Then sweep <worktree> for every place the text a reader sees breaks that skill's guidance. Source, content
files, config that holds copy, and the metadata that renders into the page. Skip node_modules, dist, build,
.git and lockfiles.
Report each finding as: file, line, the exact string as it stands today, the exact string that replaces it,
and which part of the skill it answers to. A replacement you cannot write out is not a finding.
Do not edit anything. This stage only reads.
Say what you covered, so a reader can see what you did not reach."
})
```

The bar is the same as the hunt's: a finding is a string you can point at, and
zero findings is a real answer. A skill that comes back empty is a clean.

### Rewrite

Group the findings by file and give one subagent each file, so two agents never
hold the same file. Every prompt carries the house rules verbatim - they are the
`HOUSE` block in `workflow/predator.js`, and a copy agent that has not been
handed them will redesign the page it was sent to edit. Two of them bite harder
in copy than anywhere else:

- **Invent nothing.** Not a number, a customer, a claim, a testimonial, a
  guarantee or a date. Every fact in the new sentence was in the old one or in
  the repo. A rewrite that gets its punch from a statistic nobody can source is
  worse than the sentence it replaced.
- **Never write context-derived copy.** Nothing a visitor reads may exist
  because of this run. The test: would this sentence be here if the product had
  always been this way?

Say what the product does, in the repo's voice, and keep every claim the old
line made. A marketing skill that wants to send, post, publish or schedule
stops at the file - this lane changes text in the worktree and nothing else.

### Prove

One independent subagent per file changed, and it did not write the edit it is
judging. Copy is visual: the standard is the rendered page.

Load the Chrome tools in one `ToolSearch` call, the same select line the
workflow's visual validator uses, then navigate the existing Chrome tab to the route the file renders on, screenshot
it at desktop and at mobile, read the new text off the screenshot, and answer
whether the finding is gone and whether the page still claims what it claimed
before. Uncertain is a fail. Any invented fact is a fail, and the whole file
goes back for a second pass with the reason attached.

A string that renders nowhere - a meta description, an alt attribute, a
JSON-LD field - is proved on the diff plus the search that found it, re-run.

## 7. Record, then commit

```bash
echo '{"resolved":[<numbers fully validated>],"unresolved":[<numbers not>],"clean":[<numbers with no instances>]}' \
  | "$SELECT" record --project "$PROJECT" --root "$WORKTREE" \
      --branch "$(git -C "$WORKTREE" branch --show-current)" \
      --at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

A number counts as resolved only when every instance of it passed. One instance
short and the number is unresolved, because the condition still holds somewhere.

Then run the repo's own gates once more in the worktree and commit. Write as the
human author: no AI attribution, no emoji, `ci.yml` reads every commit and
refuses either.

```bash
cd "$WORKTREE" && npm run build && npm test && git add -A && git commit -m "<what changed, in the repo's voice>"
```

Stop there. Say the branch name and let the next move be asked for.

## 8. What to say at the end

Short. Per condition: the number, what it was, how many instances, and whether
it is dead. Then the unresolved ones with the reason the validator gave, because
that reason is the useful part - it is the thing three agents could not make
true.

Then the voice pass, in a line or two: which skills ran, how many strings
changed, and any file whose rewrite the prover sent back. It has no numbers of
its own, so name the pages instead. Then the branch.

Do not narrate the rounds. The rounds are visible in the workflow view while it
runs and are noise afterwards.

## Notes

- The catalog is stable. Numbers are never reassigned, because state, reports and
  `--items` all refer to conditions by number.
- Adding conditions means appending, at 1001 and up, inside the section that
  fits or a new one at the end.
- A hunter returning zero instances is the normal case for most conditions on a
  site that has been worked on. That is a clean, and it is recorded, so the same
  number is not hunted again next run.
- If the dev server will not start, the visual lane cannot validate anything.
  Fix the server first; do not let the run proceed and grade visual conditions
  on the code alone.
