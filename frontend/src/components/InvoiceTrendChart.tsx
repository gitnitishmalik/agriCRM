/**
 * The invoiced-by-month area chart.
 *
 * Split into its own module so `Dashboard.tsx` can `lazy()` it. Recharts and
 * its d3 dependencies are around 380 kB — five times the whole application
 * before they arrived — and this system is explicitly built for field agents
 * on 2G (Doc 03 §4, and the vendor chunk split in vite.config.ts exists for
 * the same reason).
 *
 * Deferring it is not a micro-optimisation here: the chart renders only when
 * there are two or more months of issued invoices, so on an empty or new
 * register the bytes are never fetched at all.
 *
 * Colours are literals rather than Tailwind classes because Recharts writes
 * SVG attributes, not class names. They are the tokens from tailwind.config.js
 * and must be changed with them — brand green for the series, warm-earth
 * neutrals for the grid and axes.
 */

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

const BRAND = '#2F6B3C'
const LINE = '#E4DFD4'
const LINE_STRONG = '#C9C2B2'
const INK_2 = '#5C574B'
const INK_3 = '#837C6B'

export interface TrendPoint {
  month: string
  label: string
  value: number
}

export default function InvoiceTrendChart({ data }: { data: TrendPoint[] }) {
  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
          <defs>
            <linearGradient id="invoiced" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={BRAND} stopOpacity={0.22} />
              <stop offset="100%" stopColor={BRAND} stopOpacity={0} />
            </linearGradient>
          </defs>

          {/* Horizontal rules only. Vertical ones add ink without adding a
              reading the eye could not already take off the axis. */}
          <CartesianGrid stroke={LINE} vertical={false} />

          <XAxis
            dataKey="label"
            tickLine={false}
            axisLine={false}
            tick={{ fill: INK_3, fontSize: 11 }}
          />
          <YAxis
            tickLine={false}
            axisLine={false}
            width={64}
            tick={{ fill: INK_3, fontSize: 11 }}
            // Lakhs, because the register is Indian and ₹24,50,000 read as
            // "2.45M" is a number nobody in this office thinks in.
            tickFormatter={(value: number) => `₹${(value / 100000).toFixed(1)}L`}
          />
          <Tooltip
            cursor={{ stroke: LINE_STRONG }}
            contentStyle={{
              background: '#FFFFFF',
              border: `1px solid ${LINE}`,
              borderRadius: '0.375rem',
              fontSize: '0.875rem',
            }}
            labelStyle={{ color: INK_2 }}
            formatter={(value: number) => [
              `₹${value.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`,
              'Invoiced',
            ]}
          />
          <Area
            type="monotone"
            dataKey="value"
            stroke={BRAND}
            strokeWidth={2}
            fill="url(#invoiced)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
