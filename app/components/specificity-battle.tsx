'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Check, Lightbulb, RotateCcw, Swords, Trophy } from 'lucide-react';
import { battleRounds, winningRule, type BattleRound } from '../../lib/specificity';

function CascadePreview({ round }: { round: BattleRound }) {
  const host = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!host.current) return;
    const shadow = host.current.shadowRoot || host.current.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = `button { all: initial; display: block; padding: 10px 14px; border: 2px solid currentColor; border-radius: 8px 8px 2px 2px; background: #f7f5fc; font: 700 14px Arial, sans-serif; pointer-events: none; }\n` +
      round.rules.filter((rule) => !rule.inline).map((rule) => `${rule.selector} { color: ${rule.color}${rule.important ? ' !important' : ''}; }`).join('\n');
    const template = document.createElement('template');
    // Lesson markup is authored locally; no visitor input enters this preview.
    template.innerHTML = round.markup;
    const button = template.content.querySelector('button');
    if (button) button.tabIndex = -1;
    shadow.replaceChildren(style, template.content);
    return () => shadow.replaceChildren();
  }, [round]);
  return <span ref={host} className="battle-live-preview" aria-hidden="true" />;
}

export function SpecificityBattle() {
  const [roundIndex, setRoundIndex] = useState(0);
  const [answers, setAnswers] = useState<number[]>([]);
  const [hint, setHint] = useState(false);
  const [complete, setComplete] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const round = battleRounds[roundIndex];
  const answer = answers[roundIndex];
  const answered = answer !== undefined;
  const winner = winningRule(round.rules);
  const score = answers.filter((choice, index) => choice === winningRule(battleRounds[index].rules)).length;

  function advance() {
    if (roundIndex === battleRounds.length - 1) setComplete(true);
    else setRoundIndex(roundIndex + 1);
    setHint(false);
    requestAnimationFrame(() => heading.current?.focus());
  }
  function restart() {
    setAnswers([]);
    setRoundIndex(0);
    setComplete(false);
    setHint(false);
    requestAnimationFrame(() => heading.current?.focus());
  }

  return (
    <section className="battle-shell" aria-label="CSS specificity game">
      <div className="battle-toolbar">
        <span><Swords size={17} /> CASCADE ARENA</span>
        <span>{complete ? 'All rounds complete' : `Round ${roundIndex + 1} of ${battleRounds.length}`}</span>
      </div>
      <div className="battle-progress" aria-label={`${complete ? battleRounds.length : roundIndex} of ${battleRounds.length} rounds completed`}>
        {battleRounds.map((item, index) => <span key={item.title} className={index < answers.length ? 'done' : index === roundIndex && !complete ? 'current' : ''} />)}
      </div>
      {complete ? (
        <div className="battle-finish">
          <Trophy size={52} aria-hidden="true" />
          <h2 ref={heading} tabIndex={-1}>Cascade conquered.</h2>
          <p>You called {score} of {battleRounds.length} battles correctly. {score === battleRounds.length ? 'Flawless specificity.' : 'Every answer came with a lesson. That’s how you win.'}</p>
          <ul>{battleRounds.map((item) => <li key={item.title}><Check size={16} />{item.takeaway}</li>)}</ul>
          <div className="battle-finish-actions">
            <button className="button primary" onClick={restart}><RotateCcw size={16} /> Play again</button>
            <Link href="/docs?path=build" className="text-link">Build something with it <ArrowRight size={16} /></Link>
          </div>
          <p className="battle-secret">Victory deserves a coffee. Try searching the docs for <code>c0ffee</code>.</p>
        </div>
      ) : (
        <div className="battle-body">
          <div className="battle-round-heading">
            <div><span className="eyebrow">VS. {round.boss}</span><h2 ref={heading} tabIndex={-1}>{round.title}</h2></div>
            <span className="battle-round-number" aria-hidden="true">{String(roundIndex + 1).padStart(2, '0')}</span>
          </div>
          <p className="battle-prompt">Both declarations match this button. Which colour will the browser use?</p>
          <pre className="battle-markup"><code>{round.markup}</code></pre>
          <div className="battle-preview" aria-label={answered ? `Browser result: ${round.rules[winner].colorName}` : 'Browser result hidden until you answer'}>
            {answered ? <CascadePreview round={round} /> : <span className="battle-preview-tab">Firefox <span aria-hidden="true">?</span></span>}
            <span>{answered ? `The browser chose ${round.rules[winner].colorName}.` : 'Make your prediction to reveal the result.'}</span>
          </div>
          <fieldset className="battle-rules" aria-label="Choose the winning declaration, shown in source order">
            {round.rules.map((rule, index) => (
              <button
                key={`${roundIndex}-${index}`}
                className={`battle-rule ${answered && index === winner ? 'winner' : ''} ${answered && index === answer && index !== winner ? 'missed' : ''}`}
                onClick={() => { if (!answered) setAnswers([...answers, index]); }}
                aria-disabled={answered}
                aria-pressed={answer === index}
              >
                <span className="battle-rule-label">{rule.inline ? 'INLINE STYLE' : `RULE ${index + 1}`}<span>{answered && index === winner ? 'WINNER' : answered && index === answer ? 'YOUR PICK' : `Pick ${rule.colorName}`}</span></span>
                <code>{rule.inline ? `style="color: ${rule.color}"` : <>{rule.selector} {'{'}<br />{'  '}color: {rule.color}{rule.important ? ' !important' : ''};<br />{'}'}</>}</code>
                {answered && <span className="battle-weight">{rule.inline ? 'Inline declaration' : `Specificity: ${rule.specificity.join('–')}`}{rule.important ? ' · important' : ''}</span>}
              </button>
            ))}
          </fieldset>
          <div className="battle-feedback" aria-live="polite" aria-atomic="true">
            {answered ? <><h3>{answer === winner ? 'Direct hit.' : 'A plot twist in the cascade.'}</h3><p>{round.explanation}</p></> : <p>Pick a rule above. No timer, no lost lives—just CSS.</p>}
          </div>
          <div className="battle-controls">
            {answered ? <button className="button primary" onClick={advance}>{roundIndex === battleRounds.length - 1 ? 'Claim victory' : 'Next battle'} <ArrowRight size={16} /></button> : <button className="battle-hint-button" onClick={() => setHint(!hint)} aria-expanded={hint} aria-controls="battle-hint"><Lightbulb size={16} />{hint ? 'Hide hint' : 'A little help?'}</button>}
            <span>{score} correct so far</span>
          </div>
          {!answered && hint && <p className="battle-hint" id="battle-hint">{round.hint}</p>}
        </div>
      )}
      <div className="battle-footnote">
        <p>Same element, author origin, no layers, scopes, animations or transitions. The wider cascade has more rules.</p>
        <a href="https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Cascade/Specificity" target="_blank" rel="noreferrer">Read the full specificity guide ↗</a>
      </div>
    </section>
  );
}
