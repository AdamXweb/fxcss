export function watchSleepyFavicon(page: Document, Observer = MutationObserver) {
  const originals = new Map<HTMLLinkElement, { href: string | null; type: string | null }>();
  function restore() {
    for (const [icon, original] of originals) {
      for (const [attribute, value] of Object.entries(original)) {
        if (value === null) icon.removeAttribute(attribute);
        else icon.setAttribute(attribute, value);
      }
    }
    originals.clear();
  }
  function update() {
    if (page.hidden) {
      for (const icon of page.querySelectorAll<HTMLLinkElement>('link[rel="icon"]')) {
        if (originals.has(icon)) continue;
        originals.set(icon, { href: icon.getAttribute('href'), type: icon.getAttribute('type') });
        icon.href = '/assets/fxcss-sleepy.svg';
        icon.type = 'image/svg+xml';
      }
    } else restore();
  }
  page.addEventListener('visibilitychange', update);
  // Metadata can be replaced on client-side navigation while a tab is hidden.
  const observer = new Observer(update);
  observer.observe(page.head, { childList: true, subtree: true });
  update();
  return () => {
    page.removeEventListener('visibilitychange', update);
    observer.disconnect();
    restore();
  };
}
