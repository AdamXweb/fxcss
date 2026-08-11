# Examples

## Adding CI to your theme

Don't copy workflow files from here — run this from your theme's root instead:

```bash
fxcss init                       # before/after PR previews
fxcss init --watch --showcase    # plus the weekly Firefox audit and
                                 # release screenshots
```

That generates the workflows with the fxcss version pinned and your theme's
variant stylesheets already enumerated in the publish allowlist, which is the
part people used to have to hand-edit (and forget). The generated files carry
comments explaining the shape — most importantly why the preview is split into
two workflows: pull requests from forks get a read-only token, so the half that
runs their code cannot be the half that posts the comment.

The templates live in [`fxcss/templates/`](../fxcss/templates/) if you want to
read them before generating anything.

## The starter theme

`fxcss new my-theme` scaffolds a small, complete `userChrome.css` starting
point. It ships inside the package (`fxcss/templates/starter/`), and this
repo's CI renders that exact tree on macOS, Windows and Linux on every push —
an unchanged theme must render identically three times, and an obvious CSS
change must be detected. Its `custom/accent-red.css` exists so variant capture
is exercised too.

## Writing your own checks

Whatever you build, assert the comparison in **both** directions:

```bash
fxcss shot --theme . --out shots/a
fxcss shot --theme . --out shots/b
fxcss compare --base shots/a --head shots/b --out out   # must report 0 changed
```

An unchanged theme rendering identically twice is what makes a reported change
trustworthy. Every real bug in fxcss so far was caught by that assertion.
