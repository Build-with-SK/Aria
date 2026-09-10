# Portfolio site

The public site for Soundariyan Karunakaran — positioning, case studies for ARIA
and SENTINEL, services, and contact.

It is **completely separate from the ARIA application**. Nothing here imports
from `../src`, `../backend` or `../frontend`, and nothing there imports from
here. Deleting this folder would not affect ARIA; deleting ARIA would not affect
this.

## Running it

```bash
npm --prefix site run dev
```

Serves on <http://localhost:4321> and rebuilds on every file change. There is
nothing to install first — the generator uses only Node's standard library.

```bash
npm --prefix site run build     # → site/dist
npm --prefix site run check     # build, then exit non-zero on any problem
```

`check` is the one to run in CI. It fails on a dead internal link, a missing
anchor, a page with no `<h1>` or more than one, a meta description outside
70–185 characters, and dev instrumentation leaking into a production build.

## Why there is no framework

Nine pages of content and three interactions. React would cost ~45 KB of
JavaScript before the first word appeared and would move the content out of the
HTML that crawlers and no-JS readers receive. `build.mjs` composes the pages at
build time instead, and the output is a folder of plain HTML.

The only client-side JavaScript is `src/app.js` (~4 KB): a theme toggle, the
mobile menu, and a scroll reveal. Every page is complete and readable without
it.

## Layout

```
site/
  build.mjs          generator, dev server and link/metadata checker
  src/
    content.mjs      every fact the site asserts, with its source file
    layout.mjs       <head>, nav, footer, JSON-LD, OG card
    components.mjs   HTML builders — cards, tables, diagrams, code blocks
    design.css       the whole design system
    app.js           progressive enhancement
    pages/           one module per page
  public/            copied verbatim into dist/ (add a CV PDF here)
  dist/              build output — not committed
```

### Editing content

Almost everything worth changing lives in `src/content.mjs`: name, links,
credentials, the verified metrics, the services list, the technical stack and
the compliance disclaimer. Prose specific to one page lives in that page's
module.

**Every number in `content.mjs` carries a `src` field naming the file it was
counted from, and the site prints it under the figure.** If you change a number,
change the trace with it. That is the whole credibility argument of this site —
a claim a reader can check beats one they have to believe.

## Before the first deploy

Two placeholders in `src/content.mjs`; the build prints them on every run until
they are gone.

| Constant | Set it to |
|---|---|
| `SITE.origin` | The real domain, e.g. `https://soundariyan.com`. Used for canonical URLs, Open Graph and the sitemap — wrong here is invisible in a browser and costly in search. |
| `LINKS.email` | The address you want publicly listed. Left generic on purpose rather than publishing a personal inbox by default. |

Optionally drop a CV at `public/cv.pdf` and link it from `src/pages/about.mjs`.

### The social preview card

`og:image` points at `public/og.png` — 1200×630, committed. The editable source
is generated at `dist/og.svg` by `ogCard()` in `src/layout.mjs`. **SVG is not a
usable `og:image`:** LinkedIn, X and Facebook all decline to render one, and
LinkedIn is the surface that matters most here — so the PNG is what ships.

After editing `ogCard()`, re-render the PNG. Any SVG→PNG tool works; with the
site running, the shortest route is the browser console:

```js
const svg = await (await fetch('/og.svg')).text()
const img = new Image()
img.src = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }))
await img.decode()
const c = Object.assign(document.createElement('canvas'), { width: 1200, height: 630 })
c.getContext('2d').drawImage(img, 0, 0, 1200, 630)
c.toBlob(b => Object.assign(document.createElement('a'),
  { href: URL.createObjectURL(b), download: 'og.png' }).click())
```

Save the result over `public/og.png`.

## Deploying

The build output is static, so any host works. In order of least effort:

**Cloudflare Pages** — connect the repository, set the build command to
`node build.mjs`, the output directory to `site/dist`, and the root directory to
`site`. `dist/_headers` is picked up automatically and applies the security
headers and cache policy.

**Netlify** — same three settings. `_headers` is also honoured.

**Vercel** — build command `node build.mjs`, output directory `dist`, root
directory `site`. `_headers` is ignored; move those rules into `vercel.json`.

**GitHub Pages** — `node build.mjs` then publish `site/dist`. Note that Pages
serves no custom headers, and if the site is not at the domain root every
absolute path in the output needs a prefix.

The pages are emitted as `path/index.html`, so clean URLs work on every static
host without rewrite rules.

## Accessibility and performance notes

Decisions worth knowing before editing:

- **Reveal-on-scroll is opt-in, not opt-out.** `.reveal` elements are visible by
  default; the inline head script adds `js-reveal` to hide them, and withdraws
  it after 1.5 s if `app.js` never reports in. A blocked script leaves the page
  unanimated, never blank.
- **The reveal is skipped entirely** under `prefers-reduced-motion`, without
  `IntersectionObserver`, or when the document loads hidden — transitions are
  suspended in a background tab, so elements marked visible there would sit at
  opacity 0.
- **The hero pulse pauses when scrolled out of view.** `stroke-dashoffset` is
  not compositor-accelerated, so it repaints its region every frame while it
  runs.
- **`--text-3` is the lightest text token and it clears 4.5:1** on every surface
  it is used against, in both themes. If you darken it in dark mode or lighten
  it in light mode, re-check.
- **SVG type sizes are in viewBox units.** What a reader sees is
  `fontSize × (renderedWidth / viewBoxWidth)`, which is why the diagrams carry
  `max-width` caps — without them a wide column scales the annotations larger
  than body text.
- **Grid and flex children carry `min-width: 0`.** Without it a long code line
  widens its track and the whole page scrolls sideways on a phone.
