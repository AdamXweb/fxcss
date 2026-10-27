import test from 'node:test';
import assert from 'node:assert/strict';
import { searchSecret } from '../lib/easter-eggs.ts';
import { battleRounds, winningRule } from '../lib/specificity.ts';
import { watchSleepyFavicon } from '../lib/sleepy-favicon.ts';
import { retroDestinationForQuery, retroDestinations, storedRetroDestination } from '../lib/time-machine.ts';

test('secret searches accept deliberate exact phrases without catching normal documentation searches', () => {
  for (const query of ['c0ffee', '#c0ffee', '  #C0FFEE  ']) assert.equal(searchSecret(query), 'coffee');
  assert.equal(searchSecret('1999'), 'retro');
  assert.equal(searchSecret('2003'), 'retro');
  assert.equal(searchSecret('2002'), 'retro');
  for (const query of ['firebird', 'phoenix', ' Firebird ', 'PHOENIX']) assert.equal(searchSecret(query), 'retro');
  assert.equal(searchSecret('!important'), 'battle');
  for (const query of ['', 'coffee', '#coffee', 'c0ffee install', '1999 theme', 'firebird theme', 'phoenix install', '--important', 'install', '--profile']) {
    assert.equal(searchSecret(query), null, query);
  }
});

test('time machine destinations use distinct first-release years and preserve old saved trips', () => {
  assert.deepEqual(['1999', '2003', '2002'].map(retroDestinationForQuery), ['windows', 'mac', 'linux']);
  assert.equal(retroDestinationForQuery(' 2003 '), 'mac');
  for (const query of ['firebird', ' Firebird ']) assert.equal(retroDestinationForQuery(query), 'mac');
  for (const query of ['phoenix', 'PHOENIX']) assert.equal(retroDestinationForQuery(query), 'linux');
  for (const query of ['2004', '2003 theme', 'mac', '2002 linux', '']) assert.equal(retroDestinationForQuery(query), null);
  assert.equal(retroDestinations.mac.browser, 'Firebird 0.6');
  assert.equal(retroDestinations.linux.browser, 'Phoenix 0.1');
  for (const destination of ['windows', 'mac', 'linux']) assert.equal(storedRetroDestination(destination), destination);
  assert.equal(storedRetroDestination('1999'), 'windows');
  for (const invalid of [null, '', 'false', '__proto__', '2004']) assert.equal(storedRetroDestination(invalid), null);
});

test('lesson outcomes follow the cascade, including the :where, :is and inline exceptions', () => {
  const expected = [0, 1, 1, 0, 0, 1, 0];
  assert.equal(battleRounds.length, expected.length);
  battleRounds.forEach((round, index) => assert.equal(winningRule(round.rules), expected[index], round.title));
});

test('importance precedes inline status; specificity precedes source order within the same priority', () => {
  const rule = (specificity, extra = {}) => ({ selector: '.tab', color: '#000', colorName: 'black', specificity, ...extra });
  assert.equal(winningRule([rule([0, 1, 0], { important: true }), rule([9, 9, 9])]), 0);
  assert.equal(winningRule([rule([9, 9, 9], { important: true }), rule([0, 0, 0], { important: true, inline: true })]), 1);
  assert.equal(winningRule([rule([1, 0, 0], { important: true }), rule([0, 9, 9], { important: true })]), 0);
  assert.equal(winningRule([rule([0, 1, 0]), rule([0, 1, 0])]), 1);
});

test('favicon sleeps in every format, handles navigation metadata and restores exact originals', () => {
  class Icon {
    constructor(href, type) { this.attributes = new Map([['href', href], ...(type ? [['type', type]] : [])]); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    setAttribute(name, value) { this.attributes.set(name, value); }
    removeAttribute(name) { this.attributes.delete(name); }
    set href(value) { this.setAttribute('href', value); }
    set type(value) { this.setAttribute('type', value); }
  }
  const original = [new Icon('/logo.svg', 'image/svg+xml'), new Icon('/icon.png', 'image/png')];
  let icons = [...original];
  const listeners = new Map();
  const page = {
    hidden: false, head: {}, querySelectorAll: () => icons,
    addEventListener: (event, callback) => listeners.set(event, callback),
    removeEventListener: (event) => listeners.delete(event),
  };
  let metadataChanged;
  let disconnected = false;
  class Observer {
    constructor(callback) { metadataChanged = callback; }
    observe() {}
    disconnect() { disconnected = true; }
  }
  const cleanup = watchSleepyFavicon(page, Observer);
  assert.equal(icons[0].getAttribute('href'), '/logo.svg');
  page.hidden = true;
  listeners.get('visibilitychange')();
  for (const icon of icons) {
    assert.equal(icon.getAttribute('href'), '/assets/fxcss-sleepy.svg');
    assert.equal(icon.getAttribute('type'), 'image/svg+xml');
  }
  const replacement = new Icon('/replacement.ico');
  icons = [replacement];
  metadataChanged();
  assert.equal(replacement.getAttribute('href'), '/assets/fxcss-sleepy.svg');
  page.hidden = false;
  listeners.get('visibilitychange')();
  assert.equal(original[0].getAttribute('href'), '/logo.svg');
  assert.equal(original[1].getAttribute('type'), 'image/png');
  assert.equal(replacement.getAttribute('href'), '/replacement.ico');
  assert.equal(replacement.getAttribute('type'), null);
  page.hidden = true;
  listeners.get('visibilitychange')();
  cleanup();
  assert.equal(replacement.getAttribute('href'), '/replacement.ico');
  assert.equal(listeners.size, 0);
  assert.equal(disconnected, true);
});
