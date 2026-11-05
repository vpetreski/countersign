import { isProblem } from "./api";
import type { RunView } from "./types";

/** Statuses in which the run makes progress on its own when someone calls advance. */
export const ADVANCING_STATUSES: ReadonlySet<string> = new Set([
  "created",
  "planning",
  "executing",
  "verifying",
]);

export function shouldAdvance(run: Pick<RunView, "status" | "terminal" | "can"> | null | undefined): boolean {
  return Boolean(run && !run.terminal && run.can.advance && ADVANCING_STATUSES.has(run.status));
}

export type Sleep = (ms: number) => Promise<void>;

export interface AutoAdvanceOptions {
  /** One tick: POST /api/runs/{id}/advance. */
  advance: () => Promise<RunView>;
  onRun: (run: RunView) => void;
  onError: (error: unknown) => void;
  onIdle?: () => void;
  intervalMs?: number;
  /** A 409 means another tick holds the run's lease: wait and try again. */
  conflictDelayMs?: number;
  maxConflicts?: number;
  /** Network errors and 5xx are retried with a growing delay before the loop gives up. */
  transientRetries?: number;
  sleep?: Sleep;
}

const defaultSleep: Sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Drives a run forward one tick at a time: at most one request in flight, a pause between ticks, and a stop
 * as soon as the run waits for a human or ends. stop() makes the loop drop any response still in flight.
 */
export class AutoAdvancer {
  private readonly opts: Required<Omit<AutoAdvanceOptions, "onIdle">> & Pick<AutoAdvanceOptions, "onIdle">;
  private generation = 0;
  private running = false;
  /** The request a stopped loop may still have in flight; a restarted loop waits for it. */
  private inflight: Promise<unknown> | null = null;

  constructor(opts: AutoAdvanceOptions) {
    this.opts = {
      intervalMs: 500,
      conflictDelayMs: 700,
      maxConflicts: 20,
      transientRetries: 3,
      sleep: defaultSleep,
      ...opts,
    };
  }

  get active(): boolean {
    return this.running;
  }

  /** Start from the latest known run state. A no-op while a loop is already running. */
  start(run: RunView): boolean {
    if (this.running || !shouldAdvance(run)) return false;
    this.running = true;
    const gen = ++this.generation;
    void this.loop(gen);
    return true;
  }

  stop(): void {
    this.generation++;
    this.running = false;
  }

  private async loop(gen: number): Promise<void> {
    const { advance, onRun, onError, intervalMs, conflictDelayMs, maxConflicts, transientRetries, sleep } =
      this.opts;
    let conflicts = 0;
    let transient = 0;
    try {
      while (gen === this.generation) {
        if (this.inflight) {
          await this.inflight.catch(() => undefined);
          if (gen !== this.generation) return;
        }
        let run: RunView;
        const pending = advance();
        this.inflight = pending;
        try {
          run = await pending;
        } catch (err) {
          if (gen !== this.generation) return;
          if (isProblem(err, 409) && conflicts < maxConflicts) {
            conflicts++;
            await sleep(conflictDelayMs);
            continue;
          }
          if (isProblem(err) && (err.status === 0 || err.status >= 500) && transient < transientRetries) {
            transient++;
            await sleep(conflictDelayMs * 2 ** transient);
            continue;
          }
          onError(err);
          return;
        } finally {
          if (this.inflight === pending) this.inflight = null;
        }
        if (gen !== this.generation) return;
        conflicts = 0;
        transient = 0;
        onRun(run);
        if (!shouldAdvance(run)) return;
        await sleep(intervalMs);
      }
    } finally {
      if (gen === this.generation) {
        this.running = false;
        this.opts.onIdle?.();
      }
    }
  }
}
