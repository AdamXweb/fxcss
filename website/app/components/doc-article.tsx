"use client";
import { useEffect, useRef, useState } from "react";
export function DocArticle({ html, codes }: { html: string; codes: string[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState("");
  useEffect(() => {
    const element = ref.current;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function copy(event: MouseEvent) {
      if (!(event.target instanceof Element)) return;
      const button = event.target.closest<HTMLButtonElement>("button[data-copy-index]");
      if (!button || !element?.contains(button)) return;
      const code = codes[Number(button.dataset.copyIndex)];
      if (code === undefined) return;
      try {
        await navigator.clipboard.writeText(code);
        setStatus("Code example copied.");
        button.textContent = "Copied";
      } catch {
        setStatus("Select the code and copy it manually.");
      }
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        setStatus("");
        element
          ?.querySelectorAll("button[data-copy-index]")
          .forEach((b) => (b.textContent = "Copy"));
      }, 3000);
    }
    element?.addEventListener("click", copy);
    return () => {
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
