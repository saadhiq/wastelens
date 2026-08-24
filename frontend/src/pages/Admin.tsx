import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  createBin,
  createCollector,
  createStaffAccount,
  createStation,
  deleteStation,
  listBins,
  listCalendarDays,
  listCollectors,
  listStations,
  updateCalendarDay,
  type Bin,
  type CalendarDay,
  type Collector,
  type InspectionStation,
} from "../lib/api";

/** Admin console (Phase 4): stations, bins, collectors, calendar. Admin
 * only — App.tsx only links here for an admin role, but every call is also
 * enforced server-side. */

type Tab = "stations" | "bins" | "collectors" | "calendar";

export default function Admin() {
  const [tab, setTab] = useState<Tab>("stations");
  const tabs: { key: Tab; label: string }[] = [
    { key: "stations", label: "Stations" },
    { key: "bins", label: "Bins" },
    { key: "collectors", label: "Collectors" },
    { key: "calendar", label: "Calendar" },
  ];
  return (
    <div className="mx-auto max-w-4xl p-4 sm:p-8">
      <h1 className="mb-6 text-2xl font-bold text-emerald-700">Admin</h1>
      <div className="mb-6 flex gap-2 border-b">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-semibold ${
              tab === t.key
                ? "border-b-2 border-emerald-600 text-emerald-700"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "stations" && <StationsTab />}
      {tab === "bins" && <BinsTab />}
      {tab === "collectors" && <CollectorsTab />}
      {tab === "calendar" && <CalendarTab />}
    </div>
  );
}

function ErrorText({ err }: { err: unknown }) {
  if (!err) return null;
  return <p className="text-sm text-red-600">{err instanceof ApiError ? err.message : "Failed"}</p>;
}

function StationsTab() {
  const qc = useQueryClient();
  const stations = useQuery({ queryKey: ["stations"], queryFn: () => listStations() });
  const [form, setForm] = useState({ station_code: "", facility_name: "", line_name: "" });

  const create = useMutation({
    mutationFn: () => createStation(form),
    onSuccess: () => {
      setForm({ station_code: "", facility_name: "", line_name: "" });
      qc.invalidateQueries({ queryKey: ["stations"] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteStation(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["stations"] }),
  });

  return (
    <div className="space-y-6">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
        className="grid gap-3 rounded-xl bg-white p-4 shadow sm:grid-cols-4"
      >
        <input
          required
          placeholder="Station code"
          value={form.station_code}
          onChange={(e) => setForm({ ...form, station_code: e.target.value })}
          className="rounded-lg border border-gray-300 p-2"
        />
        <input
          required
          placeholder="Facility name"
          value={form.facility_name}
          onChange={(e) => setForm({ ...form, facility_name: e.target.value })}
          className="rounded-lg border border-gray-300 p-2 sm:col-span-2"
        />
        <input
          placeholder="Line name"
          value={form.line_name}
          onChange={(e) => setForm({ ...form, line_name: e.target.value })}
          className="rounded-lg border border-gray-300 p-2"
        />
        <button className="rounded-lg bg-emerald-600 px-4 py-2 font-semibold text-white sm:col-span-4">
          Add station
        </button>
        <div className="sm:col-span-4">
          <ErrorText err={create.error} />
        </div>
      </form>

      <table className="w-full overflow-hidden rounded-xl bg-white shadow">
        <thead className="bg-gray-50 text-left text-xs font-semibold uppercase text-gray-500">
          <tr>
            <th className="p-3">Code</th>
            <th className="p-3">Facility</th>
            <th className="p-3">Line</th>
            <th className="p-3" />
          </tr>
        </thead>
        <tbody>
          {(stations.data?.items ?? []).map((s: InspectionStation) => (
            <tr key={s.id} className="border-t">
              <td className="p-3 font-mono text-sm">{s.station_code}</td>
              <td className="p-3">{s.facility_name}</td>
              <td className="p-3 text-gray-500">{s.line_name ?? "—"}</td>
              <td className="p-3 text-right">
                <button
                  onClick={() => remove.mutate(s.id)}
                  className="text-sm text-red-500 hover:underline"
                >
                  delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const BIN_TYPES = ["ORGANIC", "PAPER", "POLYTHENE", "GENERAL"];

function BinsTab() {
  const qc = useQueryClient();
  const bins = useQuery({ queryKey: ["bins"], queryFn: () => listBins() });
  const [form, setForm] = useState({ bin_code: "", bin_type: "ORGANIC", vendor_name: "" });

  const create = useMutation({
    mutationFn: () => createBin(form),
    onSuccess: () => {
      setForm({ bin_code: "", bin_type: "ORGANIC", vendor_name: "" });
      qc.invalidateQueries({ queryKey: ["bins"] });
    },
  });

  return (
    <div className="space-y-6">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
        className="grid gap-3 rounded-xl bg-white p-4 shadow sm:grid-cols-4"
      >
        <input
          required
          placeholder="Bin code"
          value={form.bin_code}
          onChange={(e) => setForm({ ...form, bin_code: e.target.value })}
          className="rounded-lg border border-gray-300 p-2"
        />
        <select
          value={form.bin_type}
          onChange={(e) => setForm({ ...form, bin_type: e.target.value })}
          className="rounded-lg border border-gray-300 p-2"
        >
          {BIN_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <input
          placeholder="Vendor"
          value={form.vendor_name}
          onChange={(e) => setForm({ ...form, vendor_name: e.target.value })}
          className="rounded-lg border border-gray-300 p-2"
        />
        <button className="rounded-lg bg-emerald-600 px-4 py-2 font-semibold text-white">
          Add bin
        </button>
        <div className="sm:col-span-4">
          <ErrorText err={create.error} />
        </div>
      </form>

      <table className="w-full overflow-hidden rounded-xl bg-white shadow">
        <thead className="bg-gray-50 text-left text-xs font-semibold uppercase text-gray-500">
          <tr>
            <th className="p-3">Code</th>
            <th className="p-3">Type</th>
            <th className="p-3">Vendor</th>
          </tr>
        </thead>
        <tbody>
          {(bins.data?.items ?? []).map((b: Bin) => (
            <tr key={b.id} className="border-t">
              <td className="p-3 font-mono text-sm">{b.bin_code}</td>
              <td className="p-3">{b.bin_type}</td>
              <td className="p-3 text-gray-500">{b.vendor_name ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CollectorsTab() {
  const qc = useQueryClient();
  const collectors = useQuery({ queryKey: ["collectors"], queryFn: () => listCollectors() });
  const [form, setForm] = useState({
    email: "",
    full_name: "",
    password: "",
    employee_code: "",
    phone: "",
  });

  const create = useMutation({
    mutationFn: async () => {
      const staff = await createStaffAccount({
        email: form.email,
        full_name: form.full_name,
        password: form.password,
        role: "collector",
      });
      return createCollector({
        staff_account_id: staff.id,
        employee_code: form.employee_code,
        full_name: form.full_name,
        phone: form.phone || undefined,
      });
    },
    onSuccess: () => {
      setForm({ email: "", full_name: "", password: "", employee_code: "", phone: "" });
      qc.invalidateQueries({ queryKey: ["collectors"] });
    },
  });

  return (
    <div className="space-y-6">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
        className="grid gap-3 rounded-xl bg-white p-4 shadow sm:grid-cols-3"
      >
        <input
          required
          type="email"
          placeholder="Login email"
          value={form.email}
          onChange={(e) => setForm({ ...form, email: e.target.value })}
          className="rounded-lg border border-gray-300 p-2"
        />
        <input
          required
          placeholder="Full name"
          value={form.full_name}
          onChange={(e) => setForm({ ...form, full_name: e.target.value })}
          className="rounded-lg border border-gray-300 p-2"
        />
        <input
          required
          type="password"
          placeholder="Temporary password"
          value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })}
          className="rounded-lg border border-gray-300 p-2"
        />
        <input
          required
          placeholder="Employee code"
          value={form.employee_code}
          onChange={(e) => setForm({ ...form, employee_code: e.target.value })}
          className="rounded-lg border border-gray-300 p-2"
        />
        <input
          placeholder="Phone"
          value={form.phone}
          onChange={(e) => setForm({ ...form, phone: e.target.value })}
          className="rounded-lg border border-gray-300 p-2"
        />
        <button className="rounded-lg bg-emerald-600 px-4 py-2 font-semibold text-white">
          Add collector
        </button>
        <div className="sm:col-span-3">
          <ErrorText err={create.error} />
        </div>
      </form>

      <table className="w-full overflow-hidden rounded-xl bg-white shadow">
        <thead className="bg-gray-50 text-left text-xs font-semibold uppercase text-gray-500">
          <tr>
            <th className="p-3">Employee</th>
            <th className="p-3">Name</th>
            <th className="p-3">Phone</th>
            <th className="p-3">Active</th>
          </tr>
        </thead>
        <tbody>
          {(collectors.data?.items ?? []).map((c: Collector) => (
            <tr key={c.id} className="border-t">
              <td className="p-3 font-mono text-sm">{c.employee_code}</td>
              <td className="p-3">{c.full_name}</td>
              <td className="p-3 text-gray-500">{c.phone ?? "—"}</td>
              <td className="p-3">{c.is_active ? "yes" : "no"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CalendarTab() {
  const qc = useQueryClient();
  const [year, setYear] = useState(new Date().getFullYear());
  const days = useQuery({ queryKey: ["calendar", year], queryFn: () => listCalendarDays(year) });

  const update = useMutation({
    mutationFn: (vars: { date: string; patch: Parameters<typeof updateCalendarDay>[1] }) =>
      updateCalendarDay(vars.date, vars.patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["calendar", year] }),
  });

  const flagged = (days.data?.items ?? []).filter((d) => d.is_poya || d.is_public_holiday);
  const [showAll, setShowAll] = useState(false);
  const rows = showAll ? (days.data?.items ?? []) : flagged;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 rounded-xl bg-white p-4 shadow">
        <label className="text-sm font-semibold">Year</label>
        <input
          type="number"
          value={year}
          onChange={(e) => setYear(Number(e.target.value))}
          className="w-28 rounded-lg border border-gray-300 p-2"
        />
        <button
          onClick={() => setShowAll((v) => !v)}
          className="ml-auto text-sm text-emerald-700 hover:underline"
        >
          {showAll ? "Show flagged only" : `Show all ${days.data?.total ?? 0} days`}
        </button>
      </div>

      <p className="text-sm text-gray-500">
        Poya and public-holiday flags are never inferred automatically — set them here.
      </p>

      <table className="w-full overflow-hidden rounded-xl bg-white shadow">
        <thead className="bg-gray-50 text-left text-xs font-semibold uppercase text-gray-500">
          <tr>
            <th className="p-3">Date</th>
            <th className="p-3">Poya</th>
            <th className="p-3">Holiday</th>
            <th className="p-3">Note</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d: CalendarDay) => (
            <tr key={d.calendar_date} className="border-t">
              <td className="p-3 font-mono text-sm">
                {d.calendar_date}
                {d.is_weekend && <span className="ml-2 text-xs text-gray-400">weekend</span>}
              </td>
              <td className="p-3">
                <input
                  type="checkbox"
                  checked={d.is_poya}
                  onChange={(e) =>
                    update.mutate({ date: d.calendar_date, patch: { is_poya: e.target.checked } })
                  }
                />
              </td>
              <td className="p-3">
                <input
                  type="checkbox"
                  checked={d.is_public_holiday}
                  onChange={(e) =>
                    update.mutate({
                      date: d.calendar_date,
                      patch: { is_public_holiday: e.target.checked },
                    })
                  }
                />
              </td>
              <td className="p-3">
                <input
                  defaultValue={d.note ?? ""}
                  onBlur={(e) =>
                    e.target.value !== (d.note ?? "") &&
                    update.mutate({ date: d.calendar_date, patch: { note: e.target.value } })
                  }
                  placeholder="e.g. Vesak Full Moon Poya"
                  className="w-full rounded border border-gray-200 p-1 text-sm"
                />
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={4} className="p-6 text-center text-gray-400">
                {days.isLoading ? "Loading…" : "No flagged days yet."}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
