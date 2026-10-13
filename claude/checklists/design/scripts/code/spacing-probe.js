/*
 * Walks the rendered page and measures the space between things, which is the
 * half of spacing that source review cannot see.
 *
 * Every padding value in a stylesheet can be on the scale while two panels still
 * end up sharing an edge, one gap in a stack comes out wider than its neighbours,
 * or a group is spaced exactly like the space around it. Those are relationships,
 * and they only exist once the page is laid out.
 *
 * Run it in the browser pane:
 *
 *   spacingProbe.scan()               // whole document
 *   spacingProbe.scan('main')         // or scoped to a root
 *
 * Save the result and hand it to the run:
 *
 *   design-pass.py spacing-probe --file capture.json
 */

window.spacingProbe = (function () {
  var SCALE_STEP = 4;

  function px(v) { var n = parseFloat(v); return isNaN(n) ? 0 : n; }

  /* A block that reads as its own container: it has a border, a distinct
     background, or a shadow. These are the ones whose edges must not meet. */
  function isPanel(el, style) {
    var hasBorder = px(style.borderTopWidth) + px(style.borderBottomWidth) +
                    px(style.borderLeftWidth) + px(style.borderRightWidth) > 0;
    return hasBorder || surfaced(style) || shadowed(style);
  }

  function surfaced(style) {
    return Boolean(style.backgroundColor) &&
           style.backgroundColor !== 'rgba(0, 0, 0, 0)' &&
           style.backgroundColor !== 'transparent';
  }

  function shadowed(style) {
    return Boolean(style.boxShadow) && style.boxShadow !== 'none';
  }

  /* Whether the container draws the edge in question at all. A measure wrapper
     ruled down its two sides has no top or bottom for content to be flush
     against, and reporting one is reporting a line that is not there. */
  function hasEdge(style, side) {
    var width = side === 'top' ? style.borderTopWidth : style.borderBottomWidth;
    return px(width) > 0 || surfaced(style) || shadowed(style);
  }

  function visible(el) {
    var r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    var s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && s.position !== 'fixed';
  }

  function describe(el) {
    var id = el.id ? '#' + el.id : '';
    var cls = typeof el.className === 'string' && el.className
      ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.')
      : '';
    var text = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 32);
    return el.tagName.toLowerCase() + id + cls + (text ? ' "' + text + '"' : '');
  }

  return {
    scan: function (rootSelector) {
      var root = rootSelector ? document.querySelector(rootSelector) : document.body;
      if (!root) return JSON.stringify({ error: 'root not found: ' + rootSelector });

      /* A collapsed viewport measures every element at zero width, which filters
         out most of the page and would let a broken capture read as a clean one.
         Refuse rather than return something that looks like a pass. */
      var vw = window.innerWidth || document.documentElement.clientWidth;
      var vh = window.innerHeight || document.documentElement.clientHeight;
      if (vw < 200 || vh < 200) {
        return JSON.stringify({
          error: 'viewport measures ' + vw + 'x' + vh + '; size the window before capturing'
        });
      }

      var adjacent = [];   // pairs of stacked siblings whose edges meet
      var doubleBorder = [];
      var stacks = [];     // gap sequences within one parent
      var inset = [];      // children flush against their container's edge
      var tail = [];       // last child flush with the container bottom
      var containers = 0;

      var all = root.querySelectorAll('*');
      Array.prototype.forEach.call(all, function (parent) {
        var kids = Array.prototype.filter.call(parent.children, visible);
        if (kids.length < 2) return;

        var parentStyle = getComputedStyle(parent);
        // Only vertical stacks: a row's horizontal gaps are a different question.
        var stacked = kids.every(function (k, i) {
          if (i === 0) return true;
          var prev = kids[i - 1].getBoundingClientRect();
          var cur = k.getBoundingClientRect();
          return cur.top >= prev.bottom - 1;
        });
        if (!stacked) return;
        containers++;

        var gaps = [];
        for (var i = 1; i < kids.length; i++) {
          var a = kids[i - 1], b = kids[i];
          var ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
          var gap = Math.round((rb.top - ra.bottom) * 10) / 10;
          gaps.push(gap);

          var sa = getComputedStyle(a), sb = getComputedStyle(b);
          if (gap <= 0.5 && isPanel(a, sa) && isPanel(b, sb)) {
            // Two rules stacked read as a double edge; one rule between two
            // blocks is a seam, which is what a stack of bands is made of.
            // Neither, at zero gap, is the defect this counts.
            var above = px(sa.borderBottomWidth) > 0;
            var below = px(sb.borderTopWidth) > 0;
            if (above && below) {
              doubleBorder.push({ a: describe(a), b: describe(b), gap: gap, parent: describe(parent) });
            } else if (!above && !below) {
              adjacent.push({ a: describe(a), b: describe(b), gap: gap, parent: describe(parent) });
            }
          }
        }

        var distinct = {};
        gaps.forEach(function (g) { distinct[Math.round(g / SCALE_STEP) * SCALE_STEP] = true; });
        stacks.push({
          parent: describe(parent),
          gaps: gaps,
          distinct: Object.keys(distinct).map(Number).sort(function (x, y) { return x - y; }),
          padTop: px(parentStyle.paddingTop),
          padBottom: px(parentStyle.paddingBottom)
        });

        var pr = parent.getBoundingClientRect();
        var first = kids[0].getBoundingClientRect();
        var last = kids[kids.length - 1].getBoundingClientRect();
        if (hasEdge(parentStyle, 'top') && first.top - pr.top <= 0.5) {
          inset.push({ container: describe(parent), child: describe(kids[0]), edge: 'top' });
        }
        if (hasEdge(parentStyle, 'bottom') && pr.bottom - last.bottom <= 0.5) {
          tail.push({ container: describe(parent), child: describe(kids[kids.length - 1]) });
        }
      });

      /* Gap distribution across the page, for the tier check: steps that nearly
         match read as inconsistency rather than as hierarchy. */
      var allGaps = {};
      stacks.forEach(function (s) {
        s.gaps.forEach(function (g) {
          if (g <= 0) return;
          var k = Math.round(g);
          allGaps[k] = (allGaps[k] || 0) + 1;
        });
      });

      /* A stack whose gaps are not all the same, ignoring single-gap stacks. */
      var uneven = stacks.filter(function (s) {
        return s.gaps.length > 1 && s.distinct.filter(function (g) { return g > 0; }).length > 1;
      }).map(function (s) {
        return { parent: s.parent, gaps: s.gaps };
      });

      return JSON.stringify({
        captured: vw + 'x' + vh,
        containers: containers,
        adjacent: adjacent,
        doubleBorder: doubleBorder,
        uneven: uneven,
        inset: inset,
        tail: tail,
        gapHistogram: allGaps
      }, null, 2);
    }
  };
})();
