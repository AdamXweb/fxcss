export type BattleRule = {
  selector: string;
  color: string;
  colorName: string;
  specificity: [number, number, number];
  important?: boolean;
  inline?: boolean;
};

export type BattleRound = {
  title: string;
  boss: string;
  markup: string;
  hint: string;
  explanation: string;
  takeaway: string;
  rules: BattleRule[];
};

// These lessons use matching, unlayered author declarations on one element.
// Origins, layers, @scope, animations and transitions are deliberately excluded.
export function winningRule(rules: BattleRule[]): number {
  return rules.reduce((winner, rule, index) => {
    const current = rules[winner];
    const strength = [Number(!!rule.important), Number(!!rule.inline), ...rule.specificity];
    const previous = [Number(!!current.important), Number(!!current.inline), ...current.specificity];
    for (let i = 0; i < strength.length; i++) {
      if (strength[i] !== previous[i]) return strength[i] > previous[i] ? index : winner;
    }
    return index; // Equal weight: the later declaration wins.
  }, 0);
}

const orange = { color: '#b43e14', colorName: 'orange' };
const purple = { color: '#6842ce', colorName: 'purple' };

export const battleRounds: BattleRound[] = [
  {
    title: 'A little class', boss: 'The type selector',
    markup: '<button class="tab">Firefox</button>',
    hint: 'A class selector has more weight than an element name.',
    explanation: '.tab has weight 0–1–0. button has weight 0–0–1. Compare the columns from left to right: the class wins, even though the type selector comes later.',
    takeaway: 'Classes beat types.',
    rules: [
      { selector: '.tab', ...purple, specificity: [0, 1, 0] },
      { selector: 'button', ...orange, specificity: [0, 0, 1] },
    ],
  },
  {
    title: 'Show some ID', boss: 'The class collector',
    markup: '<button id="active" class="tab selected">Firefox</button>',
    hint: 'Specificity is a set of columns, not a single number you add up.',
    explanation: '#active has weight 1–0–0; .tab.selected has 0–2–0. The ID column decides first. Extra classes cannot outvote an ID.',
    takeaway: 'Compare IDs, then classes, then types.',
    rules: [
      { selector: '.tab.selected', ...purple, specificity: [0, 2, 0] },
      { selector: '#active', ...orange, specificity: [1, 0, 0] },
    ],
  },
  {
    title: 'Last word wins', boss: 'The perfect tie',
    markup: '<button class="tab selected">Firefox</button>',
    hint: 'Both selectors have exactly the same weight. Read from top to bottom.',
    explanation: '.tab and .selected both have weight 0–1–0. With everything else equal, the declaration that appears later in the stylesheet wins.',
    takeaway: 'Source order breaks an otherwise equal tie.',
    rules: [
      { selector: '.tab', ...orange, specificity: [0, 1, 0] },
      { selector: '.selected', ...purple, specificity: [0, 1, 0] },
    ],
  },
  {
    title: 'Where did the weight go?', boss: 'The zero-weight illusion',
    markup: '<button id="active" class="tab">Firefox</button>',
    hint: ':where() and everything inside it contribute zero specificity.',
    explanation: ':where(#active.tab) looks powerful, but its weight is 0–0–0. Even the simple button selector, with 0–0–1, beats it. :where() is useful for defaults that are easy to override.',
    takeaway: ':where() matches without adding weight.',
    rules: [
      { selector: 'button', ...orange, specificity: [0, 0, 1] },
      { selector: ':where(#active.tab)', ...purple, specificity: [0, 0, 0] },
    ],
  },
  {
    title: 'An unexpected ally', boss: 'The :is() shapeshifter',
    markup: '<button class="tab">Firefox</button>',
    hint: ':is() borrows the highest specificity in its selector list, even if that selector is not what matched.',
    explanation: ':is(#active, .tab) gets 1–0–0 from the ID in its list. It matches through .tab, but still beats button.tab at 0–1–1.',
    takeaway: ':is() takes its most specific argument.',
    rules: [
      { selector: ':is(#active, .tab)', ...purple, specificity: [1, 0, 0] },
      { selector: 'button.tab', ...orange, specificity: [0, 1, 1] },
    ],
  },
  {
    title: 'Inside job', boss: 'The inline stylist',
    markup: '<button id="active" class="tab" style="color: #6842ce">Firefox</button>',
    hint: 'A normal inline declaration wins over normal author stylesheet rules.',
    explanation: 'The style attribute wins over #active.tab, even with its ID and class. Inline styles have their own precedence over normal author stylesheet declarations.',
    takeaway: 'Normal inline styles beat normal stylesheet rules.',
    rules: [
      { selector: '#active.tab', ...orange, specificity: [1, 1, 0] },
      { selector: 'style attribute', ...purple, specificity: [0, 0, 0], inline: true },
    ],
  },
  {
    title: 'The final boss', boss: '!important',
    markup: '<button class="tab" style="color: #6842ce">Firefox</button>',
    hint: 'Importance is decided before specificity. The inline declaration here is normal.',
    explanation: 'The important .tab declaration beats the normal inline style. !important changes cascade priority; it does not add specificity points. Use it thoughtfully, rather than starting an arms race.',
    takeaway: 'Importance comes before specificity.',
    rules: [
      { selector: '.tab', ...orange, specificity: [0, 1, 0], important: true },
      { selector: 'style attribute', ...purple, specificity: [0, 0, 0], inline: true },
    ],
  },
];
