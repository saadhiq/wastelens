import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "../lib/api";

/**
 * Analytics dashboard (analyst/admin): model-quality stat tiles, top items and
 * top brands as single-hue horizontal bar lists, per-item quality table, and a
 * manual profile-rebuild trigger.
 */

interface ItemCount {
  name: string;
  count: number;
}

interface QualityByItem {
  item_name: string;
  detections: number;
  avg_confidence: number;
  reviewed: number;
  corrected: number;
}

interface QualityReport {
  total_detections: number;
  avg_confidence: number;
  pct_needs_review: number;
  capture_failure_rate: number;
  by_item: QualityByItem[];
}

// Chart ink/series tokens (validated reference palette; values always labeled).
const SERIES_ITEMS = "#2a78d6"; // blue — slot 1
const SERIES_BRANDS = "#1baf7a"; // aqua — slot 2 (sub-3:1 → values shown as text)

const PERIODS = [7, 30, 90] as const;

export default function Analytics() {
  const [days, setDays] = useState<number>(30);

  const quality = useQuery({
    queryKey: ["quality", days],
    queryFn: () => apiFetch<QualityReport>(`/analytics/quality?days=${days}`),
    retry: false,
  });
  const topItems = useQuery({
    queryKey: ["top-items", days],
    queryFn: () => apiFetch<ItemCount[]>(`/analytics/top-items?days=${days}`),
    retry: false,
  });
  const topBrands = useQuery({
    queryKey: ["top-brands", days],
    queryFn: () => apiFetch<ItemCount[]>(`/analytics/top-brands?days=${days}`),
    retry: false,
  });

  if (quality.error instanceof ApiError && quality.error.status === 403) {
    return (
      <div className="p-8 text-gray-600">
        Analytics requires the <b>analyst</b> or <b>admin</b> role.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-4 sm:p-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-emerald-700">Analytics</h1>
        <div className="flex items-center gap-2">
          {/* One filter row above the charts; filters apply to all of them. */}
          <div className="flex rounded-lg border border-gray-300 bg-white p-0.5 text-sm">
            {PERIODS.map((p) => (
              <button
                key={p}
                onClick={() => setDays(p)}
                className={`rounded-md px-3 py-1 ${
                  days === p ? "bg-emerald-600 font-semibold text-white" : "text-gray-600"
                }`}
              >
                {p}d
              </button>
            ))}
          </div>
          <RebuildButton />
        </div>
      </div>

      {/* Stat tiles — headline numbers, no chart junk */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="Detections" value={quality.data?.total_detections} />
        <StatTile
          label="Avg confidence"
          value={quality.data && `${(quality.data.avg_confidence * 100).toFixed(1)}%`}
        />
        <StatTile
          label="Needs review"
          value={quality.data && `${quality.data.pct_needs_review.toFixed(1)}%`}
        />
        <StatTile
          label="Capture failures"
          value={quality.data && `${quality.data.capture_failure_rate.toFixed(1)}%`}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <BarList title="Top items" data={topItems.data ?? []} color={SERIES_ITEMS} />
        <BarList title="Top brands (from OCR)" data={topBrands.data ?? []} color={SERIES_BRANDS} />
      </div>

      <QualityTable rows={quality.data?.by_item ?? []} />
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-white p-4 shadow-sm">
      <div className="text-3xl font-semibold text-gray-900">{value ?? "—"}</div>
      <div className="mt-1 text-sm text-gray-500">{label}</div>
    </div>
  );
}

/** Single-hue horizontal bar list: length encodes count; labels and values are
 * always-visible text (ink tokens, never the series color). */
function BarList({ title, data, color }: { title: string; data: ItemCount[]; color: string }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  return (
    <section className="rounded-xl bg-white p-5 shadow-sm">
      <h2 className="mb-4 text-sm font-semibold text-gray-700">{title}</h2>
      {data.length === 0 ? (
        <p className="text-sm text-gray-400">No data in this period.</p>
      ) : (
        <ul className="space-y-2.5">
          {data.map((d) => (
            <li key={d.name} className="group" title={`${d.name}: ${d.count}`}>
              <div className="mb-0.5 flex justify-between text-sm">
                <span className="text-gray-700">{d.name.replaceAll("_", " ")}</span>
                <span className="tabular-nums text-gray-500">{d.count}</span>
              </div>
              <div className="h-2 rounded-r bg-gray-100 group-hover:bg-gray-200">
                <div
                  className="h-full rounded-r"
                  style={{ width: `${(d.count / max) * 100}%`, backgroundColor: color }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Table view of per-item model quality — the fine-tuning priority list. */
function QualityTable({ rows }: { rows: QualityByItem[] }) {
  if (rows.length === 0) return null;
  return (
    <section className="overflow-x-auto rounded-xl bg-white p-5 shadow-sm">
      <h2 className="mb-4 text-sm font-semibold text-gray-700">Model quality by item class</h2>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-gray-500">
            <th className="pb-2 font-medium">Item</th>
            <th className="pb-2 text-right font-medium">Detections</th>
            <th className="pb-2 text-right font-medium">Avg conf.</th>
            <th className="pb-2 text-right font-medium">Reviewed</th>
            <th className="pb-2 text-right font-medium">Corrected</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.item_name} className="border-b border-gray-100">
              <td className="py-2 text-gray-700">{r.item_name.replaceAll("_", " ")}</td>
              <td className="py-2 text-right tabular-nums">{r.detections}</td>
              <td className="py-2 text-right tabular-nums">
                {(r.avg_confidence * 100).toFixed(0)}%
              </td>
              <td className="py-2 text-right tabular-nums">{r.reviewed}</td>
              <td className="py-2 text-right tabular-nums">{r.corrected}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function RebuildButton() {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function rebuild() {
    setBusy(true);
    setMsg(null);
    try {
      const res = await apiFetch<{ profiles_written: number }>("/profiles/rebuild?weeks_back=4", {
        method: "POST",
      });
      setMsg(`${res.profiles_written} profiles updated`);
      queryClient.invalidateQueries();
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : "Rebuild failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="flex items-center gap-2 text-sm">
      {msg && <span className="text-gray-500">{msg}</span>}
      <button
        onClick={rebuild}
        disabled={busy}
        className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 hover:bg-gray-100 disabled:opacity-50"
      >
        {busy ? "Rebuilding…" : "Rebuild profiles"}
      </button>
    </span>
  );
}
