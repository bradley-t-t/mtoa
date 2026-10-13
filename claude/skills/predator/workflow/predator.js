export const meta = {
  name: 'predator',
  description: 'Hunt catalogued AI-slop across a codebase, fix every instance, and let an independent agent validate each fix from Chrome screenshots or the diff before it counts as done',
  phases: [
    { title: 'Hunt', detail: 'one agent per catalog target, sweeping the whole tree' },
    { title: 'Fix', detail: 'one agent per file, so two agents never touch the same file' },
    { title: 'Guard', detail: 'build, lint and tests once per round' },
    { title: 'Validate', detail: 'an independent agent per instance, Chrome screenshot or diff evidence' },
  ],
}

const LIMIT = 10

const A = args || {}
const ROOT = A.root
const BASE = A.baseUrl
const BASE_REF = A.baseRef || 'develop'
const ROUTES = A.routes && A.routes.length ? A.routes : ['/']
const TARGETS = A.targets || []
const MAX_ROUNDS = A.maxRounds || 3
const VERIFY = A.verifyCmd || 'npm run build'

if (!ROOT) throw new Error('predator: args.root is required')
if (!TARGETS.length) throw new Error('predator: args.targets is empty')

// Hard cap on concurrent agents. The runtime caps at min(16, cpus-2); this
// holds the workflow to 10 regardless of the machine underneath.
async function pool(items, worker, limit) {
  const cap = Math.max(1, Math.min(limit === undefined ? LIMIT : limit, LIMIT))
  const out = new Array(items.length).fill(null)
  let cursor = 0
  const runners = []
  for (let r = 0; r < Math.min(cap, items.length); r++) {
    runners.push((async () => {
      for (;;) {
        const i = cursor++
        if (i >= items.length) return
        try { out[i] = await worker(items[i], i) } catch (e) { out[i] = null }
      }
    })())
  }
  await Promise.all(runners)
  return out
}

const HOUSE = [
  'House rules that override any instinct to improve things:',
  '- Change only what the finding names. No redesign, no refactor, no rename that was not asked for.',
  '- When something comes out, take it out. Do not swap in a replacement nobody asked for.',
  '- Never any emoji, in code, copy, comments or commit text.',
  '- UI labels are Title Case. Sentences, helper text and prose headlines are sentence case.',
  '- Never write copy that exists because of this conversation. The test: would this sentence be here if the product had always been this way?',
  '- Every SVG is transparent behind its drawing.',
  '- Match the surrounding file: its naming, its comment density, its idiom.',
  '- Do not create backup copies of files. Git already holds the tree.',
].join('\n')

const HUNT = {
  type: 'object',
  properties: {
    instances: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string', description: 'path relative to the repo root' },
          line: { type: 'number' },
          route: { type: 'string', description: 'the URL path this renders on, or empty for code-only' },
          surface: { type: 'string', enum: ['visual', 'code'] },
          evidence: { type: 'string', description: 'the exact text, class string or code that makes this true' },
          fix: { type: 'string', description: 'the specific change that would make the condition false' },
        },
        required: ['file', 'surface', 'evidence', 'fix'],
      },
    },
    searched: { type: 'string', description: 'what you actually searched, so a reader can see the hole if you missed one' },
    note: { type: 'string' },
  },
  required: ['instances', 'searched'],
}

const FIX = {
  type: 'object',
  properties: {
    applied: { type: 'array', items: { type: 'string' }, description: 'instance ids you actually changed code for' },
    skipped: {
      type: 'array',
      items: {
        type: 'object',
        properties: { id: { type: 'string' }, reason: { type: 'string' } },
        required: ['id', 'reason'],
      },
    },
    summary: { type: 'string' },
  },
  required: ['applied', 'summary'],
}

const GUARD = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    command: { type: 'string' },
    failures: {
      type: 'array',
      items: {
        type: 'object',
        properties: { file: { type: 'string' }, message: { type: 'string' } },
        required: ['message'],
      },
    },
    output_tail: { type: 'string' },
  },
  required: ['ok', 'failures'],
}

const VERDICT = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['pass', 'fail'] },
    reason: { type: 'string', description: 'what you observed, not what you assume' },
    evidence: { type: 'string', description: 'the screenshot you took and what was in it, or the diff hunk and the command you re-ran' },
    regression: { type: 'boolean', description: 'true if the fix broke something else you could see' },
  },
  required: ['verdict', 'reason', 'evidence'],
}

function huntPrompt(t) {
  return [
    `You are hunting one specific condition across the codebase at ${ROOT}.`,
    '',
    `CATALOG ITEM #${t.n} (${t.section}):`,
    `  ${t.text}`,
    '',
    'Find every real instance of exactly that condition. Nothing adjacent, nothing that merely reminds you of it.',
    '',
    'How to work:',
    `- Read broadly first. Search source, styles, config and content under ${ROOT}. Skip node_modules, dist, build, .git, coverage and lockfiles.`,
    '- Ground every instance in a file and, where you can, a line. An instance you cannot point at does not exist.',
    `- Mark it "visual" only if it is observable in a rendered page, and then name the route it renders on. The routes that exist here are: ${ROUTES.join(', ')}. Mark everything else "code".`,
    '- For each instance write the specific change that makes the condition false. Not "improve this" - the actual edit.',
    '',
    'Returning zero instances is a correct and useful answer. Do not manufacture one to look productive.',
    'Do not edit anything. This phase only looks.',
    '',
    'In `searched`, say which directories and patterns you actually covered, so a reader can see what you did not reach.',
  ].join('\n')
}

function fixPrompt(file, group, round) {
  const lines = group.map(g => {
    const head = `- id ${g.id} | catalog #${g.target.n}: ${g.target.text}`
    const where = `  line ${g.line || '?'} | evidence: ${g.evidence}`
    const plan = `  intended fix: ${g.fix}`
    const hist = g.history.length
      ? '  previous attempts that were REJECTED by a validator:\n' +
        g.history.map((h, i) => `    attempt ${i + 1}: ${h}`).join('\n')
      : ''
    return [head, where, plan, hist].filter(Boolean).join('\n')
  }).join('\n')
  return [
    `Fix the following in ONE file: ${ROOT}/${file}`,
    round > 1 ? `\nThis is round ${round}. Earlier attempts on these were rejected. Read why, and do something different rather than repeating the same edit.\n` : '',
    '',
    lines,
    '',
    'You own this file for this round. No other agent is touching it. Do not edit any other file unless the fix is impossible without it, and if you must, say so in the summary.',
    '',
    HOUSE,
    '',
    'A fix that only moves the problem elsewhere is not a fix. A fix that deletes the feature to make the condition false is not a fix either - the page must still do what it did.',
    'Make the edits now. Do not ask for confirmation. Do not commit, push, or run git checkout.',
    'Report which instance ids you actually changed code for, and skip with a reason any where the condition turns out not to hold.',
  ].join('\n')
}

function guardPrompt(round, files) {
  return [
    `Round ${round} edits are in. Prove the tree is still sound.`,
    '',
    `cd ${ROOT} and run: ${VERIFY}`,
    'If the repo has a lint script, run that too. If it has a test script, run that too.',
    '',
    'Files touched this round:',
    ...files.map(f => `  ${f}`),
    '',
    'Report ok=true only if everything you ran exited zero. On failure, attribute each error to a file where the output names one, and include the last 40 lines of output.',
    'Do not fix anything. You are the instrument, not the repair.',
  ].join('\n')
}

function visualPrompt(inst) {
  const route = inst.route && inst.route.trim() ? inst.route.trim() : '/'
  const url = BASE.replace(/\/$/, '') + (route.startsWith('/') ? route : '/' + route)
  return [
    'You are an adversarial validator. Your job is to REFUSE this fix unless you can see with your own eyes that it holds.',
    '',
    `CATALOG CONDITION #${inst.target.n}: ${inst.target.text}`,
    `Claimed instance: ${inst.file}${inst.line ? ':' + inst.line : ''}`,
    `Original evidence: ${inst.evidence}`,
    `The fix that was applied: ${inst.fix}`,
    '',
    'Load the Chrome tools with ToolSearch, in ONE call:',
    '  ToolSearch query "select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__read_console_messages,mcp__claude-in-chrome__resize_window"',
    '',
    'Then:',
    `1. Navigate the existing tab to ${url}. Reuse the tab you are given - do not open new windows.`,
    '2. Screenshot it at desktop width. Look at the screenshot, not at the code.',
    '3. Resize to the mobile preset, reload, screenshot again.',
    '4. Read the console. New errors are a regression.',
    '5. Put the window back to the desktop preset before you finish.',
    '',
    'Then answer one question: is the catalog condition still true on this page?',
    '',
    'Rules for your verdict:',
    '- pass means you looked at a screenshot and the condition is gone, and nothing else on the page visibly broke.',
    '- fail means it is still there, or you could not get a screenshot, or something else broke.',
    '- If you are uncertain, fail. Uncertainty is a fail, not a pass.',
    '- Describe what was in the screenshot in `evidence`. "It looks fine" is not evidence.',
    '- You may read the source to understand what changed, but the screenshot decides it.',
    'Do not edit any file.',
  ].join('\n')
}

function codePrompt(inst, guard) {
  return [
    'You are an adversarial validator. Your job is to REFUSE this fix unless the evidence forces you to accept it.',
    '',
    `CATALOG CONDITION #${inst.target.n}: ${inst.target.text}`,
    `Claimed instance: ${inst.file}${inst.line ? ':' + inst.line : ''}`,
    `Original evidence: ${inst.evidence}`,
    `The fix that was applied: ${inst.fix}`,
    '',
    'How to check:',
    `1. cd ${ROOT} and read the change: git diff ${BASE_REF} -- ${inst.file}`,
    '2. Re-run the search that would have found the original instance. If it still matches, the condition still holds.',
    `3. Sweep the rest of the tree for the same condition reintroduced elsewhere by this change.`,
    guard && !guard.ok
      ? `4. The round guard FAILED: ${(guard.failures || []).map(f => (f.file ? f.file + ': ' : '') + f.message).join(' | ')}. If this file is implicated, fail.`
      : '4. The round guard passed the build and tests, so you are judging the condition itself.',
    '',
    'Rules for your verdict:',
    '- pass means the condition is provably false now and nothing equivalent was introduced elsewhere.',
    '- fail means it still holds, or the fix papered over it, or it moved somewhere else.',
    '- If you are uncertain, fail.',
    '- Put the diff hunk and the exact command you re-ran in `evidence`.',
    'Do not edit any file.',
  ].join('\n')
}

// ---------------------------------------------------------------- hunt

phase('Hunt')
log(`Hunting ${TARGETS.length} catalog conditions across ${ROOT}, ${LIMIT} agents at most.`)

const hunts = await pool(
  TARGETS,
  t => agent(huntPrompt(t), { phase: 'Hunt', schema: HUNT, label: `hunt:#${t.n}` }),
  Math.min(LIMIT, TARGETS.length)
)

const open = []
const clean = []
let seq = 0
for (let i = 0; i < TARGETS.length; i++) {
  const t = TARGETS[i]
  const r = hunts[i]
  if (!r) { clean.push({ target: t, reason: 'hunter returned nothing' }); continue }
  const found = (r.instances || []).filter(x => x && x.file)
  if (!found.length) { clean.push({ target: t, reason: r.note || 'no instances found', searched: r.searched }); continue }
  for (const f of found) {
    seq++
    open.push({
      id: `${t.n}-${seq}`,
      target: t,
      file: f.file.replace(/^\.\//, ''),
      line: f.line,
      route: f.route || '',
      surface: f.surface === 'visual' ? 'visual' : 'code',
      evidence: f.evidence,
      fix: f.fix,
      history: [],
    })
  }
}

log(`${open.length} instances across ${new Set(open.map(i => i.file)).size} files. ${clean.length} conditions came back clean.`)

// ------------------------------------------------------- fix / validate loop

const passed = []
const unresolved = []
let queue = open

for (let round = 1; round <= MAX_ROUNDS && queue.length; round++) {
  const byFile = new Map()
  for (const inst of queue) {
    if (!byFile.has(inst.file)) byFile.set(inst.file, [])
    byFile.get(inst.file).push(inst)
  }
  const files = Array.from(byFile.entries())

  phase('Fix')
  log(`Round ${round}: ${queue.length} instances in ${files.length} files.`)
  await pool(
    files,
    ([file, group]) => agent(fixPrompt(file, group, round), {
      phase: 'Fix',
      schema: FIX,
      label: `fix:${file.split('/').slice(-1)[0]}`,
    }),
    LIMIT
  )

  phase('Guard')
  const guard = await agent(guardPrompt(round, files.map(f => f[0])), {
    phase: 'Guard',
    schema: GUARD,
    label: `guard:r${round}`,
  })
  if (guard && !guard.ok) log(`Round ${round} guard failed: ${(guard.failures || []).length} failures.`)

  phase('Validate')
  const visual = queue.filter(i => i.surface === 'visual')
  const code = queue.filter(i => i.surface !== 'visual')
  // Chrome is one browser: visual validators run one at a time. The code lane
  // takes the remaining slots so the two lanes together never exceed LIMIT.
  const [vres, cres] = await Promise.all([
    pool(visual, i => agent(visualPrompt(i), { phase: 'Validate', schema: VERDICT, label: `shot:${i.id}` }), 1),
    pool(code, i => agent(codePrompt(i, guard), { phase: 'Validate', schema: VERDICT, label: `check:${i.id}` }), visual.length ? LIMIT - 1 : LIMIT),
  ])

  const next = []
  const judge = (list, results) => {
    for (let i = 0; i < list.length; i++) {
      const inst = list[i]
      const v = results[i]
      if (v && v.verdict === 'pass' && !v.regression) {
        passed.push({ ...inst, verdict: v, round })
      } else {
        const why = v
          ? `${v.reason}${v.regression ? ' (also caused a regression)' : ''}`
          : 'validator produced no verdict'
        inst.history.push(`round ${round}: ${why}`)
        next.push(inst)
      }
    }
  }
  judge(visual, vres)
  judge(code, cres)

  log(`Round ${round}: ${passed.length} validated, ${next.length} back to the loop.`)
  queue = next
  if (round === MAX_ROUNDS && queue.length) {
    for (const inst of queue) unresolved.push(inst)
    log(`${queue.length} instances hit the ${MAX_ROUNDS}-round ceiling and are being reported unresolved rather than dropped.`)
  }
}

return {
  root: ROOT,
  targets: TARGETS,
  clean: clean.map(c => ({ n: c.target.n, text: c.target.text, reason: c.reason, searched: c.searched })),
  passed: passed.map(p => ({
    id: p.id, n: p.target.n, text: p.target.text, file: p.file, line: p.line,
    surface: p.surface, route: p.route, fix: p.fix, round: p.round,
    evidence: p.verdict.evidence, reason: p.verdict.reason,
  })),
  unresolved: unresolved.map(u => ({
    id: u.id, n: u.target.n, text: u.target.text, file: u.file, line: u.line,
    surface: u.surface, evidence: u.evidence, attempts: u.history,
  })),
  counts: {
    conditions: TARGETS.length,
    instances: open.length,
    validated: passed.length,
    unresolved: unresolved.length,
    clean: clean.length,
    rounds_allowed: MAX_ROUNDS,
    agent_limit: LIMIT,
  },
}
