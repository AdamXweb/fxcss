# Example workflows

Copy the ones you want into your theme's `.github/workflows/`. Each pins fxcss
to a tag, so a change here cannot alter your CI without a commit in your repo —
bump `FXCSS_VERSION` when you want the newer one.

| File | What it does | Triggers |
| --- | --- | --- |
| `pr-preview.yml` | Renders the theme before and after a pull request and uploads the comparison | `pull_request` |
| `pr-preview-publish.yml` | Posts that comparison as a PR comment | `workflow_run` |
| `pr-preview-cleanup.yml` | Deletes a PR's images when it closes | `pull_request_target` |
| `firefox-watch.yml` | Audits the theme against release/beta/nightly on a schedule, opening a PR or an issue | `schedule` |
| `showcase.yml` | Captures the theme against real websites for your README | `release`, manual |

## Start with the previews

`pr-preview.yml` and `pr-preview-publish.yml` are a pair and only work together.
They are split because **pull requests from forks get a read-only token**:

- `pr-preview.yml` runs the pull request's code, so it has **no write
  permissions and no secrets**. It only produces an artifact.
- `pr-preview-publish.yml` holds the write access and **never runs pull request
  code**. It treats the artifact as untrusted: the PR number is verified against
  the head SHA of the run that produced it, only recognised filenames are
  republished, and the comment is built from validated numbers rather than any
  string in the artifact.

Two things to know before you wire them up:

- **The comment only appears once these are on your default branch.**
  `workflow_run` always runs the default-branch copy of a workflow, so a pull
  request adding them exercises the rendering half only. That is expected, not a
  misconfiguration.
- Images are published to an orphan `ci-previews` branch so they can be
  embedded. `pr-preview-cleanup.yml` removes each PR's images when it closes.

## Then the watcher

`firefox-watch.yml` is the one that earns its keep over time. Firefox ships
every few weeks and renames chrome elements without announcing it; a theme does
not break loudly when that happens. Running `fxcss audit` against **beta and
nightly** gives you weeks of warning before your users see it.

## Adding your own checks

If you write your own workflow, assert the comparison in **both** directions:

```bash
fxcss shot --theme . --out shots/a
fxcss shot --theme . --out shots/b
fxcss compare --base shots/a --head shots/b --out out   # must report 0 changed
```

An unchanged theme rendering identically twice is what makes a reported change
trustworthy. Every real bug in fxcss so far was caught by that assertion rather
than by review. The repo's own `.github/workflows/ci.yml` does exactly this, and
also checks the opposite — that an obvious CSS change *is* detected — so a
comparison that silently stopped working would fail the build.
