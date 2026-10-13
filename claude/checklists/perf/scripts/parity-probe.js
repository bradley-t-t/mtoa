// Captures what a rendered page looks like, so that "nothing visible changed" is
// settled by comparing the page to itself rather than by anybody's word for it.
//
// Every rule in the performance checklist changes how bytes reach the browser and
// none of them change what those bytes describe. Splitting a route, offering an
// image at more widths, expressing a slide as a transform: each is invisible when
// it is done right and each is one line away from moving the page. So the page is
// measured before the work and again after, and the two are diffed.
//
// What is recorded, per element: where its box is, how big it is, and the computed
// values of the properties a reader would notice changing. Plus the route inventory,
// the interactive controls and whether each has a handler bound, the running
// animations, and anything the page threw.
//
// Run it in the page, in the browser pane or through Playwright:
//
//   1. Load the built site at the first width and evaluate this file.
//   2. parityProbe.capture({ route: '/', built: true })
//   3. Resize, or navigate to the next route, and capture again.
//   4. copy(parityProbe.result())   - or read the return value
//
// Then file it:
//
//   perf-pass.py parity --phase before --file capture.json
//
// The after capture must cover the same routes at the same widths, and must be taken
// against the built output: a dev server renders a page the visitor never receives.

window.parityProbe = (function () {
  var takes = [];
  var errors = [];

  // Errors are collected from the moment this loads, because a capture taken after
  // the page settled would miss everything thrown while it was starting.
  window.addEventListener('error', function (event) {
    errors.push({
      level: 'error',
      text: event.message || ('failed to load ' + ((event.target && (event.target.src ||
        event.target.href)) || 'a resource'))
    });
  }, true);
  window.addEventListener('unhandledrejection', function (event) {
    var reason = event.reason;
    errors.push({ level: 'error', text: String((reason && reason.message) || reason) });
  });
  var priorError = console.error;
  console.error = function () {
    errors.push({ level: 'error', text: Array.prototype.join.call(arguments, ' ').slice(0, 300) });
    return priorError.apply(console, arguments);
  };

  var STYLE_PROPS = ['color', 'backgroundColor', 'fontFamily', 'fontSize', 'fontWeight',
    'lineHeight', 'letterSpacing', 'textAlign', 'borderRadius', 'boxShadow', 'opacity',
    'transform', 'display', 'flexDirection', 'justifyContent', 'alignItems', 'gap',
    'padding', 'margin', 'borderColor', 'borderWidth', 'textTransform', 'zIndex',
    'overflow', 'objectFit'];

  // Elements a reader would notice moving. Every node on the page is thousands of
  // rows of noise; these are the ones carrying the layout.
  var WANTED = 'h1,h2,h3,h4,h5,h6,p,img,picture,video,svg,button,a,input,select,textarea,' +
    'label,li,th,td,section,header,footer,nav,main,article,aside,form,figure,blockquote';

  function round(n) { return Math.round(n * 10) / 10; }

  function textOf(el) {
    var own = '';
    for (var i = 0; i < el.childNodes.length; i += 1) {
      if (el.childNodes[i].nodeType === 3) own += el.childNodes[i].nodeValue;
    }
    return own.replace(/\s+/g, ' ').trim().slice(0, 40);
  }

  // A key that survives the work. A path of child indexes breaks the moment a
  // split wraps a route in one more element, which is the commonest correct fix in
  // the checklist and would report as a page-wide regression. Identity is taken
  // from what the element is and what it says instead, and a counter separates the
  // ones that are genuinely alike.
  function keyOf(el, seen) {
    var parts = [el.tagName.toLowerCase()];
    if (el.id) parts.push('#' + el.id);
    var cls = (typeof el.className === 'string' ? el.className : '').trim().split(/\s+/)
      .filter(Boolean).filter(function (c) { return !/^(css|sc|jsx)-|^[a-z]+_[A-Za-z0-9]{5,}$/.test(c); });
    if (cls.length) parts.push('.' + cls.slice(0, 2).join('.'));
    var text = textOf(el);
    if (text) parts.push('"' + text + '"');
    else if (el.tagName === 'IMG' && el.getAttribute('src')) {
      parts.push('[' + String(el.getAttribute('src')).split('/').pop().slice(0, 30) + ']');
    } else if (el.getAttribute('aria-label')) {
      parts.push('[' + el.getAttribute('aria-label').slice(0, 30) + ']');
    }
    var key = parts.join('');
    seen[key] = (seen[key] || 0) + 1;
    return seen[key] > 1 ? key + '~' + seen[key] : key;
  }

  function visible(el, box) {
    if (box.width < 1 || box.height < 1) return false;
    var style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
  }

  function bound(el) {
    // An inert control is the signature of a deferred module that never arrived, so
    // whether anything is listening matters more than what it would do.
    if (el.tagName === 'A' && el.getAttribute('href')) return true;
    if (el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA') return true;
    if (el.getAttribute('type') === 'submit' || el.closest('form')) return true;
    if (el.onclick) return true;
    var react = Object.keys(el).some(function (k) {
      return k.indexOf('__reactProps') === 0 || k.indexOf('__reactEventHandlers') === 0;
    });
    return react;
  }

  function animationsOn(el, key) {
    var out = [];
    if (typeof el.getAnimations !== 'function') return out;
    el.getAnimations().forEach(function (a) {
      var timing = a.effect && a.effect.getTiming ? a.effect.getTiming() : {};
      out.push({
        sel: key,
        name: (a.animationName || (a.effect && a.effect.target && a.transitionProperty) || 'animation'),
        duration: timing.duration || 0
      });
    });
    return out;
  }

  function sameOriginRoutes() {
    var seen = {};
    Array.prototype.forEach.call(document.querySelectorAll('a[href]'), function (a) {
      var href = a.getAttribute('href') || '';
      if (/^(https?:)?\/\//.test(href)) {
        try { if (new URL(a.href).origin !== location.origin) return; } catch (e) { return; }
      } else if (/^(mailto:|tel:|#|javascript:)/.test(href)) return;
      var path = href;
      try { path = new URL(a.href).pathname; } catch (e) { /* keep the raw value */ }
      seen[path] = true;
    });
    return Object.keys(seen).sort();
  }

  return {
    // One capture, at the current width, of the route currently on screen.
    // opts.route names it, opts.built says whether this is the production build.
    capture: function (opts) {
      opts = opts || {};
      var seen = {};
      var elements = [];
      var animations = [];
      var interactive = [];
      Array.prototype.forEach.call(document.querySelectorAll(WANTED), function (el) {
        var box = el.getBoundingClientRect();
        if (!visible(el, box)) return;
        var key = keyOf(el, seen);
        var style = getComputedStyle(el);
        var recorded = {};
        STYLE_PROPS.forEach(function (prop) { recorded[prop] = style[prop]; });
        elements.push({
          sel: key,
          x: round(box.left + window.scrollX),
          y: round(box.top + window.scrollY),
          w: round(box.width),
          h: round(box.height),
          style: recorded
        });
        animations = animations.concat(animationsOn(el, key));
        if (/^(A|BUTTON|INPUT|SELECT|TEXTAREA)$/.test(el.tagName) ||
            el.getAttribute('role') === 'button') {
          interactive.push({
            sel: key,
            tag: el.tagName.toLowerCase(),
            label: (textOf(el) || el.getAttribute('aria-label') || '').slice(0, 40),
            bound: bound(el)
          });
        }
      });
      var take = {
        route: opts.route || location.pathname,
        url: location.href,
        built: opts.built !== false,
        viewport: { w: window.innerWidth, h: window.innerHeight },
        dpr: window.devicePixelRatio || 1,
        elements: elements,
        interactive: interactive,
        animations: animations,
        routes: sameOriginRoutes(),
        console: errors.slice(0, 40),
        documentHeight: round(document.documentElement.scrollHeight)
      };
      takes.push(take);
      return { route: take.route, viewport: take.viewport.w, elements: elements.length,
               controls: interactive.length, animations: animations.length,
               errors: take.console.length };
    },

    // Everything captured so far, ready to be written to a file.
    result: function () { return JSON.stringify({ captures: takes }, null, 1); },

    takes: function () { return takes; },

    reset: function () { takes = []; errors = []; }
  };
})();
