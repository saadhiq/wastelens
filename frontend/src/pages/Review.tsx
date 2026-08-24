import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  apiFetch,
  bulkReviewDetections,
  getReviewQueue,
  getReviewStats,
  getVocabulary,
  listUnmappedLabels,
  promoteUnmappedLabel,
  reviewDetection,
  type ReviewQueueItem,
  type UnmappedLabel,
  type VocabularyItem,
} from "../lib/api";

/**
 * Review console (Phase 3): tray image + flagged detections grouped by
 * capture, confirm/correct/reject with keyboard shortcuts, a bulk-confirm
 * bar, and an unmapped-label inbox. Reviewers do hundreds of these, so
 * everything that can be a single tap or keypress is.
 */

interface CaptureGroup {
  capture_id: string;
  capture_image_url: string;
  capture_bag_type: string;
  captured_at: string;
  detections: ReviewQueueItem[];
}

function groupByCapture(items: ReviewQueueItem[]): CaptureGroup[] {
  const order: string[] = [];
  const map = new Map<string, CaptureGroup>();
  for (const item of items) {
    if (!map.has(item.capture_id)) {
      map.set(item.capture_id, {
        capture_id: item.capture_id,
        capture_image_url: item.capture_image_url,
        capture_bag_type: item.capture_bag_type,
        captured_at: item.captured_at,
        detections: [],
      });
      order.push(item.capture_id);
    }
    map.get(item.capture_id)!.detections.push(item);
  }
  return order.map((id) => map.get(id)!);
}

export default function Review() {
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => apiFetch<{ role: string }>("/auth/me") });
  const isAdmin = me.data?.role === "admin";

  const queue = useQuery({
    queryKey: ["review-queue"],
    queryFn: () => getReviewQueue(50, 0),
  });
  const stats = useQuery({ queryKey: ["review-stats"], queryFn: getReviewStats });

  // Detections handled locally since the last refetch — removed from view
  // immediately so a reviewer isn't stuck looking at their own last action.
  const [handledIds, setHandledIds] = useState<Set<string>>(new Set());
  const groups = useMemo(() => {
    const items = (queue.data?.items ?? []).filter((d) => !handledIds.has(d.id));
    return groupByCapture(items);
  }, [queue.data, handledIds]);

  const [groupIndex, setGroupIndex] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [correcting, setCorrecting] = useState(false);

  const group = groups[groupIndex];
  const detections = group?.detections ?? [];
  const selected = detections.find((d) => d.id === selectedId) ?? detections[0] ?? null;

  // Keep selection valid as the group/queue changes.
  useEffect(() => {
    if (detections.length > 0 && !detections.some((d) => d.id === selectedId)) {
      setSelectedId(detections[0].id);
    }
    if (detections.length === 0 && groupIndex >= groups.length && groups.length > 0) {
      setGroupIndex(groups.length - 1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group?.capture_id, groups.length]);

  const vocabulary = useQuery({
    queryKey: ["vocabulary", group?.capture_bag_type],
    queryFn: () => getVocabulary(group!.capture_bag_type),
    enabled: !!group,
  });

  function refetchAll() {
    queryClient.invalidateQueries({ queryKey: ["review-queue"] });
    queryClient.invalidateQueries({ queryKey: ["review-stats"] });
    setHandledIds(new Set());
  }

  async function act(
    detection: ReviewQueueItem,
    verdict: "CONFIRMED" | "CORRECTED" | "REJECTED",
    correctedItemName?: string,
  ) {
    try {
      await reviewDetection(detection.id, {
        verdict,
        corrected_item_name: verdict === "CORRECTED" ? correctedItemName : undefined,
      });
      setHandledIds((prev) => new Set(prev).add(detection.id));
      setCorrecting(false);
      queryClient.invalidateQueries({ queryKey: ["review-stats"] });
    } catch (err) {
      alert(err instanceof ApiError ? err.message : "Review failed");
    }
  }

  function moveSelection(delta: number) {
    if (detections.length === 0) return;
    const idx = detections.findIndex((d) => d.id === selected?.id);
    const next = Math.max(0, Math.min(detections.length - 1, idx + delta));
    setSelectedId(detections[next].id);
  }

  function moveGroup(delta: number) {
    setGroupIndex((i) => Math.max(0, Math.min(groups.length - 1, i + delta)));
    setSelectedId(null);
  }

  // --- Keyboard shortcuts: C confirm, R reject, E edit, arrows navigate ---
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || correcting) {
        if (e.key === "Escape") setCorrecting(false);
        return;
      }
      if (!selected) return;
      if (e.key === "c" || e.key === "C") act(selected, "CONFIRMED");
      else if (e.key === "r" || e.key === "R") act(selected, "REJECTED");
      else if (e.key === "e" || e.key === "E") setCorrecting(true);
      else if (e.key === "ArrowDown") moveSelection(1);
      else if (e.key === "ArrowUp") moveSelection(-1);
      else if (e.key === "ArrowRight") moveGroup(1);
      else if (e.key === "ArrowLeft") moveGroup(-1);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  if (queue.isLoading) return <div className="p-8 text-gray-500">Loading review queue…</div>;
  if (queue.error instanceof ApiError && queue.error.status === 403) {
    return (
      <div className="p-8 text-gray-600">
        The review console requires the <b>reviewer</b> or <b>admin</b> role.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 sm:p-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-emerald-700">Review</h1>
        {stats.data && (
          <div className="flex gap-4 text-sm text-gray-600">
            <span>
              Reviewed today: <b className="text-gray-900">{stats.data.reviewed_today}</b>
            </span>
            <span>
              Agreement rate:{" "}
              <b className="text-gray-900">{(stats.data.agreement_rate * 100).toFixed(0)}%</b>
            </span>
          </div>
        )}
      </div>

      <BulkConfirmBar
        allItems={queue.data?.items ?? []}
        handledIds={handledIds}
        onDone={refetchAll}
      />

      {groups.length === 0 ? (
        <div className="rounded-xl bg-white p-8 text-center text-gray-500 shadow">
          Queue is empty — nothing needs review right now. 🎉
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <TrayImage
            group={group}
            detections={detections}
            selectedId={selected?.id ?? null}
            onSelect={setSelectedId}
          />

          <div className="space-y-3">
            <div className="flex items-center justify-between text-sm text-gray-500">
              <span>
                Tray {groupIndex + 1} of {groups.length} · {detections.length} item
                {detections.length === 1 ? "" : "s"} to review
              </span>
              <div className="flex gap-1">
                <button
                  onClick={() => moveGroup(-1)}
                  disabled={groupIndex === 0}
                  className="rounded-md border border-gray-300 px-2 py-1 disabled:opacity-30"
                >
                  ← prev tray
                </button>
                <button
                  onClick={() => moveGroup(1)}
                  disabled={groupIndex === groups.length - 1}
                  className="rounded-md border border-gray-300 px-2 py-1 disabled:opacity-30"
                >
                  next tray →
                </button>
              </div>
            </div>

            {detections.map((d) => (
              <DetectionRow
                key={d.id}
                detection={d}
                selected={d.id === selected?.id}
                correcting={correcting && d.id === selected?.id}
                vocabulary={vocabulary.data?.items ?? []}
                onSelect={() => setSelectedId(d.id)}
                onConfirm={() => act(d, "CONFIRMED")}
                onReject={() => act(d, "REJECTED")}
                onStartCorrect={() => {
                  setSelectedId(d.id);
                  setCorrecting(true);
                }}
                onSubmitCorrect={(name) => act(d, "CORRECTED", name)}
                onCancelCorrect={() => setCorrecting(false)}
              />
            ))}

            <KeyboardHint />
          </div>
        </div>
      )}

      <UnmappedInbox isAdmin={isAdmin} />
    </div>
  );
}

function TrayImage({
  group,
  detections,
  selectedId,
  onSelect,
}: {
  group: CaptureGroup;
  detections: ReviewQueueItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  // bbox_x/y/w/h are raw pixel coordinates in the original image, not
  // percentages — scale against the image's own natural size (read once
  // it loads) so the overlay stays correctly positioned at any rendered
  // width. No detection has these populated yet (the vision pipeline
  // doesn't estimate geometry — see schemas/captures.py), so this renders
  // nothing today; it's ready for when it does.
  const [naturalSize, setNaturalSize] = useState<{ w: number; h: number } | null>(null);

  return (
    <div className="relative overflow-hidden rounded-xl bg-white shadow">
      <div className="relative">
        <img
          src={group.capture_image_url}
          alt="tray"
          className="w-full object-contain"
          onLoad={(e) => {
            const img = e.currentTarget;
            setNaturalSize({ w: img.naturalWidth, h: img.naturalHeight });
          }}
        />
        {naturalSize &&
          detections.map((d) => {
            if (d.bbox_x == null || d.bbox_y == null || d.bbox_w == null || d.bbox_h == null)
              return null;
            const isSelected = d.id === selectedId;
            return (
              <button
                key={d.id}
                onClick={() => onSelect(d.id)}
                className={`absolute border-2 ${isSelected ? "border-emerald-500 bg-emerald-500/10" : "border-amber-400 bg-amber-400/5"}`}
                style={{
                  left: `${(d.bbox_x / naturalSize.w) * 100}%`,
                  top: `${(d.bbox_y / naturalSize.h) * 100}%`,
                  width: `${(d.bbox_w / naturalSize.w) * 100}%`,
                  height: `${(d.bbox_h / naturalSize.h) * 100}%`,
                }}
                title={d.item_name}
              />
            );
          })}
      </div>
      <div className="flex items-center justify-between border-t px-4 py-2 text-xs text-gray-500">
        <span>{group.capture_bag_type}</span>
        <span>{new Date(group.captured_at).toLocaleString()}</span>
      </div>
    </div>
  );
}

function DetectionRow({
  detection,
  selected,
  correcting,
  vocabulary,
  onSelect,
  onConfirm,
  onReject,
  onStartCorrect,
  onSubmitCorrect,
  onCancelCorrect,
}: {
  detection: ReviewQueueItem;
  selected: boolean;
  correcting: boolean;
  vocabulary: VocabularyItem[];
  onSelect: () => void;
  onConfirm: () => void;
  onReject: () => void;
  onStartCorrect: () => void;
  onSubmitCorrect: (name: string) => void;
  onCancelCorrect: () => void;
}) {
  const [correctedName, setCorrectedName] = useState(detection.item_name);

  return (
    <div
      onClick={onSelect}
      className={`cursor-pointer rounded-lg border p-3 ${selected ? "border-emerald-500 ring-1 ring-emerald-500" : "border-gray-200"}`}
    >
      <div className="flex items-center justify-between">
        <div>
          <span className="font-semibold">{detection.item_name.replaceAll("_", " ")}</span>
          <span className="ml-2 rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500">
            {detection.queue_reason.replaceAll("_", " ")}
          </span>
        </div>
        <span className="text-sm tabular-nums text-gray-500">
          {(detection.confidence * 100).toFixed(0)}%
        </span>
      </div>
      {detection.brand_text && (
        <p className="mt-1 text-sm text-gray-600">
          Brand text: <span className="italic">“{detection.brand_text}”</span>
        </p>
      )}

      {correcting ? (
        <div className="mt-3 space-y-2" onClick={(e) => e.stopPropagation()}>
          <input
            list={`vocab-${detection.id}`}
            value={correctedName}
            onChange={(e) => setCorrectedName(e.target.value)}
            autoFocus
            className="w-full rounded-md border border-gray-300 p-2 text-sm"
            placeholder="Correct item name…"
          />
          <datalist id={`vocab-${detection.id}`}>
            {vocabulary.map((v) => (
              <option key={v.id} value={v.item_name} />
            ))}
          </datalist>
          <div className="flex gap-2">
            <button
              onClick={() => onSubmitCorrect(correctedName)}
              disabled={!correctedName.trim()}
              className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
            >
              Save correction
            </button>
            <button
              onClick={onCancelCorrect}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex gap-2">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onConfirm();
            }}
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700"
          >
            Confirm (C)
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onStartCorrect();
            }}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium hover:bg-gray-50"
          >
            Correct (E)
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onReject();
            }}
            className="rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50"
          >
            Reject (R)
          </button>
        </div>
      )}
    </div>
  );
}

function KeyboardHint() {
  return (
    <p className="text-xs text-gray-400">
      Shortcuts: <kbd className="rounded border px-1">C</kbd> confirm ·{" "}
      <kbd className="rounded border px-1">R</kbd> reject ·{" "}
      <kbd className="rounded border px-1">E</kbd> edit ·{" "}
      <kbd className="rounded border px-1">↑↓</kbd> select item ·{" "}
      <kbd className="rounded border px-1">←→</kbd> change tray
    </p>
  );
}

function BulkConfirmBar({
  allItems,
  handledIds,
  onDone,
}: {
  allItems: ReviewQueueItem[];
  handledIds: Set<string>;
  onDone: () => void;
}) {
  const [threshold, setThreshold] = useState(90);
  const [busy, setBusy] = useState(false);

  const eligible = allItems.filter((d) => !handledIds.has(d.id) && d.confidence * 100 >= threshold);

  async function confirmAll() {
    if (eligible.length === 0) return;
    setBusy(true);
    try {
      await bulkReviewDetections(
        eligible.map((d) => d.id),
        "CONFIRMED",
      );
      onDone();
    } catch (err) {
      alert(err instanceof ApiError ? err.message : "Bulk confirm failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl bg-white p-3 text-sm shadow">
      <span className="text-gray-600">Bulk confirm all above</span>
      <input
        type="number"
        min={0}
        max={100}
        value={threshold}
        onChange={(e) => setThreshold(Number(e.target.value))}
        className="w-16 rounded-md border border-gray-300 p-1 text-center"
      />
      <span className="text-gray-600">% confidence</span>
      <button
        onClick={confirmAll}
        disabled={busy || eligible.length === 0}
        className="rounded-md bg-emerald-600 px-3 py-1.5 font-medium text-white disabled:opacity-40"
      >
        {busy
          ? "Confirming…"
          : `Confirm ${eligible.length} item${eligible.length === 1 ? "" : "s"}`}
      </button>
    </div>
  );
}

function UnmappedInbox({ isAdmin }: { isAdmin: boolean }) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const unmapped = useQuery({
    queryKey: ["unmapped-labels"],
    queryFn: () => listUnmappedLabels(false),
    enabled: open,
  });
  const [promoting, setPromoting] = useState<string | null>(null);

  async function promote(label: UnmappedLabel) {
    setPromoting(label.id);
    try {
      await promoteUnmappedLabel(label.id);
      queryClient.invalidateQueries({ queryKey: ["unmapped-labels"] });
    } catch (err) {
      alert(err instanceof ApiError ? err.message : "Promote failed");
    } finally {
      setPromoting(null);
    }
  }

  return (
    <div className="rounded-xl bg-white shadow">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between px-4 py-3 text-left font-semibold"
      >
        <span>{open ? "▾" : "▸"} Unmapped-label inbox</span>
        {unmapped.data && (
          <span className="text-sm text-gray-500">{unmapped.data.total} pending</span>
        )}
      </button>
      {open && (
        <div className="border-t px-4 py-3">
          {!isAdmin && (
            <p className="mb-2 text-xs text-amber-700">
              Only admins can promote a label — you can still see what's pending.
            </p>
          )}
          {unmapped.isLoading && <p className="text-sm text-gray-500">Loading…</p>}
          {unmapped.data?.items.length === 0 && (
            <p className="text-sm text-gray-500">Nothing unmapped right now.</p>
          )}
          <ul className="divide-y">
            {unmapped.data?.items.map((label) => (
              <li key={label.id} className="flex items-center justify-between py-2 text-sm">
                <div>
                  <span className="font-medium">{label.raw_label}</span>
                  <span className="ml-2 text-gray-500">
                    {label.bag_type} · seen {label.occurrence_count}×
                  </span>
                </div>
                {isAdmin && (
                  <button
                    onClick={() => promote(label)}
                    disabled={promoting === label.id}
                    className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium hover:bg-gray-50 disabled:opacity-40"
                  >
                    {promoting === label.id ? "Promoting…" : "Promote to vocabulary"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
