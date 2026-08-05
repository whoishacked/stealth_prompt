/**
 * The service worker: routing and durable state, no UI and no policy.
 *
 * It never holds session state in a global — a worker is evicted freely, so
 * anything that must survive lives in `chrome.storage`. Its jobs are to inject
 * the content script on demand, relay one operation at a time to the *bound*
 * tab, and remember which tab that is.
 */

import type { Locator } from '../protocol/messages.js';
import { isBrowserOperation } from '../protocol/messages.js';
import {
  parseDirectCompletion,
  parseDirectModels,
  parseDirectProvider,
} from '../direct/provider.js';
import type { DirectProvider } from '../direct/provider.js';

const SESSION_KEY = 'sp.session';
const LOCAL_KEY = 'sp.local';

/**
 * Operations that change the target document. In `payload_only` mode none of
 * them may run: the panel hides its send button, but the worker is the single
 * chokepoint every mutation passes through, so the guarantee is enforced here
 * rather than left to the UI.
 */
const MUTATING_OPERATIONS = new Set(['fill', 'submit']);

const DIRECT_API = {
  openai: {
    origin: 'https://api.openai.com',
    models: '/v1/models',
    complete: '/v1/responses',
  },
  anthropic: {
    origin: 'https://api.anthropic.com',
    models: '/v1/models',
    complete: '/v1/messages',
  },
} as const;

const directRequests = new Map<string, AbortController>();

function directHeaders(provider: DirectProvider, key: string): Record<string, string> {
  if (provider === 'openai') {
    return { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' };
  }
  return {
    'x-api-key': key,
    'anthropic-version': '2023-06-01',
    // Anthropic requires explicit opt-in when a credential is used from a
    // browser-like client. The panel explains the risk before this can run.
    'anthropic-dangerous-direct-browser-access': 'true',
    'Content-Type': 'application/json',
  };
}

async function directFetch(
  provider: DirectProvider,
  key: string,
  path: string,
  init: RequestInit = {},
  signal?: AbortSignal,
): Promise<unknown> {
  if (!key || key.length > 512) throw new Error('Enter a valid API key.');
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  const timeout = setTimeout(abort, 120_000);
  try {
    const response = await fetch(`${DIRECT_API[provider].origin}${path}`, {
      ...init,
      credentials: 'omit',
      headers: { ...directHeaders(provider, key), ...(init.headers ?? {}) },
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`${provider} API returned HTTP ${response.status}.`);
    return (await response.json()) as unknown;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}

async function directModels(provider: DirectProvider, key: string): Promise<string[]> {
  const document = await directFetch(provider, key, DIRECT_API[provider].models);
  return parseDirectModels(provider, document);
}

async function directComplete(
  provider: DirectProvider,
  key: string,
  model: string,
  prompt: string,
  signal?: AbortSignal,
): Promise<{ text: string; model: string; usage: unknown }> {
  if (!model || model.length > 160 || /[\r\n\0]/.test(model)) throw new Error('Choose a valid model.');
  if (!prompt || new TextEncoder().encode(prompt).length > 64 * 1024) {
    throw new Error('Prompt is empty or exceeds 64 KiB.');
  }
  const body =
    provider === 'openai'
      ? { model, input: prompt, store: false, max_output_tokens: 2_000 }
      : { model, max_tokens: 2_000, messages: [{ role: 'user', content: prompt }] };
  const document = await directFetch(provider, key, DIRECT_API[provider].complete, {
    method: 'POST',
    body: JSON.stringify(body),
  }, signal);
  return parseDirectCompletion(provider, document, model);
}

/** The reviewed mode, read from durable storage rather than trusted per call. */
async function readMode(): Promise<string> {
  const stored = await chrome.storage.local.get(LOCAL_KEY);
  const local = stored[LOCAL_KEY];
  if (typeof local !== 'object' || local === null) return 'assist';
  const settings = (local as Record<string, unknown>)['settings'];
  if (typeof settings !== 'object' || settings === null) return 'assist';
  const mode = (settings as Record<string, unknown>)['mode'];
  return typeof mode === 'string' ? mode : 'assist';
}

interface WorkerSession {
  tabId: number | null;
  origin: string;
  documentId: string;
}

async function readSession(): Promise<WorkerSession> {
  const stored = await chrome.storage.session.get(SESSION_KEY);
  const value = stored[SESSION_KEY];
  if (typeof value === 'object' && value !== null) return value as WorkerSession;
  return { tabId: null, origin: '', documentId: '' };
}

async function writeSession(patch: Partial<WorkerSession>): Promise<WorkerSession> {
  const next = { ...(await readSession()), ...patch };
  await chrome.storage.session.set({ [SESSION_KEY]: next });
  return next;
}

/** Inject the executor, tolerating an already-injected document. */
async function ensureContentScript(tabId: number): Promise<void> {
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ['content.js'],
  });
}

async function relay(
  tabId: number,
  message: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  try {
    return (await chrome.tabs.sendMessage(tabId, message)) as Record<string, unknown>;
  } catch {
    // A fresh document has no listener yet; inject once and retry.
    await ensureContentScript(tabId);
    return (await chrome.tabs.sendMessage(tabId, message)) as Record<string, unknown>;
  }
}

/**
 * Open the panel ourselves from the toolbar action.
 *
 * Letting Chrome open it through `openPanelOnActionClick` looks equivalent but
 * doesn't reliably leave the worker with `activeTab` access. The result is a
 * tab id with a redacted `tab.url`, making "Use current tab" impossible. The
 * explicit action callback is the user gesture that grants `activeTab`; it
 * also gives us the exact target tab before the Side Panel exists.
 */
void chrome.sidePanel?.setPanelBehavior?.({ openPanelOnActionClick: false });

chrome.runtime.onInstalled.addListener(() => {
  void chrome.sidePanel?.setPanelBehavior?.({ openPanelOnActionClick: false });
});

chrome.action.onClicked.addListener((tab) => {
  if (tab.id === undefined) return;

  // Call open synchronously from the action callback. An awaited storage write
  // before this call would consume the transient user gesture.
  void chrome.sidePanel.open({ tabId: tab.id });

  if (!tab.url) return;
  try {
    const url = new URL(tab.url);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return;
    void writeSession({ tabId: tab.id, origin: url.origin, documentId: '' });
  } catch {
    /* restricted and non-URL tabs cannot be targets */
  }
});

/**
 * True only for our own extension pages (the Side Panel).
 *
 * The discriminator is the sender's origin, which the browser stamps and a page
 * cannot forge: a content script always reports the *page's* origin, while an
 * extension page reports `chrome-extension://<our id>`. Testing for the absence
 * of `sender.tab` would be wrong in both directions — an extension page opened
 * in a tab has one, and the origin check is what actually keeps a target page
 * from issuing operations.
 */
function fromOwnExtensionPage(sender: chrome.runtime.MessageSender): boolean {
  if (sender.id !== chrome.runtime.id) return false;
  const self = `chrome-extension://${chrome.runtime.id}`;
  if (typeof sender.origin === 'string') return sender.origin === self;
  // Older Chrome without `sender.origin`: fall back to the sender's own URL.
  return typeof sender.url === 'string' && sender.url.startsWith(`${self}/`);
}

chrome.runtime.onMessage.addListener((message: unknown, sender, sendResponse) => {
  if (typeof message !== 'object' || message === null) return false;
  const request = message as Record<string, unknown>;
  if (request['channel'] !== 'sp-panel') return false;
  if (!fromOwnExtensionPage(sender)) {
    sendResponse({ ok: false, message: 'operations may not originate in a page' });
    return false;
  }

  void (async () => {
    try {
      const kind = String(request['kind'] ?? '');
      if (kind === 'direct-cancel') {
        const requestId = typeof request['requestId'] === 'string' ? request['requestId'] : '';
        directRequests.get(requestId)?.abort();
        sendResponse({ ok: true });
        return;
      }

      if (kind === 'direct-models' || kind === 'direct-complete') {
        const provider = parseDirectProvider(request['provider']);
        if (!provider) {
          sendResponse({ ok: false, message: 'Direct provider is not allowed.' });
          return;
        }
        const key = typeof request['key'] === 'string' ? request['key'] : '';
        if (kind === 'direct-models') {
          sendResponse({ ok: true, models: await directModels(provider, key) });
          return;
        }
        const requestId = typeof request['requestId'] === 'string' ? request['requestId'] : '';
        const controller = new AbortController();
        if (requestId) directRequests.set(requestId, controller);
        try {
          const result = await directComplete(
            provider,
            key,
            typeof request['model'] === 'string' ? request['model'] : '',
            typeof request['prompt'] === 'string' ? request['prompt'] : '',
            controller.signal,
          );
          sendResponse({ ok: true, ...result });
        } finally {
          if (requestId) directRequests.delete(requestId);
        }
        return;
      }

      if (kind === 'bind-tab') {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab?.id) {
          sendResponse({
            ok: false,
            message:
              'Cannot access the current tab. Open the target page and click the Stealth Prompt toolbar icon before selecting it.',
          });
          return;
        }
        // The explicit toolbar action stores the reviewed origin while
        // `activeTab` is unquestionably present. Reuse it if Chrome redacts
        // tab.url from a later Side Panel message.
        const previous = await readSession();
        const visibleUrl = tab.url ?? '';
        let origin = previous.tabId === tab.id ? previous.origin : '';
        if (visibleUrl) {
          const url = new URL(visibleUrl);
          if (url.protocol === 'http:' || url.protocol === 'https:') origin = url.origin;
        }
        if (!origin) {
          sendResponse({
            ok: false,
            message:
              'Cannot access this tab. Activate the HTTP(S) target and click the Stealth Prompt toolbar icon once more.',
          });
          return;
        }
        const session = await writeSession({ tabId: tab.id, origin, documentId: '' });
        sendResponse({ ok: true, tabId: session.tabId, origin: session.origin });
        return;
      }

      if (kind === 'operation') {
        if (!isBrowserOperation(request['operation'])) {
          sendResponse({ ok: false, message: 'operation is not allowed' });
          return;
        }
        const operation = String(request['operation']);
        if (MUTATING_OPERATIONS.has(operation) && (await readMode()) === 'payload_only') {
          sendResponse({
            ok: false,
            message: `payload-only mode does not ${operation} the page`,
          });
          return;
        }
        const session = await readSession();
        if (session.tabId === null) {
          sendResponse({ ok: false, message: 'no target tab is bound' });
          return;
        }

        // Revalidate immediately before a mutation, at the same chokepoint the
        // mode check uses. The panel also tracks binding health, but a check
        // that ran seconds ago against a different document proves nothing: the
        // page may have re-rendered in between. Acting on a stale or ambiguous
        // locator is exactly the failure this gate exists to prevent, so a
        // failed or unreadable check blocks the operation.
        if (MUTATING_OPERATIONS.has(operation)) {
          const check = await relay(session.tabId, {
            channel: 'stealth-prompt',
            operation: 'validate',
            binding: request['binding'] ?? {},
          });
          if (!check?.['ok']) {
            sendResponse({
              ok: false,
              stale: true,
              roles: check?.['roles'] ?? {},
              message: String(
                check?.['message'] ??
                  'The interaction binding no longer matches this page. Re-detect the elements before sending.',
              ),
            });
            return;
          }
        }

        const result = await relay(session.tabId, {
          channel: 'stealth-prompt',
          operation: request['operation'],
          operationId: request['operationId'] ?? '',
          value: request['value'] ?? '',
          locator: (request['locator'] ?? null) as Locator | null,
          binding: request['binding'] ?? {},
          submitStrategy: request['submitStrategy'] ?? 'click_button',
          submitKey: request['submitKey'] ?? 'Enter',
          stableMs: request['stableMs'] ?? 1500,
          timeoutMs: request['timeoutMs'] ?? 60000,
          role: request['role'] ?? '',
        });
        sendResponse(result);
        return;
      }

      if (kind === 'get-state') {
        const local = await chrome.storage.local.get(LOCAL_KEY);
        sendResponse({ ok: true, session: await readSession(), local: local[LOCAL_KEY] ?? null });
        return;
      }

      if (kind === 'put-state') {
        await chrome.storage.local.set({ [LOCAL_KEY]: request['state'] ?? null });
        sendResponse({ ok: true });
        return;
      }

      sendResponse({ ok: false, message: `unknown request ${kind}` });
    } catch (error) {
      // Keep the reason: "Could not establish connection" and "Cannot access
      // contents of the page" are exactly what the operator needs to act on.
      const raw = (error as Error)?.message ?? String(error ?? 'operation failed');
      const flat = raw.replace(/\s+/g, ' ').trim() || 'operation failed';
      sendResponse({ ok: false, message: flat.length > 200 ? `${flat.slice(0, 200)}…` : flat });
    }
  })();
  return true;
});

// A navigation replaces the document but not the session. Record the new
// document id so stale results can be rejected; never clear the session.
//
// `onUpdated` fires for same-document history changes too, which is what makes
// SPA route changes observable without polling the DOM or asking for broader
// permissions.
chrome.tabs?.onUpdated?.addListener((tabId, info) => {
  void (async () => {
    const session = await readSession();
    if (session.tabId !== tabId || !info.url) return;
    try {
      const next = await writeSession({
        origin: new URL(info.url).origin,
        documentId: String(Date.now()),
      });
      // Tell the panel to revalidate. A panel that is closed has no listener,
      // and that is not an error: it revalidates when it next opens.
      await chrome.runtime
        .sendMessage({
          channel: 'sp-worker',
          kind: 'target-changed',
          tabId,
          origin: next.origin,
          documentId: next.documentId,
        })
        .catch(() => undefined);
    } catch {
      /* a non-URL navigation is not interesting */
    }
  })();
});
