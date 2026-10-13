/*
 * Measures the parts of a marketing page that only exist once the browser has
 * laid it out: how many lines a headline wrapped to, whether the primary action
 * survived above the fold, whether the navigation stayed on one row, and how
 * tall the bar came out.
 *
 * None of those are visible in source at any level of effort. A headline that
 * reads as two lines in the markup wraps to four at the font scale that shipped,
 * and a nav that fits in review wraps at the width a visitor uses.
 *
 * Run it in the browser pane at desktop width:
 *
 *   layoutProbe.scan()
 *
 * Save the result and hand it to the run:
 *
 *   design-pass.py layout-probe --file capture.json
 */

window.layoutProbe = (function () {
  function px(v) { var n = parseFloat(v); return isNaN(n) ? 0 : n; }

  function text(el) {
    return (el && el.textContent ? el.textContent : '').replace(/\s+/g, ' ').trim();
  }

  function words(el) {
    var t = text(el);
    return t ? t.split(' ').length : 0;
  }

  /* Line count from the box height against one line's height, which holds for
     wrapped text where counting <br> and character widths does not. */
  function lineCount(el) {
    if (!el) return null;
    var style = getComputedStyle(el);
    var rect = el.getBoundingClientRect();
    var lh = px(style.lineHeight);
    if (!lh) {
      var size = px(style.fontSize) || 16;
      lh = size * 1.2;
    }
    var inner = rect.height - px(style.paddingTop) - px(style.paddingBottom);
    if (inner <= 0 || lh <= 0) return null;
    return Math.max(1, Math.round(inner / lh));
  }

  function visible(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    var s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
  }

  function findHero() {
    var marked = document.querySelector('[data-hero], .hero, #hero, [class*="hero" i], [id*="hero" i]');
    if (marked && visible(marked)) return marked;
    var h1 = document.querySelector('h1');
    if (!h1) return null;
    var node = h1;
    while (node && node !== document.body) {
      var tag = node.tagName.toLowerCase();
      if (tag === 'section' || tag === 'header' || tag === 'main') return node;
      node = node.parentElement;
    }
    return h1.parentElement;
  }

  /* The paragraph or lead directly under the headline, which is what the word
     and line caps are about. A sibling that is itself a button group is not it. */
  function findSubtext(hero, headline) {
    if (!hero) return null;
    var candidates = hero.querySelectorAll('p, [class*="subtitle" i], [class*="lead" i], [class*="subhead" i]');
    for (var i = 0; i < candidates.length; i++) {
      var el = candidates[i];
      if (!visible(el)) continue;
      if (headline && headline.contains(el)) continue;
      if (el.querySelector('a, button')) continue;
      if (words(el) < 3) continue;
      return el;
    }
    return null;
  }

  function ctaIn(root) {
    if (!root) return null;
    var all = root.querySelectorAll('a, button, [role="button"]');
    for (var i = 0; i < all.length; i++) {
      if (visible(all[i]) && text(all[i]).length > 1) return all[i];
    }
    return null;
  }

  function findNav() {
    var candidates = document.querySelectorAll('nav, header nav, [role="navigation"], header');
    for (var i = 0; i < candidates.length; i++) {
      if (visible(candidates[i])) return candidates[i];
    }
    return null;
  }

  /* How many rows the bar's own items landed on, taken from their distinct top
     offsets rather than from the bar's height, which padding alone can inflate. */
  function navRows(nav) {
    var items = nav.querySelectorAll('a, button, [role="menuitem"]');
    var tops = {};
    var count = 0;
    for (var i = 0; i < items.length; i++) {
      if (!visible(items[i])) continue;
      var top = Math.round(items[i].getBoundingClientRect().top / 8) * 8;
      if (!tops[top]) { tops[top] = 1; count++; }
    }
    return count || 1;
  }

  function quoteShapes() {
    var out = [];
    var nodes = document.querySelectorAll('blockquote, [class*="testimonial" i], [class*="quote" i]');
    for (var i = 0; i < nodes.length && out.length < 24; i++) {
      var el = nodes[i];
      if (!visible(el)) continue;
      var body = el.querySelector('p') || el;
      var sel = 'cite, footer, figcaption, [class*="author" i], [class*="attribution" i]';
      /* The attribution is as often a sibling of the quote as a child of it, so
         a miss inside the block is not evidence that the quote has none. */
      var cite = el.querySelector(sel) || (el.parentElement && el.parentElement.querySelector(sel));
      var who = text(cite);
      out.push({
        lines: lineCount(body),
        words: words(body),
        attribution: who.slice(0, 90),
        attributionHasRole: /,|\bat\b|\bof\b|·/.test(who)
      });
    }
    return out;
  }


  function describe(el) {
    if (!el) return null;
    var name = el.tagName.toLowerCase();
    var cls = (el.getAttribute('class') || '').trim().split(/\s+/).slice(0, 2).join('.');
    return cls ? name + '.' + cls : name;
  }

  /* A square child against a rounded container's corner arc sits outside it.
     For a corner of radius r, a child whose edge is dy from the container's
     edge along the other axis has to be inset by r - sqrt(r^2 - dy^2) for its
     corner to fall inside the curve. Nothing in source shows this: the radius
     and the padding are both correct-looking numbers, and only their ratio at
     the rendered height decides whether the button hangs off the end. */
  function roundedFit() {
    var out = [];
    var nodes = document.querySelectorAll('*');
    for (var i = 0; i < nodes.length && out.length < 40; i++) {
      var el = nodes[i];
      if (!el.children.length) continue;
      var cs = getComputedStyle(el);
      if (cs.overflow !== 'visible') continue;
      var box = el.getBoundingClientRect();
      if (box.width < 80 || box.height < 24) continue;
      var r = Math.min(px(cs.borderTopRightRadius), box.height / 2, box.width / 2);
      if (r < 16) continue;
      for (var j = 0; j < el.children.length; j++) {
        var kid = el.children[j];
        var kb = kid.getBoundingClientRect();
        if (!kb.width || !kb.height) continue;
        if (getComputedStyle(kid).position === 'absolute') continue;
        var corners = [
          { gap: kb.left - box.left, dy: Math.min(kb.top - box.top, box.bottom - kb.bottom) },
          { gap: box.right - kb.right, dy: Math.min(kb.top - box.top, box.bottom - kb.bottom) }
        ];
        for (var c = 0; c < corners.length; c++) {
          var dy = Math.max(0, r - Math.max(0, corners[c].dy));
          var need = r - Math.sqrt(Math.max(0, r * r - dy * dy));
          if (corners[c].gap + 0.5 < need) {
            out.push({
              container: describe(el),
              child: describe(kid),
              radius: Math.round(r),
              inset: Math.round(corners[c].gap * 10) / 10,
              needed: Math.round(need * 10) / 10
            });
            break;
          }
        }
      }
    }
    return out;
  }

  return {
    scan: function () {
      var vw = window.innerWidth || document.documentElement.clientWidth;
      var vh = window.innerHeight || document.documentElement.clientHeight;
      if (vw < 900) {
        return JSON.stringify({
          error: 'viewport is ' + vw + 'px; these gates measure desktop layout, so widen to 1280 and re-run'
        });
      }

      var hero = findHero();
      var headline = hero ? hero.querySelector('h1, h2, [class*="headline" i], [class*="title" i]') : null;
      var subtext = findSubtext(hero, headline);
      var cta = ctaIn(hero);
      var heroStyle = hero ? getComputedStyle(hero) : null;

      var nav = findNav();

      return JSON.stringify({
        captured: vw + 'x' + vh,
        viewport: { w: vw, h: vh },
        hero: hero ? {
          found: true,
          headlineLines: lineCount(headline),
          headlineText: text(headline).slice(0, 120),
          headlineWords: words(headline),
          subtextWords: subtext ? words(subtext) : null,
          subtextLines: subtext ? lineCount(subtext) : null,
          paddingTop: heroStyle ? px(heroStyle.paddingTop) : null,
          ctaInFold: cta ? cta.getBoundingClientRect().bottom <= vh : null,
          ctaText: cta ? text(cta).slice(0, 60) : null,
          height: hero.getBoundingClientRect().height
        } : { found: false },
        nav: nav ? {
          found: true,
          height: nav.getBoundingClientRect().height,
          rows: navRows(nav),
          items: nav.querySelectorAll('a, button').length
        } : { found: false },
        quotes: quoteShapes(),
        roundedFit: roundedFit()
      }, null, 2);
    }
  };
})();
