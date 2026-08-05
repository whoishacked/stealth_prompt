/**
 * The content script: an ephemeral DOM observer and executor.
 *
 * It is not the product UI. It holds no session, no provider, no credential,
 * and no connection to the Core. It performs one named operation at a time on
 * behalf of the service worker and reports what it saw.
 *
 * The page it runs in is hostile. Two consequences shape everything here:
 *
 *  - nothing the page does is treated as a command. The only inbound channel is
 *    `chrome.runtime.onMessage`, which a page cannot post to;
 *  - nothing is rendered from model or page text as markup. The picker overlay
 *    is built with `document.createElement` and `textContent` only.
 */

import type { BrowserOperation, Locator } from '../protocol/messages.js';
import { isBrowserOperation } from '../protocol/messages.js';

/** Keys a submit may use. Anything that could trigger a browser shortcut is out. */
const ALLOWED_KEYS = new Set(['Enter', 'Shift+Enter', 'Tab', 'Escape']);

const HIGHLIGHT_ID = '__stealth_prompt_highlight__';
const PICKER_ID = '__stealth_prompt_picker__';
const PICK_TIMEOUT_MS = 45_000;

interface OperationRequest {
  operation: BrowserOperation;
  operationId?: string;
  locator?: Locator | null;
  value?: string;
  submitStrategy?: 'click_button' | 'press_key';
  submitKey?: string;
  stableMs?: number;
  timeoutMs?: number;
  role?: 'input' | 'submit' | 'response';
}

/** What discovery concluded about one role, on its own evidence. */
interface RoleSuggestion {
  locator: Locator | null;
  confidence: number;
  reason: string;
}

/** Whether one bound role still resolves, and why not when it does not. */
interface RoleValidation {
  ok: boolean;
  reason: string;
  matches: number;
}

interface OperationResult {
  ok: boolean;
  operationId?: string;
  message?: string;
  locator?: Locator;
  text?: string;
  count?: number;
  documentId?: string;
  url?: string;
  suggestion?: {
    input: RoleSuggestion;
    submit: RoleSuggestion;
    response: RoleSuggestion;
    missing: string[];
  };
  roles?: Record<string, RoleValidation>;
}

/* --------------------------------------------------------------- locators */

function accessibleName(element: Element): string {
  const aria = element.getAttribute('aria-label');
  if (aria) return aria.trim();
  const labelledBy = element.getAttribute('aria-labelledby');
  if (labelledBy) {
    const ref = document.getElementById(labelledBy);
    if (ref) return (ref.textContent ?? '').trim();
  }
  if (element.id) {
    const label = document.querySelector(`label[for="${CSS.escape(element.id)}"]`);
    if (label) return (label.textContent ?? '').trim();
  }
  const text = (element.textContent ?? '').trim();
  return text.length > 0 && text.length <= 80 ? text : '';
}

function implicitRole(element: Element): string {
  const tag = element.tagName.toLowerCase();
  if (tag === 'button') return 'button';
  if (tag === 'textarea') return 'textbox';
  if (tag === 'a' && element.hasAttribute('href')) return 'link';
  if (tag === 'input') {
    const type = (element.getAttribute('type') ?? 'text').toLowerCase();
    if (type === 'submit' || type === 'button') return 'button';
    if (['text', 'search', 'email', 'url', 'tel'].includes(type)) return 'textbox';
  }
  if ((element as HTMLElement).isContentEditable) return 'textbox';
  return element.getAttribute('role') ?? '';
}

function stableClasses(element: Element): string[] {
  if (!element.classList) return [];
  return Array.from(element.classList).filter((name) => !/\d/.test(name));
}

/**
 * A CSS selector for an element.
 *
 * For a response container this must keep matching when the *next* reply
 * arrives, so a class selector shared with siblings is preferred over a
 * positional one.
 */
function cssPath(element: Element): string {
  if (element.id) return `#${CSS.escape(element.id)}`;
  const testId = element.getAttribute('data-testid');
  if (testId) return `[data-testid="${CSS.escape(testId)}"]`;

  const classes = stableClasses(element);
  if (classes.length) {
    const selector = '.' + classes.map((name) => CSS.escape(name)).join('.');
    try {
      const matches = document.querySelectorAll(selector);
      if (matches.length === 1 && matches[0] === element) {
        return selector;
      }
    } catch {
      /* fall through */
    }
  }

  const parts: string[] = [];
  let node: Element | null = element;
  while (node && node.nodeType === 1 && parts.length < 12) {
    if (node.id) {
      parts.unshift(`#${CSS.escape(node.id)}`);
      return parts.join(' > ');
    }
    let part = node.tagName.toLowerCase();
    const stable = stableClasses(node)[0];
    if (stable) part += `.${CSS.escape(stable)}`;
    const parent: Element | null = node.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children).filter((c) => c.tagName === node!.tagName);
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
    }
    parts.unshift(part);
    const selector = parts.join(' > ');
    try {
      if (document.querySelectorAll(selector).length === 1) return selector;
    } catch {
      /* keep walking toward a stable ancestor */
    }
    node = node.parentElement;
  }
  return parts.join(' > ');
}

export function computeLocator(element: Element): Locator {
  const role = implicitRole(element);
  const name = accessibleName(element);
  const placeholder = element.getAttribute('placeholder');
  const testId = element.getAttribute('data-testid');
  const fallback = cssPath(element);

  if (role && name) return { strategy: 'role', value: role, name, css_fallback: fallback };
  if (element.id) {
    const label = document.querySelector(`label[for="${CSS.escape(element.id)}"]`);
    const text = (label?.textContent ?? '').trim();
    if (text) return { strategy: 'label', value: text, css_fallback: fallback };
  }
  if (placeholder) return { strategy: 'placeholder', value: placeholder, css_fallback: fallback };
  if (testId) return { strategy: 'test_id', value: testId, css_fallback: fallback };
  return { strategy: 'css', value: fallback, css_fallback: fallback };
}

function resolveAllExact(locator: Locator | null | undefined): Element[] {
  if (!locator) return [];
  try {
    switch (locator.strategy) {
      case 'role': {
        // Narrow the scan by candidate tag before the expensive name check.
        const candidates = document.querySelectorAll(
          'button,textarea,input,a,[role],[contenteditable]',
        );
        return Array.from(candidates).filter(
          (element) =>
            implicitRole(element) === locator.value && accessibleName(element) === locator.name,
        );
      }
      case 'label': {
        const labels = Array.from(document.querySelectorAll('label'));
        const match = labels.find((label) => (label.textContent ?? '').trim() === locator.value);
        if (!match) return [];
        const target = match.getAttribute('for');
        const element = target
          ? document.getElementById(target)
          : match.querySelector('input,textarea');
        return element ? [element] : [];
      }
      case 'placeholder':
        return Array.from(document.querySelectorAll(`[placeholder="${CSS.escape(locator.value)}"]`));
      case 'test_id':
        return Array.from(document.querySelectorAll(`[data-testid="${CSS.escape(locator.value)}"]`));
      case 'css':
      default:
        return Array.from(document.querySelectorAll(locator.value));
    }
  } catch {
    return [];
  }
}

export function resolveAll(locator: Locator | null | undefined): Element[] {
  const matches = resolveAllExact(locator);
  if (
    matches.length ||
    !locator?.css_fallback ||
    (locator.strategy === 'css' && locator.value === locator.css_fallback)
  ) {
    return matches;
  }
  return resolveAllExact({ strategy: 'css', value: locator.css_fallback, css_fallback: null });
}

function resolveOne(locator: Locator | null | undefined, pick: 'first' | 'last' = 'first'): Element | null {
  const all = resolveAll(locator);
  if (!all.length) {
    // A locator can go stale after a redeploy; the recorded CSS is a documented
    // fallback rather than a guess at a different element.
    if (locator?.css_fallback) {
      try {
        const fallback = Array.from(document.querySelectorAll(locator.css_fallback));
        if (fallback.length) return pick === 'first' ? fallback[0]! : fallback[fallback.length - 1]!;
      } catch {
        return null;
      }
    }
    return null;
  }
  return pick === 'first' ? all[0]! : all[all.length - 1]!;
}

/* ------------------------------------------------------------- operations */

function fill(locator: Locator | null | undefined, value: string): OperationResult {
  const element = resolveOne(locator);
  if (!element) return { ok: false, message: 'input element not found' };
  const target = element as HTMLElement;
  target.focus();
  if (target.isContentEditable) {
    target.textContent = value;
  } else {
    const prototype =
      element instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, 'value');
    if (setter?.set) setter.set.call(element, value);
    else (element as HTMLInputElement).value = value;
  }
  element.dispatchEvent(new Event('input', { bubbles: true }));
  element.dispatchEvent(new Event('change', { bubbles: true }));
  lastFilledText = value.trim();
  return { ok: true };
}

function press(element: Element, key: string): void {
  const shift = key.startsWith('Shift+');
  const bare = shift ? key.slice('Shift+'.length) : key;
  (element as HTMLElement).focus();
  for (const type of ['keydown', 'keypress', 'keyup']) {
    element.dispatchEvent(
      new KeyboardEvent(type, { key: bare, code: bare, shiftKey: shift, bubbles: true, cancelable: true }),
    );
  }
  if (bare === 'Enter' && !shift) {
    const form = (element as HTMLElement).closest('form');
    if (form && typeof form.requestSubmit === 'function') form.requestSubmit();
  }
}

function submit(request: OperationRequest, binding: Record<string, Locator | null>): OperationResult {
  if (request.submitStrategy === 'press_key') {
    const key = request.submitKey ?? 'Enter';
    if (!ALLOWED_KEYS.has(key)) return { ok: false, message: `key ${key} is not allowed` };
    const element = resolveOne(binding['input']);
    if (!element) return { ok: false, message: 'input element not found' };
    press(element, key);
    return { ok: true };
  }
  const element = resolveOne(binding['submit']);
  if (!element) return { ok: false, message: 'send control not found' };
  (element as HTMLElement).click();
  return { ok: true };
}

/** State captured before a send, so the reply can be correlated to it. */
let snapshot: {
  count: number;
  text: string;
  roots: Element[];
  known: WeakSet<Element>;
} = { count: 0, text: '', roots: [], known: new WeakSet() };
let lastFilledText = '';

function takeSnapshot(locator: Locator | null | undefined): OperationResult {
  const all = resolveAll(locator);
  const roots = Array.from(
    new Set(all.flatMap((element) => [element, element.parentElement].filter(Boolean) as Element[])),
  );
  const known = new WeakSet<Element>();
  for (const root of roots) {
    known.add(root);
    root.querySelectorAll('*').forEach((element) => known.add(element));
  }
  snapshot = {
    count: all.length,
    text: all.length ? ((all[all.length - 1] as HTMLElement).innerText ?? '').trim() : '',
    roots,
    known,
  };
  return { ok: true, count: snapshot.count };
}

/** Return only the reply added after the snapshot, not the whole transcript. */
function newResponseText(locator: Locator | null | undefined): string {
  const all = resolveAll(locator);
  if (all.length > snapshot.count) {
    return ((all[all.length - 1] as HTMLElement).innerText ?? '').trim();
  }

  for (const root of snapshot.roots) {
    if (!root.isConnected) continue;
    const added = Array.from(root.querySelectorAll<HTMLElement>('*')).filter(
      (element) =>
        !snapshot.known.has(element) &&
        snapshot.known.has(element.parentElement as Element) &&
        !element.matches('button,input,textarea,form,script,style') &&
        visible(element),
    );
    const reply = added.at(-1);
    const text = (reply?.innerText ?? '').trim();
    if (text && !(added.length === 1 && text === lastFilledText)) return text;
  }

  const current = all.length ? ((all[all.length - 1] as HTMLElement).innerText ?? '').trim() : '';
  if (!current || current === snapshot.text) return '';
  const delta = current.startsWith(snapshot.text) ? current.slice(snapshot.text.length).trim() : current;
  return delta === lastFilledText ? '' : delta.replace(lastFilledText, '').trim();
}

/**
 * Wait for a new or changed reply whose text stops growing.
 *
 * A fixed sleep is not used as the completion signal: streamed replies pause,
 * and a pause is not an ending. The quiet period restarts whenever the text
 * changes, and a MutationObserver wakes the poll promptly.
 */
function capture(
  locator: Locator | null | undefined,
  stableMs: number,
  timeoutMs: number,
): Promise<OperationResult> {
  return new Promise((resolve) => {
    const startedAt = Date.now();
    const deadline = startedAt + Math.max(1000, timeoutMs);
    const quiet = Math.max(250, stableMs);
    let lastText = '';
    let stableSince = 0;
    let observer: MutationObserver | null = null;
    let timer: number | null = null;

    const finish = (result: OperationResult): void => {
      observer?.disconnect();
      if (timer !== null) clearTimeout(timer);
      resolve(result);
    };

    const tick = (): void => {
      const current = newResponseText(locator);
      if (current) {
        if (current === lastText) {
          if (stableSince && Date.now() - stableSince >= quiet) {
            finish({ ok: true, text: current });
            return;
          }
          if (!stableSince) stableSince = Date.now();
        } else {
          lastText = current;
          stableSince = Date.now();
        }
      }
      if (Date.now() > deadline) {
        // A typed failure with whatever partial text was seen, never an empty
        // "success" the Core would score as "nothing disclosed".
        finish({ ok: false, message: 'capture_timeout', text: lastText });
        return;
      }
      timer = setTimeout(tick, 150) as unknown as number;
    };

    observer = new MutationObserver(() => {
      /* the poll below reads the DOM; this just keeps latency low */
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    tick();
  });
}

/** A narrowly scoped read of the visible conversation. */
function conversation(locator: Locator | null | undefined): OperationResult {
  const all = resolveAll(locator);
  const text = all
    .slice(-12)
    .map((element) => ((element as HTMLElement).innerText ?? '').trim())
    .filter(Boolean)
    .join('\n---\n');
  return { ok: true, text: text.slice(0, 8000), count: all.length };
}

function highlight(locator: Locator | null | undefined): OperationResult {
  document.getElementById(HIGHLIGHT_ID)?.remove();
  const element = resolveOne(locator);
  if (!element) return { ok: false, message: 'element not found' };
  const rect = element.getBoundingClientRect();
  const box = document.createElement('div');
  box.id = HIGHLIGHT_ID;
  box.style.cssText = [
    'position:fixed',
    `top:${rect.top}px`,
    `left:${rect.left}px`,
    `width:${rect.width}px`,
    `height:${rect.height}px`,
    'border:2px solid #1a73e8',
    'background:rgba(26,115,232,.12)',
    'pointer-events:none',
    'z-index:2147483647',
  ].join(';');
  document.body.appendChild(box);
  setTimeout(() => box.remove(), 1500);
  return { ok: true };
}

/** One click-to-pick, pinned to the exact reviewed node. */
function pick(role?: OperationRequest['role']): Promise<OperationResult> {
  return new Promise((resolve) => {
    document.getElementById(PICKER_ID)?.remove();
    const banner = document.createElement('div');
    banner.id = PICKER_ID;
    banner.style.cssText = [
      'position:fixed',
      'top:8px',
      'left:50%',
      'transform:translateX(-50%)',
      'background:#14161a',
      'color:#e8eaed',
      'padding:6px 12px',
      'border-radius:6px',
      'font:13px system-ui,sans-serif',
      'z-index:2147483647',
      'pointer-events:none',
    ].join(';');
    // textContent, never innerHTML.
    banner.textContent = 'Click the element to select — Esc to cancel';
    document.body.appendChild(banner);

    let settled = false;
    let iframeCheck: ReturnType<typeof setTimeout> | null = null;
    const timeout = setTimeout(() => {
      finish({
        ok: false,
        message:
          'Element selection timed out. If the control is inside an iframe, select a top-level interaction or re-open Stealth Prompt on the iframe origin.',
      });
    }, PICK_TIMEOUT_MS);

    const cleanup = (): void => {
      settled = true;
      clearTimeout(timeout);
      if (iframeCheck !== null) clearTimeout(iframeCheck);
      banner.remove();
      document.removeEventListener('click', onClick, true);
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('blur', onBlur, true);
    };
    const finish = (result: OperationResult): void => {
      if (settled) return;
      cleanup();
      resolve(result);
    };
    const onClick = (event: MouseEvent): void => {
      const target = event.composedPath()[0];
      if (!(target instanceof Element) || target === banner) return;
      event.preventDefault();
      event.stopPropagation();
      if (!role) {
        finish({ ok: true, locator: computeLocator(target) });
        return;
      }
      const selector = {
        input: 'textarea,input,[role="textbox"],[contenteditable="true"]',
        submit: 'button,[role="button"],input[type="submit"]',
        response:
          '[data-message-author-role="assistant"],[data-testid*="assistant"],[class*="assistant"],[class*="bot"],[class*="response"],[class*="message"],[role="log"],[aria-live],#log',
      }[role];
      const selected = target.closest(selector) ?? target;
      const fallback = cssPath(selected);
      const attribute = `data-stealth-prompt-${role}`;
      document.querySelectorAll(`[${attribute}]`).forEach((element) =>
        element.removeAttribute(attribute),
      );
      const token = crypto.randomUUID();
      selected.setAttribute(attribute, token);
      finish({
        ok: true,
        locator: { strategy: 'css', value: `[${attribute}="${token}"]`, css_fallback: fallback },
      });
    };
    const onKey = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return;
      finish({ ok: false, message: 'cancelled' });
    };
    const onBlur = (): void => {
      // Clicks inside an iframe do not bubble to the parent document. Detect
      // the focused frame and fail visibly instead of leaving the panel's
      // operation pending forever.
      iframeCheck = setTimeout(() => {
        if (document.activeElement instanceof HTMLIFrameElement) {
          finish({
            ok: false,
            message:
              'The selected control is inside an iframe. Cross-frame interaction selection is not supported yet.',
          });
        }
      }, 0);
    };
    document.addEventListener('click', onClick, true);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('blur', onBlur, true);
  });
}

/* ------------------------------------------------------------- discovery */

function visible(element: Element): boolean {
  const rect = element.getBoundingClientRect();
  const style = getComputedStyle(element);
  return rect.width >= 12 && rect.height >= 12 && style.display !== 'none' && style.visibility !== 'hidden';
}

function description(element: Element): string {
  return [
    element.id,
    element.className,
    accessibleName(element),
    element.getAttribute('placeholder'),
    element.getAttribute('name'),
    element.getAttribute('data-testid'),
  ]
    .filter((value): value is string => typeof value === 'string')
    .join(' ')
    .toLowerCase();
}

function best<T extends Element>(elements: Iterable<T>, score: (element: T) => number): [T | null, number] {
  let winner: T | null = null;
  let high = -Infinity;
  for (const element of elements) {
    const current = score(element);
    if (current > high) [winner, high] = [element, current];
  }
  return [winner, high];
}

/**
 * Suggest a binding from DOM semantics. The result is never saved or acted on
 * automatically; the operator reviews it and the normal validation gate still
 * applies. This keeps a heuristic miss from becoming a page mutation.
 */
function discover(): OperationResult {
  const inputCandidates = Array.from(
    document.querySelectorAll<HTMLElement>(
      'textarea,input[type="text"],input[type="search"],[role="textbox"],[contenteditable="true"]',
    ),
  ).filter(visible);
  const [input, inputScore] = best(inputCandidates, (element) => {
    const hint = description(element);
    let score = element.tagName === 'TEXTAREA' ? 55 : element.isContentEditable ? 45 : 35;
    if (/message|chat|prompt|ask|question|compose|reply/.test(hint)) score += 35;
    if (/search|filter|email|newsletter|coupon/.test(hint)) score -= 45;
    if (element.closest('form')) score += 12;
    if (element.getBoundingClientRect().width >= 180) score += 8;
    return score;
  });

  const submitCandidates = new Set<HTMLElement>();
  if (input) {
    input.closest('form')?.querySelectorAll<HTMLElement>('button,[role="button"],input[type="submit"]')
      .forEach((element) => submitCandidates.add(element));
    let parent = input.parentElement;
    for (let depth = 0; parent && depth < 3; depth += 1, parent = parent.parentElement) {
      parent.querySelectorAll<HTMLElement>('button,[role="button"],input[type="submit"]')
        .forEach((element) => submitCandidates.add(element));
    }
  }
  const [submit, submitScore] = best(Array.from(submitCandidates).filter(visible), (element) => {
    const hint = description(element);
    let score = element.getAttribute('type') === 'submit' ? 45 : 20;
    if (/send|submit|ask|post|reply|arrow|paper.?plane/.test(hint)) score += 45;
    if (/cancel|delete|remove|clear|search/.test(hint)) score -= 55;
    if (input && element.closest('form') === input.closest('form')) score += 20;
    return score;
  });

  const responseCandidates = Array.from(
    document.querySelectorAll<HTMLElement>(
      '[data-message-author-role="assistant"],[data-testid*="assistant"],[class*="assistant"],[class*="bot"],[class*="response"],[class*="message"],[role="log"],[aria-live],#log',
    ),
  ).filter((element) => visible(element) && element !== input && element !== submit);
  const [response, responseScore] = best(responseCandidates, (element) => {
    const hint = description(element);
    let score = 15;
    if (/assistant|bot|response|answer/.test(hint)) score += 60;
    if (element.matches('[role="log"],[aria-live],#log')) score += 45;
    if (/message|chat|conversation|transcript/.test(hint)) score += 25;
    if (input && element.getBoundingClientRect().top < input.getBoundingClientRect().top) score += 10;
    return score;
  });

  const selectedInput = inputScore >= 35 ? input : null;
  const selectedSubmit = submitScore >= 35 ? submit : null;
  const selectedResponse = responseScore >= 35 ? response : null;
  const missing = [
    !selectedInput && 'input',
    !selectedSubmit && 'send control',
    !selectedResponse && 'response container',
  ].filter((value): value is string => Boolean(value));

  /**
   * Confidence is reported per role rather than as one aggregate.
   *
   * An aggregate hides the case that actually matters: a confident input and a
   * guessed response container average out to "medium", and the operator
   * accepts a binding whose weakest part is the one that decides the verdict.
   */
  const scale = (score: number): number =>
    Math.max(0, Math.min(100, Math.round(score)));

  const describe = (element: HTMLElement | null, kind: string): string => {
    if (!element) return `No ${kind} candidate scored high enough to suggest.`;
    const tag = element.tagName.toLowerCase();
    const name = accessibleName(element);
    const hint = name ? `“${name.slice(0, 40)}”` : element.getAttribute('placeholder') ?? '';
    const where = element.closest('form') ? ' inside a form' : '';
    return `Matched <${tag}>${hint ? ` ${hint}` : ''}${where}.`.slice(0, 160);
  };

  return {
    ok: Boolean(selectedInput || selectedSubmit || selectedResponse),
    message: missing.length
      ? `Review suggested binding; missing ${missing.join(', ')}.`
      : 'Review all suggested elements before saving.',
    suggestion: {
      input: {
        locator: selectedInput ? computeLocator(selectedInput) : null,
        confidence: selectedInput ? scale(inputScore) : 0,
        reason: selectedInput
          ? `${describe(selectedInput, 'input')} Editable chat semantics.`
          : describe(null, 'input'),
      },
      submit: {
        locator: selectedSubmit ? computeLocator(selectedSubmit) : null,
        confidence: selectedSubmit ? scale(submitScore) : 0,
        reason: selectedSubmit
          ? `${describe(selectedSubmit, 'send')} Associated with the input.`
          : describe(null, 'send control'),
      },
      response: {
        // Response locators must survive new message nodes and changing text.
        locator: selectedResponse
          ? {
              strategy: 'css',
              value: cssPath(selectedResponse),
              css_fallback: cssPath(selectedResponse),
            }
          : null,
        confidence: selectedResponse ? scale(responseScore) : 0,
        reason: selectedResponse
          ? `${describe(selectedResponse, 'response')} Conversation or live-region semantics.`
          : describe(null, 'response container'),
      },
      missing,
    },
  };
}

/**
 * Confirm each bound locator still resolves to exactly what it should.
 *
 * Every role is checked, not just up to the first failure: after a redeploy
 * more than one may have moved, and an operator who fixes the input only to be
 * told the send control is also stale has been made to do the work twice.
 *
 * This is read-only. Health checking must never mutate the page, or merely
 * asking "is this still valid?" would itself be an interaction with the target.
 */
function validate(binding: Record<string, Locator | null>): OperationResult {
  const roles: Record<string, RoleValidation> = {};
  let ok = true;

  for (const [role, locator] of Object.entries(binding)) {
    if (!locator) continue;
    const matches = resolveAll(locator).length;
    if (matches === 0) {
      roles[role] = { ok: false, matches, reason: `The ${role} element no longer matches.` };
      ok = false;
    } else if (role !== 'response' && matches > 1) {
      // Acting on the wrong element is worse than stopping.
      roles[role] = {
        ok: false,
        matches,
        reason: `The ${role} locator is ambiguous (${matches} elements match).`,
      };
      ok = false;
    } else {
      roles[role] = { ok: true, matches, reason: '' };
    }
  }

  const failed = Object.entries(roles)
    .filter(([, result]) => !result.ok)
    .map(([role]) => role);
  return {
    ok,
    roles,
    url: location.href,
    message: ok ? '' : `Stale binding: ${failed.join(', ')}.`,
  };
}

/* ---------------------------------------------------------------- routing */

export async function performOperation(
  request: OperationRequest,
  binding: Record<string, Locator | null>,
): Promise<OperationResult> {
  // A closed switch. There is no name lookup and no default execution path.
  switch (request.operation) {
    case 'discover':
      return discover();
    case 'pick':
      return pick(request.role);
    case 'validate':
      return validate(binding);
    case 'highlight':
      return highlight(request.locator ?? null);
    case 'snapshot':
      return takeSnapshot(binding['response']);
    case 'fill':
      return fill(binding['input'], request.value ?? '');
    case 'submit':
      return submit(request, binding);
    case 'capture':
      return capture(binding['response'], request.stableMs ?? 1500, request.timeoutMs ?? 60000);
    case 'conversation':
      return conversation(binding['response']);
    default:
      return { ok: false, message: 'operation is not allowed' };
  }
}

/**
 * A short, bounded reason for a failed operation.
 *
 * The operator needs to know *why* a step failed ("no element matches …"), so
 * the message is kept rather than reduced to the error's class name. It is
 * truncated because an exception raised while touching the page can carry page
 * text, and only a bounded, single-line excerpt should ever reach the panel.
 */
function failureReason(error: unknown): string {
  const raw = (error as Error)?.message ?? String(error ?? 'operation failed');
  const flat = raw.replace(/\s+/g, ' ').trim();
  if (!flat) return 'operation failed';
  return flat.length > 200 ? `${flat.slice(0, 200)}…` : flat;
}

/* Only wire up listeners inside a real extension page. */
declare const chrome: typeof globalThis extends { chrome: infer C } ? C : any;

if (typeof chrome !== 'undefined' && chrome?.runtime?.onMessage) {
  chrome.runtime.onMessage.addListener(
    (message: unknown, _sender: unknown, sendResponse: (result: OperationResult) => void) => {
      if (typeof message !== 'object' || message === null) return false;
      const request = message as Record<string, unknown>;
      if (request['channel'] !== 'stealth-prompt') return false;
      if (!isBrowserOperation(request['operation'])) {
        sendResponse({ ok: false, message: 'operation is not allowed' });
        return false;
      }
      const binding = (request['binding'] ?? {}) as Record<string, Locator | null>;
      performOperation(request as unknown as OperationRequest, binding)
        .then((result) =>
          sendResponse({
            ...result,
            operationId: (request['operationId'] as string) ?? '',
            documentId: (request['documentId'] as string) ?? '',
          }),
        )
        .catch((error: unknown) => sendResponse({ ok: false, message: failureReason(error) }));
      return true; // async response
    },
  );
}
