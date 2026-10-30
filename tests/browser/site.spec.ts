import { test, expect } from '@playwright/test';

let browserErrors: string[];
test.beforeEach(async ({ context, page }) => {
  // Exercise the built site without sending analytics or visiting outside hosts.
  await context.route('**/*', async (route) => {
    if (new URL(route.request().url()).hostname === '127.0.0.1')
      return route.continue();
    return route.abort();
  });
  browserErrors = [];
  page.on('pageerror', (error) => browserErrors.push(error.message));
});
test.afterEach(() =>
  expect(browserErrors, 'uncaught browser errors').toEqual([]),
);

test('game completion opens the Build journey', async ({ page }) => {
  await page.goto('/important');
  for (let round = 0; round < 7; round++) {
    await page
      .getByRole('group', {
        name: 'Choose the winning declaration, shown in source order',
      })
      .getByRole('button')
      .first()
      .click();
    await page
      .getByRole('button', { name: /Next battle|Claim victory/ })
      .click();
  }
  await expect(
    page.getByRole('heading', { name: 'Cascade conquered.' }),
  ).toBeVisible();
  await page.getByRole('link', { name: 'Build something with it' }).click();
  await expect(page).toHaveURL(/\/docs\?path=build$/);
  await expect(
    page.getByRole('tab', { name: 'Build a theme' }),
  ).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('tab', { name: 'Maintain a theme' }).click();
  await expect(page).toHaveURL(/\/docs\?path=maintain$/);
});

test('search loads on demand, accepts reordered words, and follows a passage link', async ({
  page,
}) => {
  const requests: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/search/')) requests.push(request.url());
  });
  await page.goto('/docs');
  expect(requests).toEqual([]);
  await page
    .getByRole('textbox', { name: 'Search documentation' })
    .fill('screenshots compare');
  const result = page
    .locator('.docs-search-result')
    .filter({ hasText: 'fxcss compare' })
    .first();
  await expect(result).toBeVisible();
  expect(requests).toHaveLength(1);
  await result.click();
  await expect(page).toHaveURL(/\/docs\/compare#passage-/);
  await expect(
    page.getByRole('heading', { name: 'fxcss compare', exact: true }),
  ).toBeVisible();
});

test('search recovers from a failed index download', async ({ page }) => {
  let fail = true;
  await page.route('**/search/index-*.json', (route) =>
    fail
      ? route.fulfill({ status: 503, body: 'Unavailable' })
      : route.continue(),
  );
  await page.goto('/docs');
  await page
    .getByRole('textbox', { name: 'Search documentation' })
    .fill('--profile');
  await expect(
    page.getByRole('status').filter({ hasText: 'Search could not load' }),
  ).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Documentation', exact: true })
    .getByRole('link', { name: 'watch', exact: true })).toBeVisible();
  fail = false;
  const retry = page.getByRole('button', { name: 'Retry search' });
  await retry.focus();
  await retry.press('Enter');
  await expect(page.locator('.docs-search-result').first()).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Search documentation' })).toBeFocused();
});

test('client-side documentation navigation announces the new page title', async ({ page }) => {
  await page.goto('/docs');
  const announcement = page.getByRole('status', { name: 'Page navigation' });
  await expect(announcement).toBeEmpty();
  const navigation = page.getByRole('navigation', { name: 'Documentation', exact: true });
  await navigation.getByRole('link', { name: 'watch', exact: true }).press('Enter');
  await expect(page).toHaveURL(/\/docs\/watch$/);
  await expect(announcement).toHaveText('fxcss watch · fxcss');
  await navigation.getByRole('link', { name: 'compare', exact: true }).press('Enter');
  await expect(page).toHaveURL(/\/docs\/compare$/);
  await expect(announcement).toHaveText('fxcss compare · fxcss');
});

test('code copying writes the actual example to the clipboard', async ({
  page,
  context,
}) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/docs/watch');
  const block = page.locator('.doc-code').first();
  const code = await block.locator('code').innerText();
  await block
    .getByRole('button', { name: 'Copy code example 1', exact: true })
    .click();
  await expect(page.locator('.doc-copy-status')).toHaveText(
    'Code example copied.',
  );
  await expect(block.getByRole('button')).toHaveText('Copied');
  await expect(block.getByRole('button')).toHaveAttribute(
    'aria-label',
    'Copied code example 1',
  );
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(code);
});

test('mobile Escape clears search, then closes the menu, then exits retro mode', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/docs');
  const menu = page.getByRole('button', { name: 'Browse documentation' });
  await menu.click();
  const search = page.getByRole('textbox', { name: 'Search documentation' });
  await search.fill('1999');
  const machine = page.getByRole('complementary', {
    name: 'Theme time machine',
  });
  await expect(machine).toBeVisible();
  await search.press('Escape');
  await expect(search).toHaveValue('');
  await expect(menu).toHaveAttribute('aria-expanded', 'true');
  await search.press('Escape');
  await expect(menu).toHaveAttribute('aria-expanded', 'false');
  await expect(menu).toBeFocused();
  await expect(machine).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await menu.press('Escape');
  await expect(machine).toHaveCount(0);
});

test('comparison images wait for visibility and the slider works by keyboard', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const requests: string[] = [];
  page.on('request', (request) => requests.push(request.url()));
  await page.goto('/');
  await expect(
    page.locator('link[rel="preload"][href^="/evidence/"]'),
  ).toHaveCount(0);
  const slider = page.getByRole('slider', {
    name: 'Image comparison',
    exact: true,
  });
  await expect(slider).not.toBeInViewport();
  await page.clock.install();
  await page.clock.fastForward(1500);
  expect(requests.some((url) => url.endsWith('/toolbar-after.png'))).toBe(
    false,
  );
  await slider.scrollIntoViewIfNeeded();
  await expect
    .poll(() => requests.some((url) => url.endsWith('/toolbar-after.png')))
    .toBe(true);
  await slider.press('Home');
  await expect(slider).toHaveAttribute('aria-valuenow', '0');
  await slider.press('ArrowRight');
  await expect(slider).not.toHaveAttribute('aria-valuenow', '0');
  await slider.press('End');
  await expect(slider).toHaveAttribute('aria-valuenow', '100');
});

test('catalogue controls expose pressed state', async ({ page }) => {
  await page.goto('/catalogue/');
  await expect(
    page.getByRole('button', { name: 'Light', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Dark', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Dark', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  await expect(
    page.getByRole('button', { name: 'Light', exact: true }),
  ).toHaveAttribute('aria-pressed', 'false');
});
