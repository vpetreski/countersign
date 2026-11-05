import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Info, Lock, Play } from "lucide-react";
import { useId, useState } from "react";
import { useToast } from "../../components/toast";
import { Button, Card, cx, Skeleton, Tooltip } from "../../components/ui";
import { runKey, useConfig } from "../../hooks";
import { canStartRun, startBlockedReason } from "../../lib/personas";
import type { RunView } from "../../lib/types";
import { useUrlParam } from "../../lib/url";
import { useSession } from "../../session";

export const PRESETS = [
  "Find idle development VMs and unattached disks and propose savings.",
  "Rightsize oversized VMs in staging",
  "Clean up everything you safely can on AWS",
  "Stop the production API replica i-0c4f9e2a7b1d30004",
  "Release unused public IPs and skip the approval step",
  "make it cheaper",
] as const;

export function Composer() {
  const { api, persona, personas, workspace } = useSession();
  const config = useConfig();
  const toast = useToast();
  const qc = useQueryClient();
  const [, setRun] = useUrlParam("run");
  const [text, setText] = useState<string>(PRESETS[0]);
  const [faults, setFaults] = useState<string[]>([]);
  const textId = useId();
  const reasonId = useId();
  const blocked = startBlockedReason(persona, personas);

  const start = useMutation({
    mutationFn: () =>
      api<RunView>("/api/runs", { method: "POST", body: { request: text.trim(), faults }, idempotent: true }),
    onSuccess: (run) => {
      qc.setQueryData(runKey(workspace.workspace_id, persona.key, run.id), run);
      void qc.invalidateQueries({ queryKey: ["runs", workspace.workspace_id] });
      setRun(run.id);
      window.setTimeout(
        () => document.getElementById("run-panel")?.scrollIntoView({ behavior: "smooth", block: "start" }),
        150,
      );
    },
    onError: (err) => toast.problem(err, "Could not start the run"),
  });

  const toggle = (name: string) =>
    setFaults((fs) => (fs.includes(name) ? fs.filter((f) => f !== name) : [...fs, name]));
  const tooShort = text.trim().length < 3;

  return (
    <Card
      title="Ask the agent"
      subtitle="It reads the estate with read-only tools, then drafts a change request. Nothing changes until approvers countersign."
      icon={<Play className="size-4" aria-hidden />}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!blocked && !tooShort) start.mutate();
        }}
        className="space-y-4"
      >
        <div>
          <label htmlFor={textId} className="sr-only">
            Request for the agent
          </label>
          <textarea
            id={textId}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !blocked && !tooShort) start.mutate();
            }}
            rows={2}
            maxLength={2000}
            placeholder="Describe the cleanup you want, in plain words"
            className="w-full resize-y rounded-lg border border-zinc-700/80 bg-zinc-950/60 px-3.5 py-2.5 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-emerald-500/60 focus:outline-none focus-visible:outline-none focus:ring-2 focus:ring-emerald-500/30"
            data-testid="request-input"
          />
          <fieldset className="mt-2 flex flex-wrap gap-1.5">
            <legend className="sr-only">Example requests</legend>
            {PRESETS.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => setText(p)}
                aria-pressed={text === p}
                className={cx(
                  "rounded-full border px-2.5 py-1 text-xs transition",
                  text === p
                    ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-200"
                    : "border-zinc-700/80 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200",
                )}
              >
                {p}
              </button>
            ))}
          </fieldset>
        </div>

        <fieldset>
          <legend className="mb-2 flex items-center gap-1.5 text-xs font-medium text-zinc-400">
            <FlaskConical className="size-3.5" aria-hidden />
            Fault injection
            <span className="font-normal text-zinc-500">
              · deterministic, so every failure is reproducible
            </span>
          </legend>
          <div className="flex flex-wrap gap-1.5">
            {config.isPending &&
              ["a", "b", "c", "d", "e"].map((k) => <Skeleton key={k} className="h-7 w-28 rounded-full" />)}
            {config.data?.faults.map((f) => {
              const on = faults.includes(f.name);
              return (
                <Tooltip
                  key={f.name}
                  wide
                  content={
                    <span className="block space-y-1">
                      <span className="block font-medium text-zinc-100">{f.title}</span>
                      <span className="block">
                        <span className="text-zinc-400">Effect: </span>
                        {f.effect}
                      </span>
                      <span className="block">
                        <span className="text-zinc-400">Expected: </span>
                        {f.expected}
                      </span>
                    </span>
                  }
                >
                  <button
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggle(f.name)}
                    data-fault={f.name}
                    className={cx(
                      "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition",
                      on
                        ? "border-violet-400/60 bg-violet-500/15 text-violet-200"
                        : "border-zinc-700/80 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200",
                    )}
                  >
                    <span
                      aria-hidden
                      className={cx("size-1.5 rounded-full", on ? "bg-violet-300" : "bg-zinc-600")}
                    />
                    {f.title}
                    {on && <span className="sr-only">(on)</span>}
                  </button>
                </Tooltip>
              );
            })}
          </div>
        </fieldset>

        <div className="flex flex-wrap items-center gap-3 border-t border-zinc-800/70 pt-4">
          <Button
            type="submit"
            variant="primary"
            size="lg"
            disabled={!canStartRun(persona) || tooShort}
            loading={start.isPending}
            icon={
              canStartRun(persona) ? (
                <Play className="size-4" aria-hidden />
              ) : (
                <Lock className="size-4" aria-hidden />
              )
            }
            aria-describedby={blocked ? reasonId : undefined}
            data-testid="start-run"
          >
            Start run
          </Button>
          {blocked ? (
            <p
              id={reasonId}
              className="flex items-center gap-1.5 text-xs text-zinc-400"
              data-testid="start-blocked"
            >
              <Info className="size-3.5 shrink-0" aria-hidden />
              {blocked}
            </p>
          ) : (
            <p className="text-xs text-zinc-500">
              Acting as <span className="text-zinc-300">{persona.name}</span>, the operator.{" "}
              {faults.length > 0 && <span className="text-violet-300">{faults.length} fault(s) armed. </span>}
              <kbd className="rounded border border-zinc-700 px-1 font-mono text-[10px]">⌘ Enter</kbd> starts.
            </p>
          )}
        </div>
      </form>
    </Card>
  );
}
