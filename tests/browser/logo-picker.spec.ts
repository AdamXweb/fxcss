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
  await expect(page.locator('html')).toHaveAttribute('data-retro', 'windows');
  // The press continues without the logo and ends without a click.
  const home = await centre(page.locator('.site-header .brand-mark'));
  await page.mouse.move(home.x, home.y, { steps: 5 });
  await expect(pickerParts(page)).toHaveCount(0);
  await page.mouse.up();
  await expect(page).toHaveURL(/\/docs\/watch$/);
});

test('a click that wobbles less than the drag distance still goes home', async ({
  page,
}) => {
  await page.goto('/docs/watch');
  const start = await centre(page.locator('.site-header .brand-mark'));
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(start.x + 2, start.y + 1);
  await page.mouse.up();
  await expect(page).toHaveURL('/');
  await expect(pickerParts(page)).toHaveCount(0);
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
