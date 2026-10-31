// What `fxcss pick` prints when you click an element, built from the website's
// own page: the selector, classes, box, notable styles and the rules that
// style it.

import { authoredClasses, classesOf, classLabel } from './logo-picker.ts';

/** The properties `fxcss pick` reports, in its order. */
export const REPORTED_STYLES = [
  'background-color',
  'color',
  'border-radius',
  'box-shadow',
  'list-style-image',
  'font-size',
  'padding',
  'margin',
  'opacity',
  'height',
  'width',
  'display',
] as const;
/** Values `fxcss pick` leaves out, because they say nothing. */
const UNREMARKABLE = new Set([
  'none',
  'normal',
  'auto',
  '0px',
  'rgba(0, 0, 0, 0)',
  '1',
]);
/** `fxcss pick` lists this many rules, then counts the rest. */
export const SHOWN_RULES = 8;
/** Tailwind components carry dozens of classes; the rest are counted. */
export const SHOWN_CLASSES = 6;

export type PickedRule = { selector: string; layer?: string };
export type PickReport = {
  tag: string;
  /** Every class the label names, for copying. */
  selector: string;
  /** The selector as the pink label shows it, shortened when long. */
  label: string;
  /** The site's own classes first, then the rest in attribute order. */
  classes: string[];
  box: { x: number; y: number; width: number; height: number };
  styles: [property: string, value: string][];
  /** The site's own rules first, then Tailwind's layered ones. */
  rules: PickedRule[];
};

type Sheet = { readonly cssRules: CSSRuleList };
type Environment = {
  matchMedia(query: string): { matches: boolean };
  supports(condition: string): boolean;
  LayerBlock: typeof CSSLayerBlockRule;
  MediaRule: typeof CSSMediaRule;
  SupportsRule: typeof CSSSupportsRule;
};

/**
 * Style rules whose selectors match the element right now, with the cascade
 * layer each one sits in. Conditional groups count only when their condition
 * holds; rules nested inside style rules and cross-origin sheets are skipped.
 *
 * Like `fxcss pick`, which lists the rules that name the element, rules naming
 * one of its classes come first; then the site's own rules before Tailwind's
 * layered ones, each in stylesheet order.
 */
export function matchingRules(
  element: Pick<Element, 'matches'>,
  sheets: Iterable<Sheet>,
  environment: Environment,
  classes: readonly string[] = [],
) {
  const found: PickedRule[] = [];
  function read(rules: Iterable<CSSRule>, layer?: string) {
    for (const rule of rules) {
      if ('selectorText' in rule) {
        const selector = String(rule.selectorText);
        try {
          if (element.matches(selector))
            found.push(layer ? { selector, layer } : { selector });
        } catch {
          // A selector the engine cannot match against, such as a pseudo-element.
        }
        continue;
      }
      if (!('cssRules' in rule)) continue;
      if (rule instanceof environment.LayerBlock) {
        const name = rule.name || 'anonymous';
        read(rule.cssRules, layer ? `${layer}.${name}` : name);
        continue;
      }
      if (
        rule instanceof environment.MediaRule &&
        !environment.matchMedia(rule.conditionText).matches
      )
        continue;
      if (
        rule instanceof environment.SupportsRule &&
        !environment.supports(rule.conditionText)
      )
        continue;
      read(rule.cssRules as CSSRuleList, layer);
    }
  }
  for (const sheet of sheets) {
    try {
      read(sheet.cssRules);
    } catch {
      // A stylesheet from another origin keeps its rules private.
    }
  }
  const names = new Set(classes);
  const rank = (rule: PickedRule) =>
    (rule.layer ? 1 : 0) +
    ([...rule.selector.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)].some(([, name]) =>
      names.has(name),
    )
      ? 0
      : 2);
  return found
    .map((rule, order) => ({ rule, order, rank: rank(rule) }))
    .sort((a, b) => a.rank - b.rank || a.order - b.order)
    .map(({ rule }) => rule);
}

export function reportedStyles(style: {
  getPropertyValue(property: string): string;
}) {
  return REPORTED_STYLES.flatMap((property) => {
    const value = style.getPropertyValue(property);
    return value && !UNREMARKABLE.has(value)
      ? [[property, value] as [string, string]]
      : [];
  });
}

/** The report for one picked element of the live page. */
export function pickReport(element: Element): PickReport {
  const doc = element.ownerDocument;
  const view = doc.defaultView!;
  const authored = authoredClasses(doc.styleSheets);
  const classes = classesOf(element);
  const box = element.getBoundingClientRect();
  return {
    tag: element.localName,
    selector: classLabel(classes, authored, undefined, Infinity),
    label: classLabel(classes, authored),
    classes: [
      ...classes.filter((name) => authored.has(name)),
      ...classes.filter((name) => !authored.has(name)),
    ],
    box: {
      x: Math.round(box.x),
      y: Math.round(box.y),
      width: Math.round(box.width),
      height: Math.round(box.height),
    },
    styles: reportedStyles(view.getComputedStyle(element)),
    rules: matchingRules(
      element,
      doc.styleSheets,
      {
        matchMedia: (query) => view.matchMedia(query),
        supports: (condition) => CSS.supports(condition),
        LayerBlock: CSSLayerBlockRule,
        MediaRule: CSSMediaRule,
        SupportsRule: CSSSupportsRule,
      },
      classes,
    ),
  };
}

/** What a piece of the report is, so the card can colour it. */
export type TokenKind =
  | 'text'
  | 'label'
  | 'muted'
  | 'tag'
  | 'heading'
  | 'class'
  | 'attribute'
  | 'pseudo'
  | 'property'
  | 'value'
  | 'colour';
export type Token = { kind: TokenKind; text: string };

const token = (kind: TokenKind, text: string): Token => ({ kind, text });

/** Pieces of text matched by a pattern, with the text between them. */
function split(
  text: string,
  pattern: RegExp,
  kindOf: (match: RegExpMatchArray) => TokenKind,
  between: TokenKind,
) {
  const tokens: Token[] = [];
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last)
      tokens.push(token(between, text.slice(last, match.index)));
    tokens.push(token(kindOf(match), match[0]));
    last = match.index + match[0].length;
  }
  if (last < text.length) tokens.push(token(between, text.slice(last)));
  return tokens;
}

/** A selector with its classes, attribute tests and pseudo-classes marked. */
export function selectorTokens(selector: string) {
  return split(
    selector,
    /(\.(?:\\.|[\w-])+)|(\[[^\]]*\])|(::?[\w-]+)/g,
    (match) => (match[1] ? 'class' : match[2] ? 'attribute' : 'pseudo'),
    'text',
  );
}

/** A computed value with each colour in it marked, for a swatch. */
export function valueTokens(value: string) {
  return split(
    value,
    /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\([^)]*\)/g,
    () => 'colour',
    'value',
  );
}

/**
 * The report as `fxcss pick` prints it, one array of tokens per line. Style
 * values line up in a column, which the terminal leaves ragged.
 */
export function reportTokens(report: PickReport): Token[][] {
  const { box, classes, rules, styles } = report;
  const lines: Token[][] = [
    [
      token('tag', report.tag),
      token('muted', '  →  '),
      token('heading', report.label),
    ],
  ];
  if (classes.length)
    lines.push([
      token('label', 'classes   '),
      ...classes
        .slice(0, SHOWN_CLASSES)
        .flatMap((name, index) => [
          ...(index ? [token('text', ' ')] : []),
          token('class', name),
        ]),
      ...(classes.length > SHOWN_CLASSES
        ? [token('muted', ` +${classes.length - SHOWN_CLASSES} more`)]
        : []),
    ]);
  lines.push([
    token('label', 'box       '),
    token('value', `${box.width}×${box.height}`),
    token('label', ' at '),
    token('value', `(${box.x}, ${box.y})`),
    ...(box.width && box.height
      ? []
      : [token('muted', '   (not visible in this layout)')]),
  ]);
  if (styles.length) {
    lines.push([token('label', 'styles')]);
    const width = Math.max(...styles.map(([property]) => property.length));
    for (const [property, value] of styles)
      lines.push([
        token('text', '  '),
        token('property', property),
        token('muted', ':'.padEnd(width - property.length + 2)),
        ...valueTokens(value),
      ]);
  }
  if (!rules.length) {
    lines.push([
      token('label', 'styled by no rules on this site (browser defaults only)'),
    ]);
    return lines;
  }
  lines.push([
    token(
      'label',
      `styled by ${rules.length} rule${rules.length === 1 ? '' : 's'} on this site`,
    ),
  ]);
  for (const rule of rules.slice(0, SHOWN_RULES))
    lines.push([
      token('text', '  '),
      ...(rule.layer ? [token('muted', `@layer ${rule.layer}  `)] : []),
      ...selectorTokens(rule.selector),
    ]);
  if (rules.length > SHOWN_RULES)
    lines.push([token('muted', `  … and ${rules.length - SHOWN_RULES} more`)]);
  return lines;
}

/** The report as plain text, one string per line. */
export function reportLines(report: PickReport) {
  return reportTokens(report).map((line) =>
    line.map(({ text }) => text).join(''),
  );
}
