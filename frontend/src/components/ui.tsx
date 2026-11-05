import { Loader2 } from "lucide-react";
import { type ButtonHTMLAttributes, type ReactNode, useId } from "react";

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

// ---------------------------------------------------------------- card

export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
  id,
  icon,
  ...rest
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
  icon?: ReactNode;
  "data-testid"?: string;
}) {
  const headingId = useId();
  return (
    <section
      id={id}
      aria-labelledby={title ? headingId : undefined}
      className={cx(
        "rounded-xl border border-zinc-800/80 bg-zinc-900/40 shadow-[0_1px_0_0_rgba(255,255,255,0.03)_inset] animate-fade-in",
        className,
      )}
      data-testid={rest["data-testid"]}
    >
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-zinc-800/70 px-5 py-3.5">
          <div className="min-w-0">
            {title && (
              <h2 id={headingId} className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
                {icon && <span className="text-zinc-400">{icon}</span>}
                {title}
              </h2>
            )}
            {subtitle && <p className="mt-0.5 text-xs text-zinc-400">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cx("px-5 py-4", bodyClassName)}>{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------- badge

export type Tone = "neutral" | "accent" | "warn" | "danger" | "info" | "violet" | "sky";

const TONES: Record<Tone, string> = {
  neutral: "border-zinc-700/80 bg-zinc-800/60 text-zinc-300",
  accent: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  warn: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  danger: "border-red-500/30 bg-red-500/10 text-red-300",
  info: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  violet: "border-violet-500/30 bg-violet-500/10 text-violet-300",
  sky: "border-cyan-500/30 bg-cyan-500/10 text-cyan-300",
};

export function Badge({
  tone = "neutral",
  children,
  icon,
  title,
  className,
  mono,
}: {
  tone?: Tone;
  children: ReactNode;
  icon?: ReactNode;
  title?: string;
  className?: string;
  mono?: boolean;
}) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-4",
        TONES[tone],
        mono && "font-mono",
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}

// ---------------------------------------------------------------- button

type Variant = "primary" | "secondary" | "ghost" | "danger" | "accent-soft";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-emerald-500 text-zinc-950 hover:bg-emerald-400 disabled:bg-zinc-800 disabled:text-zinc-500 shadow-[0_0_0_1px_rgba(16,185,129,0.4),0_8px_24px_-12px_rgba(16,185,129,0.6)] disabled:shadow-none",
  secondary:
    "border border-zinc-700 bg-zinc-900 text-zinc-100 hover:border-zinc-600 hover:bg-zinc-800 disabled:text-zinc-500",
  ghost: "text-zinc-300 hover:bg-zinc-800/80 hover:text-zinc-100 disabled:text-zinc-600",
  danger: "border border-red-500/40 bg-red-500/10 text-red-200 hover:bg-red-500/20 disabled:opacity-50",
  "accent-soft": "border border-emerald-500/30 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/20",
};

export function Button({
  variant = "secondary",
  size = "md",
  loading,
  icon,
  children,
  className,
  disabled,
  type = "button",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  icon?: ReactNode;
}) {
  const sizes = {
    sm: "h-7 px-2.5 text-xs gap-1.5",
    md: "h-9 px-3.5 text-sm gap-2",
    lg: "h-11 px-5 text-sm gap-2",
  };
  return (
    <button
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cx(
        "inline-flex select-none items-center justify-center rounded-lg font-medium transition-colors duration-150 disabled:cursor-not-allowed",
        sizes[size],
        VARIANTS[variant],
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

// ---------------------------------------------------------------- small pieces

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("skeleton h-4", className)} aria-hidden />;
}

export function Spinner({ className, label }: { className?: string; label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-zinc-400">
      <Loader2 className={cx("size-4 animate-spin", className)} aria-hidden />
      {label && <span className="text-xs">{label}</span>}
    </span>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  className,
  ...rest
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  className?: string;
  "data-testid"?: string;
}) {
  return (
    <div
      data-testid={rest["data-testid"]}
      className={cx(
        "flex flex-col items-center justify-center rounded-lg border border-dashed border-zinc-800 px-6 py-10 text-center",
        className,
      )}
    >
      {icon && <div className="mb-3 text-zinc-500">{icon}</div>}
      <p className="text-sm font-medium text-zinc-200">{title}</p>
      {children && <div className="mt-1 max-w-md text-xs text-zinc-400">{children}</div>}
    </div>
  );
}

export function Mono({
  children,
  className,
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span title={title} className={cx("font-mono text-[12px] text-zinc-300", className)}>
      {children}
    </span>
  );
}

export function Tooltip({
  content,
  children,
  side = "top",
  className,
  wide,
}: {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "bottom";
  className?: string;
  wide?: boolean;
}) {
  const id = useId();
  return (
    <span className={cx("group/tt relative inline-flex", className)} aria-describedby={id}>
      {children}
      <span
        role="tooltip"
        id={id}
        className={cx(
          "pointer-events-none invisible absolute left-1/2 z-50 -translate-x-1/2 rounded-lg border border-zinc-700 bg-zinc-900/95 px-3 py-2 text-left text-xs font-normal leading-relaxed text-zinc-200 opacity-0 shadow-xl shadow-black/40 backdrop-blur transition-opacity duration-150 group-focus-within/tt:visible group-focus-within/tt:opacity-100 group-hover/tt:visible group-hover/tt:opacity-100",
          wide ? "w-80 max-w-[calc(100vw-2rem)]" : "w-64 max-w-[calc(100vw-2rem)]",
          side === "top" ? "bottom-full mb-2" : "top-full mt-2",
        )}
      >
        {content}
      </span>
    </span>
  );
}

export function Meter({
  label,
  value,
  max,
  hint,
  format = (n: number) => n.toLocaleString("en-US"),
}: {
  label: string;
  value: number;
  max: number;
  hint?: ReactNode;
  format?: (n: number) => string;
}) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const tone = pct >= 90 ? "bg-red-400" : pct >= 70 ? "bg-amber-400" : "bg-emerald-400";
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="text-zinc-300">{label}</span>
        <span className="font-mono text-zinc-400">
          {format(value)} <span className="text-zinc-600">/ {format(max)}</span>
        </span>
      </div>
      {/* biome-ignore lint/a11y/useSemanticElements: a styled bar; <meter> cannot be themed consistently */}
      <div
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-zinc-800"
        role="meter"
        aria-label={label}
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={max}
      >
        <div
          className={cx("h-full rounded-full transition-[width] duration-500", tone)}
          style={{ width: `${pct}%` }}
        />
      </div>
      {hint && <p className="mt-1 text-[11px] text-zinc-500">{hint}</p>}
    </div>
  );
}

export function Kv({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</dt>
      <dd className="mt-0.5 truncate text-sm text-zinc-200">{children}</dd>
    </div>
  );
}

export const th =
  "whitespace-nowrap px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wide text-zinc-500 first:pl-0 last:pr-0";
export const td = "px-3 py-2.5 align-top text-sm text-zinc-200 first:pl-0 last:pr-0";

export function Callout({
  tone = "info",
  icon,
  title,
  children,
  actions,
  className,
  ...rest
}: {
  tone?: "info" | "warn" | "danger" | "accent";
  icon?: ReactNode;
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
  "data-testid"?: string;
  role?: string;
}) {
  const tones = {
    info: "border-sky-500/30 bg-sky-500/[0.07] text-sky-100",
    warn: "border-amber-500/35 bg-amber-500/[0.08] text-amber-100",
    danger: "border-red-500/35 bg-red-500/[0.08] text-red-100",
    accent: "border-emerald-500/35 bg-emerald-500/[0.08] text-emerald-100",
  };
  return (
    <div
      role={rest.role}
      data-testid={rest["data-testid"]}
      className={cx(
        "flex flex-wrap items-start gap-3 rounded-xl border px-4 py-3 animate-fade-in",
        tones[tone],
        className,
      )}
    >
      {icon && <div className="mt-0.5 shrink-0">{icon}</div>}
      <div className="min-w-0 flex-1 basis-64">
        <div className="text-sm font-semibold">{title}</div>
        {children && <div className="mt-1 text-sm opacity-90">{children}</div>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
