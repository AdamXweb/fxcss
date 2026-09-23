"use client";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import Image from "next/image";
import Link from "next/link";
import { ArrowUpRight, Pause, Play, ZoomIn, ZoomOut } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Slider } from "@/components/ui/slider";
import { Button } from "@/components/ui/button";
import evidence from "../../content/evidence.json";
const percentage = evidence.comparison.percent.toFixed(2);
export function Comparison() {
  const [position, setPosition] = useState([50]);
  const [zoomed, setZoomed] = useState(false);
  const [view, setView] = useState("compare");
  const [autoplay, setAutoplay] = useState(true);
  const [hovered, setHovered] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(true);
  const [visible, setVisible] = useState(false);
  const [pageVisible, setPageVisible] = useState(false);
  const figure = useRef<HTMLElement>(null);
  const currentPosition = useRef(50);
  const animationPhase = useRef(0);
  const controlId = useId();
  const hintId = useId();
  const updatePosition = useCallback((value: number) => {
    const next = Math.round(Math.max(0, Math.min(100, value)) * 10) / 10;
    currentPosition.current = next;
    setPosition([next]);
  }, []);

  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updatePreference = () => setReducedMotion(preference.matches);
    const updateVisibility = () => setPageVisible(!document.hidden);
    updatePreference();
    updateVisibility();
    preference.addEventListener("change", updatePreference);
    document.addEventListener("visibilitychange", updateVisibility);
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), {
      threshold: 0.15,
    });
    if (figure.current) observer.observe(figure.current);
    return () => {
      observer.disconnect();
      preference.removeEventListener("change", updatePreference);
      document.removeEventListener("visibilitychange", updateVisibility);
    };
  }, []);

  useEffect(() => {
    if (!autoplay || hovered || reducedMotion || !visible || !pageVisible || view !== "compare") return;
    // Resume from the current split in the same direction after a hover pause.
    const currentPhase = Math.asin(Math.max(-1, Math.min(1, (currentPosition.current - 50) / 45)));
    const phase = Math.cos(animationPhase.current) < 0 ? Math.PI - currentPhase : currentPhase;
    let start: number | undefined;
    let frame: number;
    const animate = (time: number) => {
      start ??= time;
      animationPhase.current = phase + ((time - start) / 7000) * Math.PI * 2;
      updatePosition(50 + 45 * Math.sin(animationPhase.current));
      frame = requestAnimationFrame(animate);
    };
    frame = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frame);
  }, [autoplay, hovered, reducedMotion, visible, pageVisible, view, updatePosition]);

  function positionFromPointer(event: PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    if (bounds.width) updatePosition(((event.clientX - bounds.left) / bounds.width) * 100);
  }
  function startDrag(event: PointerEvent<HTMLDivElement>) {
    if (!event.isPrimary || event.button !== 0) return;
    setAutoplay(false);
    event.currentTarget.focus({ preventScroll: true });
    event.currentTarget.setPointerCapture(event.pointerId);
    positionFromPointer(event);
  }
  function endDrag(event: PointerEvent<HTMLDivElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
  }
  function handleKey(event: KeyboardEvent<HTMLDivElement>) {
    const step = event.shiftKey ? 10 : 1;
    const changes: Record<string, number> = {
      ArrowLeft: -step,
      ArrowDown: -step,
      ArrowRight: step,
      ArrowUp: step,
      PageDown: -10,
      PageUp: 10,
    };
    if (event.key !== "Home" && event.key !== "End" && !(event.key in changes)) return;
    event.preventDefault();
    setAutoplay(false);
    updatePosition(
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? 100
          : Math.round(currentPosition.current) + changes[event.key],
    );
  }
  return (
    <figure className="comparison-figure" ref={figure}>
      <Tabs
        className={`comparison ${zoomed ? "zoomed" : ""}`}
        value={view}
        onValueChange={(value) => {
          setView(value);
          setAutoplay(false);
        }}
      >
        <div className="comparison-top">
          <span>
            <span className="status-dot" /> Firefox {evidence.browser.version} · macOS
          </span>
          <TabsList aria-label="Comparison view">
            <TabsTrigger value="compare">Compare</TabsTrigger>
            <TabsTrigger value="diff">Changed pixels</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="compare">
          <div className="compare-labels">
            <span>Before · {evidence.change.before}</span>
            <span>After · {evidence.change.after}</span>
          </div>
          {/* The image surface captures dragging across its full area and supplies slider keyboard semantics. */}
          {/* eslint-disable jsx-a11y/prefer-tag-over-role */}
          <div
            className="compare-image"
            style={{ "--split": `${position[0]}%` } as CSSProperties}
            role="slider"
            tabIndex={0}
            aria-label="Image comparison"
            aria-describedby={hintId}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={position[0]}
            aria-valuetext={`${Math.round(position[0])}% before`}
            aria-orientation="horizontal"
            onFocus={() => setAutoplay(false)}
            onKeyDown={handleKey}
            onPointerEnter={() => setHovered(true)}
            onPointerLeave={() => setHovered(false)}
            onPointerDown={startDrag}
            onPointerMove={(event) => {
              if (event.currentTarget.hasPointerCapture(event.pointerId))
                positionFromPointer(event);
            }}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
          >
            <div className="capture-strip">
              <Image
                unoptimized
                src="/evidence/after.png"
                width={evidence.dimensions.width}
                height={evidence.dimensions.height}
                alt="Starter theme after the active tab accent changed to orange"
                draggable={false}
                priority
              />
            </div>
            <div className="before-layer">
              <div className="capture-strip">
                <Image
                  unoptimized
                  src="/evidence/before.png"
                  width={evidence.dimensions.width}
                  height={evidence.dimensions.height}
                  alt="Starter theme before the change, with a blue active tab accent"
                  draggable={false}
                  priority
                />
              </div>
            </div>
            <span className="compare-line" aria-hidden="true">
              <span>‹ ›</span>
            </span>
          </div>
          {/* eslint-enable jsx-a11y/prefer-tag-over-role */}
          <div className="compare-interaction">
            <span id={hintId}>Drag the image to compare</span>
            {!reducedMotion && (
              <Button
                variant="ghost"
                size="sm"
                className="motion-toggle"
                onClick={() => setAutoplay(!autoplay)}
                aria-label={autoplay ? "Pause comparison animation" : "Play comparison animation"}
              >
                {autoplay ? <Pause size={14} /> : <Play size={14} />}
                {autoplay ? "Pause" : "Play"}
              </Button>
            )}
          </div>
          <div
            className="compare-control"
            onFocusCapture={() => setAutoplay(false)}
            onPointerEnter={() => setHovered(true)}
            onPointerLeave={() => setHovered(false)}
            onPointerDownCapture={() => setAutoplay(false)}
          >
            <span id={controlId}>Reveal before</span>
            <Slider
              aria-labelledby={controlId}
              value={position}
              onValueChange={(value) => {
                setAutoplay(false);
                updatePosition(Array.isArray(value) ? value[0] : value);
              }}
              min={0}
              max={100}
            />
            <output aria-label="Before image percentage" aria-live="off">
              {Math.round(position[0])}%
            </output>
          </div>
        </TabsContent>
        <TabsContent value="diff">
          <div className="diff-view">
            <div className="capture-strip">
              <Image
                unoptimized
                src="/evidence/difference.png"
                width={960}
                height={Math.round((evidence.dimensions.height * 960) / evidence.dimensions.width)}
                alt="Changed pixels highlighted in pink on the starter theme’s active tab"
              />
            </div>
            <p>
              Pink highlights the changed pixels. The highlight is enlarged slightly for visibility.
            </p>
          </div>
        </TabsContent>
        <div className="compare-foot">
          <Button
            variant="ghost"
            size="sm"
            className="detail-toggle"
            onClick={() => {
              setAutoplay(false);
              setZoomed(!zoomed);
            }}
            aria-pressed={zoomed}
          >
            {zoomed ? <ZoomOut size={15} /> : <ZoomIn size={15} />}{" "}
            {zoomed ? "Full toolbar" : "Zoom into the tab"}
          </Button>
          <span>
            <strong>{percentage}%</strong> of the full capture changed
          </span>
        </div>
      </Tabs>
      <figcaption className="evidence-caption">
        <span>
          Actual starter-theme captures · fxcss {evidence.fxcssVersion} ·{" "}
          {evidence.generatedAt.slice(0, 10)}
        </span>
        <a href="/evidence/comparison.png" target="_blank" rel="noreferrer">
          Full comparison <ArrowUpRight size={14} />
        </a>
        <Link href="/docs/screenshot-evidence">
          Capture details <ArrowUpRight size={14} />
        </Link>
      </figcaption>
    </figure>
  );
}
export function AppearancePreview() {
  return (
    <figure className="appearance-preview" id="captured-browser">
      <Tabs defaultValue="light">
        <div className="appearance-heading">
          <span>Bundled starter · Firefox {evidence.browser.version}</span>
          <TabsList aria-label="Screenshot appearance">
            <TabsTrigger value="light">Light</TabsTrigger>
            <TabsTrigger value="dark">Dark</TabsTrigger>
          </TabsList>
        </div>
        {["light", "dark"].map((mode) => (
          <TabsContent value={mode} key={mode}>
            <a
              href={`/evidence/${mode === "dark" ? "dark" : "before"}.png`}
              target="_blank"
              rel="noreferrer"
            >
              <Image
                unoptimized
                src={`/evidence/${mode === "dark" ? "dark" : "before"}.png`}
                width={evidence.dimensions.width}
                height={evidence.dimensions.height}
                alt={`Unedited ${mode} mode capture of the fxcss starter theme`}
              />
            </a>
          </TabsContent>
        ))}
      </Tabs>
      <figcaption>
        Captured on macOS with fxcss {evidence.fxcssVersion}. Open an image to view it at full size.
      </figcaption>
    </figure>
  );
}
