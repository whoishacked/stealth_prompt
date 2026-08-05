import { parseStoredReport } from '../protocol/messages.js';
import type { StoredReport } from '../protocol/messages.js';

const DATABASE = 'stealth-prompt';
const STORE = 'direct-reports';
const MAX_REPORTS = 50;
const MAX_REPORT_BYTES = 8 * 1024 * 1024;
const DIRECT_REPORT_ID = /^direct-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

interface StoredDirectReport {
  reportId: string;
  createdAt: string;
  document: Record<string, unknown>;
}

export interface DirectReport extends StoredDirectReport {
  parsed: StoredReport;
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

/** IndexedDB is durable input too; validate every record before rendering it. */
export function parseDirectReport(value: unknown): DirectReport | null {
  const item = record(value);
  if (!item) return null;
  const reportId = item['reportId'];
  const createdAt = item['createdAt'];
  const document = record(item['document']);
  const parsed = parseStoredReport(document);
  if (
    typeof reportId !== 'string'
    || !DIRECT_REPORT_ID.test(reportId)
    || typeof createdAt !== 'string'
    || createdAt.length > 40
    || !document
    || !parsed
    || parsed.sessionId !== reportId
  ) return null;
  return { reportId, createdAt, document, parsed };
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE)) {
        request.result.createObjectStore(STORE, { keyPath: 'reportId' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('Could not open report storage.'));
  });
}

function complete(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error ?? new Error('Report storage failed.'));
    transaction.onabort = () => reject(transaction.error ?? new Error('Report storage was aborted.'));
  });
}

export async function saveDirectReport(value: StoredDirectReport): Promise<DirectReport> {
  const report = parseDirectReport(value);
  if (!report) throw new Error('The Direct API report is invalid.');
  if (new TextEncoder().encode(JSON.stringify(value)).length > MAX_REPORT_BYTES) {
    throw new Error('The Direct API report is too large for local history. Download it instead.');
  }
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE, 'readwrite');
    const store = transaction.objectStore(STORE);
    store.put(value);
    const all = store.getAll();
    all.onsuccess = () => {
      const overflow = all.result
        .map(parseDirectReport)
        .filter((item): item is DirectReport => item !== null)
        .sort((left, right) => right.createdAt.localeCompare(left.createdAt))
        .slice(MAX_REPORTS);
      for (const item of overflow) store.delete(item.reportId);
    };
    await complete(transaction);
    return report;
  } finally {
    database.close();
  }
}

export async function listDirectReports(): Promise<DirectReport[]> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE, 'readonly');
    const request = transaction.objectStore(STORE).getAll();
    const values = await new Promise<unknown[]>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error('Could not read report history.'));
    });
    return values
      .map(parseDirectReport)
      .filter((item): item is DirectReport => item !== null)
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt))
      .slice(0, MAX_REPORTS);
  } finally {
    database.close();
  }
}

export async function deleteDirectReport(reportId: string): Promise<void> {
  if (!DIRECT_REPORT_ID.test(reportId)) throw new Error('The Direct API report id is invalid.');
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE, 'readwrite');
    transaction.objectStore(STORE).delete(reportId);
    await complete(transaction);
  } finally {
    database.close();
  }
}
