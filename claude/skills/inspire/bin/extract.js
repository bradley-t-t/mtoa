/*
 * The element extractor.
 *
 * Paste into mcp__claude-in-chrome__javascript_tool with SEL set to the target
 * selector. Returns three lanes, because matching an element means matching
 * three different things and only one of them is visible in a screenshot:
 *
 *   form      what it looks like standing still
 *   motion    what it does over time, and what starts it
 *   behavior  what it is to a keyboard, a screen reader and a pointer
 *
 * Cross-origin stylesheets throw on .cssRules. That is not a bug to route
 * around; it is a hole in the capture, so unreadable sheets are counted and
 * named. A capture reporting unreadable sheets has not seen the hover rules,
 * and the states have to be driven by hand instead - see references/capture.md.
 */
((SEL) => {
  const el = document.querySelector(SEL)
  if (!el) return { error: `no element matches ${SEL}` }

  // Curated rather than exhaustive. getComputedStyle carries ~340 properties and
  // 300 of them are the initial value; dumping all of them buries the dozen that
  // were actually decided.
  const FORM_PROPS = [
    'display', 'position', 'inset', 'z-index', 'box-sizing', 'overflow',
    'width', 'height', 'min-width', 'max-width', 'min-height', 'aspect-ratio',
    'margin', 'padding', 'gap', 'row-gap', 'column-gap',
    'grid-template-columns', 'grid-template-rows', 'grid-auto-flow',
    'flex-direction', 'flex-wrap', 'align-items', 'justify-content', 'flex',
    'font-family', 'font-size', 'font-weight', 'font-variation-settings',
    'font-feature-settings', 'line-height', 'letter-spacing', 'text-transform',
    'text-wrap', 'text-align', 'text-decoration', 'white-space', 'font-style',
    'color', 'background-color', 'background-image', 'background-size',
    'background-position', 'background-clip', '-webkit-text-fill-color',
    'border-width', 'border-style', 'border-color', 'border-radius',
    'outline', 'outline-offset', 'box-shadow', 'opacity', 'filter',
    'backdrop-filter', '-webkit-backdrop-filter', 'mix-blend-mode',
    'transform', 'transform-origin', 'perspective', 'will-change',
    'cursor', 'user-select', 'pointer-events', 'touch-action',
    'scroll-behavior', 'scroll-snap-type', 'scroll-snap-align',
    'clip-path', 'mask-image', 'isolation', 'contain',
  ]

  const MOTION_PROPS = [
    'transition-property', 'transition-duration', 'transition-timing-function',
    'transition-delay', 'transition-behavior',
    'animation-name', 'animation-duration', 'animation-timing-function',
    'animation-delay', 'animation-iteration-count', 'animation-direction',
    'animation-fill-mode', 'animation-play-state',
    'animation-timeline', 'animation-range', 'view-transition-name',
    'offset-path', 'offset-distance',
  ]

  const cs = getComputedStyle(el)
  const pick = (props) => {
    const out = {}
    for (const p of props) {
      const v = cs.getPropertyValue(p)
      if (v && v !== 'none' && v !== 'normal' && v !== 'auto' && v !== '0s' && v !== '0px') out[p] = v.trim()
    }
    return out
  }

  /* ---- stylesheet walk: the rules that matched, not just the result ---- */
  // Computed style flattens everything into one resting answer. The authored
  // rules are what say which of those values belong to :hover, which to a media
  // query, and which curve the author actually named.
  const sheets = { read: 0, blocked: [] }
  const matched = []      // rules whose selector this element satisfies
  const stateRules = []   // rules for :hover / :focus / [data-state] on it
  const keyframes = {}
  const STATE_RE = /:(hover|focus|focus-visible|focus-within|active|disabled|checked|target|open)\b|\[(data-|aria-)[^\]]*\]|::(before|after|backdrop|placeholder|marker|selection)\b/

  const baseSelector = (sel) =>
    sel.split(',').map((s) => s.trim().replace(STATE_RE, '').replace(/\s+/g, ' ').trim()).filter(Boolean)

  const walk = (rules, media) => {
    for (const r of rules) {
      if (r.type === CSSRule.MEDIA_RULE || r.type === CSSRule.SUPPORTS_RULE || r.constructor.name === 'CSSContainerRule' || r.constructor.name === 'CSSLayerBlockRule') {
        walk(r.cssRules || [], r.conditionText || r.media?.mediaText || media)
        continue
      }
      if (r.type === CSSRule.KEYFRAMES_RULE) {
        keyframes[r.name] = [...r.cssRules].map((k) => `${k.keyText} { ${k.style.cssText} }`)
        continue
      }
      if (r.type !== CSSRule.STYLE_RULE || !r.selectorText) continue
      // A rule whose selector is the universal one matches every element on the
      // page. It is framework preflight, it is identical for the element next to
      // this one, and left in it buries the twenty rules that were actually
      // written for this element.
      if (/^\s*\*(\s*,|\s*$)/.test(r.selectorText) || /^(html|body|:root)\b/.test(r.selectorText)) continue
      let hits = false
      try { hits = el.matches(r.selectorText) } catch { hits = false }
      const entry = { selector: r.selectorText, css: r.style.cssText, media: media || undefined }
      if (hits) { matched.push(entry); continue }
      // A state rule does not match at rest, so it has to be found by taking the
      // state off the selector and testing what is left.
      if (STATE_RE.test(r.selectorText)) {
        for (const b of baseSelector(r.selectorText)) {
          let bhit = false
          try { bhit = b && (el.matches(b) || el.querySelector(b)) } catch { bhit = false }
          if (bhit) { stateRules.push(entry); break }
        }
      }
    }
  }

  for (const sheet of document.styleSheets) {
    try { walk(sheet.cssRules || [], ''); sheets.read++ }
    catch { sheets.blocked.push(sheet.href || '(inline)') }
  }

  /* ---- custom properties in scope ---- */
  // The tokens, which are the thing worth carrying across. A raw #1a4ed8 tells
  // you nothing; --accent-lo used in nine places tells you the system.
  const tokens = {}
  // Framework plumbing wearing a custom property's clothes. Tailwind alone
  // declares ~50 of these on every element; they are machinery, not decisions,
  // and they crowd out the handful of tokens worth carrying across.
  const PLUMBING = /^--(tw|radix|chakra|mantine|mui|next|nuxt|ck|reach)-/
  const note = (name, authored) => {
    name = name.trim()
    if (PLUMBING.test(name)) return
    const resolved = cs.getPropertyValue(name).trim()
    if (!resolved && !authored) return
    if (!tokens[name] || (authored && !tokens[name].authored)) tokens[name] = { authored: authored || null, resolved }
  }
  // The authored rule text is where the tokens actually appear. Computed style
  // has already resolved every var() away, so scanning it finds nothing - which
  // is how the first cut of this collected 50 Tailwind internals and none of
  // the six tokens the element was really built from.
  for (const entry of [...matched, ...stateRules]) {
    for (const decl of entry.css.split(';')) {
      const declared = decl.match(/(--[\w-]+)\s*:\s*(.+)/)
      if (declared) note(declared[1], declared[2].trim())
      for (const ref of (decl.match(/var\(\s*(--[\w-]+)/g) || [])) note(ref.replace(/var\(\s*/, ''), null)
    }
  }

  /* ---- motion actually running, and what triggers it ---- */
  const running = (el.getAnimations ? el.getAnimations({ subtree: true }) : []).map((a) => ({
    kind: a.constructor.name,
    name: a.animationName || a.transitionProperty || null,
    duration: a.effect?.getTiming?.().duration,
    easing: a.effect?.getTiming?.().easing,
    delay: a.effect?.getTiming?.().delay,
    iterations: a.effect?.getTiming?.().iterations,
    playState: a.playState,
    timeline: a.timeline?.constructor?.name,
  }))

  // A reveal-on-scroll leaves fingerprints even though the observer itself is
  // unreadable: a state attribute that flips, a class added on entry, an inline
  // custom property carrying an index for the stagger.
  const staggerHints = []
  const kids = [...el.children].slice(0, 12)
  kids.forEach((k, i) => {
    const style = k.getAttribute('style') || ''
    const idx = style.match(/--[\w-]*(index|i|delay|order|stagger)[\w-]*\s*:\s*([^;]+)/i)
    const ks = getComputedStyle(k)
    const delay = ks.transitionDelay !== '0s' ? ks.transitionDelay : ks.animationDelay !== '0s' ? ks.animationDelay : null
    if (idx || delay) staggerHints.push({ child: i, inlineVar: idx?.[0] || null, delay })
  })

  const scrollDriven = [...new Set([
    ...(cs.animationTimeline && cs.animationTimeline !== 'auto' ? ['animation-timeline: ' + cs.animationTimeline] : []),
    ...(running.some((r) => r.timeline && r.timeline !== 'DocumentTimeline') ? ['ScrollTimeline in getAnimations()'] : []),
  ])]

  /* ---- behavior: what this is, not what it looks like ---- */
  const attrs = {}
  for (const a of el.attributes) if (a.name !== 'style') attrs[a.name] = a.value

  const focusables = [...el.querySelectorAll('a[href],button,input,select,textarea,[tabindex]:not([tabindex="-1"]),[contenteditable="true"]')]
  const behavior = {
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role') || el.tagName.toLowerCase(),
    attributes: attrs,
    // The aria-* set is the interaction contract written down. Reproducing the
    // look without it produces something that photographs the same and is not
    // the same component.
    aria: Object.fromEntries(Object.entries(attrs).filter(([k]) => k.startsWith('aria-') || k === 'role')),
    stateAttributes: Object.keys(attrs).filter((k) => /^(data-state|data-open|data-active|data-selected|data-side|open|checked|disabled|hidden|inert)$/.test(k)),
    focusable: el.tabIndex >= 0,
    focusableDescendants: focusables.length,
    focusOrder: focusables.slice(0, 10).map((f) => ({
      tag: f.tagName.toLowerCase(),
      type: f.getAttribute('type') || null,
      label: (f.getAttribute('aria-label') || f.textContent || '').trim().slice(0, 40),
      tabindex: f.getAttribute('tabindex'),
    })),
    inlineHandlers: [...el.attributes].filter((a) => a.name.startsWith('on')).map((a) => a.name),
    // Structure at a depth that shows the composition without pasting the page.
    skeleton: (function outline(node, depth) {
      if (depth > 3) return null
      return {
        tag: node.tagName.toLowerCase(),
        cls: (node.getAttribute('class') || '').slice(0, 120) || undefined,
        role: node.getAttribute('role') || undefined,
        children: [...node.children].slice(0, 8).map((c) => outline(c, depth + 1)).filter(Boolean),
      }
    })(el, 0),
  }

  const box = el.getBoundingClientRect()
  // Every number below describes an element that is actually laid out. A zero
  // box means it is display:none, behind a closed disclosure, or off at a
  // breakpoint this viewport is not at - and a capture of that is a capture of
  // nothing, so it is flagged rather than quietly returned as measurements.
  const warnings = []
  if (!box.width || !box.height) warnings.push(`element has a zero-area box (${box.width}x${box.height}) - it is not rendered at this viewport or state, so form and motion here describe nothing`)
  if (!innerWidth || !innerHeight) warnings.push('viewport reports 0x0 - the pane may be hidden; resize or front it before trusting layout values')
  if (sheets.blocked.length) warnings.push(`${sheets.blocked.length} stylesheet(s) unreadable (cross-origin) - state rules are incomplete, drive the states by hand`)

  return {
    warnings: warnings.length ? warnings : undefined,
    selector: SEL,
    url: location.href,
    viewport: { w: innerWidth, h: innerHeight, dpr: devicePixelRatio },
    box: { w: Math.round(box.width), h: Math.round(box.height), x: Math.round(box.x), y: Math.round(box.y) },
    form: pick(FORM_PROPS),
    motion: {
      resting: pick(MOTION_PROPS),
      running,
      keyframes: Object.fromEntries(Object.entries(keyframes).filter(([n]) => cs.animationName.includes(n) || matched.some((m) => m.css.includes(n)))),
      staggerHints,
      scrollDriven,
      reducedMotionRules: stateRules.filter((r) => (r.media || '').includes('prefers-reduced-motion')),
    },
    behavior,
    tokens,
    rules: { matched: matched.slice(0, 40), states: stateRules.slice(0, 40) },
    sheets,
  }
})('REPLACE_WITH_SELECTOR')
