"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export type EntriesPoint = { hour: string; entries: number };

export function EntriesChart({ data }: { data: EntriesPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
        <defs>
          <linearGradient id="entries-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--brand)" stopOpacity={0.45} />
            <stop offset="100%" stopColor="var(--brand)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="4 4" vertical={false} stroke="var(--hairline)" />
        <XAxis
          dataKey="hour"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          tick={{ fontSize: 12, fill: "currentColor" }}
          className="tabular-nums"
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
          width={44}
          tick={{ fontSize: 12, fill: "currentColor" }}
          className="tabular-nums"
        />
        <Tooltip
          cursor={{ stroke: "var(--hairline)" }}
          contentStyle={{
            borderRadius: 8,
            border: "1px solid var(--hairline)",
            boxShadow: "none",
            fontSize: 12,
          }}
          labelStyle={{ color: "var(--ink-muted)" }}
        />
        <Area
          type="monotone"
          dataKey="entries"
          stroke="var(--brand)"
          strokeWidth={2}
          fill="url(#entries-fill)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
