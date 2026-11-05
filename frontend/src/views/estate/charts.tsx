import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatDay, formatUsd, humanize } from "../../lib/format";
import type { CategoryWaste, Dashboard, ResourceDetail } from "../../lib/types";

const AXIS = { stroke: "#52525b", fontSize: 11, tickLine: false, axisLine: false } as const;
const GRID = { stroke: "#27272a", strokeDasharray: "3 3", vertical: false } as const;
const TOOLTIP = {
  contentStyle: {
    background: "rgba(24,24,27,0.96)",
    border: "1px solid #3f3f46",
    borderRadius: 10,
    fontSize: 12,
    color: "#e4e4e7",
  },
  labelStyle: { color: "#a1a1aa", marginBottom: 4 },
  cursor: { fill: "rgba(63,63,70,0.25)" },
} as const;

export const AWS_COLOR = "#fb923c";
export const AZURE_COLOR = "#38bdf8";

export function SpendChart({ daily }: { daily: Dashboard["spend"]["daily"] }) {
  return (
    <div className="h-64" role="img" aria-label="Daily billed spend in October, stacked by provider">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={daily} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="day" tickFormatter={formatDay} {...AXIS} interval={4} />
          <YAxis tickFormatter={(v: number) => `$${v}`} {...AXIS} width={48} />
          <Tooltip
            {...TOOLTIP}
            labelFormatter={(l) => formatDay(String(l))}
            formatter={(v, name) => [formatUsd(Number(v)), name === "aws" ? "AWS" : "Azure"]}
          />
          <Legend
            formatter={(v: string) => (v === "aws" ? "AWS" : "Azure")}
            wrapperStyle={{ fontSize: 12, color: "#a1a1aa" }}
            iconType="circle"
            iconSize={8}
          />
          <Bar dataKey="aws" stackId="spend" fill={AWS_COLOR} fillOpacity={0.85} isAnimationActive={false} />
          <Bar
            dataKey="azure"
            stackId="spend"
            fill={AZURE_COLOR}
            fillOpacity={0.85}
            radius={[3, 3, 0, 0]}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function WasteChart({ byCategory }: { byCategory: Record<string, CategoryWaste> }) {
  const data = Object.entries(byCategory)
    .map(([k, v]) => ({
      category: humanize(k),
      eligible: Math.round(v.eligible_monthly_usd * 100) / 100,
      blocked: Math.round((v.monthly_usd - v.eligible_monthly_usd) * 100) / 100,
    }))
    .sort((a, b) => b.eligible + b.blocked - (a.eligible + a.blocked));
  return (
    <div
      className="h-64"
      role="img"
      aria-label="Identified waste per month by category, split into eligible and blocked"
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 8, right: 12, bottom: 0, left: 8 }}>
          <CartesianGrid {...GRID} horizontal={false} vertical />
          <XAxis type="number" tickFormatter={(v: number) => `$${v}`} {...AXIS} />
          <YAxis type="category" dataKey="category" {...AXIS} width={110} />
          <Tooltip
            {...TOOLTIP}
            formatter={(v, name) => [
              `${formatUsd(Number(v))}/mo`,
              name === "eligible" ? "Eligible" : "Blocked by policy",
            ]}
          />
          <Legend
            formatter={(v: string) => (v === "eligible" ? "Eligible" : "Blocked by policy")}
            wrapperStyle={{ fontSize: 12, color: "#a1a1aa" }}
            iconType="circle"
            iconSize={8}
          />
          <Bar dataKey="eligible" stackId="w" fill="#34d399" isAnimationActive={false} />
          <Bar dataKey="blocked" stackId="w" fill="#52525b" radius={[0, 3, 3, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function CpuChart({ cpu }: { cpu: ResourceDetail["cpu"] }) {
  return (
    <div
      className="h-56"
      role="img"
      aria-label="Daily CPU utilization, average and maximum, with idle thresholds at 5% and 20%"
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={cpu} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="day" tickFormatter={formatDay} {...AXIS} interval={6} />
          <YAxis
            tickFormatter={(v: number) => `${v}%`}
            {...AXIS}
            width={40}
            domain={[0, (max: number) => Math.max(25, Math.ceil(max / 5) * 5)]}
          />
          <Tooltip
            {...TOOLTIP}
            cursor={{ stroke: "#52525b" }}
            labelFormatter={(l) => formatDay(String(l))}
            formatter={(v, name) => [
              v === null || v === undefined ? "no data" : `${Number(v).toFixed(2)}%`,
              name === "avg" ? "Daily average" : "Daily max",
            ]}
          />
          <ReferenceLine
            y={5}
            stroke="#34d399"
            strokeDasharray="4 4"
            label={{ value: "5% idle", fill: "#34d399", fontSize: 10, position: "insideTopRight" }}
          />
          <ReferenceLine
            y={20}
            stroke="#fbbf24"
            strokeDasharray="4 4"
            label={{ value: "20% peak", fill: "#fbbf24", fontSize: 10, position: "insideTopRight" }}
          />
          <Line
            type="monotone"
            dataKey="avg"
            stroke="#38bdf8"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="max"
            stroke="#a78bfa"
            strokeWidth={1.5}
            strokeDasharray="2 2"
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Legend
            formatter={(v: string) => (v === "avg" ? "Daily average" : "Daily max")}
            wrapperStyle={{ fontSize: 12, color: "#a1a1aa" }}
            iconSize={8}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function CostChart({ cost }: { cost: ResourceDetail["daily_cost"] }) {
  return (
    <div className="h-44" role="img" aria-label="Daily billed cost of this resource">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={cost} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="day" tickFormatter={formatDay} {...AXIS} interval={6} />
          <YAxis tickFormatter={(v: number) => `$${v}`} {...AXIS} width={44} />
          <Tooltip
            {...TOOLTIP}
            labelFormatter={(l) => formatDay(String(l))}
            formatter={(v) => [formatUsd(Number(v)), "Billed"]}
          />
          <Bar
            dataKey="billed_usd"
            fill="#a1a1aa"
            fillOpacity={0.7}
            radius={[2, 2, 0, 0]}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
