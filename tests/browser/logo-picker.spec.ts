import { test, expect, type Locator, type Page } from '@playwright/test';

let browserErrors: string[];
test.beforeEach(async ({ context, page }) => {
  await context.route('**/*', (route) =>
    new URL(route.request().url()).hostname === '127.0.0.1'
      ? route.continue()
      : route.abort(),
  );
  // Reduced motion lands the logo at once instead of flying it home.
  await page.emulateMedia({ reducedMotion: 'reduce' });
  browserErrors = [];
  page.on('pageerror', (error) => browserErrors.push(error.message));
});
test.afterEach(() =>
  expect(browserErrors, 'uncaught browser errors').toEqual([]),
);

async function centre(locator: Locator) {
  const box = (await locator.boundingBox())!;
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function dragLogoTo(page: Page, target: Locator) {
  const start = await centre(page.locator('.site-header .brand-mark'));
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  const end = await centre(target);
  await page.mouse.move(end.x, end.y, { steps: 10 });
}

const pickerParts = (page: Page) =>
  page.locator('.logo-picker-outline, .logo-picker-ghost, .logo-picker-label');

test('dragging the logo outlines the element underneath and names its class', async ({
  page,
}) => {
  await page.goto('/docs/watch');
  const home = page.locator('.site-header .brand-mark');
  const github = page.locator('.site-header .github-link');
  await dragLogoTo(page, github);
  const label = page.locator('.logo-picker-label');
  await expect(label).toHaveText('.github-link');
  await expect(home).toHaveCSS('visibility', 'hidden');
  const target = (await github.boundingBox())!;
  const outline = (await page.locator('.logo-picker-outline').boundingBox())!;
  for (const side of ['x', 'y', 'width', 'height'] as const)
    expect(outline[side]).toBeCloseTo(target[side], 0);
  const tag = (await label.boundingBox())!;
  expect(tag.y, 'the label sits underneath').toBeGreaterThanOrEqual(
    target.y + target.height,
  );
  expect(tag.x).toBeCloseTo(target.x, 0);

  await page.mouse.up();
  await expect(pickerParts(page)).toHaveCount(0);
  await expect(home).toHaveCSS('visibility', 'visible');
  await expect(page).toHaveURL(/\/docs\/watch$/);
});

test('a dropped logo follows no link, and Escape puts it back without leaving the time machine', async ({
  page,
}) => {
  await page.addInitScript(() =>
    sessionStorage.setItem('fxcss-time-machine', 'windows'),
  );
  await page.goto('/docs/watch');
  await expect(page.locator('html')).toHaveAttribute('data-retro', 'windows');
  await dragLogoTo(page, page.locator('.site-header .header-install'));
  await expect(page.locator('.logo-picker-label')).toHaveText(
    '.header-install',
  );
  await page.mouse.up();
  await expect(pickerParts(page)).toHaveCount(0);
  await expect(page).toHaveURL(/\/docs\/watch$/);

  await dragLogoTo(page, page.locator('.time-machine-heading strong'));
  await expect(page.locator('.logo-picker-label')).toHaveText(
    '.time-machine-heading',
  );
  await page.keyboard.press('Escape');
  await expect(pickerParts(page)).toHaveCount(0);
  await expect(page.locator('.pick-card'), 'Escape picks nothing').toHaveCount(
    0,
  );
  await expect(page.locator('html')).toHaveAttribute('data-retro', 'windows');
  // The press continues without the logo and ends without a click.
  const home = await centre(page.locator('.site-header .brand-mark'));
  await page.mouse.move(home.x, home.y, { steps: 5 });
  await expect(pickerParts(page)).toHaveCount(0);
  await page.mouse.up();
  await expect(page).toHaveURL(/\/docs\/watch$/);
});

test('a shaky click that stays on the logo still goes home', async ({
  page,
}) => {
  await page.goto('/docs/watch');
  const start = await centre(page.locator('.site-header .brand-mark'));
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  // Well past a drag threshold, but still on the logo and its wordmark.
  await page.mouse.move(start.x + 30, start.y + 6, { steps: 5 });
  await expect(pickerParts(page), 'the mark stays put').toHaveCount(0);
  await page.mouse.move(start.x - 4, start.y - 3, { steps: 3 });
  await page.mouse.up();
  await expect(page).toHaveURL('/');
  await expect(pickerParts(page)).toHaveCount(0);
  await expect(page.locator('.pick-card')).toHaveCount(0);
});

test('carrying the logo back home puts it down without picking or following it', async ({
  page,
}) => {
  await page.goto('/docs/watch');
  await dragLogoTo(page, page.locator('.site-header .github-link'));
  await expect(page.locator('.logo-picker-label')).toHaveText('.github-link');
  const home = await centre(page.locator('.site-header .brand-mark'));
  await page.mouse.move(home.x + 3, home.y + 2, { steps: 10 });
  await expect(
    page.locator('.logo-picker-outline'),
    'nothing is outlined over the logo',
  ).toBeHidden();
  await page.mouse.up();
  await expect(pickerParts(page)).toHaveCount(0);
  await expect(page.locator('.pick-card')).toHaveCount(0);
  await expect(page).toHaveURL(/\/docs\/watch$/);
});

test('a Tailwind component is named by the site’s own class', async ({
  page,
}) => {
  await page.goto('/');
  const toggle = page.getByRole('button', {
    name: 'Zoom into the tab',
    exact: true,
  });
  await toggle.evaluate((element) =>
    element.scrollIntoView({ block: 'center', behavior: 'instant' }),
  );
  await dragLogoTo(page, toggle);
  await expect(page.locator('.logo-picker-label')).toHaveText('.detail-toggle');
  await page.mouse.up();
  await expect(pickerParts(page)).toHaveCount(0);
});

test('dropping the logo prints the fxcss pick report and copies the selector', async ({
  page,
}) => {
  await page.addInitScript(() => {
    const copied: string[] = [];
    Object.assign(window, { copied });
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: async (text: string) => void copied.push(text) },
    });
  });
  await page.goto('/docs/watch');
  const github = page.locator('.site-header .github-link');
  await dragLogoTo(page, github);
  await page.mouse.up();

  const card = page.getByRole('dialog', { name: 'fxcss pick' });
  await expect(card).toBeVisible();
  await expect(card).toBeFocused();
  const report = card.locator('.pick-card-report');
  await expect(report.locator('strong')).toHaveText('a  →  .github-link');
  const line = (text: RegExp) =>
    report.locator(':scope > span').filter({ hasText: text });
  await expect(line(/^classes {3}.*github-link/)).toHaveCount(1);
  const box = (await github.boundingBox())!;
  await expect(
    line(
      new RegExp(
        `^box {7}${Math.round(box.width)}×${Math.round(box.height)} at \\(${Math.round(box.x)}, ${Math.round(box.y)}\\)$`,
      ),
    ),
  ).toHaveCount(1);
  const colour = line(/^ {2}color:\s+rgb/);
  await expect(colour).toHaveCount(1);
  // Properties, values and classes are coloured apart; colours get a swatch.
  const tint = (part: string) =>
    report
      .locator(part)
      .first()
      .evaluate((element) => getComputedStyle(element).color);
  const tints = new Set([
    await tint('.pick-token-property'),
    await tint('.pick-token-value, .pick-token-colour'),
    await tint('.pick-token-class'),
    await tint('.pick-token-label'),
  ]);
  expect(tints.size, 'four distinct colours').toBe(4);
  await expect(colour.locator('.pick-swatch')).toHaveAttribute(
    'aria-hidden',
    'true',
  );
  expect(
    await colour
      .locator('.pick-swatch')
      .evaluate((element) => getComputedStyle(element).boxShadow),
  ).toMatch(/^rgb.* inset$/);
  // A wrapped value stays under its own column.
  expect(
    await colour.evaluate((element) => getComputedStyle(element).textIndent),
  ).toMatch(/^-\d/);
  const styledBy = line(/^styled by \d+ rules? on this site$/);
  await expect(styledBy).toHaveCount(1);
  // Rules naming the element's class come first.
  await expect(styledBy.locator('xpath=following-sibling::span[1]')).toHaveText(
    /^ {2}.*\.github-link/,
  );
  const cardBox = (await card.boundingBox())!;
  expect(cardBox.y, 'the card sits beside the element').toBeGreaterThanOrEqual(
    box.y + box.height,
  );

  await card.getByRole('button', { name: 'Copy selector' }).click();
  await expect(card.locator('.pick-card-status')).toHaveText('Copied');
  expect(
    await page.evaluate(
      () => (window as unknown as { copied: string[] }).copied,
    ),
  ).toEqual(['.github-link']);
  await expect(page).toHaveURL(/\/docs\/watch$/);

  await page.keyboard.press('Escape');
  await expect(card).toHaveCount(0);
  await expect(page.locator('.site-header .brand')).toBeFocused();
  await expect(page).toHaveURL(/\/docs\/watch$/);
});

test('the pick card closes on a press elsewhere, a new drag, and its docs link', async ({
  page,
}) => {
  await page.goto('/docs/watch');
  const card = page.locator('.pick-card');
  await dragLogoTo(page, page.locator('.site-header .header-install'));
  await page.mouse.up();
  await expect(card.locator('strong')).toHaveText(/→ {2}\.header-install$/);
  // A press beside the card closes it and activates nothing else.
  const heading = (await page.locator('h1').boundingBox())!;
  await page.mouse.click(heading.x + 2, heading.y + 2);
  await expect(card).toHaveCount(0);

  await dragLogoTo(page, page.locator('.site-header .github-link'));
  await page.mouse.up();
  await expect(card).toHaveCount(1);
  // Picking up the logo again replaces the card rather than adding another.
  await dragLogoTo(page, page.locator('.site-header .header-install'));
  await expect(card).toHaveCount(0);
  await page.mouse.up();
  await expect(card.locator('strong')).toHaveText(/→ {2}\.header-install$/);

  await card.getByRole('link', { name: /fxcss pick/ }).click();
  await expect(page).toHaveURL(/\/docs\/pick$/);
  await expect(card).toHaveCount(0);
});

for (const [stop, prompt] of [
  ['windows', 'C:\\> '],
  ['mac', '% '],
  ['linux', '$ '],
] as const)
  test(`in the ${stop} time machine the card is a period terminal that reports the retro rules`, async ({
    page,
  }) => {
    await page.addInitScript(
      (destination) =>
        sessionStorage.setItem('fxcss-time-machine', destination),
      stop,
    );
    await page.goto('/docs/watch');
    await expect(page.locator('html')).toHaveAttribute('data-retro', stop);
    await dragLogoTo(page, page.locator('.site-header .github-link'));
    await page.mouse.up();
    const card = page.getByRole('dialog', { name: 'fxcss pick' });
    await expect(card).toBeVisible();
    const promptText = await card
      .locator('.pick-card-prompt')
      .evaluate((element) =>
        getComputedStyle(element, '::before').content.replace(/^"|"$/g, ''),
      );
    expect(promptText.replace(/\\\\/g, '\\')).toBe(prompt);
    await expect(
      card
        .locator('.pick-card-report > span')
        .filter({ hasText: /^ {2}html\[data-retro\]/ })
        .first(),
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(card).toHaveCount(0);
    await expect(
      page.locator('html'),
      'Escape closes only the card',
    ).toHaveAttribute('data-retro', stop);
  });
