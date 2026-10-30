import { test, expect } from '@playwright/test';
import content from '../../content/docs.json' with { type: 'json' };

test.beforeEach(async ({ context }) => {
  await context.route('**/*', async (route) => {
    if (new URL(route.request().url()).hostname === '127.0.0.1')
      return route.continue();
    return route.abort();
  });
});

test('documentation metadata stays in the head after hydration and navigation', async ({ page }) => {
  await page.goto('/docs/watch');
  // Use the hydrated search to navigate, rather than testing only initial HTML.
  await page.getByRole('textbox', { name: 'Search documentation' }).fill('fxcss rollback');
  await page.locator('.docs-search-result').filter({ hasText: 'fxcss rollback' }).first().click();
  await expect(page).toHaveURL(/\/docs\/rollback#passage-/);
  const doc = content.pages.find((item) => item.slug === 'rollback')!;
  await expect(page.locator('head > title')).toHaveJSProperty('textContent', `${doc.title} · fxcss`);
  await expect(page.locator('head > meta[name="description"]')).toHaveAttribute('content', doc.description);
  await expect(page.locator('head > link[rel="canonical"]')).toHaveAttribute('href', 'https://fxcss.com/docs/rollback');
  await expect(page.locator('head > meta[property="og:url"]')).toHaveAttribute('content', 'https://fxcss.com/docs/rollback');
  await expect(page.locator('body title, body meta[name="description"], body link[rel="canonical"]')).toHaveCount(0);
});

test('unknown application and documentation routes retain specific 404 metadata', async ({ page }) => {
  for (const route of ['/this-page-does-not-exist', '/docs/this-command-does-not-exist']) {
    const response = await page.goto(route);
    expect(response?.status()).toBe(404);
    await expect(page.getByRole('heading', { name: 'Let’s find your way back.' })).toBeVisible();
    await expect(page.locator('head > title')).toHaveJSProperty('textContent', 'Page not found · fxcss');
    await expect(page.locator('head > meta[name="description"]')).toHaveAttribute('content', /This page could not be found/);
    await expect(page.locator('head > meta[name="robots"]')).toHaveAttribute('content', /noindex/);
    await expect(page.locator('head > meta[property="og:image"]')).toHaveAttribute('content', 'https://fxcss.com/assets/og-card.png');
  }
});
