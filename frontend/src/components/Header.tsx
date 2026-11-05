import { Check, ChevronDown, Cpu, Eye, ShieldCheck, Sparkles, UserCog } from "lucide-react";
import type { ReactNode } from "react";
import { useConfig } from "../hooks";
import { ROLE_ABILITIES, roleLabel } from "../lib/personas";
import type { Persona, Role } from "../lib/types";
import { hrefWith, useUrlParam } from "../lib/url";
import { useSession } from "../session";
import { Popover } from "./Popover";
import { cx, Skeleton } from "./ui";

export const VIEWS = [
  { key: "agent", label: "Agent" },
  { key: "estate", label: "Estate" },
  { key: "data", label: "Data" },
  { key: "evals", label: "Evals" },
  { key: "audit", label: "Audit" },
] as const;

export type ViewKey = (typeof VIEWS)[number]["key"];

export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <path
        d="M16 4.5 26 8.5v7c0 6.2-4.2 10.6-10 12-5.8-1.4-10-5.8-10-12v-7z"
        fill="rgba(16,185,129,0.12)"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path
        d="m11 16.2 3.4 3.4L21.5 12"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const ROLE_ICON: Record<Role, ReactNode> = {
  viewer: <Eye className="size-3.5" aria-hidden />,
  operator: <UserCog className="size-3.5" aria-hidden />,
  approver: <ShieldCheck className="size-3.5" aria-hidden />,
};

const ROLE_COLOR: Record<Role, string> = {
  viewer: "bg-zinc-700 text-zinc-100",
  operator: "bg-sky-500/20 text-sky-200 ring-1 ring-sky-400/40",
  approver: "bg-emerald-500/20 text-emerald-200 ring-1 ring-emerald-400/40",
};

export function Avatar({ persona, size = "md" }: { persona: Persona; size?: "sm" | "md" }) {
  return (
    <span
      aria-hidden
      className={cx(
        "inline-flex shrink-0 items-center justify-center rounded-full font-semibold",
        size === "sm" ? "size-6 text-[11px]" : "size-8 text-xs",
        ROLE_COLOR[persona.role],
      )}
    >
      {persona.name.charAt(0)}
    </span>
  );
}

function ModelBadge() {
  const config = useConfig();
  if (config.isPending) return <Skeleton className="h-7 w-56" />;
  const model = config.data?.model;
  const label = !model
    ? "Model unavailable"
    : model.keyless
      ? "Keyless demo · offline reference policy"
      : `Claude · ${model.model}`;
  return (
    <Popover
      label="About the model"
      align="right"
      panelClassName="w-[min(24rem,calc(100vw-2rem))] p-4"
      trigger={({ open, toggle, id }) => (
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-controls={id}
          aria-haspopup="dialog"
          className="inline-flex h-8 items-center gap-2 rounded-full border border-zinc-700/80 bg-zinc-900 px-3 text-xs text-zinc-300 transition hover:border-zinc-600 hover:text-zinc-100"
          data-testid="model-badge"
        >
          {model?.keyless ? (
            <Cpu className="size-3.5 text-emerald-400" aria-hidden />
          ) : (
            <Sparkles className="size-3.5 text-emerald-400" aria-hidden />
          )}
          <span className="hidden sm:inline">{label}</span>
          <span className="sm:hidden">{model?.keyless ? "Keyless" : "Claude"}</span>
        </button>
      )}
    >
      <div className="space-y-2 text-sm">
        <p className="font-semibold text-zinc-100">{label}</p>
        <p className="font-mono text-xs text-zinc-400">
          {model?.provider} · {model?.model}
        </p>
        <p className="leading-relaxed text-zinc-300">
          {model?.reason ??
            "This deployment calls Claude through the Anthropic API with the same runtime and policy."}
        </p>
        {config.data && (
          <p className="text-xs text-zinc-500">
            v{config.data.version} · build{" "}
            <span className="font-mono">{config.data.build.sha.slice(0, 7)}</span> · policy v
            {config.data.policy.version}
          </p>
        )}
      </div>
    </Popover>
  );
}

export function PersonaSwitcher() {
  const { persona, personas, setPersona } = useSession();
  return (
    <Popover
      label="Switch persona"
      align="right"
      role="menu"
      panelClassName="w-[min(22rem,calc(100vw-2rem))]"
      trigger={({ open, toggle, id }) => (
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-controls={id}
          aria-haspopup="menu"
          aria-label={`Persona: ${persona.name}, ${persona.role}. Switch persona`}
          data-testid="persona-switcher"
          className="inline-flex h-9 items-center gap-2 rounded-full border border-zinc-700/80 bg-zinc-900 py-1 pr-2.5 pl-1 text-sm transition hover:border-zinc-600"
        >
          <Avatar persona={persona} size="sm" />
          <span className="font-medium text-zinc-100">{persona.name}</span>
          <span className="hidden text-xs text-zinc-400 sm:inline">{roleLabel(persona.role)}</span>
          <ChevronDown className="size-3.5 text-zinc-500" aria-hidden />
        </button>
      )}
    >
      {(close) => (
        <div>
          <p className="px-2.5 pt-1.5 pb-2 text-[11px] uppercase tracking-wide text-zinc-500">
            Act as one of the demo personas
          </p>
          {personas.map((p) => {
            const active = p.key === persona.key;
            return (
              <button
                key={p.key}
                type="button"
                role="menuitemradio"
                aria-checked={active}
                onClick={() => {
                  setPersona(p.key);
                  close();
                }}
                className={cx(
                  "flex w-full items-start gap-3 rounded-lg px-2.5 py-2 text-left transition",
                  active ? "bg-zinc-800" : "hover:bg-zinc-800/60",
                )}
              >
                <Avatar persona={p} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="text-sm font-medium text-zinc-100">{p.name}</span>
                    <span className="inline-flex items-center gap-1 text-xs text-zinc-400">
                      {ROLE_ICON[p.role]}
                      {roleLabel(p.role)}
                    </span>
                  </span>
                  <span className="mt-0.5 block text-xs text-zinc-400">{p.description}</span>
                  <span className="mt-1 block text-[11px] text-zinc-500">
                    Can: {ROLE_ABILITIES[p.role].join(" · ")}
                  </span>
                </span>
                {active && <Check className="mt-1 size-4 text-emerald-400" aria-label="Active" />}
              </button>
            );
          })}
        </div>
      )}
    </Popover>
  );
}

export function Header() {
  const [view, setView] = useUrlParam("view");
  const current = (view ?? "agent") as ViewKey;
  return (
    <header className="sticky top-0 z-40 border-b border-zinc-800/80 bg-zinc-950/80 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1440px] items-center gap-4 px-4 py-3 sm:px-6 lg:px-8">
        <a
          href="?view=agent"
          className="flex min-w-0 items-center gap-3"
          onClick={(e) => {
            e.preventDefault();
            setView("agent");
          }}
        >
          <Logo className="size-8 shrink-0 text-emerald-400" />
          <span className="min-w-0">
            <span className="block text-[15px] font-semibold tracking-tight text-zinc-50">Countersign</span>
            <span className="hidden truncate text-xs text-zinc-400 md:block">
              Finds cloud waste, drafts a governed change request, and changes nothing until a human
              countersigns.
            </span>
          </span>
        </a>
        <div className="ml-auto flex items-center gap-2">
          <ModelBadge />
          <PersonaSwitcher />
        </div>
      </div>
      <nav aria-label="Views" className="mx-auto max-w-[1440px] px-4 sm:px-6 lg:px-8">
        <ul className="-mb-px flex gap-1 overflow-x-auto scroll-thin">
          {VIEWS.map((v) => {
            const active = v.key === current;
            return (
              <li key={v.key}>
                <a
                  href={hrefWith({ view: v.key })}
                  aria-current={active ? "page" : undefined}
                  onClick={(e) => {
                    if (e.metaKey || e.ctrlKey) return;
                    e.preventDefault();
                    setView(v.key);
                  }}
                  className={cx(
                    "inline-flex h-10 items-center border-b-2 px-3 text-sm transition-colors",
                    active
                      ? "border-emerald-400 font-medium text-zinc-50"
                      : "border-transparent text-zinc-400 hover:text-zinc-200",
                  )}
                >
                  {v.label}
                </a>
              </li>
            );
          })}
        </ul>
      </nav>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="mt-16 border-t border-zinc-800/80">
      <div className="mx-auto flex max-w-[1440px] flex-col gap-2 px-4 py-6 text-xs text-zinc-500 sm:px-6 lg:px-8 md:flex-row md:items-center md:justify-between">
        <p>
          <a
            href="https://github.com/vpetreski/countersign"
            className="font-medium text-zinc-300 hover:text-emerald-300"
            rel="noreferrer"
            target="_blank"
          >
            Countersign · open source · MIT
          </a>
        </p>
        <p className="max-w-3xl md:text-right">
          <span className="font-medium text-zinc-400">Why keyless?</span> A public page that spends model
          tokens invites cost abuse, so this demo runs the offline reference policy through the same runtime;
          with ANTHROPIC_API_KEY set the same code uses Claude.
        </p>
      </div>
    </footer>
  );
}
