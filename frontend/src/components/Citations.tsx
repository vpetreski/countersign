import { FileSearch } from "lucide-react";
import { Fragment } from "react";
import { notProposedItems, parseCitations, splitSummary } from "../lib/citations";
import type { Evidence } from "../lib/types";
import { cx, Tooltip } from "./ui";

export function EvidenceChip({ id, evidence }: { id: string; evidence?: Record<string, Evidence> }) {
  const ev = evidence?.[id];
  return (
    <Tooltip
      wide
      content={
        ev ? (
          <span className="block space-y-1">
            <span className="flex items-center justify-between gap-2">
              <span className="font-mono text-emerald-300">{ev.id}</span>
              <span className="font-mono text-[11px] text-zinc-400">{ev.tool}</span>
            </span>
            <span className="block text-zinc-100">{ev.summary}</span>
            <span className="block text-[11px] text-zinc-400">
              {ev.kind}
              {ev.ref ? (
                <>
                  {" · "}
                  <span className="font-mono">{ev.ref}</span>
                </>
              ) : (
                " · estate-wide"
              )}
            </span>
          </span>
        ) : (
          <span>No evidence is recorded under {id} in this run.</span>
        )
      }
    >
      <button
        type="button"
        data-evidence={id}
        aria-label={ev ? `Evidence ${id}: ${ev.summary}` : `Evidence ${id} (not found)`}
        className={cx(
          "mx-0.5 inline-flex items-center gap-1 rounded-md border px-1.5 py-px align-baseline font-mono text-[11px] leading-4 transition",
          ev
            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300 hover:border-emerald-400/60 hover:bg-emerald-500/20"
            : "border-zinc-700 bg-zinc-800 text-zinc-400 line-through",
        )}
      >
        <FileSearch className="size-3" aria-hidden />
        {id}
      </button>
    </Tooltip>
  );
}

export function EvidenceChips({ ids, evidence }: { ids: string[]; evidence?: Record<string, Evidence> }) {
  if (ids.length === 0) return <span className="text-xs text-zinc-500">none</span>;
  return (
    <span className="inline-flex flex-wrap gap-y-1">
      {ids.map((id) => (
        <EvidenceChip key={id} id={id} evidence={evidence} />
      ))}
    </span>
  );
}

/** Text with its [ev-NN] citations rendered as interactive chips. The text itself is never altered. */
export function CitedText({ text, evidence }: { text: string; evidence?: Record<string, Evidence> }) {
  return (
    <>
      {parseCitations(text).map((s, i) =>
        s.type === "text" ? (
          // biome-ignore lint/suspicious/noArrayIndexKey: segments are positional and never reorder
          <Fragment key={i}>{s.text}</Fragment>
        ) : (
          // biome-ignore lint/suspicious/noArrayIndexKey: segments are positional and never reorder
          <span key={i} className="whitespace-nowrap">
            {s.ids.map((id) => (
              <EvidenceChip key={id} id={id} evidence={evidence} />
            ))}
          </span>
        ),
      )}
    </>
  );
}

/** A summary or report: the first sentence as a lead, each action as a list item, exclusions grouped. */
export function CitedSummary({
  text,
  evidence,
  className,
  collapseItems,
  hideExcluded,
}: {
  text: string;
  evidence?: Record<string, Evidence>;
  className?: string;
  /** Fold the per-action lines into a disclosure (the change request table already lists each action). */
  collapseItems?: boolean;
  /** Drop the "Not proposed:" lines because a structured table shows them. */
  hideExcluded?: boolean;
}) {
  const lines = splitSummary(text);
  const [lead, ...all] = lines;
  const excludedLines = all.filter((l) => notProposedItems(l));
  const itemLines = all.filter((l) => !notProposedItems(l));
  const shownExcluded = hideExcluded ? [] : excludedLines;
  const rest = collapseItems ? shownExcluded : hideExcluded ? itemLines : all;
  return (
    <div className={cx("space-y-2 text-sm leading-relaxed text-zinc-300", className)}>
      {lead && (
        <p className="text-zinc-200">
          <CitedText text={lead} evidence={evidence} />
        </p>
      )}
      {collapseItems && itemLines.length > 0 && (
        <details className="group rounded-lg border border-zinc-800 bg-zinc-950/30 px-3 py-2">
          <summary className="flex items-center gap-1.5 text-xs text-zinc-400 hover:text-zinc-200">
            <span className="transition-transform group-open:rotate-90" aria-hidden>
              ›
            </span>
            The agent's note on each action ({itemLines.length}), with citations
          </summary>
          <ul className="mt-2 space-y-1.5">
            {itemLines.map((line) => (
              <li key={line} className="flex gap-2">
                <span aria-hidden className="mt-2 size-1.5 shrink-0 rounded-full bg-emerald-500/70" />
                <span>
                  <CitedText text={line} evidence={evidence} />
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {rest.length > 0 && (
        <ul className="space-y-1.5">
          {rest.map((line) => {
            const excluded = notProposedItems(line);
            if (excluded) {
              return (
                <li key={line} className="pt-1">
                  <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">Not proposed</p>
                  <ul className="mt-1 space-y-1">
                    {excluded.map((item) => (
                      <li key={item} className="flex gap-2 text-zinc-400">
                        <span aria-hidden className="mt-2 size-1 shrink-0 rounded-full bg-zinc-600" />
                        <span>
                          <CitedText text={item} evidence={evidence} />
                        </span>
                      </li>
                    ))}
                  </ul>
                </li>
              );
            }
            return (
              <li key={line} className="flex gap-2">
                <span aria-hidden className="mt-2 size-1.5 shrink-0 rounded-full bg-emerald-500/70" />
                <span>
                  <CitedText text={line} evidence={evidence} />
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
