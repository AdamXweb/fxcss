import { test, expect, type Page } from '@playwright/test';

test.beforeEach(async ({ context, page }) => {
  await context.route('**/*', (route) =>
    new URL(route.request().url()).hostname === '127.0.0.1'
      ? route.continue()
      : route.abort(),
  );
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

async function startRetro(page: Page, destination: string, path = '/docs') {
  await page.addInitScript((value) => {
    sessionStorage.setItem('fxcss-time-machine', value);
  }, destination);
  await page.goto(path);
  await expect(page.locator('html')).toHaveAttribute('data-retro', destination);
}

async function expectReflow(page: Page) {
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(await page.evaluate(() => innerWidth));
}

for (const destination of ['windows', 'mac', 'linux']) {
  test(`${destination} retro chrome scrolls away at high zoom and keyboard focus stays visible`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 256 });
    await startRetro(page, destination);
    await expect(page.locator('.time-machine-panel')).toHaveCSS(
      'position',
      'static',
    );
    await expect(page.locator('.site-header')).toHaveCSS('position', 'static');
    await expect(page.locator('html')).toHaveCSS('scroll-padding-top', '16px');
    await expectReflow(page);
    await page.evaluate(() => window.scrollTo(0, 1000));
    expect(
      await page
        .locator('.site-header')
        .evaluate((element) => element.getBoundingClientRect().bottom),
    ).toBeLessThanOrEqual(0);

    // Tab from the last header link into the article's mobile navigation.
    await page.locator('.site-header .github-link').focus();
    await page.keyboard.press('Tab');
    const browse = page.getByRole('button', { name: 'Browse documentation' });
    await expect(browse).toBeFocused();
    await expect(browse).toBeInViewport({ ratio: 1 });

    await page
      .getByRole('button', { name: 'Minimise controls', exact: true })
      .first()
      .click();
    const restore = page.getByRole('button', {
      name: 'Restore theme controls',
      exact: true,
    });
    await expect(restore).toBeFocused();
    await expect(restore).toBeInViewport({ ratio: 1 });
    await restore.click();
    const selected = page.locator(
      '.time-machine-destinations [aria-pressed="true"]',
    );
    await expect(selected).toBeFocused();
    await expect(selected).toBeInViewport({ ratio: 1 });

    // A short desktop viewport also avoids sticky chrome; a full-height
    // desktop retains it. Resizing exercises the measured CSS offsets.
    await page.setViewportSize({ width: 1280, height: 500 });
    await expect(page.locator('.site-header')).toHaveCSS('position', 'static');
    await expect(page.locator('.desktop-docs-nav')).toHaveCSS('top', '0px');
    await page.setViewportSize({ width: 1280, height: 900 });
    await expect(page.locator('.time-machine-panel')).toHaveCSS(
      'position',
      'sticky',
    );
    await expect(page.locator('.site-header')).toHaveCSS('position', 'sticky');
  });
}

test('the expanded film description and journey tabs reflow at 320px', async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 256 });
  await page.goto('/');
  await page
    .getByText('Read the film’s text description', { exact: true })
    .click();
  await expect(page.locator('.film-transcript')).toHaveAttribute('open', '');
  await expectReflow(page);
  await expect(page.locator('.film video')).toHaveAttribute(
    'aria-describedby',
    'film-description',
  );
  await expect(page.locator('#film-description')).toContainText(
    'Firefox Stable, Beta, Nightly and ESR',
  );

  for (const path of ['', '?path=build', '?path=maintain']) {
    await page.goto(`/docs${path}`);
    await expectReflow(page);
    const tabs = page.locator('.journey-tabs [role="tab"]');
    await expect(tabs).toHaveCount(3);
    for (const tab of await tabs.all()) {
      const bounds = await tab.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(320);
    }
  }
});

test('comparison has one keyboard slider, normal images and a stable zoom toggle', async ({
  page,
}) => {
  await page.goto('/');
  const figure = page.locator('.comparison-figure');
  const slider = figure.getByRole('slider', {
    name: 'Image comparison',
    exact: true,
  });
  await expect(figure.getByRole('slider')).toHaveCount(1);
  await expect(figure.locator('.compare-image')).not.toHaveAttribute('role');
  await expect(figure.locator('.compare-image')).not.toHaveAttribute(
    'tabindex',
  );
  await expect(figure.locator('.compare-image img[alt]')).toHaveCount(2);
  await expect(
    figure.getByRole('tabpanel', { name: 'Compare', exact: true }),
  ).toHaveAttribute('tabindex', '-1');
  await slider.press('Home');
  await expect(slider).toHaveAttribute('aria-valuenow', '0');
  await slider.press('End');
  await expect(slider).toHaveAttribute('aria-valuenow', '100');

  // The larger drag target updates the same range without adding another stop.
  const surface = figure.locator('.compare-image');
  await surface.scrollIntoViewIfNeeded();
  const bounds = await surface.boundingBox();
  expect(bounds).not.toBeNull();
  await page.mouse.move(
    bounds!.x + bounds!.width * 0.25,
    bounds!.y + bounds!.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(
    bounds!.x + bounds!.width * 0.75,
    bounds!.y + bounds!.height / 2,
  );
  await page.mouse.up();
  await expect(slider).toBeFocused();
  const value = Number(await slider.getAttribute('aria-valuenow'));
  expect(value).toBeGreaterThan(70);
  expect(value).toBeLessThan(80);
  const zoom = figure.getByRole('button', {
    name: 'Zoom into the tab',
    exact: true,
  });
  await expect(zoom).toHaveAttribute('aria-pressed', 'false');
  await zoom.click();
  await expect(zoom).toHaveAttribute('aria-pressed', 'true');
  await zoom.click();
  await expect(zoom).toHaveAttribute('aria-pressed', 'false');
});

test('game progress is decorative and hint controls always identify their target', async ({
  page,
}) => {
  await page.goto('/important');
  await expect(page.locator('.battle-progress')).toHaveAttribute(
    'aria-hidden',
    'true',
  );
  await expect(
    page.locator('.battle-progress[aria-label], .battle-preview[aria-label]'),
  ).toHaveCount(0);
  await expect(page.locator('.battle-toolbar')).toContainText('Round 1 of 7');
  const hint = page.getByRole('button', { name: 'A little help?' });
  const target = page.locator(`#${await hint.getAttribute('aria-controls')}`);
  await expect(target).toHaveCount(1);
  await expect(target).toBeHidden();
  await hint.click();
  await expect(target).toBeVisible();
  await page.getByRole('button', { name: 'Hide hint' }).click();
  await expect(target).toBeHidden();
});

// Check the specific palette regressions without treating this as a full
// accessibility audit. For stripes/gradients use the lowest contrast stop.
async function expectTextContrast(page: Page, selector: string) {
  const measurements = await page.locator(selector).evaluateAll((elements) => {
    function colors(value: string): number[][] {
      return [...value.matchAll(/rgba?\(([^)]+)\)/g)].map((match) =>
        match[1].split(/[, ]+/).map(Number),
      );
    }
    function luminance(rgb: number[]) {
      return rgb
        .slice(0, 3)
        .map((value) => {
          const channel = value / 255;
          return channel <= 0.04045
            ? channel / 12.92
            : ((channel + 0.055) / 1.055) ** 2.4;
        })
        .reduce(
          (total, value, index) =>
            total + value * [0.2126, 0.7152, 0.0722][index],
          0,
        );
    }
    return elements.map((element) => {
      const foreground = colors(getComputedStyle(element).color)[0];
      let backgrounds: number[][] = [];
      for (
        let parent: Element | null = element;
        parent;
        parent = parent.parentElement
      ) {
        const style = getComputedStyle(parent);
        const candidates =
          style.backgroundImage === 'none'
            ? colors(style.backgroundColor)
            : colors(style.backgroundImage);
        if (
          candidates.length &&
          candidates.every((value) => (value[3] ?? 1) === 1)
        ) {
          backgrounds = candidates;
          break;
        }
      }
      const ratios = backgrounds.map((background) => {
        const [a, b] = [luminance(foreground), luminance(background)];
        return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      });
      return {
        text: element.textContent?.trim(),
        ratio: ratios.length ? Math.min(...ratios) : 0,
      };
    });
  });
  expect(measurements.length).toBeGreaterThan(0);
  for (const item of measurements)
    expect(item.ratio, item.text).toBeGreaterThanOrEqual(4.5);
}

for (const destination of ['windows', 'mac', 'linux']) {
  test(`${destination} retro text keeps sufficient contrast on home and game surfaces`, async ({
    page,
  }) => {
    await startRetro(page, destination, '/');
    await expectTextContrast(
      page,
      '.demo-heading .eyebrow, .evidence-caption span, .evidence-caption a, .demo-caption p, .demo-caption a, .compare-labels span, .site-header nav a',
    );
    await page.goto('/important');
    await expect(page.locator('html')).toHaveAttribute(
      'data-retro',
      destination,
    );
    await expectTextContrast(
      page,
      '.battle-intro .eyebrow, .battle-round-heading .eyebrow',
    );
  });
}
