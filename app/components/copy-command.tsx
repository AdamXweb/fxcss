"use client";
import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
export function CopyCommand({ command, compact = false }: { command: string; compact?: boolean }) {
  const [status, setStatus] = useState("");
  const timeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timeout.current) clearTimeout(timeout.current);
    },
    [],
  );
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setStatus("Copied");
    } catch {
      setStatus("Select the command and copy it manually.");
    }
    if (timeout.current) clearTimeout(timeout.current);
    timeout.current = setTimeout(() => setStatus(""), 3000);
  }
  return (
    <div className={`command ${compact ? "compact" : ""}`}>
      <span className="prompt" aria-hidden="true">
        $
      </span>
      <code>{command}</code>
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={copy}
        aria-label={`Copy ${command}`}
        title="Copy command"
      >
        {status === "Copied" ? <Check size={17} /> : <Copy size={17} />}
      </Button>
      <output className="copy-status">{status}</output>
    </div>
  );
}
