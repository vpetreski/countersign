import { EyeOff } from "lucide-react";
import type { Evidence, NotProposed } from "../lib/types";
import { EvidenceChips } from "./Citations";
import { Mono, td, th } from "./ui";

/** What the agent reviewed and left out, with the reason and the evidence: what an approver needs to see. */
export function NotProposedTable({
  items,
  evidence,
  title = "Reviewed, not proposed",
}: {
  items: NotProposed[];
  evidence?: Record<string, Evidence>;
  title?: string;
}) {
  if (items.length === 0) return null;
  return (
    <section aria-label={title} data-testid="not-proposed">
      <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-400">
        <EyeOff className="size-3.5" aria-hidden />
        {title}
        <span className="font-normal normal-case tracking-normal text-zinc-500">
          · {items.length} finding{items.length === 1 ? "" : "s"} left out on purpose
        </span>
      </h3>
      <div className="scroll-thin mt-2 overflow-x-auto">
        <table className="w-full min-w-[640px] border-separate border-spacing-0">
          <thead>
            <tr className="[&>th]:border-b [&>th]:border-zinc-800">
              <th className={th}>Resource</th>
              <th className={th}>Why it is not in the change request</th>
              <th className={th}>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {items.map((n) => (
              <tr
                key={n.ref}
                className="[&>td]:border-b [&>td]:border-zinc-800/60"
                data-testid="not-proposed-row"
              >
                <td className={td}>
                  <Mono className="text-zinc-100">{n.ref}</Mono>
                </td>
                <td className={`${td} text-zinc-300`}>{n.reason}</td>
                <td className={td}>
                  <EvidenceChips ids={n.evidence_ids} evidence={evidence} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
