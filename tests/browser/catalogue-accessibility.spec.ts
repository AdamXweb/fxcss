import { test, expect } from '@playwright/test';

function luminance(color: string) {
  const channels = color
    .match(/[\d.]+/g)
    ?.slice(0, 3)
    .map(Number);
  if (!channels || channels.length !== 3)
    throw new Error(`Unexpected colour: ${color}`);
  const linear = channels.map((channel) => {
    const value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}

function contrast(foreground: string, background: string) {
  const values = [luminance(foreground), luminance(background)].sort(
    (a, b) => b - a,
  );
  return (values[0] + 0.05) / (values[1] + 0.05);
}

for (const colorScheme of ['light', 'dark'] as const) {
  test(`catalogue remains readable and navigable in ${colorScheme} appearance`, async ({
    page,
    context,
  }) => {
    await context.route('**/*', (route) =>
      new URL(route.request().url()).hostname === '127.0.0.1'
        ? route.continue()
        : route.abort(),
    );
    await page.emulateMedia({ colorScheme });
    await page.goto('/catalogue/');
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await expect(page.getByRole('main')).toHaveCount(1);
    await expect(page).toHaveTitle('Firefox themeable UI catalogue · fxcss');
    await expect(page.locator('head meta[name="robots"]')).toHaveAttribute(
      'content',
      'noindex,follow',
    );
    await expect(page.locator('head link[rel="canonical"]')).toHaveAttribute(
      'href',
      'https://fxcss.com/catalogue/',
    );

    const missing = page.locator('.card.missing').first();
    await expect(missing).toHaveCSS('opacity', '1');
    const textColors = await missing.evaluate((card) => {
      const background = getComputedStyle(card).backgroundColor;
      return [...card.querySelectorAll('h3, .badge, .sel, p, summary')]
        .filter((element) => element.getClientRects().length)
        .map((element) => ({
          text: element.textContent,
          foreground: getComputedStyle(element).color,
          background,
        }));
    });
    for (const { text, foreground, background } of textColors)
      expect(
        contrast(foreground, background),
        `${colorScheme}: ${text}`,
      ).toBeGreaterThanOrEqual(4.5);

    for (const name of ['Light', 'Dark']) {
      const button = page.getByRole('button', { name, exact: true });
      await button.click();
      await expect(button).toHaveAttribute('aria-pressed', 'true');
      const colors = await button.evaluate((element) => {
        const style = getComputedStyle(element);
        return { foreground: style.color, background: style.backgroundColor };
      });
      expect(
        contrast(colors.foreground, colors.background),
        `${colorScheme}: selected ${name} button`,
      ).toBeGreaterThanOrEqual(4.5);
    }
    await missing.locator('summary').focus();
    await page.keyboard.press('Enter');
    await expect(missing.locator('details')).toHaveAttribute('open', '');
  });
}
