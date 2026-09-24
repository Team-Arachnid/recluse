# How This Site Is Published

Every page you are reading is a file in the Recluse repository, under `docs/`.
That directory is the whole website: content, layouts and stylesheet together.
GitHub Actions builds it with Jekyll on every push to `main` and deploys the
result to GitHub Pages.

## Why the pages live in the repository

A documentation change is only trustworthy if it is reviewed next to the change
that caused it. Because the pages live in `docs/`, editing a route and editing
[API Reference](API-Reference.md) are the same pull request, seen by the same
reviewer, merged or rejected together. Documentation kept somewhere else drifts,
because nothing forces the two to move at once.

There is no second copy. The repository is the only source, and the published
site is built from it — nothing is edited through a web UI, so nothing can be
silently overwritten.

## The build

`.github/workflows/jekyll-gh-pages.yml` runs on every push to `main` that
touches `docs/**` or the workflow itself, and can be run by hand from the
Actions tab.

| Step | What it does |
| --- | --- |
| `actions/checkout` | Fetches the repository |
| `actions/configure-pages` | Reads the Pages settings and exposes the base URL |
| `actions/jekyll-build-pages` | Builds `./docs` into `./_site` with the GitHub Pages toolchain |
| `actions/upload-pages-artifact` | Packages `_site` |
| `actions/deploy-pages` | Publishes it |

The `source: ./docs` input matters. Left at the repository root — the value the
sample workflow ships with — Jekyll would take the backend, the dashboard, every
lockfile and every Markdown file in the tree as site input and publish them.

Deployments are serialised by a `concurrency: pages` group, so two pushes in
quick succession cannot race.

## Local preview

The published build uses GitHub's own pinned toolchain, and `docs/Gemfile`
reproduces it on a laptop. Ruby and Bundler are the only prerequisites, and
neither is needed to write a page — only to see it rendered before pushing.

```
make docs-serve          # http://localhost:4000/recluse/
./make.ps1 docs-serve    # Windows
```

`make docs` builds into `docs/_site` without serving. Both build outputs are
gitignored.

## Page naming and links

A page's filename becomes its URL: `Data-Pipeline.md` is served at
`/recluse/Data-Pipeline/`, and the `#` heading at the top of the file becomes
its title in the browser tab and in the sidebar. Use `Title-Case-With-Hyphens.md`
and both come out right with no front matter.

**Links between pages carry the `.md` extension**: `[Architecture](Architecture.md)`.
The `jekyll-relative-links` plugin rewrites them to the built URL at publish
time, so the same link resolves here *and* when someone reads `docs/` in
GitHub's file browser. Anchors work the same way:
`[Getting Started](Getting-Started.md#troubleshooting)`.

Pages carry no front matter. `jekyll-optional-front-matter` renders them anyway
and `jekyll-titles-from-headings` reads the title from the first heading, with
`strip_title: false` so that heading stays in the body as the visible `<h1>` —
the layout never prints the title itself, so there is nothing to duplicate.

## Adding or renaming a page

Adding:

1. Create `docs/<Page-Name>.md` with a single `#` heading at the top.
2. Add its name, without the extension, to the right group in `docs/_data/nav.yml`.
3. Link it from any sibling page it belongs next to.
4. Commit and push to `main`.

Renaming:

1. `git mv docs/Old-Name.md docs/New-Name.md`.
2. Update `docs/_data/nav.yml` and every inbound link — `grep -rn "Old-Name" docs/`.
3. Commit and push.

A renamed page's old URL will 404. If it has been linked from outside the
repository, consider leaving a short stub at the old filename pointing at the
new one rather than deleting it.

## The theme

The site ships its own layouts rather than a published theme, so there is
nothing to fight when a page needs something specific.

| Path | Role |
| --- | --- |
| `docs/_config.yml` | Site metadata, plugins, permalink style, the `layout: page` default |
| `docs/_data/nav.yml` | Sidebar groups and their page order |
| `docs/_layouts/default.html` | The shell: topbar, search, sidebar, footer |
| `docs/_layouts/page.html` | A documentation page inside the shell |
| `docs/_layouts/home.html` | The landing page |
| `docs/_includes/head.html` | `<head>`, metadata, fonts |
| `docs/_includes/sidebar.html` | Navigation, built from `_data/nav.yml` |
| `docs/assets/css/recluse.css` | The whole stylesheet |
| `docs/assets/js/search.js` | Client-side search and the mobile drawer |
| `docs/search.json` | The search index, generated at build time from every page |

Search is a single JSON file scored in the browser. At this size that is
cheaper and more predictable than a hosted index, and it keeps the site free of
third-party requests: the only external resource is the webfont.

Colour is deliberate. Red marks detection, status and anything this project has
not yet measured; everything else is grey, so the red reads as signal rather
than decoration.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| A page 404s after a rename | The URL comes from the filename, and nothing redirects the old one. | Update `_data/nav.yml` and inbound links, or leave a stub at the old filename. |
| A link works in the repository but 404s on the site | The link is missing its `.md`, so `jekyll-relative-links` left it alone. | Write `[Architecture](Architecture.md)`, not `[Architecture](Architecture)`. |
| A new page is not in the sidebar | The sidebar is explicit, not automatic. | Add the filename without its extension to `docs/_data/nav.yml`. |
| The Actions run did not start | The push changed nothing under `docs/**` or the workflow, or it did not target `main`. | Run it from the Actions tab with **Run workflow**. |
| The build published the whole repository | `source:` is `./` instead of `./docs`. | Set `source: ./docs` in the workflow's build step. |
| `bundle: command not found` locally | Ruby and Bundler are not installed. They are needed only for local preview. | Install Ruby, then `gem install bundler`. Or skip it — push and read the built site. |
| The site renders unstyled | The stylesheet 404s, usually because `baseurl` does not match where the site is served. | `baseurl` in `_config.yml` must be `/recluse`, matching the repository name. |
| The site renders with someone else's styling | With no `theme` set, GitHub Pages applies `jekyll-theme-primer`, and its `style.scss` compiles to `assets/css/style.css`. A site stylesheet at that same path is served as the theme's instead. | `theme: null` in `_config.yml`, and the stylesheet is named `recluse.css` so the two can never resolve to the same path. Both are already set; do not reintroduce a file called `style.css`. |
| The stylesheet or script is served as HTML | Assets carry front matter so Jekyll templates them, which also brings them under the `layout: page` default and wraps them in the site shell. | The `defaults` block scopes `layout: null` to `assets`. Keep any new asset under `docs/assets/`. |
