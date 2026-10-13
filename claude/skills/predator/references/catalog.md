# The slop catalog

1000 recognizable tells that a page or a file was generated rather than made.
Each entry is a single observable condition: you can look at a screen, a
stylesheet or a diff and say yes or no. Numbering is continuous and stable;
the skill samples by number, so nothing here is renumbered once published.

## 1. Layout and composition

1. Every section is a centered max-width column; no asymmetry anywhere on the page.
2. A three-card grid as the answer to every list of things: features, benefits, testimonials, steps.
3. Every section carries equal visual weight, so nothing is primary and the eye has no path.
4. Identical vertical padding on every section, no rhythm between dense and open.
5. Icon plus heading plus two lines of body as the universal content unit.
6. Alternating left/right image-text zigzag that never breaks pattern.
7. Cards around content that is not a card: a bordered rounded box wrapping a plain paragraph.
8. Nested cards, a card inside a card inside a bordered section.
9. Container inside container inside container, each adding its own padding.
10. A stats bar of four round numbers that nothing in the business produces.
11. A 100vh hero regardless of how little is in it.
12. Eyebrow label plus heading plus subheading on every section, including ones that need none.
13. Content that never touches the viewport edge, including on mobile where it should.
14. Grids that go three columns to one with no tablet step, so mid-widths get one enormous column.
15. Grids whose item count does not divide the column count, leaving a stranded orphan.
16. A hero that occupies the fold and says nothing specific about the product.
17. A symmetric 50/50 split hero regardless of which side carries the message.
18. A bento grid whose cells are all the same size, which defeats the form.
19. Section order that follows a template rather than an argument.
20. Every page ending with the same full-width CTA band.
21. Decorative dividers between sections that already read as separate.
22. Background alternating white and gray section by section as the only structure.
23. A sticky header that eats a sixth of a mobile viewport.
24. Sticky elements that overlap content at short viewport heights.
25. Z-index assigned ad hoc, with values like 9999.
26. Absolute positioning where flow layout would work.
27. Fixed pixel heights on containers holding variable text.
28. `overflow-hidden` used to hide a layout bug rather than fix it.
29. Enforced aspect ratios on content that reflows, cropping text.
30. A footer with five link columns, three of which have one item.
31. A footer column titled Company containing only About.
32. Section max-widths that differ by a few pixels for no reason.
33. Cards of different content length with no min-height, so the row is ragged.
34. Equal card heights achieved by stretching whitespace inside them.
35. A grid gap that differs from the section padding by an arbitrary amount.
36. Content centered vertically inside a box that is taller than it needs to be.
37. Full-bleed background images behind text with no scrim, so the text sits on noise.
38. A two-column layout whose second column is empty at most breakpoints.
39. Repeated hero patterns on interior pages where a page title would do.
40. Breadcrumbs on a site two levels deep.
41. A sidebar duplicating the main nav.
42. Two CTAs stacked within one viewport.
43. Primary and secondary buttons styled so similarly the hierarchy is lost.
44. Buttons stretching full width on desktop because the mobile style was never scoped.
45. Sections that read top to bottom on desktop and scramble order on mobile.
46. Visual grouping that contradicts DOM order.
47. Layout built entirely from utility classes with no named component boundaries.
48. Grid columns hardcoded per breakpoint where `auto-fit` and `minmax` would fit the content.

## 2. Spacing and rhythm

49. Uniform whitespace instead of proximity-based grouping.
50. Margin and padding both applied to the same relationship, doubling the gap.
51. Vertical margins collapsing unpredictably because both sides set margin.
52. The space between a heading and its own body equal to the space to the next section.
53. Arbitrary spacing values off the scale.
54. `space-y-*` on a container plus margins on the children.
55. Padding on a wrapper and padding again on its only child.
56. Section padding that does not shrink on mobile.
57. Gutters that vanish at the narrowest breakpoint.
58. Line height set globally and never adjusted for display sizes.
59. Button vertical padding that ignores the label's line height.
60. Icon and label aligned by a magic top offset instead of flex alignment.
61. A spacer div used to create vertical gap.
62. `<br>` used for spacing.
63. Negative margins used to undo a parent's padding.
64. Card padding inconsistent across one page: 24, 32, 20.
65. First and last children of a scroll container missing their inset.
66. Gap on a flex container and margins on its children as well.
67. Text blocks with no measure, so the spacing looks fine and the reading does not.
68. Sections separated by an `<hr>` and by padding and by a background change.
69. A footer with more vertical space than the content above it.
70. Whitespace tuned on desktop only, so mobile is cramped or cavernous.

## 3. Typography

71. Two sizes total: one display, one body, no intermediate scale.
72. Gradient fills on headings.
73. Centered paragraphs that run more than one line.
74. Line length past ninety characters because the container has no measure.
75. Heading levels chosen for size rather than structure.
76. `tracking-tight` applied to body copy, not just display.
77. All-caps micro labels above every block.
78. One font weight everywhere, no 400/500/700 range.
79. Muted body text at a contrast ratio that fails, because subtle was the goal.
80. A second font family loaded and never used.
81. A type scale with no ratio, sizes picked one at a time.
82. Font sizes in px on some elements and rem on others.
83. No fluid type, so sizes jump hard at a breakpoint.
84. `clamp()` with bounds that invert at extreme widths.
85. Headings that wrap to a single orphaned word.
86. Balance applied to body copy where it hurts, or absent on display text where it helps.
87. Non-tabular numerals in tables and price comparisons.
88. Straight quotation marks in prose.
89. Straight apostrophes in prose.
90. Hyphens where en or em dashes belong.
91. Ellipsis typed as three periods.
92. Ampersands in prose to save space.
93. Fonts loaded without `font-display: swap`, so text is invisible while loading.
94. Google Fonts pulled from the CDN rather than self-hosted, adding a third-party connection.
95. Every weight of a variable font loaded when three are used.
96. Italic and bold synthesized because those faces were never loaded.
97. Letter-spacing omitted on all-caps labels, so they read cramped.
98. Body copy set at 14px on desktop.
99. Line height under 1.4 on body copy.
100. Line height above 1.8 on display headings.
101. Links in body copy indistinguishable from surrounding text except on hover.
102. Underlines removed from inline links with nothing put back.
103. Color used for emphasis in a way that carries no meaning.
104. Placeholder and helper text at the same size and weight as the value.
105. Lists rendered as paragraphs with bullet characters typed in.
106. `<b>` and `<i>` where `<strong>` and `<em>` are meant.
107. All-caps applied to a full sentence.
108. Title case in one place and sentence case in another for the same class of label.
109. No widow control on marketing headlines.
110. Long unbreakable strings overflowing their containers.
111. `word-break: break-all` applied globally to fix one overflow.
112. Multiple h1 sizes across pages with no shared token.
113. Blockquote styled identically to body copy.
114. Code and prose set in the same family.
115. Footnote markers faked with parentheses.
116. A type scale applied to marketing pages but not to app UI, or the reverse.
117. Text over images with no minimum contrast enforcement.
118. `text-transform: capitalize` applied to user-entered data.

## 4. Color and theming

119. A purple-to-blue or indigo-violet gradient as the brand identity.
120. Three gradients on one screen: the button, the heading, the background.
121. Blurred colored orbs floating behind the hero.
122. Glassmorphism over a solid background, where there is nothing to blur.
123. Hardcoded hex values inline instead of tokens, so the palette cannot be changed in one place.
124. A dark mode that inverts grays and leaves brand colors unreadable.
125. Semantic colors used decoratively, green and red as accents rather than status.
126. `white/10` borders sitting on light backgrounds, invisible.
127. The same shadow on everything, so nothing is elevated relative to anything.
128. Six ad-hoc grays where a two-step scale would do.
129. A palette with no neutral scale at all, only brand tints.
130. The brand color used for both the primary action and body links, so nothing is the action.
131. A hover state that changes lightness by two percent.
132. No active or pressed state at all.
133. Disabled state expressed by opacity alone, dropping contrast below threshold.
134. Focus color identical to hover color.
135. Success green and brand green differing by a hair.
136. Error red used for a neutral badge.
137. Alpha colors layered so the final value is unpredictable.
138. Gradients interpolated in sRGB, producing a gray dead zone in the middle.
139. Colors defined in the stylesheet and again in a JS theme object.
140. A theme file that no component reads.
141. A framework config extended with colors that duplicate its defaults.
142. Both `dark:` variants and a class-based theme provider in one codebase.
143. A dark mode toggle with no persistence.
144. A dark mode toggle that ignores `prefers-color-scheme` on first load.
145. A flash of light theme before the dark class applies.
146. `color-scheme` unset, so form controls and scrollbars stay light in dark mode.
147. Images with baked-in white backgrounds sitting on a dark surface.
148. SVGs with hardcoded fills that do not respond to theme.
149. A logo supplied in one color only, illegible on one of the two themes.
150. Charts using a rainbow palette with no ordering meaning.
151. Chart series colors that repeat after five categories.
152. Color as the only carrier of meaning in a status indicator.
153. Selection color left at the browser default against a dark background.
154. Scrollbars restyled decoratively and made harder to grab.
155. Overlay scrims at an opacity chosen by eye, differing per modal.
156. Backdrop blur plus a dark overlay plus a shadow on the same scrim.
157. Elevation expressed by border in one component and by shadow in another.
158. Four unrelated border-radius values on one page.
159. Fully rounded pills used for buttons and also for cards.
160. Borders drawn where a background change already separates the regions.
161. The brand color applied to a destructive action.
162. No print styles, so a dark theme prints as black pages.
163. Colors named for their value rather than their role.

## 5. Imagery, icons, media

164. Stock photography of people in an office nobody works in.
165. Generated hero imagery with the telltale smeared background.
166. Illustrations from one library mixed with photographs from another.
167. Icons picked for shape rather than meaning: a rocket for speed, a bolt for anything.
168. Icons from three different sets on one page.
169. The same generic default icon standing in for every concept.
170. Icon sizes inconsistent within a single row.
171. Icon stroke widths that differ from each other.
172. Decorative icons exposed to screen readers.
173. Icon-only buttons with no accessible name.
174. Emoji used as icons.
175. A logo cloud of companies with no relationship to the business.
176. Product screenshots with placeholder data still visible.
177. Screenshots at the wrong pixel ratio, blurry on retina.
178. Images with no width and height, causing layout shift.
179. Images shipped at full resolution with no responsive source set.
180. PNG where WebP or AVIF would be a fraction of the weight.
181. SVGs exported with kilobytes of editor metadata.
182. An SVG carrying a hardcoded white background rectangle.
183. The same inline SVG duplicated in ten files instead of componentized.
184. The above-fold image lazy-loaded, delaying the largest paint.
185. Below-fold images loaded eagerly.
186. Background images used for content images, invisible to search and screen readers.
187. Alt text written for crawlers rather than for a listener.
188. Alt text repeating the visible caption verbatim.
189. Empty alt on an image that carries meaning.
190. Video autoplaying with sound.
191. Background video with no poster frame.
192. An animated GIF where a compressed video would be a tenth the weight.
193. The framework's default favicon still in place.
194. An Open Graph image that is a screenshot of the page.
195. No Open Graph image, so shares render as a bare link.
196. Apple touch icon missing or at the wrong size.
197. Images cropped by CSS rather than at build time, shipping pixels nobody sees.
198. A photograph of a team that does not exist.

## 6. Motion and animation

199. Fade-up-on-scroll on every section, so the page stutters into view.
200. No `prefers-reduced-motion` guard anywhere.
201. Hover scale on cards that are not interactive.
202. Transitions on layout properties rather than transform and opacity.
203. One duration for everything, no distinction between a hover and a route change.
204. A count-up animation on a figure that is static.
205. A typewriter or rotating-word headline.
206. A logo marquee that never pauses and proves nothing.
207. Loading skeletons over content already present in the HTML.
208. Parallax that breaks scrolling on trackpads and touch.
209. `transition: all`.
210. Easing left at linear or the browser default.
211. Entrance animations replaying every time a section re-enters the viewport.
212. Stagger delays long enough that the last item arrives after the reader has passed.
213. An animation library loaded for a single fade.
214. Scroll-jacking that overrides the reader's scroll position.
215. Global smooth-scroll that breaks anchor jumps for keyboard users.
216. Hover animations on touch devices, where the hover state sticks.
217. Animated gradients repainting continuously and pinning a CPU core.
218. Infinite animations running off-screen.
219. Animations that start before the font loads, so text shifts mid-motion.
220. Page transitions that delay the next page's content.
221. A spinner for an operation that finishes in fifty milliseconds.
222. No spinner at all for an operation that takes two seconds.
223. Optimistic UI with no rollback path.
224. Toasts that dismiss faster than they can be read.
225. Toasts that stack and cover the control that produced them.
226. A modal entrance animation with no matching exit.
227. An accordion animating height in JS where `grid-template-rows` or `<details>` would do.
228. Carousel autoplay with no pause on hover or focus.
229. Micro-interactions on decorative elements and none on the real controls.
230. Hover states that change layout and shift their neighbours.
231. Animation used to disguise slowness rather than fix it.
232. Confetti.
233. A cursor-following element that lags or traps the pointer.

## 7. Forms and input

234. Placeholder used as the label, so the label vanishes on typing.
235. Validation only on submit.
236. A single generic "Something went wrong."
237. Errors shown without saying which field.
238. Error state carried by red text alone.
239. Required fields unmarked.
240. Optional fields unmarked while nearly everything is required.
241. Every field required, including ones the business does not need.
242. A contact form asking for company size on a five-person site.
243. Field order that does not match how people say the information.
244. Name split into first and last where one field would do.
245. `type="text"` on email, tel, and url inputs.
246. `inputmode` and `autocomplete` attributes absent.
247. Autocomplete disabled deliberately.
248. A password field that blocks paste.
249. `maxlength` set below what real values need.
250. A phone input rejecting valid international formats.
251. Email validated with a homemade regular expression.
252. Postcode or state as free text where a list exists.
253. A country dropdown of two hundred items with no search.
254. Date entry as three separate selects.
255. A native date input styled until it breaks on Safari.
256. Submit not disabled during submission, allowing double posts.
257. Submit permanently disabled until valid, with no statement of what is invalid.
258. No loading state on submit.
259. Form state lost on a validation error.
260. Form state lost on back navigation.
261. No success state, just a reload.
262. A success message that does not say what happens next.
263. A form posting to an endpoint that does nothing.
264. No honeypot or rate limit on a public form.
265. A captcha challenge on a low-risk newsletter field.
266. Labels not linked to inputs by id.
267. Fieldset and legend missing on grouped radios.
268. Tab order broken by custom controls.

## 8. Navigation and information architecture

269. A nav item that exists because the bar needed a fifth.
270. Solutions, Platform, Resources as top-level labels.
271. A mega menu on a seven-page site.
272. Nav labels that differ from the titles of the pages they lead to.
273. The current page not indicated in the nav.
274. A logo not linked to home.
275. A hamburger menu on desktop.
276. A mobile menu that does not trap focus or lock scroll.
277. A mobile menu with no close affordance other than the toggle.
278. Nav order that changes between desktop and mobile.
279. A footer duplicating the header nav exactly.
280. A footer with five columns and eleven links.
281. Legal links buried below a wall of marketing links.
282. Anchor links with no scroll offset for the sticky header.
283. A back-to-top button on a two-screen page.
284. Site search that returns nothing for the site's own product names.
285. A 404 page with no way back into the site.
286. Deep pages reachable only from the sitemap.
287. Orphan pages linked from nowhere.
288. URLs that do not match the page hierarchy.
289. Trailing-slash inconsistency between links and canonicals.
290. Query-string routing where a path would be indexable.
291. Breadcrumbs whose last crumb repeats the page title and links to itself.
292. External links opening in a new tab with no indication.
293. Every link opening in a new tab.
294. In-page tabs that do not update the URL.
295. Pagination with no page numbers, only next.
296. Infinite scroll with a footer beneath it.
297. A nav that collapses at a breakpoint where there was still room.
298. No skip-to-content link.

## 9. Components and interaction

299. A whole component library installed for one button.
300. Primitives dropped in and then overridden with `!important`.
301. `outline-none` with no focus style put back.
302. A div with an onClick where a button or link belongs.
303. A modal with no Escape key, no focus trap, no scroll lock.
304. A modal rendered inside the component tree, inheriting stacking bugs.
305. Dialogs stacking on top of dialogs.
306. Tooltips carrying information available nowhere else.
307. Tooltips on touch devices with no alternative.
308. Tooltips triggered on hover only, unreachable by keyboard.
309. A carousel where a list would work.
310. A carousel whose dots are the only navigation.
311. Accordions hiding content that should be visible.
312. Every accordion closed by default, including the one people came for.
313. Tabs holding content that should be one page.
314. A dropdown built from divs with no roving focus.
315. A custom select that does not open from the keyboard.
316. A combobox with no announced result count.
317. Buttons and links used interchangeably.
318. A button that navigates and a link that mutates state.
319. Destructive actions with no confirmation.
320. Confirmation dialogs on harmless actions.
321. Confirm text that does not name the object being deleted.
322. An empty state that spins forever when there is simply nothing.
323. An empty state with no action to escape it.
324. Loading, empty, and error collapsed into one blank div.
325. Skeletons whose shape does not match the content that replaces them.
326. Disabled buttons with no reason given.
327. Icon buttons whose meaning depends entirely on the tooltip.
328. Three components producing the same badge.
329. Two button components in one repo with different APIs.
330. The same concept named `variant`, `type`, and `kind` across three components.
331. A component taking a className and also fifteen boolean style props.
332. More than five boolean props on one component.
333. Mutually exclusive boolean props with no enforcement.
334. Components rendering different elements by prop with no polymorphic typing.
335. Copy-pasted component variants instead of one parameterized component.
336. A design-system folder alongside ad-hoc components doing the same job.
337. Controlled and uncontrolled behavior mixed in one input component.
338. Compound components with implicit context that breaks when reordered.

## 10. Accessibility

339. No skip link.
340. Focus visibility removed globally.
341. Focus order following a DOM order that contradicts the visual order.
342. Positive `tabindex` values.
343. `tabindex="-1"` on something that needs to be reachable.
344. `aria-label` overriding a perfectly good visible label.
345. `role="button"` on a div instead of using a button.
346. ARIA roles applied where native semantics already exist.
347. `aria-hidden` on a focusable element.
348. Landmarks missing: no main, nav, header, footer.
349. More than one main element.
350. Heading levels skipped.
351. Headings used for styling rather than structure.
352. Lists built from divs.
353. Tables built from divs with no row or column semantics.
354. Data tables with no `th` or `scope`.
355. Table captions missing.
356. Form errors not associated by `aria-describedby`.
357. No live region for asynchronous status.
358. Live regions set to assertive for non-urgent updates.
359. Body text below 4.5:1 contrast.
360. UI borders and icons below 3:1 contrast.
361. Contrast checked on the light theme only.
362. Text over a gradient that passes at one end and fails at the other.
363. Link purpose unclear out of context: "click here", "learn more", repeated.
364. A button whose accessible name is only an icon glyph.
365. Images of text.
366. Video with no captions.
367. Audio with no transcript.
368. Motion with no reduced-motion alternative.
369. Autoplaying content with no pause control.
370. Time limits with no extension.
371. Touch targets under 44 pixels.
372. Adjacent touch targets with no spacing between them.
373. Information disclosed on hover only.
374. Custom scroll containers that cannot be scrolled by keyboard.
375. Modal focus not returned to the trigger on close.
376. Page title not updated on a client-side route change.
377. Route changes not announced.
378. `lang` missing or wrong.
379. No RTL handling on a site that claims multiple languages.
380. Zoom disabled with `user-scalable=no`.
381. Layout breaking at 200 percent zoom.
382. Content reachable only by dragging.
383. An accessibility statement claiming conformance nothing tested.

## 11. Responsive and mobile

384. Designed at 1440 and squeezed downward.
385. Breakpoints copied from the framework with no relation to the content.
386. A layout that breaks between 768 and 1024.
387. Horizontal scroll on mobile caused by one overflowing element.
388. Fixed widths inside a fluid layout.
389. `100vw` used where it triggers scrollbar overflow.
390. `100vh` on mobile, ignoring browser chrome, with `dvh` available and unused.
391. Tables that neither scroll nor reflow on narrow screens.
392. Long text overflowing a fixed-width card.
393. Font sizes that do not change between mobile and desktop.
394. Touch targets sized for a mouse.
395. Hover-dependent interactions with no touch equivalent.
396. A sticky header, a sticky CTA, and a cookie banner on a 667-pixel screen.
397. Modals taller than the viewport with no internal scroll.
398. Bottom-fixed elements colliding with the iOS home indicator.
399. Safe-area insets ignored.
400. Inputs under 16px triggering iOS zoom on focus.
401. A mobile menu that lets the page scroll behind it.
402. Desktop-sized images served to phones.
403. Desktop-only components rendered and then hidden with CSS.
404. `display: none` hiding content that is still fetched and parsed.
405. Two copies of the nav in the DOM, one per breakpoint.
406. Landscape orientation never tested.
407. Tablet treated as small desktop where it should be large mobile.
408. Container queries available and unused where they fit.
409. Media queries written in px against a rem-based type scale.
410. `min-width` and `max-width` queries mixed, creating gaps or overlaps.
411. No print stylesheet.
412. Scroll momentum broken by a custom scroll handler.
413. Pinch-zoom broken inside an embedded map or image.

## 12. Copy: words, tone, voice

414. Seamlessly.
415. Effortlessly.
416. Unlock.
417. Elevate.
418. Empower.
419. Supercharge.
420. Transform.
421. Leverage as a verb.
422. Robust.
423. Cutting-edge.
424. State-of-the-art.
425. Best-in-class.
426. Game-changing.
427. Revolutionary.
428. Innovative.
429. Delightful.
430. Intuitive.
431. Powerful yet simple.
432. Tailored to your needs.
433. "Whether you are X or Y, we have you covered."
434. "In today's fast-paced world."
435. "Take your X to the next level."
436. "The future of X."
437. Reimagined.
438. Curated.
439. Bespoke, on something mass-produced.
440. Journey, used for a purchase.
441. Ecosystem, used for a product with one integration.
442. Solutions, used where the product has a name.
443. A suite of tools.
444. End-to-end.
445. Holistic.
446. Synergy.
447. "Not just X, but Y."
448. "It is more than a Z, it is a W."
449. Rule of three in adjectives, bullets, and sections alike.
450. Em-dash asides a person would have cut.
451. Every sentence opening with a gerund clause.
452. Rhetorical questions used as subheadings.
453. Second person switching to first person plural mid-page.
454. Exclamation marks in a brand voice that has none elsewhere.
455. Sentence fragments for emphasis. Repeatedly.
456. American and British spellings mixed.
457. Title case and sentence case mixed within one nav.
458. The product name capitalized differently across pages.

## 13. Copy: structure and claims

459. A headline promising something nobody claimed and nothing measures.
460. Feature descriptions that restate the feature name as a sentence.
461. Testimonials from invented people at invented companies.
462. Testimonials with no name, role, or company.
463. Testimonials that all praise the same vague quality.
464. Placeholder copy shipped live.
465. Lorem ipsum in production.
466. "Your Company" and an example address left in.
467. An FAQ answering questions nobody asks.
468. FAQ answers written to fill a schema block rather than to answer.
469. "Get Started" with no object: started at what.
470. Two CTAs of equal weight, so neither is the next step.
471. A CTA that repeats the page title.
472. Unsourced numbers: customers served, uptime, speed multiples.
473. Percentages with no denominator.
474. Awards and badges with no issuer or year.
475. Client logos for companies with no relationship to the business.
476. "Trusted by thousands" on a new site.
477. Copy about the company's virtues rather than the buyer's outcome.
478. Feature lists with no indication of which plan includes what.
479. Pricing described as affordable instead of stated.
480. "Contact us for pricing" on a product that has a price.
481. A guarantee with no terms.
482. A trial with no stated length or card requirement.
483. Benefit statements with no mechanism: nothing says what makes it faster.
484. Copy written for a persona that is never named.
485. Jargon from the vendor's internal vocabulary.
486. Acronyms expanded nowhere.
487. Headings that summarize instead of asserting.
488. Body copy repeating the heading it sits under.
489. Bullet lists where every bullet is a paragraph.
490. A bullet list of one item.
491. Numbered steps that are not sequential.
492. A three-step "how it works" that hides the actual work.
493. Microcopy absent at the point a decision is made.
494. Error and empty-state copy written in a different voice from the marketing copy.
495. Legal copy pasted from a template that names another company.
496. A privacy policy describing data practices the site does not have.
497. Dates written in an ambiguous numeric format.
498. Times given with no timezone.

## 14. Page and content architecture

499. Every page is hero, three cards, CTA band.
500. Interior pages that differ only in copy.
501. Service pages generated from a template with the noun swapped.
502. Location pages identical except for the city name.
503. A blog with three posts published the same day and none since.
504. Posts with no author, date, or update history.
505. Content with no internal links.
506. Every page linked from the footer instead of in context.
507. A resources section with no resources.
508. A changelog that stopped.
509. A status page that is a static image.
510. Case studies with no numbers.
511. Case studies about anonymous clients.
512. An About page describing the industry rather than the company.
513. A team page of stock portraits.
514. Duplicate content across pages competing for the same query.
515. Thin pages published to fill a nav.
516. A page whose only content is a form.
517. A pricing page with no pricing.
518. Comparison pages that misstate the competitor.
519. Testimonials, logos, and stats repeated on every page.
520. The same CTA copy on every page regardless of context.
521. No next step at the end of long-form content.
522. Content that never states who it is for.
523. Content organized by internal team structure rather than by reader task.
524. Documentation and marketing describing the same feature differently.
525. Two pages describing the same feature in contradictory terms.
526. Content that assumes the reader arrived at the homepage first.
527. A hierarchy with exactly one child under each parent.
528. Tags and categories used interchangeably.

## 15. SEO and metadata

529. Titles and descriptions generated from one template.
530. Meta descriptions past 160 characters, truncated.
531. Meta descriptions duplicated across pages.
532. Missing descriptions with the framework default left in.
533. Two h1s on a page, or none.
534. The title tag identical to the h1 on every page.
535. Keyword stuffing in the title.
536. The brand name first in every title, pushing the topic out of view.
537. Canonical tags pointing at the homepage from every page.
538. Canonicals missing on paginated or filtered pages.
539. `noindex` left on from staging.
540. `robots.txt` blocking CSS and JS.
541. A sitemap listing URLs that 404.
542. A sitemap not referenced from `robots.txt`.
543. A sitemap not regenerated on build.
544. Redirect chains left behind after a restructure.
545. Internal links pointing at redirects.
546. Mixed http and https internal links.
547. www and non-www both resolving with no canonical choice.
548. Trailing-slash duplicates indexed.
549. Query parameters creating indexable duplicates.
550. Structured data present and failing validation.
551. Schema describing content that is not on the page.
552. Review schema with no reviews.
553. FAQ schema on a page with no visible FAQ.
554. LocalBusiness schema whose address does not match the Google profile.
555. Breadcrumb schema that does not match the visible breadcrumbs.
556. Open Graph title and page title disagreeing.
557. An `og:image` at the wrong aspect ratio.
558. Twitter card tags missing or malformed.
559. Hreflang absent or self-referencing incorrectly on a multi-language site.
560. No pagination markup on archives.
561. Image filenames like `image-1.png`.
562. Anchor text that is the URL.
563. Heading text written for crawlers and body text written for people.

## 16. Performance

564. Images shipped at full resolution with no dimensions set.
565. The largest contentful element lazy-loaded.
566. The hero image not preloaded.
567. Fonts not preloaded, causing a late swap.
568. A render-blocking stylesheet for below-fold styles.
569. An entire icon library imported for four icons.
570. An entire date library imported to format one date.
571. Lodash imported wholesale.
572. A motion library loaded for one fade.
573. Client-side rendering for content that could be static.
574. `use client` at the top of a page that needs no interactivity.
575. Server components fetching in a waterfall.
576. Data fetched on the client that the server already had.
577. The same request made by three components on one page.
578. No caching headers on static assets.
579. Cache-busting query strings on assets that are already hashed.
580. Third-party scripts in the head, blocking render.
581. Analytics, chat widget, and heatmap all loaded at first paint.
582. A chat widget costing 300kb.
583. A cookie banner script larger than the page.
584. A tag manager loading four tags that do the same thing.
585. An embedded video iframe where a facade would do.
586. An embedded map loaded on page load rather than on interaction.
587. Web fonts used for icon glyphs.
588. Unused CSS shipped because the purge scope is wrong.
589. A safelist grown until purging does nothing.
590. Duplicate framework copies or polyfills in the bundle.
591. Polyfills shipped to modern browsers.
592. Source maps served in production.
593. No compression on text assets.
594. No HTTP/2 or HTTP/3.
595. Images served from the origin rather than a CDN.
596. Every locale's strings shipped to every visitor.
597. Long tasks over 200ms blocking interaction.
598. Layout thrash from reading and writing layout in a loop.
599. Effect chains causing repeated re-renders on load.
600. Animations on box-shadow and filter repainting large areas.
601. Scroll listeners with no passive flag and no throttle.
602. Resize observers doing layout work every frame.
603. Performance measured once at launch and never again.

## 17. React and component code

604. `useEffect` with a missing or fabricated dependency array.
605. `useEffect` syncing state that could be derived.
606. `useState` plus `useEffect` where a computed value would do.
607. State holding a value already in props or in the URL.
608. Derived state that drifts from its source.
609. `useEffect` fetching where the framework has a loader.
610. Fetch in a component with no cache, no error path, no cancellation.
611. Race conditions from unguarded async effects.
612. Cleanup missing on subscriptions and timers.
613. Event listeners added and never removed.
614. `key={index}` on a reorderable list.
615. Keys missing entirely.
616. The list index used both as key and as identity in state.
617. `useMemo` and `useCallback` everywhere with no measurement.
618. Memoization defeated by an inline object or arrow prop.
619. `React.memo` on a component whose props change every render.
620. Context holding a new object literal each render.
621. One giant context re-rendering the whole tree.
622. Context used where a prop would do.
623. Props drilled five levels rather than colocating.
624. Global state for a value used in one component.
625. A state manager installed for three booleans.
626. Redux boilerplate for a form.
627. State mutated directly.
628. Props mutated.
629. `dangerouslySetInnerHTML` on unsanitized content.
630. Refs used to read state instead of the state itself.
631. `useLayoutEffect` running on the server.
632. Hydration mismatches from `Date.now()` or `Math.random()` in render.
633. `window` or `document` touched during render.
634. `typeof window !== "undefined"` sprinkled instead of drawing a boundary.
635. No Suspense boundary, so one slow fetch blanks a page.
636. No error boundary around a risky subtree.
637. Conditional hooks.
638. Hooks called in loops.
639. Custom hooks returning objects rebuilt on every call.
640. Custom hooks doing too much to be reused.
641. Components over 500 lines.
642. One component rendering three unrelated concerns.
643. JSX nested more than six levels in a single return.
644. Ternaries nested inside JSX attributes.
645. Conditional rendering with `&&` on a number, printing a stray zero.
646. Inline styles mixed with utility classes.
647. Anonymous default exports.
648. A file exporting one component and three helpers used elsewhere.
649. Props spread blindly onto DOM elements.
650. `forwardRef` omitted on components wrapping inputs.
651. Controlled-to-uncontrolled input warnings ignored.
652. Server actions and client fetches performing the same mutation.
653. Route handlers duplicating logic that already lives in a server component.

## 18. General code structure

654. Comments restating the line below them.
655. Section banner comments.
656. `try/catch` swallowing the error into a log line.
657. Optional chaining on values that cannot be null.
658. Defensive checks that hide the real failure.
659. A utils file of one-line wrappers around the standard library.
660. `utils`, `helpers`, `constants` as junk drawers.
661. A barrel index re-exporting everything.
662. An environment variable with a real value as its fallback.
663. Magic numbers with no name.
664. Magic strings repeated across files.
665. The old implementation left commented out above the new one.
666. Dead exports nothing imports.
667. Unreachable branches.
668. Duplicate functions whose behavior has drifted.
669. Copy-pasted blocks differing by one literal.
670. Abstractions built for a single caller.
671. Config objects with keys nothing reads.
672. Feature flags that are always true.
673. More than four positional parameters.
674. Boolean parameters that change what a function does.
675. Functions that both compute and write.
676. Side effects at module scope.
677. Circular imports resolved by moving a file rather than the dependency.
678. Deep relative import paths.
679. Path aliases configured in tsconfig and not in the bundler.
680. Index files that only re-export a sibling.
681. Naming that describes implementation rather than intent.
682. Variables named data, item, result, temp.
683. Handlers named handleClick2.
684. `newComponent.tsx` sitting next to `component.tsx`.
685. Plural and singular naming mixed for one concept.
686. camelCase and snake_case mixed within a module.
687. Promises in one file and async/await in the next.
688. Awaits run serially where they could run in parallel.
689. Promises never awaited.
690. `forEach` with an async callback.
691. A `for` loop where `map` reads better, or the reverse.
692. Array methods chained four deep over a large list.
693. Nested ternaries.
694. An if-chain where a lookup table fits.
695. A switch with no default.
696. No early returns, so logic nests four levels.
697. Exceptions used for control flow.
698. Null and undefined both used to mean absent.
699. Empty string, null, and zero treated as one falsy case.
700. Dates handled in local time with no timezone declared.
701. Currency stored as a float.
702. URLs built by string concatenation.
703. Regular expressions with no comment and no test.

## 19. CSS and styling code

704. The same forty-character utility string copied across ten files.
705. Arbitrary values instead of the scale.
706. A stylesheet and utility classes fighting over the same element.
707. `!important` used to win a specificity battle.
708. Selectors four levels deep.
709. IDs used as style hooks.
710. Global element selectors overriding component styles.
711. Reset and normalize both included.
712. Two CSS-in-JS libraries in one repo.
713. CSS modules alongside styled-components alongside utilities.
714. Styles defined in a component and again in a stylesheet.
715. Media queries repeated in every component instead of tokenized.
716. Breakpoint values duplicated in JS and CSS.
717. `:hover` styled without `:focus-visible`.
718. `:focus-within` missing where it matters.
719. Transitions declared on the base state, so they run on load.
720. `will-change` left on permanently.
721. `translateZ(0)` applied as superstition.
722. `height` where `min-height` is meant.
723. Viewport units inside scrollable containers.
724. `calc()` mixing units with no need.
725. Custom properties defined and never referenced.
726. Custom properties referenced and never defined.
727. Design tokens added to config that the code does not use.
728. Class precedence depending on file import order.
729. Utility classes built by string templating, defeating tooling and purging.
730. A class-merge helper used inconsistently across the codebase.
731. Inline styles for values that belong in tokens.
732. Z-index values with no scale.
733. `position: fixed` inside a transformed ancestor.
734. Stacking contexts created accidentally by opacity or filter.
735. Grid and flex both declared on one element.
736. Floats still in use for layout.
737. Print, forced-colors, and reduced-transparency modes unstyled.
738. Hand-written vendor prefixes alongside an autoprefixer.

## 20. TypeScript and types

739. `any` in a public signature.
740. `as` used to silence an error rather than narrow a type.
741. `as unknown as T`.
742. `@ts-ignore` with no explanation.
743. The same shape retyped inline in four files.
744. Duplicate interfaces that have drifted apart.
745. Types hand-written where a schema or query could generate them.
746. Database types not generated, so schema and code disagree.
747. `string` where a union of literals is known.
748. Enums where a union would serialize better.
749. Optional properties used to model mutually exclusive states.
750. One interface covering loading, error, and success.
751. Nullable fields typed as non-null.
752. API responses typed as the happy path only.
753. `unknown` that is never narrowed.
754. Return types omitted on exported functions.
755. `Function` and `object` used as types.
756. Index signatures replacing real keys.
757. Generic parameters that are never used.
758. Type assertions on `JSON.parse` with no validation.
759. No runtime validation at the network boundary.
760. A validation schema and a TypeScript interface maintained separately.
761. Prop types defined below the component, out of sight.
762. `React.FC` relied on for implicit children.
763. Strict mode disabled.

## 21. Data, API, and state

764. Fetch with no timeout.
765. No retry on idempotent reads, or retries on non-idempotent writes.
766. Retries with no backoff.
767. Pagination ignored, the first page treated as the whole set.
768. Client-side filtering over a full table pulled from the server.
769. `select *` where three columns are used.
770. N+1 queries behind a list render.
771. Data fetched and then discarded by a later render.
772. An endpoint called on every keystroke with no debounce.
773. Debounce implemented with a timeout and no cleanup.
774. Polling where a subscription exists.
775. Polling every second for data that changes daily.
776. A socket opened and never closed.
777. Cache keys that omit the parameters.
778. Cache invalidation performed by refetching everything.
779. Stale data shown with no indication that it is stale.
780. Optimistic updates with no rollback.
781. Mutations that do not invalidate the queries they affect.
782. Loading stuck true forever when a request fails.
783. Errors returned as 200 with a flag in the body.
784. HTTP status codes chosen arbitrarily.
785. Error responses with no machine-readable code.
786. The same entity shaped differently per endpoint.
787. Dates serialized three ways across one API.
788. IDs as numbers in one place and strings in another.
789. Booleans encoded as the strings "true" and "false".
790. Nulls omitted rather than sent, so absent and unset are indistinguishable.
791. Internal or secret fields returned to the client.
792. Unbounded queries with no limit.
793. No rate limiting on a public endpoint.
794. Client-side sorting of a paginated list.
795. Search implemented as `includes()` over a fetched array.
796. Local storage treated as the source of truth.
797. State duplicated between URL, local storage, and memory.
798. Form state, server state, and UI state all managed by one tool.

## 22. Errors, logging, edge cases

799. `console.log` left in shipped code.
800. `console.error` standing in for error handling.
801. Errors caught and rethrown with the original lost.
802. Stack traces exposed to users.
803. Error messages that say nothing.
804. One generic message for network, validation, and permission failures.
805. Errors reported nowhere, with no monitoring wired up.
806. Monitoring wired up with no alerting.
807. Alerts with no owner.
808. An error boundary that renders nothing.
809. Fallback UI that hides the fact that something failed.
810. Empty arrays and null handled by different code paths meaning the same thing.
811. Zero, one, and many treated identically in copy.
812. Pluralization hardcoded with a trailing "(s)".
813. Long values untested: names, addresses, and titles that overflow.
814. Unicode and emoji in user input untested.
815. Timezone boundaries untested.
816. Leap years and month ends untested.
817. Offline behavior undefined.
818. Slow-network behavior undefined.
819. A third-party failure taking the page down.
820. A dependency outage rendering a blank screen rather than a degraded one.
821. Retry buttons that retry nothing.
822. Background jobs failing silently.
823. Errors deduplicated so aggressively that new ones are hidden.

## 23. Security and configuration

824. Secrets committed to the repo.
825. Secrets exposed through public-prefixed environment variables.
826. Service-role keys used from the browser.
827. Row-level security disabled because it was inconvenient.
828. Authorization checked in the UI and not on the server.
829. Admin routes protected by an unlisted URL.
830. CORS set to a wildcard on an authenticated endpoint.
831. Raw HTML injected from user content with no sanitizer.
832. User input interpolated into SQL.
833. Redirects taking an unvalidated destination parameter.
834. File uploads with no type or size validation.
835. Secrets compared without a constant-time check.
836. Sessions with no expiry.
837. Tokens stored in local storage.
838. Cookies without HttpOnly, Secure, or SameSite.
839. No CSRF protection on state-changing forms.
840. No Content-Security-Policy.
841. A CSP with unsafe-inline and unsafe-eval added to make it pass.
842. Security headers absent: HSTS, referrer policy, frame options.
843. Dependencies with known advisories left unpatched.
844. Audit output dismissed as noise.
845. Lockfile not committed.
846. Environment variables documented nowhere.
847. Production and staging sharing a database.
848. Debug flags enabled in production.

## 24. Testing

849. Tests that assert the mock rather than the behavior.
850. Snapshots regenerated whenever they fail.
851. Snapshots of entire pages.
852. Tests duplicating the implementation line by line.
853. Full coverage on getters and none on the logic.
854. Coverage thresholds set below current coverage.
855. Tests skipped and left skipped.
856. Tests that pass because the assertion is missing.
857. Assertions that cannot fail.
858. Tests depending on execution order.
859. Tests depending on real network calls.
860. Time-dependent tests with no clock control.
861. Flaky tests retried in CI rather than fixed.
862. No test for the bug that was just fixed.
863. Happy paths only.
864. Accessibility never asserted.
865. No visual regression on a design-heavy site.
866. End-to-end tests clicking through the UI to test a pure function.
867. Fixtures that do not resemble production data.
868. Real customer data in test fixtures.
869. A suite so slow nobody runs it.
870. CI green on a branch where the tests do not actually run.

## 25. Repo, build, tooling

871. A README describing features the code does not have.
872. The framework's default README left in place.
873. Setup instructions that do not work from a clean clone.
874. `.env.example` missing or out of date.
875. Editor and IDE config committed.
876. Build artifacts committed.
877. `node_modules` in git history.
878. A tracked `dist/` alongside source.
879. Backup copies left beside the originals.
880. Files named final, v2, old, copy.
881. Commented-out config kept for reference.
882. Dependencies installed and never imported.
883. Two package manager lockfiles present.
884. Node version unpinned.
885. Scripts in package.json that no longer run.
886. Linting configured and then disabled inline everywhere.
887. Formatter and linter with conflicting rules.
888. Pre-commit hooks everyone bypasses.
889. CI that lints but does not build.
890. CI that runs on push but not on pull request.
891. No branch protection on the deploying branch.
892. Deploys from a local machine.
893. No preview deployment for review.
894. Preview environments pointed at production data.
895. Version numbers that never move, or move with no release.

## 26. Docs and comments

896. Comments explaining what, never why.
897. Doc comments restating the type signature.
898. Doc comments on trivial functions and none on the hard one.
899. TODOs with no owner or date.
900. TODOs older than the feature they describe.
901. FIXME on code that was fixed.
902. Comments describing behavior that has since changed.
903. A changelog generated from commit messages nobody wrote for readers.
904. Commit messages that say update or fix.
905. Pull request descriptions restating the diff.
906. Architecture docs describing an architecture that was abandoned.
907. Diagrams that do not match the code.
908. API docs generated from types with no examples.
909. Examples in docs that do not run.
910. Docs written for the author rather than the next reader.
911. Onboarding docs assuming access nobody grants.
912. Runbooks with no failure cases.
913. Comments written in a different voice from the rest of the file.
914. Explanatory comments standing in for clearer naming.
915. Licence headers on every file in a private repo.
916. Generated files edited by hand with no warning banner.
917. The same documentation in three places, drifting.

## 27. Database and schema

918. Every column nullable.
919. No foreign keys.
920. No unique constraint where uniqueness is assumed.
921. Enum values enforced in the app and not in the schema.
922. Booleans where a state enum is needed.
923. Timestamps without timezone.
924. `created_at` present and `updated_at` absent.
925. Soft deletes with no filter in half the queries.
926. Money stored as a float.
927. JSON columns holding what should be columns.
928. Arrays holding what should be a join table.
929. Denormalized copies that drift.
930. No index on foreign keys or on filtered columns.
931. An index on every column, slowing writes.
932. Migrations that are not reversible.
933. Migrations edited after they have run.
934. Schema changed through a dashboard rather than a migration.
935. Seed data indistinguishable from real data.
936. Table names inconsistent in number and case.
937. Primary keys that are business values.
938. Client-generated UUIDs with no ordering.
939. Row-level security policies that allow all.
940. Policies duplicated per operation with drifted conditions.
941. Generated types not regenerated after a schema change.
942. Queries written against columns that no longer exist.

## 28. Email and notifications

943. Transactional email designed with the marketing template.
944. Email that renders broken in Outlook.
945. No plain-text alternative.
946. Preheader text left as the first line of the body.
947. Subject lines written by the same generator as the headlines.
948. A from-name that is the domain rather than a person or brand.
949. No reply-to on a message that invites a reply.
950. Unsubscribe missing on anything but a receipt.
951. Unsubscribe that requires a login.
952. Notifications with no digest option.
953. Every event producing an email.
954. Email content duplicating what the app already shows.
955. Links in email with tracking that breaks them, or with no attribution at all.
956. Images in email with no alt text.
957. Dark-mode email putting a white logo on white.
958. Send times in the sender's timezone.
959. Test sends reaching real recipients.
960. Templates hardcoded in the sending code.

## 29. Analytics and tracking

961. Analytics installed and never read.
962. Event names inconsistent across the same action.
963. Events with no properties.
964. Properties with unbounded cardinality.
965. Page views tracked and conversions not.
966. Conversion defined as a view of the thank-you page and nothing else.
967. Two analytics tools disagreeing with no reconciliation.
968. A consent banner that loads trackers before consent.
969. Consent choices not honored.
970. UTM parameters lost in a redirect.
971. Internal traffic not excluded.
972. Bot traffic counted.
973. Cross-domain tracking broken at the checkout hand-off.
974. Goals never updated after the funnel changed.
975. Dashboards showing vanity metrics only.
976. Tracking code duplicated by both a tag manager and a direct snippet.
977. No server-side events, so ad platforms optimize on nothing.
978. No alert when tracking stops firing.

## 30. Trust, legal, and business surface

979. A contact page with no phone, address, or hours where the business has all three.
980. A phone number that is not a link on mobile.
981. Business hours that are not machine-readable.
982. A physical address that does not match the Google Business Profile.
983. Social links to profiles that are empty or dead.
984. A stale copyright year.
985. A copyright year computed in client-side JavaScript.
986. Terms and privacy pages linked but not written.
987. A privacy policy naming a template's example company.
988. A cookie banner on a site that sets no cookies.
989. A cookie banner with no reject option.
990. Privacy language for a jurisdiction the business does not serve, or none where it is needed.
991. An accessibility statement claiming conformance nothing tested.
992. Refund and cancellation terms absent on a paid product.
993. Pricing on the page that does not match what checkout charges.
994. A booking link to an empty calendar.
995. A form emailing an address nobody monitors.
996. A support address on a free mail domain.
997. Licensing or credentials claimed with no number.
998. Reviews displayed with no source and no link.
999. Security or compliance badges the company does not hold.
1000. A launch date, "coming soon", or beta label that has not moved in a year.
