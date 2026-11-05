import { ShieldCheck } from "lucide-react";
import { EmptyState } from "../../components/ui";
import { useUrlParam } from "../../lib/url";
import { Composer } from "./Composer";
import { Kpis } from "./Kpis";
import { RunList } from "./RunList";
import { RunBudgets, RunPanel } from "./RunPanel";

export default function AgentView() {
  const [runId] = useUrlParam("run");
  return (
    <div className="space-y-6">
      <Kpis />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-6">
          <Composer />
          {runId ? (
            <RunPanel key={runId} runId={runId} />
          ) : (
            <EmptyState
              icon={<ShieldCheck className="size-7" />}
              title="No run selected"
              className="bg-zinc-900/20 py-14"
            >
              Pick a preset and start a run as Sam, the operator. The agent plans with read-only tools, the
              policy engine checks every proposed action, and nothing changes until approvers countersign.
            </EmptyState>
          )}
        </div>
        <aside className="space-y-6 xl:sticky xl:top-32 xl:self-start" aria-label="Runs and budgets">
          <RunList />
          {runId && <RunBudgets runId={runId} />}
        </aside>
      </div>
    </div>
  );
}
