/*
 * Measures the agency bar against the footer it belongs to.
 *
 * The rail paints no ground and names no ink, so every tone it shows is mixed
 * from whatever colour context it was mounted in. That is what lets one
 * component sit on a white brochure site and a dark dashboard. It is also the
 * whole failure mode: mounted at the app root instead of inside the footer
 * band, it inherits the page body, and on a site whose footer is dark that
 * renders a pale strip hanging underneath it. Source cannot show this. The
 * markup is identical either way and every colour in it is correct in
 * isolation; only the laid-out page knows which ground the rail ended up on.
 *
 * So the reading is comparative: the ground actually painted behind the rail
 * against the ground painted behind the footer, plus the contrast the rail's
 * ink achieves on it, plus the geometry that is supposed to be identical on
 * every site.
 *
 *   railProbe.scan({marker: '<agency.bar.marker>', domain: '<agency.domain>'})
 *
 * The two values are the profile's: the attribute that marks the bar, and the
 * domain its attribution links to. `stack-pass.py rail-probe --script` prints
 * this file with them already set, after which a bare railProbe.scan() reads
 * them; so do data-marker and data-domain on the script tag that loads it. The
 * result reaches the run through:
 *
 *   stack-pass.py rail-probe --file <capture.json>
 */

window.railProbe = (function () {
  var TRANSPARENT = /^(transparent|rgba\(0,\s*0,\s*0,\s*0\)|color\([^)]*\/\s*0\))$/;
  var loadedBy = document.currentScript;

  function settings(options) {
    var given = options || window.railProbeConfig || {};
    var data = (loadedBy && loadedBy.dataset) || {};
    return {
      marker: String(given.marker || data.marker || ''),
      domain: String(given.domain || data.domain || '')
    };
  }

  function px(v) {
    var n = parseFloat(v);
    return isNaN(n) ? 0 : n;
  }

  /* Computed colours arrive as rgb(), rgba() or color(srgb r g b / a), and the
     comparison has to work across all three. Anything with a channel list is
     reduced to 0-255 triples plus alpha; a colour that parses to nothing is
     reported rather than guessed at. */
  function parse(value) {
    if (!value) return null;
    var nums = String(value).match(/-?[\d.]+(?:e-?\d+)?/g);
    if (!nums || nums.length < 3) return null;
    var scaled = /color\(/.test(value) && Math.max(+nums[0], +nums[1], +nums[2]) <= 1;
    var r = +nums[0], g = +nums[1], b = +nums[2];
    if (scaled) { r *= 255; g *= 255; b *= 255; }
    return { r: r, g: g, b: b, a: nums.length > 3 ? +nums[3] : 1 };
  }

  function opaque(value) {
    if (!value || TRANSPARENT.test(String(value).replace(/\s+/g, ' ').trim())) return false;
    var c = parse(value);
    return !!c && c.a > 0.02;
  }

  function over(top, bottom) {
    if (!top || !bottom) return top || bottom;
    var a = top.a;
    return {
      r: top.r * a + bottom.r * (1 - a),
      g: top.g * a + bottom.g * (1 - a),
      b: top.b * a + bottom.b * (1 - a),
      a: 1
    };
  }

  function luminance(c) {
    var parts = [c.r, c.g, c.b].map(function (v) {
      var s = v / 255;
      return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2];
  }

  function contrast(ink, ground) {
    if (!ink || !ground) return null;
    var a = luminance(over(ink, ground)) + 0.05;
    var b = luminance(ground) + 0.05;
    return Math.round((Math.max(a, b) / Math.min(a, b)) * 100) / 100;
  }

  function hex(c) {
    if (!c) return null;
    function two(v) {
      var s = Math.max(0, Math.min(255, Math.round(v))).toString(16);
      return s.length < 2 ? '0' + s : s;
    }
    return '#' + two(c.r) + two(c.g) + two(c.b);
  }

  /* The ground a visitor actually sees behind an element: the first ancestor
     that paints one. An element on a transparent stack shows whatever is
     furthest up, which is the body, and that is exactly the case worth
     catching. */
  function groundBehind(el) {
    var node = el;
    while (node && node !== document.documentElement) {
      var bg = getComputedStyle(node).backgroundColor;
      if (opaque(bg)) {
        return { colour: hex(parse(bg)), raw: bg, from: describe(node) };
      }
      node = node.parentElement;
    }
    var root = getComputedStyle(document.documentElement).backgroundColor;
    return { colour: hex(parse(root)), raw: root, from: 'html' };
  }

  function describe(el) {
    if (!el) return null;
    var name = el.tagName.toLowerCase();
    var cls = (el.getAttribute('class') || '').trim().split(/\s+/).slice(0, 3).join('.');
    return cls ? name + '.' + cls : name;
  }

  function chain(el, stop) {
    var out = [], node = el;
    while (node && node !== stop && out.length < 8) {
      out.push(describe(node));
      node = node.parentElement;
    }
    return out;
  }

  return {
    scan: function (options) {
      var named = settings(options);
      if (!named.marker) {
        throw new Error('railProbe.scan needs the bar\'s marker attribute: pass ' +
          '{marker, domain}, or print the probe with stack-pass.py rail-probe --script');
      }
      var marker = window.CSS && CSS.escape ? CSS.escape(named.marker) : named.marker;
      var rails = document.querySelectorAll('[' + marker + ']');
      /* A hand-rolled credit in the footer sits beside the shared rail rather
         than replacing it, so the page ends up signed twice. Counting the
         attribution links catches that: the copy carries no rail marker, so
         counting rails alone reads it as absent. */
      var signatures = !named.domain ? [] : [].slice
        .call(document.querySelectorAll(
          'a[href*="' + named.domain.replace(/["\\]/g, '\\$&') + '"]'))
        .filter(function (a) { return !/\/status/.test(a.getAttribute('href') || ''); });
      var rail = rails[0];
      if (!rail) {
        return JSON.stringify({
          captured: location.pathname,
          viewport: innerWidth + 'x' + innerHeight,
          rail: { found: false, count: 0, signatures: signatures.length }
        }, null, 2);
      }

      var style = getComputedStyle(rail);
      var rect = rail.getBoundingClientRect();
      var railGround = groundBehind(rail);
      var ink = parse(style.color);

      var footer = document.querySelector('footer');
      var footerGround = footer ? groundBehind(footer) : null;
      var footerInk = footer ? parse(getComputedStyle(footer).color) : null;

      var groundColour = parse(railGround.raw);
      var links = rail.querySelectorAll('a');
      var linkInk = links.length ? parse(getComputedStyle(links[0]).color) : null;
      var linkOpacity = links.length ? parseFloat(getComputedStyle(links[0]).opacity) : null;
      if (linkInk && linkOpacity != null && !isNaN(linkOpacity)) linkInk.a *= linkOpacity;

      return JSON.stringify({
        captured: location.pathname,
        viewport: innerWidth + 'x' + innerHeight,
        rail: {
          found: true,
          count: rails.length,
          signatures: signatures.length,
          insideFooter: !!(footer && footer.contains(rail)),
          ancestry: chain(rail.parentElement, document.body),
          paintsOwnGround: opaque(style.backgroundColor),
          ownBackground: style.backgroundColor,
          ink: hex(ink),
          ground: railGround.colour,
          groundFrom: railGround.from,
          contrast: contrast(ink, groundColour),
          linkContrast: contrast(linkInk, groundColour),
          height: Math.round(rect.height),
          width: Math.round(rect.width),
          fontSize: px(style.fontSize),
          letterSpacing: style.letterSpacing,
          borderTop: style.borderTopColor,
          display: style.display,
          justifyContent: style.justifyContent,
          groups: rail.children.length - rail.querySelectorAll('style').length
        },
        footer: footer ? {
          found: true,
          ground: footerGround.colour,
          groundFrom: footerGround.from,
          ink: hex(footerInk)
        } : { found: false }
      }, null, 2);
    }
  };
})();
