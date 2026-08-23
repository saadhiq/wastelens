import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch, getCapture, uploadCapture, type Capture } from "../lib/api";

/**
 * Station Capture: upload one tray photo per emptied bag, watch analysis live,
 * see detections. Optimized for tablet use — big touch targets, camera input.
 */

const STATION_ID_KEY = "wastelens_station_id";

export default function Station() {
  const [stationId, setStationId] = useState(
    () => localStorage.getItem(STATION_ID_KEY) ?? "station-1",
  );
  const [bagTag, setBagTag] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [captureId, setCaptureId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  // Poll the capture every 2.5s until analysis reaches a terminal state.
  const capture = useQuery({
    queryKey: ["capture", captureId],
    queryFn: () => getCapture(captureId!),
    enabled: captureId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.analysis_status;
      return status === "done" || status === "failed" ? false : 2500;
    },
  });

  function pickFile(f: File | null) {
    setFile(f);
    setPreview((old) => {
      if (old) URL.revokeObjectURL(old);
      return f ? URL.createObjectURL(f) : null;
    });
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setUploading(true);
    setError(null);
    localStorage.setItem(STATION_ID_KEY, stationId);
    try {
      const created = await uploadCapture(file, bagTag.trim(), stationId.trim());
      setCaptureId(created.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  function nextBag() {
    setCaptureId(null);
    setBagTag("");
    pickFile(null);
    if (fileInput.current) fileInput.current.value = "";
  }

  const status = capture.data?.analysis_status;

  return (
    <div className="mx-auto max-w-2xl p-4 sm:p-8">
      <h1 className="mb-6 text-2xl font-bold text-emerald-700">Station Capture</h1>

      {captureId === null ? (
        <form onSubmit={submit} className="space-y-4 rounded-xl bg-white p-6 shadow">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm font-medium">Bag QR / tag</label>
              <input
                required
                value={bagTag}
                onChange={(e) => setBagTag(e.target.value)}
                placeholder="TEST-QR-001"
                className="w-full rounded-lg border border-gray-300 p-3 text-lg focus:border-emerald-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">Station</label>
              <input
                required
                value={stationId}
                onChange={(e) => setStationId(e.target.value)}
                className="w-full rounded-lg border border-gray-300 p-3 text-lg focus:border-emerald-500 focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">Tray photo</label>
            <input
              ref={fileInput}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              capture="environment"
              onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
              className="w-full rounded-lg border border-dashed border-gray-400 p-3 file:mr-3 file:rounded-md file:border-0 file:bg-emerald-600 file:px-4 file:py-2 file:font-semibold file:text-white"
            />
            {preview && (
              <img
                src={preview}
                alt="tray preview"
                className="mt-3 max-h-64 rounded-lg border object-contain"
              />
            )}
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <button
            type="submit"
            disabled={!file || uploading}
            className="w-full rounded-lg bg-emerald-600 py-3 text-lg font-semibold text-white hover:bg-emerald-700 disabled:opacity-40"
          >
            {uploading ? "Uploading…" : "Analyze tray"}
          </button>
        </form>
      ) : (
        <div className="rounded-xl bg-white p-6 shadow">
          <StatusBanner status={status} />
          {status === "done" && capture.data && <Results capture={capture.data} />}
          {status === "failed" && (
            <p className="mt-2 text-sm text-gray-600">
              The vision model could not produce a valid result for this image. The raw output was
              saved for engineers to inspect.
            </p>
          )}
          {(status === "done" || status === "failed") && (
            <button
              onClick={nextBag}
              className="mt-6 w-full rounded-lg bg-emerald-600 py-3 text-lg font-semibold text-white hover:bg-emerald-700"
            >
              Next bag →
            </button>
          )}
        </div>
      )}

      <QuickSetup />
    </div>
  );
}

function StatusBanner({ status }: { status?: string }) {
  const styles: Record<string, string> = {
    pending: "bg-amber-100 text-amber-800",
    processing: "bg-blue-100 text-blue-800",
    done: "bg-emerald-100 text-emerald-800",
    failed: "bg-red-100 text-red-800",
  };
  const labels: Record<string, string> = {
    pending: "Queued for analysis…",
    processing: "Analyzing tray with vision model…",
    done: "Analysis complete",
    failed: "Analysis failed",
  };
  const s = status ?? "pending";
  return (
    <div className={`flex items-center gap-3 rounded-lg p-4 ${styles[s]}`}>
      {(s === "pending" || s === "processing") && (
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
      )}
      <span className="font-semibold">{labels[s]}</span>
    </div>
  );
}

function Results({ capture }: { capture: Capture }) {
  const detections = capture.detections ?? [];
  if (detections.length === 0)
    return <p className="mt-4 text-gray-600">No items detected on this tray.</p>;
  return (
    <ul className="mt-4 space-y-3">
      {detections.map((d) => (
        <li key={d.id} className="rounded-lg border border-gray-200 p-4">
          <div className="flex items-center justify-between">
            <span className="font-semibold">{d.item_name.replaceAll("_", " ")}</span>
            <span className="flex items-center gap-2">
              {d.needs_review && (
                <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                  needs review
                </span>
              )}
              <span className="text-sm tabular-nums text-gray-500">
                {(d.confidence * 100).toFixed(0)}%
              </span>
            </span>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-gray-100">
            <div
              className={`h-full ${d.confidence >= 0.75 ? "bg-emerald-500" : "bg-amber-400"}`}
              style={{ width: `${d.confidence * 100}%` }}
            />
          </div>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 text-sm text-gray-600">
            {d.estimated_quantity && (
              <div>
                <dt className="inline font-medium">Qty: </dt>
                <dd className="inline">{d.estimated_quantity}</dd>
              </div>
            )}
            {d.ocr_text && (
              <div className="col-span-2">
                <dt className="inline font-medium">Text read: </dt>
                <dd className="inline italic">“{d.ocr_text}”</dd>
              </div>
            )}
            {d.subcategory && (
              <div className="col-span-2">
                <dt className="inline font-medium">Detail: </dt>
                <dd className="inline">{d.subcategory}</dd>
              </div>
            )}
          </dl>
        </li>
      ))}
    </ul>
  );
}

/** Admin helper: create a test resident + bag without leaving the page. */
function QuickSetup() {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function createTestBag(bagType: string) {
    setBusy(true);
    setResult(null);
    try {
      const suffix = Math.random().toString(36).slice(2, 8);
      const user = await apiFetch<{ id: string }>("/users", {
        method: "POST",
        body: JSON.stringify({
          name: `Test Household ${suffix}`,
          phone: `+9477${Math.floor(1000000 + Math.random() * 8999999)}`,
          address: "Quick-setup lane",
        }),
      });
      const tag = `QS-${suffix.toUpperCase()}`;
      await apiFetch("/bags", {
        method: "POST",
        body: JSON.stringify({ user_id: user.id, bag_type: bagType, tag_id: tag }),
      });
      setResult(`Created bag tag: ${tag} (${bagType})`);
    } catch (err) {
      setResult(err instanceof ApiError ? `Failed: ${err.message}` : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-6 rounded-xl border border-dashed border-gray-300 p-4 text-sm">
      <button onClick={() => setOpen(!open)} className="font-medium text-gray-500">
        {open ? "▾" : "▸"} Quick setup (create a test bag — admin only)
      </button>
      {open && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {["organic", "polythene", "paper", "general"].map((bt) => (
            <button
              key={bt}
              disabled={busy}
              onClick={() => createTestBag(bt)}
              className="rounded-lg bg-gray-100 px-3 py-1.5 font-medium hover:bg-gray-200 disabled:opacity-50"
            >
              + {bt} bag
            </button>
          ))}
          {result && <span className="font-mono text-emerald-700">{result}</span>}
        </div>
      )}
    </div>
  );
}
