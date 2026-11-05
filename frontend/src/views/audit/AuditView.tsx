import { useMutation } from "@tanstack/react-query";
import { Link2, ShieldCheck, ShieldX } from "lucide-react";
import { useToast } from "../../components/toast";
import { Badge, Button, Card, cx, EmptyState, Mono, Skeleton, td, th } from "../../components/ui";
import { useAudit } from "../../hooks";
import { formatTime, shortHash } from "../../lib/format";
import { personaKeyOf } from "../../lib/personas";
import type { AuditVerify } from "../../lib/types";
import { useSession } from "../../session";

export default function AuditView() {
  const { api, personas } = useSession();
  const toast = useToast();
  const audit = useAudit();
  const verify = useMutation({
    mutationFn: () => api<AuditVerify>("/api/audit/verify"),
    onError: (err) => toast.problem(err, "The chain could not be verified"),
  });
  const v = verify.data;
  const actorName = (actor: string) => {
    const key = personaKeyOf(actor);
    const p = personas.find((x) => x.key === key);
    return p && actor.includes(":") ? p.name : actor;
  };
  return (
    <div className="space-y-6" data-testid="audit-view">
      <Card
        title="Audit log"
        icon={<Link2 className="size-4" aria-hidden />}
        subtitle="Append-only and hash-chained: each entry commits to the previous one, so any edit breaks the chain"
        actions={
          <Button
            variant="secondary"
            icon={<ShieldCheck className="size-4" aria-hidden />}
            loading={verify.isPending}
            onClick={() => verify.mutate()}
            data-testid="verify-chain"
          >
            Verify chain
          </Button>
        }
      >
        {v && (
          <div
            role="status"
            data-testid="verify-result"
            data-valid={v.valid}
            className={cx(
              "mb-4 flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3 text-sm animate-pop",
              v.valid
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-100"
                : "border-red-500/40 bg-red-500/10 text-red-100",
            )}
          >
            {v.valid ? (
              <ShieldCheck className="size-5 text-emerald-300" aria-hidden />
            ) : (
              <ShieldX className="size-5 text-red-300" aria-hidden />
            )}
            <span className="font-semibold">{v.valid ? "Chain valid ✓" : "Chain broken ✗"}</span>
            <span>{v.entries} entries</span>
            <span>
              head{" "}
              <Mono className="text-[11px]" title={v.head ?? ""}>
                {shortHash(v.head, 16)}
              </Mono>
            </span>
            {!v.valid && (
              <span>
                broken at {v.broken_at}: {v.reason}
              </span>
            )}
          </div>
        )}
        {audit.isPending ? (
          <div className="space-y-2">
            {["a", "b", "c", "d"].map((k) => (
              <Skeleton key={k} className="h-9" />
            ))}
          </div>
        ) : (audit.data?.entries.length ?? 0) === 0 ? (
          <EmptyState title="No entries yet" />
        ) : (
          <div className="scroll-thin -mx-5 overflow-x-auto px-5">
            <table
              className="w-full min-w-[820px] border-separate border-spacing-0"
              data-testid="audit-table"
            >
              <thead>
                <tr className="[&>th]:border-b [&>th]:border-zinc-800">
                  <th className={th}>Seq</th>
                  <th className={th}>Time</th>
                  <th className={th}>Actor</th>
                  <th className={th}>Action</th>
                  <th className={th}>Subject</th>
                  <th className={th}>Hash</th>
                  <th className={th}>Previous</th>
                </tr>
              </thead>
              <tbody>
                {audit.data?.entries.map((e) => (
                  <tr
                    key={e.seq}
                    className="[&>td]:border-b [&>td]:border-zinc-800/70"
                    data-testid="audit-row"
                  >
                    <td className={cx(td, "font-mono text-xs text-zinc-400")}>{e.seq}</td>
                    <td className={cx(td, "font-mono text-xs text-zinc-400")}>{formatTime(e.at)}</td>
                    <td className={td}>{actorName(e.actor)}</td>
                    <td className={td}>
                      <details>
                        <summary>
                          <Badge
                            mono
                            tone={
                              e.action.startsWith("plan.denied")
                                ? "danger"
                                : e.action.startsWith("capability")
                                  ? "accent"
                                  : "neutral"
                            }
                          >
                            {e.action}
                          </Badge>
                        </summary>
                        <pre className="scroll-thin mt-1 max-h-48 max-w-[420px] overflow-auto rounded-md bg-zinc-950/70 p-2 font-mono text-[11px] text-zinc-400">
                          {JSON.stringify(e.payload, null, 2)}
                        </pre>
                      </details>
                    </td>
                    <td className={td}>
                      <Mono className="text-xs">{e.subject}</Mono>
                    </td>
                    <td className={td}>
                      <Mono className="text-xs text-emerald-300/90" title={e.hash}>
                        {shortHash(e.hash, 10)}
                      </Mono>
                    </td>
                    <td className={td}>
                      <Mono className="text-xs text-zinc-500" title={e.prev_hash}>
                        {shortHash(e.prev_hash, 10)}
                      </Mono>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
