import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  createSession,
  lookupResidentByPhone,
  lookupResidentByQr,
  type Resident,
  type SessionBagInput,
} from "../lib/api";
import {
  listQueuedSessions,
  queueSession,
  syncQueuedSessions,
  type QueuedSession,
} from "../lib/offlineQueue";

/**
 * Collector doorstep screen (Phase 4). Mobile, one-handed, evening use in
 * dead zones — every submission goes into an offline queue first and syncs
 * in the background, so a missing signal never blocks the next stop.
 */

const ROUTE_KEY = "wastelens_route_code";
const VEHICLE_KEY = "wastelens_vehicle_code";
const BAG_TYPES = ["organic", "polythene", "paper", "general"] as const;
const CONDITIONS = ["GOOD", "WET", "TORN", "OVERFILLED"] as const;

type DraftBag = SessionBagInput & { key: string };

function newDraftBag(bagType: string): DraftBag {
  return { key: crypto.randomUUID(), bag_type: bagType, tag_id: "", gross_weight_kg: "" };
}

export default function Collector() {
  const queryClient = useQueryClient();
  const [online, setOnline] = useState(navigator.onLine);
  const [phone, setPhone] = useState("");
  const [qrInput, setQrInput] = useState("");
  const [resident, setResident] = useState<Resident | null>(null);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [lookingUp, setLookingUp] = useState(false);

  const [vehicleCode, setVehicleCode] = useState(() => localStorage.getItem(VEHICLE_KEY) ?? "");
  const [routeCode, setRouteCode] = useState(() => localStorage.getItem(ROUTE_KEY) ?? "");
  const [gps, setGps] = useState<{ lat: string; lng: string }>({ lat: "", lng: "" });
  const [gpsSource, setGpsSource] = useState<"pending" | "device" | "manual" | "unavailable">(
    "pending",
  );
  const [notes, setNotes] = useState("");
  const [bags, setBags] = useState<DraftBag[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);

  const queue = useQuery({ queryKey: ["offline-queue"], queryFn: listQueuedSessions });

  useEffect(() => {
    navigator.geolocation?.getCurrentPosition(
      (pos) => {
        setGps({ lat: pos.coords.latitude.toFixed(6), lng: pos.coords.longitude.toFixed(6) });
        setGpsSource("device");
      },
      () => setGpsSource("unavailable"),
      { enableHighAccuracy: true, timeout: 8000 },
    );
  }, []);

  useEffect(() => {
    function handleOnline() {
      setOnline(true);
      runSync();
    }
    function handleOffline() {
      setOnline(false);
    }
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function runSync() {
    const result = await syncQueuedSessions();
    if (result.synced.length > 0 || result.failed.length > 0) {
      queryClient.invalidateQueries({ queryKey: ["offline-queue"] });
    }
  }

  useEffect(() => {
    if (navigator.onLine) runSync();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function lookupByPhone() {
    if (!phone.trim()) return;
    setLookingUp(true);
    setLookupError(null);
    try {
      setResident(await lookupResidentByPhone(phone.trim()));
    } catch (err) {
      setResident(null);
      setLookupError(err instanceof ApiError ? err.message : "Lookup failed — try again");
    } finally {
      setLookingUp(false);
    }
  }

  async function lookupByQr() {
    if (!qrInput.trim()) return;
    setLookingUp(true);
    setLookupError(null);
    try {
      setResident(await lookupResidentByQr(qrInput.trim()));
    } catch (err) {
      setResident(null);
      setLookupError(err instanceof ApiError ? err.message : "QR not recognized");
    } finally {
      setLookingUp(false);
    }
  }

  function resetForNextHousehold() {
    setResident(null);
    setPhone("");
    setQrInput("");
    setBags([]);
    setNotes("");
    setSubmitMessage(null);
  }

  function updateBag(key: string, patch: Partial<DraftBag>) {
    setBags((prev) => prev.map((b) => (b.key === key ? { ...b, ...patch } : b)));
  }

  function removeBag(key: string) {
    setBags((prev) => prev.filter((b) => b.key !== key));
  }

  async function submit() {
    if (!resident) return;
    localStorage.setItem(VEHICLE_KEY, vehicleCode);
    localStorage.setItem(ROUTE_KEY, routeCode);
    setSubmitting(true);
    setSubmitMessage(null);

    const body = {
      user_id: resident.id,
      vehicle_code: vehicleCode || undefined,
      route_code: routeCode || undefined,
      gps_latitude: gps.lat || undefined,
      gps_longitude: gps.lng || undefined,
      notes: notes || undefined,
      bags: bags.map((bag) => ({
        bag_type: bag.bag_type,
        tag_id: bag.tag_id || undefined,
        gross_weight_kg: bag.gross_weight_kg || undefined,
        tare_weight_kg: bag.tare_weight_kg || undefined,
        bag_condition: bag.bag_condition || undefined,
      })),
    };

    try {
      if (!navigator.onLine) throw new Error("offline");
      await createSession(body);
      setSubmitMessage(`Recorded ${bags.length} bag(s) for ${resident.name}.`);
      resetForNextHousehold();
    } catch {
      await queueSession(body, resident.name);
      queryClient.invalidateQueries({ queryKey: ["offline-queue"] });
      setSubmitMessage(
        `Offline — queued ${bags.length} bag(s) for ${resident.name}. Will sync automatically.`,
      );
      resetForNextHousehold();
    } finally {
      setSubmitting(false);
    }
  }

  const queuedCount = queue.data?.length ?? 0;

  return (
    <div className="mx-auto max-w-md p-3 pb-24 sm:p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold text-emerald-700">Collector</h1>
        <ConnectionBadge online={online} queuedCount={queuedCount} onSync={runSync} />
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3">
        <BigField
          label="Vehicle"
          value={vehicleCode}
          onChange={setVehicleCode}
          placeholder="VAN-1"
        />
        <BigField label="Route" value={routeCode} onChange={setRouteCode} placeholder="R-9" />
      </div>

      <GpsField
        gps={gps}
        source={gpsSource}
        onChange={(g) => {
          setGps(g);
          setGpsSource("manual");
        }}
      />

      {resident === null ? (
        <div className="mt-4 space-y-4 rounded-xl bg-white p-4 shadow">
          <div>
            <label className="mb-1 block text-sm font-semibold">Scan household QR</label>
            <div className="flex gap-2">
              <input
                autoFocus
                value={qrInput}
                onChange={(e) => setQrInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && lookupByQr()}
                placeholder="Scan or paste QR code"
                className="min-h-14 flex-1 rounded-lg border border-gray-300 p-3 text-lg focus:border-emerald-500 focus:outline-none"
              />
              <button
                onClick={lookupByQr}
                disabled={lookingUp}
                className="min-h-14 rounded-lg bg-emerald-600 px-5 font-semibold text-white disabled:opacity-40"
              >
                Go
              </button>
            </div>
          </div>

          <div className="text-center text-sm text-gray-400">— or —</div>

          <div>
            <label className="mb-1 block text-sm font-semibold">Phone number</label>
            <div className="flex gap-2">
              <input
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && lookupByPhone()}
                placeholder="+9477XXXXXXX"
                className="min-h-14 flex-1 rounded-lg border border-gray-300 p-3 text-lg focus:border-emerald-500 focus:outline-none"
              />
              <button
                onClick={lookupByPhone}
                disabled={lookingUp}
                className="min-h-14 rounded-lg bg-emerald-600 px-5 font-semibold text-white disabled:opacity-40"
              >
                Find
              </button>
            </div>
          </div>

          {lookupError && <p className="text-sm text-red-600">{lookupError}</p>}
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          <div className="rounded-xl bg-white p-4 shadow">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-lg font-bold">{resident.name}</p>
                <p className="text-sm text-gray-500">{resident.phone}</p>
                <p className="text-sm text-gray-500">{resident.address}</p>
              </div>
              <button onClick={resetForNextHousehold} className="text-sm font-medium text-gray-500">
                change
              </button>
            </div>
          </div>

          <div className="rounded-xl bg-white p-4 shadow">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold">Bags</h2>
            </div>
            <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {BAG_TYPES.map((bt) => (
                <button
                  key={bt}
                  onClick={() => setBags((prev) => [...prev, newDraftBag(bt)])}
                  className="min-h-14 rounded-lg bg-emerald-50 font-semibold text-emerald-700 hover:bg-emerald-100"
                >
                  + {bt}
                </button>
              ))}
            </div>
            <div className="space-y-3">
              {bags.map((bag) => (
                <BagRow key={bag.key} bag={bag} onChange={updateBag} onRemove={removeBag} />
              ))}
              {bags.length === 0 && (
                <p className="text-sm text-gray-400">No bags added yet — tap a type above.</p>
              )}
            </div>
          </div>

          <div>
            <label className="mb-1 block text-sm font-semibold">Notes</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
              className="w-full rounded-lg border border-gray-300 p-3 focus:border-emerald-500 focus:outline-none"
            />
          </div>

          {submitMessage && <p className="text-sm text-emerald-700">{submitMessage}</p>}

          <button
            onClick={submit}
            disabled={submitting || bags.length === 0}
            className="min-h-16 w-full rounded-xl bg-emerald-600 text-xl font-bold text-white shadow-lg disabled:opacity-40"
          >
            {submitting ? "Saving…" : `Save collection (${bags.length})`}
          </button>
        </div>
      )}

      <QueuedList items={queue.data ?? []} />
    </div>
  );
}

function BigField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-semibold text-gray-500">{label}</label>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="min-h-12 w-full rounded-lg border border-gray-300 p-2 text-base focus:border-emerald-500 focus:outline-none"
      />
    </div>
  );
}

function GpsField({
  gps,
  source,
  onChange,
}: {
  gps: { lat: string; lng: string };
  source: "pending" | "device" | "manual" | "unavailable";
  onChange: (gps: { lat: string; lng: string }) => void;
}) {
  const labels: Record<string, string> = {
    pending: "Locating…",
    device: "GPS captured",
    manual: "Manually edited",
    unavailable: "GPS unavailable — enter manually",
  };
  return (
    <div className="mb-4 rounded-lg bg-white p-3 shadow">
      <div className="mb-2 flex items-center justify-between text-xs font-semibold text-gray-500">
        <span>Location</span>
        <span className={source === "device" ? "text-emerald-600" : "text-amber-600"}>
          {labels[source]}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <input
          value={gps.lat}
          onChange={(e) => onChange({ ...gps, lat: e.target.value })}
          placeholder="Latitude"
          className="rounded-lg border border-gray-300 p-2 text-sm"
        />
        <input
          value={gps.lng}
          onChange={(e) => onChange({ ...gps, lng: e.target.value })}
          placeholder="Longitude"
          className="rounded-lg border border-gray-300 p-2 text-sm"
        />
      </div>
    </div>
  );
}

function BagRow({
  bag,
  onChange,
  onRemove,
}: {
  bag: DraftBag;
  onChange: (key: string, patch: Partial<DraftBag>) => void;
  onRemove: (key: string) => void;
}) {
  return (
    <div className="rounded-lg border border-gray-200 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-semibold capitalize">{bag.bag_type}</span>
        <button onClick={() => onRemove(bag.key)} className="text-sm text-red-500">
          remove
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <input
          value={bag.tag_id ?? ""}
          onChange={(e) => onChange(bag.key, { tag_id: e.target.value })}
          placeholder="Tag (optional)"
          className="min-h-12 rounded-lg border border-gray-300 p-2 text-base"
        />
        <input
          inputMode="decimal"
          value={bag.gross_weight_kg ?? ""}
          onChange={(e) => onChange(bag.key, { gross_weight_kg: e.target.value })}
          placeholder="Weight (kg)"
          className="min-h-12 rounded-lg border border-gray-300 p-2 text-base"
        />
      </div>
      <div className="mt-2 grid grid-cols-4 gap-1">
        {CONDITIONS.map((c) => (
          <button
            key={c}
            onClick={() => onChange(bag.key, { bag_condition: c })}
            className={`min-h-10 rounded-lg text-xs font-semibold ${
              bag.bag_condition === c
                ? "bg-emerald-600 text-white"
                : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            {c}
          </button>
        ))}
      </div>
    </div>
  );
}

function ConnectionBadge({
  online,
  queuedCount,
  onSync,
}: {
  online: boolean;
  queuedCount: number;
  onSync: () => void;
}) {
  return (
    <button
      onClick={onSync}
      className={`rounded-full px-3 py-1 text-xs font-semibold ${
        online ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"
      }`}
    >
      {online ? "Online" : "Offline"}
      {queuedCount > 0 && ` · ${queuedCount} queued`}
    </button>
  );
}

function QueuedList({ items }: { items: QueuedSession[] }) {
  const pending = useMemo(() => items, [items]);
  if (pending.length === 0) return null;
  return (
    <div className="mt-6 rounded-xl border border-dashed border-amber-300 bg-amber-50 p-3 text-sm">
      <p className="mb-2 font-semibold text-amber-800">Waiting to sync ({pending.length})</p>
      <ul className="space-y-1">
        {pending.map((item) => (
          <li key={item.localId} className="flex items-center justify-between text-amber-900">
            <span>
              {item.residentLabel} · {item.body.bags.length} bag(s)
            </span>
            {item.lastError && <span className="text-red-600">retry pending</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
