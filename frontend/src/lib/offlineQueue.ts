/**
 * Offline submission queue for the collector screen (Phase 4). Evening
 * collection routes hit dead zones — a session the collector just recorded
 * can't wait for a network response before the next doorstep. Every
 * createSession call is queued in IndexedDB first and synced in the
 * background; the collector never sees a spinner blocking their route.
 *
 * Native IndexedDB, no added dependency — the API surface needed here
 * (put/getAll/delete on one object store) doesn't justify pulling in a
 * wrapper library.
 */

import { createSession, generateUUID, type SessionCreateBody, type SessionDetail } from "./api";

const DB_NAME = "wastelens-offline";
const DB_VERSION = 1;
const STORE = "pending_sessions";

export interface QueuedSession {
  localId: string;
  body: SessionCreateBody;
  residentLabel: string;
  queuedAt: string;
  lastError?: string;
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "localId" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function queueSession(
  body: SessionCreateBody,
  residentLabel: string,
): Promise<QueuedSession> {
  const entry: QueuedSession = {
    localId: generateUUID(),
    body,
    residentLabel,
    queuedAt: new Date().toISOString(),
  };
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(entry);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
  return entry;
}

export async function listQueuedSessions(): Promise<QueuedSession[]> {
  const db = await openDb();
  const items = await new Promise<QueuedSession[]>((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const req = tx.objectStore(STORE).getAll();
    req.onsuccess = () => resolve(req.result as QueuedSession[]);
    req.onerror = () => reject(req.error);
  });
  db.close();
  return items.sort((a, b) => a.queuedAt.localeCompare(b.queuedAt));
}

async function removeQueuedSession(localId: string): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).delete(localId);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

async function markError(localId: string, message: string): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    const store = tx.objectStore(STORE);
    const req = store.get(localId);
    req.onsuccess = () => {
      const entry = req.result as QueuedSession | undefined;
      if (entry) store.put({ ...entry, lastError: message });
    };
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

export interface SyncResult {
  synced: string[];
  failed: string[];
}

/**
 * Attempts every queued session, oldest first. A session that fails is left
 * queued (with the error recorded) and sync stops there — later sessions in
 * the queue may depend on state the failed one would have created (e.g. a
 * bag tag first seen in an earlier stop), so skipping ahead risks
 * out-of-order writes.
 */
export async function syncQueuedSessions(
  onProgress?: (result: SessionDetail, queued: QueuedSession) => void,
): Promise<SyncResult> {
  const queued = await listQueuedSessions();
  const result: SyncResult = { synced: [], failed: [] };
  for (const item of queued) {
    try {
      const created = await createSession(item.body);
      await removeQueuedSession(item.localId);
      result.synced.push(item.localId);
      onProgress?.(created, item);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Sync failed";
      await markError(item.localId, message);
      result.failed.push(item.localId);
      break;
    }
  }
  return result;
}
