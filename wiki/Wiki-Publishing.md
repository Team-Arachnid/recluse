# How This Wiki Is Published

Every page you are reading is a file in the Recluse repository, under `wiki/`. The copy served at
`github.com/Team-Arachnid/recluse/wiki` is a mirror, produced by `scripts/publish_wiki.py`. This page
explains that arrangement, the three ways a publish is triggered, and what to do when one fails.

Related: [Repository-Layout](Repository-Layout.md), [Getting-Started](Getting-Started.md),
[Code-Infrastructure](Code-Infrastructure.md).

## Why the pages live in the repository

A documentation change is only trustworthy if it is reviewed next to the change that caused it. When
the pages live in `wiki/`, editing a route and editing [API-Reference](API-Reference.md) are the same
pull request, seen by the same reviewer, merged or rejected together. A wiki kept somewhere else
drifts, because nothing forces the two to move at once.

GitHub does not serve its wiki from the code repository. It serves it from a second, separate git
repository whose URL is the code repository's URL with `.wiki.git` in place of `.git`. A normal
`git push` to `origin` never touches that second repository. Something has to bridge the two, and
that something is `scripts/publish_wiki.py`.

The consequence to internalise: **`wiki/` is the source of truth, and the GitHub wiki is a mirror.**
If you edit a page in the GitHub wiki web UI, your edit lives only in `<repo>.wiki.git`, and the next
publish overwrites it with whatever `wiki/` contains. Nothing warns you and nothing is recovered.
Edits belong in `wiki/`, in a commit, in this repository.

## One-time setup

The wiki repository does not exist until two things are true: the wiki feature is enabled, **and**
at least one page has been saved through the web UI. GitHub creates `<repo>.wiki.git` lazily, on
that first save. Until then there is nothing to clone, and the publish script cannot create it.

1. Open the repository on GitHub, then **Settings** -> **Features**, and tick **Wikis**.
2. Open `https://github.com/Team-Arachnid/recluse/wiki` and click **Create the first page**. Save anything
   at all; the content does not matter, because the first publish replaces it.
3. Run the publish once from a checkout:

   ```
   make wiki            # or: ./make.ps1 wiki
   ```

If you skip step 2, the clone fails and the script prints exactly this, then exits `1`:

```
[error] The GitHub wiki for this repository does not exist yet.
[error] GitHub does not create the wiki repository until it has one page,
[error] and that first page can only be created through the web UI:
[error]   1. Settings -> General -> Features -> tick 'Wikis'
[error]   2. Open https://github.com/Team-Arachnid/recluse/wiki/_new and save a page called Home
[error]      (the content does not matter; step 3 overwrites it)
[error]   3. Re-run this script
[error] This is a one-time step per repository.
```

That branch is chosen by matching `not found` or `does not exist` in git's stderr; any other clone
failure is reported verbatim as `git clone failed: <stderr>`.

## The three ways it publishes

| Trigger | Mechanism | When to use |
| --- | --- | --- |
| Push to `main` touching `wiki/**` | `.github/workflows/publish-wiki.yml` runs `publish_wiki.py` on a GitHub-hosted runner | The default. Nothing to install, works for every contributor, and publishes what was actually merged. |
| Local commit touching `wiki/` | `.git/hooks/post-commit`, installed from `scripts/hooks/post-commit` | You want the mirror updated the moment you commit locally, without waiting for CI. |
| Manual run | `make wiki`, `./make.ps1 wiki`, or `python scripts/publish_wiki.py` | Recovering from a failed run, publishing from a branch the hook will not touch, or the very first publish during setup. |

All three run the same script, so the result is the same regardless of which one fires. Because the
script is idempotent, two of them firing for the same change is harmless: the second finds nothing to
do and exits `0` with `already up to date, nothing to push`.

### The GitHub Actions workflow

Preferred, because it requires no local setup and it publishes the merged state of `main`. See
[The GitHub Actions workflow](#the-github-actions-workflow-1) below for the detail.

### The post-commit hook

Optional, opt-in per developer, installed with `make hooks`. Useful when you are writing several
pages in a row and want to see them rendered immediately. See
[The post-commit hook](#the-post-commit-hook-1) below.

### A manual run

`python scripts/publish_wiki.py` from the repository root. Add `--dry-run` first if you want to see
the change list without pushing. This is also the fallback the hook itself tells you to run when it
fails.

## publish_wiki.py

`scripts/publish_wiki.py` is a single-file Python script with no third-party dependencies; it shells
out to `git`. Its source directory is `REPO_ROOT / "wiki"` by default, resolved from the script's own
location, so it works regardless of the current working directory.

What a run does, in order:

1. **Validate the source.** The directory must exist and must contain at least one publishable file,
   otherwise it logs `No wiki source directory at <path>` or `<path> contains no publishable files`
   and returns `1`. It also checks that `git` is on `PATH`.
2. **Resolve the remote.** `wiki_remote_url()` takes, in precedence order: an explicit `--remote`;
   then `WIKI_REMOTE_URL`; then the `origin` remote of this repository with `.git` replaced by
   `.wiki.git` (a URL already ending in `.wiki.git` is used as is, and one with no suffix gets
   `.wiki.git` appended). SSH remotes stay SSH, so an existing key keeps working. With no `origin`
   and no `--remote` it exits with a message telling you to pass `--remote`.
3. **Clone shallow.** `git clone --depth 1 --quiet` into a temporary directory created with
   `tempfile.TemporaryDirectory(prefix="recluse-wiki-")`, so nothing is left behind on disk. The
   full history of the wiki repository is never needed, only its current tree.
4. **Sync.** `sync()` makes the checkout contain exactly the publishable contents of `wiki/`. Files
   missing from the checkout are added; files present but byte-different (compared with
   `filecmp.cmp(..., shallow=False)`) are updated; files in the checkout that are not in `wiki/` are
   deleted. It returns the three lists and they are printed as `+`, `~` and `-` lines.
5. **Detect changes.** If all three lists are empty it logs `already up to date, nothing to push` and
   returns `0` without committing. This is what makes repeated runs free.
6. **Commit.** The wiki commit's author is taken from `committer_identity()`, and the message from
   `--message` or `default_message()`, which is `Update wiki from <short sha> — <subject of HEAD>`.
   If git still finds nothing to commit — content that differed only in file mode or line endings —
   it logs `git found nothing to commit after normalisation` and returns `0`.
7. **Push.** `git push origin HEAD`. A failure prints `git push failed: <stderr>` and returns `1`.

### Flags

| Flag | Effect |
| --- | --- |
| `-m`, `--message <text>` | Use this as the wiki commit message instead of the generated one. |
| `--remote <url>` | Publish to this wiki repository instead of the one derived from `origin`. |
| `--source <dir>` | Read pages from this directory instead of `wiki/`. |
| `--dry-run` | Clone, compare and print the add/update/remove list, then stop. Nothing is committed or pushed. |
| `--check` | Implies `--dry-run`, and exits `1` with `wiki is out of date (--check)` when the mirror is behind. Intended for CI gating. Exits `0` when in sync. |

### Environment variables

| Variable | Effect |
| --- | --- |
| `WIKI_REMOTE_URL` | The wiki repository URL, used when `--remote` is not given. The Actions workflow sets it to `https://github.com/${{ github.repository }}.wiki.git`. |
| `GITHUB_TOKEN` | If set and the remote is an `https://` URL with no existing credentials in it, the URL becomes `https://x-access-token:<token>@...` for the clone and push. |
| `GH_TOKEN` | Checked only when `GITHUB_TOKEN` is unset; same effect. |
| `WIKI_GIT_NAME` | Author name for the wiki commit. Falls back to this repository's `user.name`, then to `recluse-docs`. |
| `WIKI_GIT_EMAIL` | Author email for the wiki commit. Falls back to this repository's `user.email`, then to `recluse-docs@users.noreply.github.com`. |

On a developer machine neither token variable is normally set, so the URL is used untouched and git's
own credential helper authenticates. No token is ever written to disk by this script, and any URL it
prints is passed through `redact()`, which rewrites `//user:pass@` as `//***@`.

### What gets published

Only files whose suffix (lower-cased) is one of:

```
.md  .png  .jpg  .jpeg  .svg  .gif
```

Anything else in `wiki/` is ignored, which leaves room for local scratch files that you do not want
mirrored. Files under a dot-prefixed directory, and dot-prefixed files, are skipped as well. Nested
directories are walked with `rglob`, and paths are preserved relative to `wiki/`.

### Idempotence and deletion

Two properties matter for automation. First, a second run immediately after a successful one does
nothing: the comparison finds no difference and the script returns `0` before touching git. Second,
the sync is a true mirror, not an overlay — a file removed from `wiki/` is removed from the wiki on
the next publish. You do not have to delete pages by hand in the web UI.

## The post-commit hook

Git does not version-control hooks: `.git/hooks/` is not part of the tree, so a hook committed to the
repository would never install itself. That is why the real hook lives at `scripts/hooks/post-commit`
and an installer copies it into place.

Install and remove:

```
make hooks              # ./make.ps1 hooks
make hooks-uninstall    # ./make.ps1 hooks-uninstall
```

Both delegate to `python scripts/install_hooks.py`. Its flags:

| Flag | Effect |
| --- | --- |
| *(none)* | Copy every file in `scripts/hooks/` into the hooks directory and mark it executable. |
| `--list` | Print the hooks directory and, for each hook, `installed`, `not installed`, or `present, but not ours`. |
| `--uninstall` | Remove only hooks that carry this repository's marker line. |
| `--force` | Overwrite a hook that is present but was not installed by this script. |

The installer resolves the destination by asking git: `core.hooksPath` if it is configured, otherwise
`git rev-parse --git-common-dir` plus `hooks`, so it behaves correctly inside a git worktree. It
copies rather than symlinks, because symlink creation on Windows needs developer mode or elevation.
Each installed copy gets a marker line inserted after the shebang:

```
# Installed by scripts/install_hooks.py — do not edit here; edit scripts/hooks/.
```

`--uninstall` and `--list` use that marker to tell this repository's hooks from one you wrote
yourself; a hook without it is reported and left alone. Because the installed file is a copy, editing
it has no lasting effect — edit `scripts/hooks/post-commit` and re-run the installer.

What the hook does when a commit lands:

1. Exits immediately if `RECLUSE_SKIP_WIKI_PUBLISH=1`.
2. `cd`s to the repository root, then checks whether the commit touched the wiki source, with
   `git diff-tree --no-commit-id --name-only -r --root HEAD | grep -q '^wiki/'`. The `--root` flag is
   there so the check also works on the very first commit of a repository. A commit that changed no
   file under `wiki/` exits `0` and publishes nothing.
3. Checks the branch. `RECLUSE_WIKI_BRANCHES` is a space-separated list, defaulting to `main`. The
   guard exists because the GitHub wiki has no branches of its own — anything pushed to it is live
   immediately, so publishing from a feature branch would put unreviewed pages in front of readers.
   On a branch that is not listed, the hook prints the branch and the allowed list and tells you to
   run `python scripts/publish_wiki.py` by hand if you meant it.
4. Finds `python3` or `python` on `PATH`, and skips with a message if neither is there.
5. Runs `python scripts/publish_wiki.py`.

Useful overrides:

```sh
RECLUSE_SKIP_WIKI_PUBLISH=1 git commit -m "wip: notes"   # skip this one commit
RECLUSE_WIKI_BRANCHES="main develop" git commit -m "..."  # allow another branch
```

A failing publish never rewrites or undoes the commit. The hook ends with `exit 0` in every path; on
failure it prints `publish failed. The commit is fine; re-run:` followed by the manual command.

## The GitHub Actions workflow

`.github/workflows/publish-wiki.yml`, named **Publish wiki**.

**Triggers.** A `push` to `main` whose changed files match any of `wiki/**`,
`scripts/publish_wiki.py`, or `.github/workflows/publish-wiki.yml`. The last two are included so a
change to the publishing machinery itself is exercised straight away. There is also a
`workflow_dispatch` trigger with one optional input, `message`, which is passed to the script as
`-m` when non-empty, so you can re-publish from the Actions tab at any time.

**Permissions.** `contents: write` at the workflow level. The wiki repository lives under the same
repository's permission scope, so this is what lets the job push to it.

**Concurrency.** The group is `publish-wiki` with `cancel-in-progress: false`. The wiki repository
has a single branch and no merge story, so two jobs pushing at once would race and one would be
rejected as non-fast-forward. Serialising them removes the race; not cancelling in progress means a
run that is already pushing is allowed to finish rather than being killed mid-push.

**Steps.** `actions/checkout@v4` with `fetch-depth: 1` — one commit is enough, because
`default_message()` only reads `HEAD`. Then `actions/setup-python@v5` pinned to `3.11`. Then the
publish step, whose `env` block wires the token and identity into the script:

```yaml
GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
WIKI_REMOTE_URL: https://github.com/${{ github.repository }}.wiki.git
WIKI_GIT_NAME: ${{ github.actor }}
WIKI_GIT_EMAIL: ${{ github.actor }}@users.noreply.github.com
```

The token never appears on a command line. `WIKI_REMOTE_URL` is a plain `https://` URL, and
`authenticated_url()` inside the script rewrites it to
`https://x-access-token:<token>@github.com/...` only for the clone and push subprocesses. The wiki
commit is attributed to whoever pushed, because `WIKI_GIT_NAME` and `WIKI_GIT_EMAIL` come from
`github.actor`.

The workflow cannot create the wiki repository. The one-time setup above is still a prerequisite; if
it has not been done, the job fails on the clone with the message shown earlier.

## Page naming and links

A GitHub wiki derives a page's title from its filename: hyphens are rendered as spaces and the `.md`
extension is dropped. `Data-Pipeline.md` becomes the page **Data Pipeline** at `/wiki/Data-Pipeline`.
Use `Title-Case-With-Hyphens.md` and the title comes out right with no front matter.

Links between pages are written with the extension, `[Architecture](Architecture.md)`, because that
form resolves in both places: GitHub's file browser follows it to the file in `wiki/`, and the wiki
renderer follows it to the page. A bare `[[Architecture]]` wiki link renders only in the wiki and
looks like literal text in the repository view, so it is not used here.

Two filenames are special to the wiki renderer:

- `_Sidebar.md` — rendered as the navigation column on every page.
- `_Footer.md` — rendered at the bottom of every page.

Both publish like any other `.md` file; the wiki gives them their meaning. They have no special
meaning in the repository file browser, where they appear as ordinary files in `wiki/`.

## Adding or renaming a page

Adding:

1. Create `wiki/<Page-Name>.md` with a single `#` heading at the top.
2. Add it to `wiki/_Sidebar.md` so it is reachable from the navigation.
3. Link it from [Home](Home.md), and from any sibling page it belongs next to.
4. Commit. The publish happens on push to `main`, or at commit time if you installed the hook.

Renaming:

1. `git mv wiki/Old-Name.md wiki/New-Name.md`.
2. Update `_Sidebar.md`, `Home.md`, and every inbound link. `grep -rn "Old-Name.md" wiki/` finds them.
3. Commit.

The publish script deletes pages that are no longer in `wiki/`, so the old wiki page disappears on
the next publish. What it cannot fix is inbound links you missed, and any external URL pointing at
the old page, which will 404. If a page has been linked from outside the repository, consider leaving
a short stub at the old filename that points to the new one instead of deleting it.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `The GitHub wiki for this repository does not exist yet.` on clone | GitHub creates `<repo>.wiki.git` only after the first page is saved in the web UI. | Do the [One-time setup](#one-time-setup): tick **Wikis** in Settings -> Features, save any page, re-run. |
| `git clone failed: ... Repository not found` with the wiki enabled | The remote resolved to the wrong URL, or the credentials in use cannot see the repository at all. | Check `git remote get-url origin`, or pass `--remote https://github.com/<owner>/<repo>.wiki.git` explicitly. For a private repository, confirm you are authenticated. |
| `git push failed: ... permission denied` / `403` | The identity pushing has read access but not write. In Actions this means the token scope; locally it means your account is not a collaborator. | In Actions, confirm `permissions: contents: write` is present and that the repository's Actions setting allows write tokens. Locally, push to `origin` once to verify your credentials. |
| Run says `already up to date, nothing to push` | The mirror already matches `wiki/` byte for byte. This is the normal result of a second run. | Nothing to do. If you expected a change, confirm the file was saved and that its extension is in the published list. |
| `<path> contains no publishable files` | `--source` pointed somewhere wrong, or every file has an unpublished extension. | Run from the repository root, or check the suffix — only `.md`, `.png`, `.jpg`, `.jpeg`, `.svg` and `.gif` are published. |
| The hook did not run after a commit | It was never installed; or the commit touched no file under `wiki/`; or you are on a branch outside `RECLUSE_WIKI_BRANCHES`; or `RECLUSE_SKIP_WIKI_PUBLISH=1` was set; or no `python` on `PATH`. | `python scripts/install_hooks.py --list` shows whether it is installed. The branch guard prints its own message when it fires. Publish by hand with `make wiki`. |
| `an unrelated hook is already installed, skipping` | `.git/hooks/post-commit` exists without this repository's marker line, so the installer will not clobber it. | Merge the two by hand, or re-run with `python scripts/install_hooks.py --force` if the existing hook is disposable. |
| The Actions job did not appear | The push did not change any path in `wiki/**`, `scripts/publish_wiki.py` or `.github/workflows/publish-wiki.yml`, or it was not to `main`. | Trigger it from the Actions tab via **Run workflow** (`workflow_dispatch`), or publish locally with `make wiki`. |
| A web-UI edit disappeared | `wiki/` is the source of truth and the publish overwrote the mirror. | Re-make the edit in `wiki/` and commit it. The lost text may still be recoverable from the wiki repository's history: `git clone https://github.com/<owner>/<repo>.wiki.git` and read the log. |
| Two runs raced and one was rejected | Only possible outside the `publish-wiki` concurrency group, for example a local hook publishing at the same moment as CI. | Re-run `make wiki`. The script is idempotent, so the later run simply converges. |

## See also

- [Getting-Started](Getting-Started.md) — installing the toolchain and running the stack.
- [Repository-Layout](Repository-Layout.md) — where `wiki/`, `scripts/` and `.github/` sit.
- [Code-Infrastructure](Code-Infrastructure.md) — the rest of the repository's tooling.
- [Home](Home.md) — index of every page.
