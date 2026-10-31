// Drag the fxcss mark across the site and it outlines the element underneath,
// with its classes labelled below it: `fxcss pick` for the website itself.

/** Movement that turns a press into a drag; anything shorter stays a click. */
export const PICK_DISTANCE = 4;
/** Longer class lists are shortened to this many classes and a count. */
const SHOWN_CLASSES = 3;

type Box = { left: number; top: number; bottom: number };
type Size = { width: number; height: number };
type Picker = { follow(x: number, y: number): void; land(fly: boolean): void };

export function classesOf(element: Element) {
  return [
    ...new Set(element.getAttribute('class')?.split(/\s+/).filter(Boolean)),
  ];
}

/** The element itself or its nearest ancestor with a class, inside <body>. */
export function classedElement(element: Element | null) {
  for (
    let current = element;
    current && current.localName !== 'body' && current.localName !== 'html';
    current = current.parentElement
  )
    if (classesOf(current).length) return current;
  return null;
}

/**
 * Class names in style rules outside cascade layers. Those are the site's own
 * stylesheets; Tailwind keeps its utilities in a layer.
 */
export function authoredClasses(
  sheets: Iterable<{ readonly cssRules: CSSRuleList }>,
  LayerBlock: typeof CSSLayerBlockRule = CSSLayerBlockRule,
) {
  const names = new Set<string>();
  function read(rules: Iterable<CSSRule>) {
    for (const rule of rules) {
      if (rule instanceof LayerBlock) continue;
      if ('selectorText' in rule)
        for (const [, name] of String(rule.selectorText).matchAll(
          /\.(-?[_a-zA-Z][\w-]*)/g,
        ))
          names.add(name);
      if ('cssRules' in rule) read(rule.cssRules as CSSRuleList);
    }
  }
  for (const sheet of sheets) {
    try {
      read(sheet.cssRules);
    } catch {
      // A stylesheet from another origin keeps its rules private.
    }
  }
  return names;
}

/**
 * A compound class selector. When the site's own CSS names some of the
 * classes, only those are shown, so a Tailwind component's utilities do not
 * bury the one class that identifies it.
 */
export function classLabel(
  classes: string[],
  authored: ReadonlySet<string>,
  escape = (name: string) => CSS.escape(name),
) {
  const own = classes.filter((name) => authored.has(name));
  const shown = own.length ? own : classes;
  const selector = shown
    .slice(0, SHOWN_CLASSES)
    .map((name) => `.${escape(name)}`)
    .join('');
  return shown.length > SHOWN_CLASSES
    ? `${selector} +${shown.length - SHOWN_CLASSES}`
    : selector;
}

/**
 * Underneath the element when there is room, above it when not, otherwise
 * pinned to the bottom of the viewport. Never off the side of the screen.
 */
export function labelPosition(
  box: Box,
  label: Size,
  viewport: Size,
  gap = 2,
  margin = 4,
) {
  const below = box.bottom + gap;
  const above = box.top - gap - label.height;
  const top =
    below + label.height <= viewport.height - margin
      ? below
      : above >= margin
        ? above
        : viewport.height - margin - label.height;
  return {
    left: Math.max(
      margin,
      Math.min(box.left, viewport.width - margin - label.width),
    ),
    top: Math.max(margin, top),
  };
}

/**
 * Follows a press on the logo. Past PICK_DISTANCE the mark lifts off and
 * labels the classed element under the pointer; releasing flies it home, and
 * Escape puts it back early. The click that ends a drag is swallowed, so
 * neither the logo nor whatever it was dropped on activates. Returns a
 * function that ends everything at once.
 */
export function followLogoPress(link: HTMLElement, press: PointerEvent) {
  const home = link.querySelector<SVGSVGElement>('.brand-mark');
  const view = link.ownerDocument.defaultView;
  if (!home || !view) return () => {};
  const listening = new AbortController();
  const { signal } = listening;
  let picker: Picker | undefined;
  let held = true;

  function end(fly: boolean) {
    listening.abort();
    if (link.hasPointerCapture(press.pointerId))
      link.releasePointerCapture(press.pointerId);
    picker?.land(fly);
  }

  link.setPointerCapture(press.pointerId);
  link.addEventListener(
    'pointermove',
    (event) => {
      if (event.pointerId !== press.pointerId || !held) return;
      const moved = Math.hypot(
        event.clientX - press.clientX,
        event.clientY - press.clientY,
      );
      if (!picker && moved < PICK_DISTANCE) return;
      picker ??= liftMark(link, home);
      picker.follow(event.clientX, event.clientY);
    },
    { signal },
  );
  link.addEventListener(
    'pointerup',
    (event) => {
      if (event.pointerId !== press.pointerId) return;
      end(true);
      if (picker) swallowClick(view);
    },
    { signal },
  );
  for (const type of ['pointercancel', 'lostpointercapture'] as const)
    link.addEventListener(
      type,
      (event) => {
        if (event.pointerId === press.pointerId) end(true);
      },
      { signal },
    );
  view.addEventListener(
    'keydown',
    (event) => {
      if (event.key !== 'Escape' || !picker || !held) return;
      // Only put the logo back; search and the time machine keep their Escape.
      event.preventDefault();
      event.stopPropagation();
      held = false;
      picker.land(true);
    },
    { signal, capture: true },
  );
  return () => end(false);
}

/** The click that ends a drag arrives in the same task as its pointerup. */
function swallowClick(view: Window) {
  function swallow(event: MouseEvent) {
    event.preventDefault();
    event.stopPropagation();
  }
  view.addEventListener('click', swallow, { capture: true, once: true });
  view.setTimeout(() => view.removeEventListener('click', swallow, true));
}

function liftMark(link: HTMLElement, home: SVGSVGElement): Picker {
  const doc = link.ownerDocument;
  const view = doc.defaultView!;
  const root = doc.documentElement;
  const size = home.getBoundingClientRect();
  // appendChild, because the Workers types replace Element.append.
  const [outline, ghost, label] = ['outline', 'ghost', 'label'].map((part) => {
    const element = doc.body.appendChild(doc.createElement('div'));
    element.className = `logo-picker-${part}`;
    element.setAttribute('aria-hidden', 'true');
    return element;
  });
  const mark = ghost.appendChild(home.cloneNode(true) as SVGSVGElement);
  mark.style.width = `${size.width}px`;
  mark.style.height = `${size.height}px`;
  outline.hidden = label.hidden = true;
  link.toggleAttribute('data-picking', true);
  root.toggleAttribute('data-logo-picking', true);
  const authored = authoredClasses(doc.styleSheets);
  let pointer = { x: 0, y: 0 };
  let target: Element | null = null;
  let labelSize = { width: 0, height: 0 };
  let frame = 0;
  let flight: Animation | undefined;
  let landed = false;

  // Every frame, so scrolling and layout changes under a still pointer count.
  function draw() {
    ghost.style.translate = `${pointer.x - size.width / 2}px ${pointer.y - size.height / 2}px`;
    const found = classedElement(doc.elementFromPoint(pointer.x, pointer.y));
    if (found !== target) {
      target = found;
      outline.hidden = label.hidden = !target;
      if (target) {
        label.textContent = classLabel(classesOf(target), authored);
        labelSize = { width: label.offsetWidth, height: label.offsetHeight };
      }
    }
    if (target) {
      const box = target.getBoundingClientRect();
      outline.style.translate = `${box.left}px ${box.top}px`;
      outline.style.width = `${box.width}px`;
      outline.style.height = `${box.height}px`;
      const { left, top } = labelPosition(box, labelSize, {
        width: root.clientWidth,
        height: root.clientHeight,
      });
      label.style.translate = `${left}px ${top}px`;
    }
    frame = view.requestAnimationFrame(draw);
  }

  function settle() {
    if (landed) return;
    landed = true;
    flight?.cancel();
    ghost.remove();
    link.removeAttribute('data-picking');
  }

  return {
    follow(x, y) {
      pointer = { x, y };
      if (!frame) draw();
    },
    land(fly) {
      view.cancelAnimationFrame(frame);
      outline.remove();
      label.remove();
      root.removeAttribute('data-logo-picking');
      if (!fly || view.matchMedia('(prefers-reduced-motion: reduce)').matches)
        return settle();
      if (flight || landed) return;
      const to = home.getBoundingClientRect();
      flight = ghost.animate(
        [
          {
            translate: ghost.style.translate,
            scale: view.getComputedStyle(ghost).scale,
          },
          { translate: `${to.left}px ${to.top}px`, scale: '1' },
        ],
        {
          duration: 260,
          easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
          fill: 'forwards',
        },
      );
      flight.finished.then(settle, settle);
    },
  };
}
