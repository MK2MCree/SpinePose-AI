# Local browser dependencies

These files remove the existing pages' runtime dependency on external CSS,
JavaScript and icon-font servers. Downloaded assets are kept unchanged, with
their upstream license files and embedded attribution.

| Dependency | Pinned version | Source | License |
| --- | --- | --- | --- |
| Tailwind browser bundle | 3.4.17 | https://cdn.tailwindcss.com/3.4.17 | `tailwindcss-3.4.17/LICENSE.txt` (MIT) |
| Font Awesome Free | 6.0.0 | https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css and its eight declared `webfonts` files | `fontawesome-free-6.0.0/LICENSE.txt`; CSS code MIT, font files SIL OFL 1.1 |

Upstream license sources:

- https://raw.githubusercontent.com/tailwindlabs/tailwindcss/v3.4.17/LICENSE
- https://raw.githubusercontent.com/FortAwesome/Font-Awesome/6.0.0/LICENSE.txt

The Tailwind file is the v3 browser runtime, not the separate v4 build dependency
used by the Sites shell. It still generates utility CSS in the browser and
retains its upstream production-build warning. This change makes the existing
runtime local; it does not migrate the pages to a precompiled CSS build.

Google Fonts imports were removed. Application text uses Segoe UI, Microsoft
YaHei and system sans-serif fallback; icon fonts use the local Font Awesome
files. External clinical citation links remain normal user-opened links.

`manifest.json` records source URLs, byte sizes and SHA-256 hashes. The
`site/scripts/sync-web-assets.mjs` script copies this entire directory into
`site/public/vendor`; no site build or deployment is needed for that copy.
