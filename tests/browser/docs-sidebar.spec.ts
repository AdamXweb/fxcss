import { test, expect } from '@playwright/test';

test.beforeEach(async ({ context, page }) => {
  await context.route('**/*', (route) =>
    new URL(route.request().url()).hostname === '127.0.0.1'
      ? route.continue()
      : route.abort(),
  );
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

for (const width of [761, 1150, 1151, 1920]) {
  test(`desktop docs search fits the sidebar and stays inset when scrolling at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/docs/watch');
    const sidebar = page.getByRole('complementary', {
      name: 'Documentation navigation',
    });
    const scrollArea = sidebar.locator('.desktop-docs-nav');
    const search = sidebar.getByRole('textbox', {
      name: 'Search documentation',
    });
    await expect(search).toBeVisible();

    const placeholder = await search.evaluate((input: HTMLInputElement) => {
      const style = getComputedStyle(input);
      const context = document.createElement('canvas').getContext('2d')!;
      context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
      return {
        textWidth: context.measureText(input.placeholder).width,
        availableWidth:
          input.clientWidth -
          parseFloat(style.paddingLeft) -
          parseFloat(style.paddingRight),
      };
    });
    expect(
      placeholder.textWidth,
      'the entire placeholder is readable',
    ).toBeLessThanOrEqual(placeholder.availableWidth);

    const sidebarBox = (await sidebar.boundingBox())!;
    const scrollBox = (await scrollArea.boundingBox())!;
    const field = scrollArea.locator('.guide-search-field');
    const fieldBox = (await field.boundingBox())!;
    // The scroll track belongs at the panel edge, with padding inside it.
    expect(scrollBox.x).toBeCloseTo(sidebarBox.x, 0);
    expect(
      sidebarBox.x + sidebarBox.width - scrollBox.x - scrollBox.width,
    ).toBeLessThanOrEqual(1);
    expect(fieldBox.x - sidebarBox.x).toBeGreaterThanOrEqual(16);
    expect(
      sidebarBox.x + sidebarBox.width - fieldBox.x - fieldBox.width,
    ).toBeGreaterThanOrEqual(16);
    expect(
      await scrollArea.evaluate(
        (element) => element.scrollWidth - element.clientWidth,
      ),
    ).toBe(0);

    await scrollArea.evaluate((element) => {
      element.scrollTop = 400;
    });
    expect(
      await scrollArea.evaluate((element) => element.scrollTop),
    ).toBeGreaterThan(0);
    await expect(search).toBeInViewport({ ratio: 1 });
    const scrolledFieldBox = (await field.boundingBox())!;
    expect(
      scrolledFieldBox.y,
      'search keeps its top inset while links scroll',
    ).toBeCloseTo(fieldBox.y, 0);

    await search.fill('rollback');
    await expect(sidebar.locator('.docs-search-result').first()).toBeVisible();
    const clear = sidebar.getByRole('button', { name: 'Clear search' });
    await expect(clear).toBeInViewport({ ratio: 1 });
    await clear.click();
    await expect(search).toBeEmpty();
    await expect(search).toBeFocused();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
  });
}
