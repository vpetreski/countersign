const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
const usdWhole = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});
const integer = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

/** "$1,234.56"; null and non-finite values render as an em dash. */
export function formatUsd(value: number | null | undefined, opts: { whole?: boolean } = {}): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const rounded = Math.round(value * 100) / 100;
  const normalized = Object.is(rounded, -0) ? 0 : rounded;
  return opts.whole ? usdWhole.format(normalized) : usd.format(normalized);
}

/** "$682.00/mo". */
export function formatMonthly(value: number | null | undefined): string {
  const formatted = formatUsd(value);
  return formatted === "—" ? formatted : `${formatted}/mo`;
}

/** A signed delta such as "−$682.00" or "+$12.00" (true minus sign). */
export function formatUsdDelta(value: number): string {
  if (!Number.isFinite(value) || Math.abs(value) < 0.005) return formatUsd(0);
  return `${value < 0 ? "−" : "+"}${formatUsd(Math.abs(value))}`;
}

export function formatInt(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return integer.format(value);
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

/** A 0..1 ratio as a percentage: 0.875 -> "87.5%". */
export function formatRatio(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function shortHash(value: string | null | undefined, length = 8): string {
  if (!value) return "—";
  return value.length <= length ? value : value.slice(0, length);
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  if (ms < 1) return `${(ms * 1000).toFixed(0)} µs`;
  if (ms < 1000) return `${ms < 10 ? ms.toFixed(2) : ms.toFixed(0)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDay(iso: string): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" });
}

/** "idle_vm" -> "Idle VM"; "unused_ip" -> "Unused IP". */
export function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  const words = value.replace(/[_-]+/g, " ").trim().split(/\s+/);
  const upper = new Set(["vm", "vms", "ip", "ips", "id", "api", "cpu", "sku", "usd", "aws"]);
  return words
    .map((w, i) => {
      const lower = w.toLowerCase();
      if (upper.has(lower)) return lower === "vms" ? "VMs" : lower === "ips" ? "IPs" : lower.toUpperCase();
      return i === 0 ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower;
    })
    .join(" ");
}

export function providerLabel(provider: string): string {
  if (provider === "aws") return "AWS";
  if (provider === "azure") return "Azure";
  return provider;
}
