// Dev-only accessibility/layout audit helper, served from site/public.
// Removed before the production build ships (see build.mjs --check).
window.__audit = function () {
  var d = document, de = d.documentElement, out = { url: location.pathname, w: de.clientWidth };
  out.overflow = [];
  d.querySelectorAll('body *').forEach(function (el) {
    var b = el.getBoundingClientRect();
    if (b.width > 0 && b.right > de.clientWidth + 1) {
      var p = el.parentElement, contained = false;
      while (p) { var pc = getComputedStyle(p);
        if (pc.overflowX === 'auto' || pc.overflowX === 'scroll' || pc.overflowX === 'hidden') { contained = true; break; }
        p = p.parentElement; }
      if (!contained) out.overflow.push(el.tagName + '.' + String(el.className).slice(0, 36) + ' r=' + Math.round(b.right));
    }
  });
  out.overflow = out.overflow.slice(0, 8);
  out.scrollW = de.scrollWidth; out.clientW = de.clientWidth;
  var lv = [].map.call(d.querySelectorAll('h1,h2,h3,h4'), function (h) { return +h.tagName[1]; });
  var bad = [], prev = 0; lv.forEach(function (l, i) { if (prev && l > prev + 1) bad.push(i + ':h' + prev + '->h' + l); prev = l; });
  out.headingJumps = bad; out.h1count = d.querySelectorAll('h1').length;
  out.smallTargets = [].filter.call(d.querySelectorAll('a,button'), function (e) {
    var b = e.getBoundingClientRect(); return b.width > 0 && (b.height < 24 || b.width < 24);
  }).map(function (e) { var b = e.getBoundingClientRect(); return (e.textContent || '').trim().slice(0, 18) + ' ' + Math.round(b.width) + 'x' + Math.round(b.height); }).slice(0, 8);
  out.tinyText = [].filter.call(d.querySelectorAll('p,li,td,span,a'), function (e) {
    var fs = parseFloat(getComputedStyle(e).fontSize); return fs > 0 && fs < 11 && (e.textContent || '').trim().length > 3;
  }).map(function (e) { return Math.round(parseFloat(getComputedStyle(e).fontSize) * 10) / 10 + 'px'; }).slice(0, 6);
  out.svgNoLabel = [].filter.call(d.querySelectorAll('svg[role="img"]'), function (s) { return !s.getAttribute('aria-label'); }).length;
  out.title = d.title;
  out.desc = (d.querySelector('meta[name=description]') || {}).content;
  return out;
};
