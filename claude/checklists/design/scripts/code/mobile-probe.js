/*
 * Measures the things a phone decides and a desktop review cannot see: whether
 * the bar is still reachable a thousand pixels down, whether the last row of an
 * open menu is above the browser's own toolbar or behind it, whether a bar
 * pinned to the bottom edge covers the end of the page, whether the document
 * scrolls sideways, and whether every control is big enough for a thumb.
 *
 * None of these can be read from source. A menu capped in vh looks correct in
 * the markup and loses its last two rows to the URL bar; a fixed bottom rail
 * looks correct until the footer ends underneath it.
 *
 * Run it in the browser pane at a phone width, on a page whose menu can be
 * opened. It opens the menu itself, so it returns a promise:
 *
 *   await mobileProbe.scan()
 *
 * Save the result and hand it to the run:
 *
 *   design-pass.py mobile-probe --file capture.json
 */

window.mobileProbe = (function () {
  var TAP_MIN = 44;
  var PHONE_MAX = 500;
  var SETTLE_MS = 450;

  function scrollNow(y) {

    var root = document.documentElement;

    var was = root.style.scrollBehavior;

    root.style.scrollBehavior = 'auto';

    window.scrollTo(0, y);

    root.style.scrollBehavior = was;

  }


  function px(v) { var n = parseFloat(v); return isNaN(n) ? 0 : n; }

  function wait(ms) {
    return new Promise(function (done) { setTimeout(done, ms); });
  }

  function visible(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    var s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
  }

  function name(el) {
    if (!el) return '';
    var id = el.id ? '#' + el.id : '';
    var cls = typeof el.className === 'string' && el.className
      ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.')
      : '';
    return el.tagName.toLowerCase() + id + cls;
  }

  function box(el) {
    var r = el.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, left: r.left, right: r.right,
             width: r.width, height: r.height };
  }

  /* The bar a visitor navigates from: the banner landmark, else the first
     header that holds a nav. */
  function findBar() {
    var marked = document.querySelector('header[role="banner"], [role="banner"], header');
    if (marked && visible(marked)) return marked;
    var nav = document.querySelector('nav[aria-label], nav');
    return nav && visible(nav) ? nav : null;
  }

  /* Anything the page has pinned to the bottom edge of the viewport. */
  function findBottomFixed() {
    var out = [];
    var all = document.body.querySelectorAll('*');
    for (var i = 0; i < all.length; i += 1) {
      var el = all[i];
      if (!visible(el)) continue;
      var s = getComputedStyle(el);
      if (s.position !== 'fixed' && s.position !== 'sticky') continue;
      var r = el.getBoundingClientRect();
      if (Math.abs(r.bottom - window.innerHeight) > 4) continue;
      if (r.height > window.innerHeight * 0.5) continue;
      /* The outermost pinned element only; its children inherit the position. */
      if (out.some(function (o) { return o.el.contains(el); })) continue;
      out.push({ el: el, rect: r, style: s });
    }
    return out;
  }

  /* The control that opens the small-screen menu, and the element it names. */
  function findMenuToggle() {
    var controls = document.querySelectorAll('[aria-controls][aria-expanded]');
    for (var i = 0; i < controls.length; i += 1) {
      var el = controls[i];
      if (!visible(el)) continue;
      var panel = document.getElementById(el.getAttribute('aria-controls'));
      if (!panel) continue;
      return { toggle: el, panel: panel };
    }
    return null;
  }

  function rows(panel) {
    return Array.prototype.slice
      .call(panel.querySelectorAll('a[href], button, [role="menuitem"]'))
      .filter(visible);
  }

  function scrollsInside(el) {
    for (var node = el; node && node !== document.body; node = node.parentElement) {
      var s = getComputedStyle(node);
      var overflowing = node.scrollHeight - node.clientHeight > 2;
      if (/(auto|scroll)/.test(s.overflowY) && overflowing) {
        return { found: true, on: name(node), overscroll: s.overscrollBehaviorY };
      }
    }
    return { found: false, on: '', overscroll: '' };
  }

  /* The deepest thing the document itself lays out, ignoring the fixed chrome
     over it. What a bottom rail can cover. */
  // The end of the page is the deepest thing that scrolls with it. A row
  // inside a fixed panel does not, and the menu is open by the time this runs,
  // so without this the floor is a drawer row and every pinned bar reads as
  // covering the end of the page.
  function pinnedAncestor(el) {
    for (var node = el; node && node !== document.body; node = node.parentElement) {
      if (getComputedStyle(node).position === 'fixed') return true;
    }
    return false;
  }

  function documentFloor() {
    var deepest = null;
    var all = document.body.querySelectorAll('*');
    for (var i = 0; i < all.length; i += 1) {
      var el = all[i];
      if (!visible(el) || el.children.length) continue;
      if (pinnedAncestor(el)) continue;
      var r = el.getBoundingClientRect();
      if (r.height < 1) continue;
      if (!deepest || r.bottom > deepest.bottom) deepest = { el: el, bottom: r.bottom };
    }
    return deepest;
  }

  function measureReach() {
    var bar = findBar();
    if (!bar) return { found: false };
    var style = getComputedStyle(bar);
    var start = box(bar);
    var depth = Math.min(2000, Math.max(0, document.documentElement.scrollHeight - window.innerHeight));
    var was = window.scrollY;
    scrollNow(depth);
    var after = box(bar);
    var pinned = getComputedStyle(bar).position;
    var railed = findBottomFixed().length > 0;
    scrollNow(was);
    return {
      found: true,
      element: name(bar),
      position: style.position,
      positionAtDepth: pinned,
      scrolledTo: depth,
      topAtRest: start.top,
      topAtDepth: after.top,
      onScreenAtDepth: after.bottom > 0 && after.top < window.innerHeight,
      bottomRail: railed,
    };
  }

  function measureRail() {
    var pinned = findBottomFixed();
    if (!pinned.length) return { found: false };
    var was = window.scrollY;
    scrollNow(document.documentElement.scrollHeight);
    var floor = documentFloor();
    var bars = pinned.map(function (p) {
      var r = p.el.getBoundingClientRect();
      return {
        element: name(p.el),
        height: r.height,
        top: r.top,
        paddingBottom: px(p.style.paddingBottom),
        covers: floor ? Math.max(0, floor.bottom - r.top) : null,
        coveredElement: floor ? name(floor.el) : '',
      };
    });
    scrollNow(was);
    return { found: true, bars: bars };
  }

  function measureTaps(root) {
    var scope = root || document.body;
    var small = [];
    var all = scope.querySelectorAll('a[href], button, input, select, textarea, [role="button"]');
    for (var i = 0; i < all.length; i += 1) {
      var el = all[i];
      if (!visible(el)) continue;
      var r = el.getBoundingClientRect();
      if (r.height >= TAP_MIN && r.width >= TAP_MIN) continue;
      /* A link inside a sentence is read, not tapped as a target. */
      if (el.tagName === 'A' && el.closest('p, li, address, figcaption')) continue;
      small.push({ element: name(el), width: Math.round(r.width), height: Math.round(r.height),
                   label: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 40) });
      if (small.length >= 40) break;
    }
    return small;
  }

  function measureSideScroll() {
    var doc = document.scrollingElement || document.documentElement;
    var over = doc.scrollWidth - window.innerWidth;
    var culprits = [];
    if (over > 1) {
      var all = document.body.querySelectorAll('*');
      for (var i = 0; i < all.length; i += 1) {
        var el = all[i];
        if (!visible(el)) continue;
        var r = el.getBoundingClientRect();
        if (r.right <= window.innerWidth + 1 && r.left >= -1) continue;
        if (getComputedStyle(el).position === 'fixed') continue;
        culprits.push({ element: name(el), right: Math.round(r.right), left: Math.round(r.left) });
        if (culprits.length >= 6) break;
      }
    }
    return { overflowPx: Math.max(0, over), culprits: culprits };
  }

  async function measureMenu() {
    var pair = findMenuToggle();
    if (!pair) return { found: false, reason: 'no control carrying aria-controls and aria-expanded' };
    var opened = pair.toggle.getAttribute('aria-expanded') === 'true';
    if (!opened) {
      pair.toggle.click();
      await wait(SETTLE_MS);
    }
    if (!visible(pair.panel)) {
      return { found: false, reason: 'the panel named by aria-controls did not become visible' };
    }
    var panelRect = pair.panel.getBoundingClientRect();
    var scroller = scrollsInside(pair.panel);
    var items = rows(pair.panel);
    var below = items
      .filter(function (el) { return el.getBoundingClientRect().bottom > window.innerHeight + 1; })
      .map(function (el) {
        return { element: name(el), bottom: Math.round(el.getBoundingClientRect().bottom),
                 label: (el.textContent || '').trim().slice(0, 40) };
      });
    var taps = measureTaps(pair.panel);
    if (!opened) {
      pair.toggle.click();
      await wait(SETTLE_MS);
    }
    return {
      found: true,
      panel: name(pair.panel),
      rows: items.length,
      height: panelRect.height,
      bottom: panelRect.bottom,
      viewportHeight: window.innerHeight,
      overflowsViewport: panelRect.bottom > window.innerHeight + 1,
      scrollsInside: scroller.found,
      scroller: scroller.on,
      overscroll: scroller.overscroll,
      rowsBelowFold: below,
      smallTargets: taps,
    };
  }

  async function scan() {
    if (window.innerWidth > PHONE_MAX) {
      return { error: 'this is a phone reading and the viewport is ' + window.innerWidth
                      + 'px wide; resize to 390x844 or narrower and run it again' };
    }
    var menu = await measureMenu();
    return {
      captured: window.innerWidth + 'x' + window.innerHeight,
      url: location.href,
      reach: measureReach(),
      menu: menu,
      rail: measureRail(),
      sideScroll: measureSideScroll(),
      smallTargets: measureTaps(null),
    };
  }

  return { scan: scan, TAP_MIN: TAP_MIN };
})();
