"use client";
import { useEffect, useRef, useState } from "react";
export function DocArticle({ html, codes }: { html: string; codes: string[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState("");
  useEffect(() => {
    const element = ref.current;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // A button's name keeps its visible word: "Copy code example 1" becomes
    // "Copied code example 1" while it shows "Copied".
    const label = (button: Element, word: string) => {
      button.textContent = word;
      const name = button.getAttribute("aria-label");
      if (name) button.setAttribute("aria-label", name.replace(/^\w+/, word));
    };
    async function copy(event: MouseEvent) {
      if (!(event.target instanceof Element)) return;
      const button = event.target.closest<HTMLButtonElement>("button[data-copy-index]");
      if (!button || !element?.contains(button)) return;
      const code = codes[Number(button.dataset.copyIndex)];
      if (code === undefined) return;
      try {
        await navigator.clipboard.writeText(code);
        setStatus("Code example copied.");
        label(button, "Copied");
      } catch {
        setStatus("Select the code and copy it manually.");
      }
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        setStatus("");
        element
          ?.querySelectorAll("button[data-copy-index]")
          .forEach((b) => label(b, "Copy"));
      }, 3000);
    }
    // Code and tables that scroll sideways take keyboard focus, so they can be
    // scrolled without a mouse; ones that fit stay out of the tab order.
    const scrollers = element ? [...element.querySelectorAll("pre, table")] : [];
    const updateScrollers = () => {
      for (const scroller of scrollers) {
        if (scroller.scrollWidth > scroller.clientWidth) scroller.setAttribute("tabindex", "0");
        else scroller.removeAttribute("tabindex");
      }
    };
    const resize = new ResizeObserver(updateScrollers);
    for (const scroller of scrollers) resize.observe(scroller);
    element?.addEventListener("click", copy);
    return () => {
      resize.disconnect();
      element?.removeEventListener("click", copy);
      if (timer) clearTimeout(timer);
    };
  }, [codes]);
  return (
    <>
      <div className="doc-body" ref={ref} dangerouslySetInnerHTML={{ __html: html }} />
      <output className="doc-copy-status">{status}</output>
    </>
  );
}
