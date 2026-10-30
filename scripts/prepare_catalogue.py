#!/usr/bin/env python3
"""Publish a captured fxcss catalogue with the website's document conventions."""

from html.parser import HTMLParser
from pathlib import Path
import re
import sys


class CatalogueParts(HTMLParser):
    """Locate the generated pieces without rewriting their captured content."""

    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
            'meta', 'param', 'source', 'track', 'wbr'}
    SHELL = {'html', 'head', 'body', 'meta', 'link', 'title', 'style', 'script'}

    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.lines = [0]
        for match in re.finditer('\n', source):
            self.lines.append(match.end())
        self.stack = []
        self.parts = {}
        self.feed(source)
        self.close()
        if self.stack or set(self.parts) != {'style', 'script', 'wrap'}:
            raise ValueError('Expected one complete catalogue style, mode script, and .wrap container')

    def source_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        is_wrapper = tag in {'div', 'main'} and 'wrap' in attributes.get('class', '').split()
        key = 'wrap' if is_wrapper else tag if tag in {'style', 'script'} else None
        if not any(entry[1] == 'wrap' for entry in self.stack) and not is_wrapper and tag not in self.SHELL:
            raise ValueError(f'Unexpected element outside the catalogue container: {tag}')
        if tag not in self.VOID:
            self.stack.append((tag, key, self.source_offset() + len(self.get_starttag_text())))

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1][0] != tag:
            raise ValueError(f'Unexpected or unbalanced closing tag: {tag}')
        _, key, start = self.stack.pop()
        if key:
            if key in self.parts:
                raise ValueError(f'Expected only one catalogue {key}')
            self.parts[key] = self.source[start:self.source_offset()].strip()


def replace_rule(source, pattern, replacement, label):
    result, count = re.subn(pattern, replacement, source)
    if count != 1:
        raise ValueError(f'Expected one {label}; found {count}. Check the toolkit catalogue format.')
    return result


def prepare_catalogue(source):
    parts = CatalogueParts(source).parts
    style = replace_rule(
        parts['style'],
        r'\.tabs button\[aria-(?:selected|pressed)=true\]\s*\{[^}]*\}',
        '.tabs button[aria-pressed=true] { background: var(--accent); color:var(--bg); border-color:transparent; }',
        'selected catalogue button rule',
    )
    # Missing means the Firefox control was absent in this capture, not that
    # the explanatory text or its disclosure is disabled.
    style = replace_rule(style, r'\.missing\s*\{[^}]*\}',
                         '.missing { border-style:dashed; }', 'missing-card rule')
    script = replace_rule(
        parts['script'], r"setAttribute\('aria-(?:selected|pressed)',",
        "setAttribute('aria-pressed',", 'mode-button state update',
    )
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,follow">
<title>Firefox themeable UI catalogue · fxcss</title>
<meta name="description" content="Captured Firefox interface elements, their computed styles, and the theme rules that affect them.">
<link rel="canonical" href="https://fxcss.com/catalogue/">
<style>
{style}
</style>
</head>
<body>
<main class="wrap">
{parts['wrap']}
</main>
<script>
{script}
</script>
</body>
</html>
'''


def main():
    if len(sys.argv) != 2:
        sys.exit('usage: prepare_catalogue.py <catalogue index.html>')
    path = Path(sys.argv[1])
    try:
        prepared = prepare_catalogue(path.read_text(encoding='utf-8'))
    except ValueError as error:
        sys.exit(f'Cannot prepare catalogue: {error}')
    path.write_text(prepared, encoding='utf-8')


if __name__ == '__main__':
    main()
