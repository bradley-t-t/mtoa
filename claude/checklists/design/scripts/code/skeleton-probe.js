/*
 * Captures a placeholder and the content that replaces it, so their
 * correspondence is settled by measurement rather than by eye.
 *
 * A placeholder that looks about right is the usual state of things: the box is
 * eight pixels short, the grid gap is a step off, the radius is square where the
 * avatar is round, and the page nudges when the data lands. None of that is
 * visible while writing the component and all of it is visible to the reader.
 * So both states get measured at the same viewport and diffed.
 *
 * Run it in the browser pane (javascript_tool), twice:
 *
 *   1. With the loading state on screen:
 *      skeletonProbe.capture('loading', { 'card row': '.card-skeleton', 'hero': '.hero-skeleton' })
 *
 *   2. Once the content has landed:
 *      skeletonProbe.capture('loaded',  { 'card row': '.card',          'hero': '.hero' })
 *
 *   3. Then:  skeletonProbe.result()
 *
 * For the timing half - whether the placeholders come and go as a set, and whether
 * what replaces them animates in - watch the load instead of measuring its ends:
 *
 *      await skeletonProbe.watch()   // reload or trigger the load while it runs
 *      skeletonProbe.result()
 *
 * Paste the result into a file and hand it to the run:
 *
 *   design-pass.py skeleton-probe --file capture.json
 *
 * Each named entry pairs a placeholder selector with the selector of whatever
 * replaces it. Both calls must use the same names and the same viewport.
 */

window.skeletonProbe = (function () {
  var takes = {};

  function measure(selector) {
    var nodes = Array.prototype.slice.call(document.querySelectorAll(selector));
    if (!nodes.length) return null;
    var first = nodes[0];
    var box = first.getBoundingClientRect();
    var style = getComputedStyle(first);
    var parent = first.parentElement;
    var parentStyle = parent ? getComputedStyle(parent) : null;
    var gap = parentStyle ? parseFloat(parentStyle.rowGap || parentStyle.gap || '0') : 0;

    // The union of every node in the group is what the reader actually sees move,
    // so the block's own height comes from the group rather than from item one.
    var top = Infinity, bottom = -Infinity, left = Infinity, right = -Infinity;
    nodes.forEach(function (n) {
      var r = n.getBoundingClientRect();
      top = Math.min(top, r.top); bottom = Math.max(bottom, r.bottom);
      left = Math.min(left, r.left); right = Math.max(right, r.right);
    });

    return {
      w: round(box.width),
      h: round(box.height),
      x: round(box.left + window.scrollX),
      y: round(box.top + window.scrollY),
      radius: style.borderRadius,
      count: nodes.length,
      gap: round(isNaN(gap) ? 0 : gap),
      aspect: box.height ? round(box.width / box.height, 3) : 0,
      blockH: round(bottom - top),
      blockY: round(top + window.scrollY),
      display: parentStyle ? parentStyle.display : '',
      columns: parentStyle ? parentStyle.gridTemplateColumns : ''
    };
  }

  function round(n, places) {
    var f = Math.pow(10, places === undefined ? 1 : places);
    return Math.round(n * f) / f;
  }

  return {
    /* phase is 'loading' or 'loaded'; map pairs a name to a selector. */
    capture: function (phase, map) {
      var out = {};
      Object.keys(map).forEach(function (name) {
        var m = measure(map[name]);
        if (!m) out[name] = { missing: map[name] };
        else out[name] = m;
      });
      takes[phase] = { viewport: window.innerWidth + 'x' + window.innerHeight, entries: out };
      var missing = Object.keys(out).filter(function (k) { return out[k].missing; });
      return JSON.stringify({
        phase: phase,
        viewport: takes[phase].viewport,
        measured: Object.keys(out).length - missing.length,
        missing: missing
      });
    },

    result: function () {
      if (!takes.loading || !takes.loaded) {
        return JSON.stringify({ error: 'capture both phases first: loading, then loaded' });
      }
      if (takes.loading.viewport !== takes.loaded.viewport) {
        return JSON.stringify({
          error: 'the two captures were taken at different viewports (' +
            takes.loading.viewport + ' and ' + takes.loaded.viewport + ')'
        });
      }
      var items = [];
      Object.keys(takes.loading.entries).forEach(function (name) {
        var a = takes.loading.entries[name];
        var b = takes.loaded.entries[name];
        if (!b || a.missing || b.missing) return;
        items.push({
          name: name,
          skeleton: a,
          content: b,
          // What everything below the block moves by when the data lands.
          shift: round((b.blockY + b.blockH) - (a.blockY + a.blockH))
        });
      });
      var out = { captured: takes.loading.viewport, items: items };
      if (takes.resolution) out.resolution = takes.resolution;
      return JSON.stringify(out, null, 2);
    },

    /* Watches a load happen, rather than measuring its two ends.
     *
     * The defect this exists for is not visible in a before-and-after pair: the
     * top block resolves, everything below jumps, then the next block resolves
     * and it jumps again. Both ends can measure identical while the reader was
     * moved around twice in between. So the placeholders are counted every frame,
     * and the page is re-measured each time that count changes.
     *
     *   await skeletonProbe.watch()          // then trigger the load, or reload first
     *   skeletonProbe.result()
     */
    watch: function (rootSelector, timeoutMs) {
      var root = (rootSelector && document.querySelector(rootSelector)) || document.body;
      var SEL = '[class*="skeleton" i],[class*="shimmer" i],[class*="placeholder" i],' +
                '[data-loading],[aria-busy="true"]';

      function count() { return root.querySelectorAll(SEL).length; }
      /* Only what the reader can see. A footer sitting a screen below the
         placeholder is pushed the length of the page when the route lands, and
         counting that reports the page as having moved under someone who never
         saw it move. */
      function anchors() {
        return Array.prototype.map.call(root.querySelectorAll('section,article,table,form,ul,h1,h2'),
          function (el) {
            var r = el.getBoundingClientRect();
            return {
              el: el,
              y: r.top + window.scrollY,
              onScreen: r.bottom > 0 && r.top < window.innerHeight,
            };
          });
      }

      var moments = [], entrances = [], start = performance.now();
      var last = count(), marks = anchors(), settledFor = 0;

      /* Anything inserted while placeholders are still clearing is arriving
         content, so its computed transition or animation at insert time is what
         says whether it animated in or simply appeared. */
      var observer = new MutationObserver(function (records) {
        records.forEach(function (r) {
          Array.prototype.forEach.call(r.addedNodes, function (n) {
            if (n.nodeType !== 1 || n.matches(SEL)) return;
            var st = getComputedStyle(n);
            var animated = (st.animationName && st.animationName !== 'none') ||
                           (st.transitionDuration && parseFloat(st.transitionDuration) > 0);
            entrances.push({ node: n.tagName.toLowerCase() +
              (typeof n.className === 'string' && n.className ? '.' + n.className.trim().split(/\s+/)[0] : ''),
              animated: !!animated });
          });
        });
      });
      observer.observe(root, { childList: true, subtree: true });

      return new Promise(function (resolve) {
        function frame() {
          var now = count();
          if (now !== last) {
            // Placeholders came or went: measure what that did to everything else.
            var moved = 0, next = anchors();
            marks.forEach(function (m) {
              if (!m.onScreen) return;
              var found = next.filter(function (n) { return n.el === m.el; })[0];
              if (found) moved += Math.abs(found.y - m.y);
            });
            moments.push({
              at: Math.round(performance.now() - start),
              from: last, to: now, shifted: Math.round(moved * 10) / 10
            });
            marks = next;
            last = now;
            settledFor = 0;
          } else if (last === 0) {
            settledFor++;
          }
          var done = (last === 0 && settledFor > 30) ||
                     performance.now() - start > (timeoutMs || 15000);
          if (done) {
            observer.disconnect();
            takes.resolution = {
              moments: moments,
              distinctMoments: moments.length,
              totalShift: moments.reduce(function (a, m) { return a + m.shifted; }, 0),
              inserted: entrances.length,
              animatedIn: entrances.filter(function (e) { return e.animated; }).length,
              stillPending: last
            };
            resolve(JSON.stringify(takes.resolution, null, 2));
          } else {
            requestAnimationFrame(frame);
          }
        }
        requestAnimationFrame(frame);
      });
    },

    reset: function () { takes = {}; return 'cleared'; }
  };
})();
