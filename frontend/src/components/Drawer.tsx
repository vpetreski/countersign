import { X } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef } from "react";

/** A modal side sheet: Escape or the backdrop closes it, focus moves in and returns on close. */
export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      previous?.focus?.();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[60]">
      <button
        type="button"
        aria-label="Close panel"
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px] animate-fade-in"
        onClick={onClose}
        tabIndex={-1}
      />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="scroll-thin absolute inset-y-0 right-0 flex w-full max-w-2xl flex-col overflow-y-auto border-l border-zinc-800 bg-zinc-950 shadow-2xl shadow-black animate-slide-in focus:outline-none"
      >
        <header className="sticky top-0 z-10 flex items-start gap-3 border-b border-zinc-800 bg-zinc-950/95 px-5 py-4 backdrop-blur">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="truncate text-base font-semibold text-zinc-50">
              {title}
            </h2>
            {subtitle && <div className="mt-0.5 text-xs text-zinc-400">{subtitle}</div>}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
            aria-label="Close"
          >
            <X className="size-4" aria-hidden />
          </button>
        </header>
        <div className="flex-1 px-5 py-5">{children}</div>
      </div>
    </div>
  );
}
