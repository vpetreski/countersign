import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { cx } from "./ui";

/** A click-to-open panel anchored to its trigger; closes on Escape and on a click outside. */
export function Popover({
  trigger,
  children,
  align = "left",
  label,
  className,
  panelClassName,
  role = "dialog",
}: {
  trigger: (props: { open: boolean; toggle: () => void; id: string }) => ReactNode;
  children: ReactNode | ((close: () => void) => ReactNode);
  align?: "left" | "right";
  label: string;
  className?: string;
  panelClassName?: string;
  role?: "dialog" | "menu";
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        (ref.current?.querySelector("[aria-haspopup]") as HTMLElement | null)?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const close = () => setOpen(false);
  return (
    <div ref={ref} className={cx("relative", className)}>
      {trigger({ open, toggle: () => setOpen((o) => !o), id })}
      {open && (
        // biome-ignore lint/a11y/useAriaPropsSupportedByRole: the role is dialog or menu, and both take a label
        <div
          id={id}
          role={role}
          aria-label={label}
          className={cx(
            "absolute top-full z-50 mt-2 rounded-xl border border-zinc-700/80 bg-zinc-900/98 p-1.5 shadow-2xl shadow-black/60 backdrop-blur animate-fade-in",
            align === "right" ? "right-0" : "left-0",
            panelClassName,
          )}
        >
          {typeof children === "function" ? children(close) : children}
        </div>
      )}
    </div>
  );
}
