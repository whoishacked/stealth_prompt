/**
 * Content script: the operator dock and the six allowlisted page operations.
 *
 * Two boundaries are enforced here.
 *
 * 1. The dock lives in a *closed* shadow root, so scripts belonging to the
 *    target page cannot reach into it, read the authored payload, or click its
 *    buttons. Styles are scoped to that root and never leak into the page.
 *
 * 2. Operations are dispatched through a fixed `switch`, never by looking up a
 *    name on an object. There is no `eval`, no `Function`, no `innerHTML` with
 *    untrusted content, and no way to ask this script to run arbitrary code.
 *    Text arriving from the broker is inserted with `textContent` only.
 */

(() => {
  'use strict';

  if (window.__stealthPromptDockLoaded) {
    return;
  }
  window.__stealthPromptDockLoaded = true;

  // A streamed reply routinely pauses for a second between chunks, so a short
  // quiet period reports a half-written answer as complete. Both values are
  // overridden per target by the saved binding.
  const DEFAULT_STABILIZE_MS = 1500;
  const DEFAULT_CAPTURE_TIMEOUT_MS = 60000;

  const port = chrome.runtime.connect({ name: 'stealth-prompt-dock' });

  const state = {
    connected: false,
    payload: '',
    streaming: false,
    inputLocator: null,
    submitLocator: null,
    responseLocator: null,
    picking: null,
    cancelCapture: false,
    bound: true,
    runId: '',
    mode: 'manual',
    turn: 0,
    maxTurns: 0,
    status: 'not_detected',
    requireApproval: true,
    baselineCount: 0,
    baselineText: '',
    submitStrategy: 'click_button',
    submitKey: 'Enter',
    captureStableMs: 0,
    captureTimeoutMs: 0,
    awaitingApproval: false,
  };

  /** Serialize a picked locator into the binding wire shape. */
  function locatorToBinding(locator, pick) {
    if (!locator) return null;
    const document_ = {
      strategy: locator.strategy,
      value: locator.value,
      pick: pick || 'first',
    };
    if (locator.name) document_.name = locator.name;
    if (locator.css) document_.css_fallback = locator.css;
    return document_;
  }

  /** Rebuild a locator from a saved binding. */
  function bindingToLocator(document_) {
    if (!document_) return null;
    return {
      strategy: document_.strategy,
      value: document_.value,
      name: document_.name || null,
      css: document_.css_fallback || null,
    };
  }

  // ----------------------------------------------------------------- locators

  function accessibleName(el) {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const ref = document.getElementById(labelledBy);
      if (ref) return (ref.textContent || '').trim();
    }
    if (el.id) {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (label) return (label.textContent || '').trim();
    }
    const text = (el.textContent || '').trim();
    return text.length > 0 && text.length <= 80 ? text : '';
  }

  function implicitRole(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'submit' || type === 'button') return 'button';
      if (['text', 'search', 'email', 'url', 'tel'].includes(type)) return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return el.getAttribute('role') || '';
  }

  function stableClasses(el) {
    if (!el.classList) return [];
    return Array.from(el.classList).filter((c) => !/\d/.test(c));
  }

  function cssPath(el) {
    if (el.id) return `#${CSS.escape(el.id)}`;

    const testId = el.getAttribute && el.getAttribute('data-testid');
    if (testId) return `[data-testid="${CSS.escape(testId)}"]`;

    // Prefer a class-based selector that also matches this element's siblings.
    // A reply locator has to keep matching when the *next* reply arrives, so a
    // positional selector is exactly wrong for the element that matters most.
    const classes = stableClasses(el);
    if (classes.length) {
      const selector = '.' + classes.map((c) => CSS.escape(c)).join('.');
      try {
        const matches = document.querySelectorAll(selector);
        if (matches.length && Array.prototype.indexOf.call(matches, el) !== -1) {
          return selector;
        }
      } catch (err) {
        // Fall through to the positional walk below.
      }
    }

    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 5) {
      let part = node.tagName.toLowerCase();
      const testId = node.getAttribute && node.getAttribute('data-testid');
      if (testId) {
        parts.unshift(`[data-testid="${CSS.escape(testId)}"]`);
        break;
      }
      if (node.classList && node.classList.length) {
        const stable = Array.from(node.classList).find((c) => !/\d/.test(c));
        if (stable) part += `.${CSS.escape(stable)}`;
      }
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (siblings.length > 1) {
          part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(' > ');
  }

  /** Accessibility-first, CSS last. */
  function computeLocator(el) {
    const testId = el.getAttribute('data-testid');
    const placeholder = el.getAttribute('placeholder');
    const role = implicitRole(el);
    const name = accessibleName(el);

    if (role && name) {
      return { strategy: 'role', value: role, name, css: cssPath(el) };
    }
    if (el.id) {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (label && label.textContent.trim()) {
        return { strategy: 'label', value: label.textContent.trim(), css: cssPath(el) };
      }
    }
    if (placeholder) {
      return { strategy: 'placeholder', value: placeholder, css: cssPath(el) };
    }
    if (testId) {
      return { strategy: 'test_id', value: testId, css: cssPath(el) };
    }
    return { strategy: 'css', value: cssPath(el), css: cssPath(el) };
  }

  function resolveAll(locator) {
    if (!locator) return [];
    switch (locator.strategy) {
      case 'role': {
        const candidates = Array.from(document.querySelectorAll('*'));
        return candidates.filter(
          (el) => implicitRole(el) === locator.value && accessibleName(el) === locator.name
        );
      }
      case 'label': {
        const labels = Array.from(document.querySelectorAll('label'));
        const match = labels.find((l) => l.textContent.trim() === locator.value);
        if (!match) return [];
        const forId = match.getAttribute('for');
        const el = forId ? document.getElementById(forId) : match.querySelector('input,textarea');
        return el ? [el] : [];
      }
      case 'placeholder':
        return Array.from(document.querySelectorAll(`[placeholder="${CSS.escape(locator.value)}"]`));
      case 'test_id':
        return Array.from(document.querySelectorAll(`[data-testid="${CSS.escape(locator.value)}"]`));
      case 'css':
      default:
        try {
          return Array.from(document.querySelectorAll(locator.value));
        } catch (err) {
          return [];
        }
    }
  }

  function resolveOne(locator, pick) {
    const all = resolveAll(locator);
    if (!all.length) return null;
    return pick === 'first' ? all[0] : all[all.length - 1];
  }

  function describeLocator(locator) {
    if (!locator) return 'not set';
    if (locator.strategy === 'role') return `role=${locator.value} "${locator.name}"`;
    return `${locator.strategy}=${locator.value}`;
  }

  // --------------------------------------------------------------- operations

  function opFill(locator, value) {
    const el = resolveOne(locator, 'first');
    if (!el) return { ok: false, message: 'input element not found' };
    el.focus();
    if (el.isContentEditable) {
      el.textContent = value;
    } else {
      const setter = Object.getOwnPropertyDescriptor(
        el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
        'value'
      );
      if (setter && setter.set) {
        setter.set.call(el, value);
      } else {
        el.value = value;
      }
    }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return { ok: true };
  }

  function opClick(locator) {
    const el = resolveOne(locator, 'first');
    if (!el) return { ok: false, message: 'element not found' };
    el.click();
    return { ok: true };
  }

  function opPress(locator, key) {
    const el = resolveOne(locator, 'first') || document.activeElement;
    if (!el) return { ok: false, message: 'element not found' };
    const shift = key.startsWith('Shift+');
    const bare = shift ? key.slice('Shift+'.length) : key;
    el.focus();
    for (const type of ['keydown', 'keypress', 'keyup']) {
      el.dispatchEvent(
        new KeyboardEvent(type, {
          key: bare,
          code: bare === 'Enter' ? 'Enter' : bare,
          shiftKey: shift,
          bubbles: true,
          cancelable: true,
        })
      );
    }
    if (bare === 'Enter' && !shift) {
      const form = el.closest && el.closest('form');
      if (form && typeof form.requestSubmit === 'function') {
        form.requestSubmit();
      }
    }
    return { ok: true };
  }

  function opExtract(locator, pick) {
    const el = resolveOne(locator, pick || 'last');
    if (!el) return { ok: false, message: 'element not found' };
    return { ok: true, text: (el.innerText || el.textContent || '').trim() };
  }

  function opWaitFor(locator, timeoutMs) {
    return new Promise((resolve) => {
      const deadline = Date.now() + (timeoutMs || 10000);
      const tick = () => {
        if (resolveAll(locator).length) {
          resolve({ ok: true });
          return;
        }
        if (Date.now() > deadline) {
          resolve({ ok: false, message: 'timed out waiting for element' });
          return;
        }
        setTimeout(tick, 100);
      };
      tick();
    });
  }

  /**
   * Resolve which bound element an operation applies to.
   *
   * The broker names the target ("input", "submit", "response"); the extension
   * never picks. That keeps element choice on the Python side with the rest of
   * the policy.
   */
  function locatorFor(target) {
    if (target === 'input') return state.inputLocator;
    if (target === 'response') return state.responseLocator;
    return state.submitLocator;
  }

  /** Dispatch through a closed switch. Never a name lookup. */
  async function performOperation(request) {
    const locator = locatorFor(request.target);

    switch (request.operation) {
      case 'fill':
        return opFill(state.inputLocator, request.value);
      case 'click':
        return opClick(locator);
      case 'press':
        return opPress(locator, request.key || 'Enter');
      case 'wait_for':
        return opWaitFor(locator, request.timeout_ms || 10000);
      case 'extract':
        return opExtract(state.responseLocator, 'last');
      case 'pick_locator':
        return { ok: false, message: 'pick_locator is initiated from the dock' };
      default:
        return { ok: false, message: 'operation is not allowed' };
    }
  }

  /** Execute the configured submit strategy. */
  function performSubmit() {
    if (state.submitStrategy === 'press_key') {
      return opPress(state.inputLocator, state.submitKey || 'Enter');
    }
    return opClick(state.submitLocator);
  }

  // ------------------------------------------------------------------ capture

  function snapshotResponses() {
    const all = resolveAll(state.responseLocator);
    state.baselineCount = all.length;
    state.baselineText = all.length ? (all[all.length - 1].innerText || '').trim() : '';
  }

  /**
   * Wait for a new or changed assistant message whose text stops growing.
   * Correlating on both count and text avoids capturing the previous turn.
   */
  function captureResponse(options) {
    const settings = options || {};
    const stabilizeMs = settings.stableMs > 0 ? settings.stableMs : DEFAULT_STABILIZE_MS;
    const timeoutMs = settings.timeoutMs > 0 ? settings.timeoutMs : DEFAULT_CAPTURE_TIMEOUT_MS;
    const startedAt = Date.now();

    return new Promise((resolve) => {
      const deadline = startedAt + timeoutMs;
      let lastText = '';
      let stableSince = 0;

      const tick = () => {
        if (state.cancelCapture) {
          resolve({
            ok: false,
            code: 'cancelled',
            text: lastText,
            elapsedMs: Date.now() - startedAt,
          });
          return;
        }

        const all = resolveAll(state.responseLocator);
        const grew = all.length > state.baselineCount;
        const current = all.length ? (all[all.length - 1].innerText || '').trim() : '';
        const changed = current && current !== state.baselineText;

        // An explicit "still generating" indicator beats any text heuristic.
        let generating = false;
        if (settings.stopIndicatorCss) {
          try {
            generating = Boolean(document.querySelector(settings.stopIndicatorCss));
          } catch (err) {
            generating = false;
          }
        }

        if ((grew || changed) && current && !generating) {
          if (current === lastText) {
            if (stableSince && Date.now() - stableSince >= stabilizeMs) {
              resolve({ ok: true, text: current, elapsedMs: Date.now() - startedAt });
              return;
            }
            if (!stableSince) stableSince = Date.now();
          } else {
            lastText = current;
            stableSince = Date.now();
          }
        } else if (generating) {
          // Reset the quiet period: the page says more is coming.
          stableSince = 0;
          if (current) lastText = current;
        }

        if (Date.now() > deadline) {
          // A typed failure, never an empty successful reply. Partial text is
          // preserved so the operator can see how far it got.
          resolve({
            ok: false,
            code: 'capture_timeout',
            text: lastText,
            elapsedMs: Date.now() - startedAt,
          });
          return;
        }
        setTimeout(tick, 150);
      };
      tick();
    });
  }

  // --------------------------------------------------------------------- dock

  const host = document.createElement('div');
  host.id = '__stealth_prompt_dock_host__';
  // The host is a zero-sized, click-through anchor. Without this it sits over
  // the page and swallows pointer events, which would make the very target the
  // operator is testing unclickable. Only the dock panel itself takes clicks.
  host.style.cssText =
    'all:initial;position:fixed;top:0;left:0;width:0;height:0;' +
    'pointer-events:none;z-index:2147483647;';
  const shadow = host.attachShadow({ mode: 'closed' });

  const style = document.createElement('style');
  style.textContent = `
    :host { all: initial; }
    .dock {
      pointer-events: auto;
      position: fixed; right: 16px; bottom: 16px;
      width: 380px; height: 520px; min-width: 300px; min-height: 260px;
      max-width: 90vw; max-height: 90vh;
      display: flex; flex-direction: column; overflow: hidden;
      background: #14161a; color: #e8eaed; border: 1px solid #3c4043;
      border-radius: 10px; box-shadow: 0 8px 32px rgba(0,0,0,.45);
      font: 13px/1.45 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
      resize: both;
    }
    .bar {
      display:flex; align-items:center; gap:8px; padding:8px 10px;
      background:#1f2126; border-bottom:1px solid #3c4043; cursor:move; user-select:none;
    }
    .title { font-weight:600; font-size:12px; letter-spacing:.02em; flex:1; }
    .dot { width:8px; height:8px; border-radius:50%; background:#d93025; }
    .dot.on { background:#34a853; }
    .body { flex:1; overflow-y:auto; padding:10px; display:flex; flex-direction:column; gap:10px; }
    .sec { border:1px solid #3c4043; border-radius:6px; padding:8px; }
    .sec h4 { margin:0 0 6px; font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:#9aa0a6; }
    textarea, .out {
      width:100%; box-sizing:border-box; background:#0f1114; color:#e8eaed;
      border:1px solid #3c4043; border-radius:4px; padding:6px; font:12px/1.4 ui-monospace, monospace;
    }
    textarea { resize:vertical; min-height:52px; }
    .out { min-height:72px; max-height:180px; overflow-y:auto; white-space:pre-wrap; word-break:break-word; }
    .row { display:flex; gap:6px; flex-wrap:wrap; margin-top:6px; }
    button {
      background:#2d2f34; color:#e8eaed; border:1px solid #5f6368; border-radius:4px;
      padding:5px 9px; font-size:12px; cursor:pointer; font-family:inherit;
    }
    button:hover:not(:disabled) { background:#3c4043; }
    button:disabled { opacity:.45; cursor:not-allowed; }
    button.primary { background:#1a73e8; border-color:#1a73e8; }
    button.primary:hover:not(:disabled) { background:#2b7de9; }
    button.danger { background:#5c1f1b; border-color:#8c2f28; }
    .loc { font:11px/1.4 ui-monospace, monospace; color:#9aa0a6; word-break:break-all; margin-top:4px; }
    .loc.set { color:#81c995; }
    .status { padding:6px 10px; border-top:1px solid #3c4043; background:#1f2126; font-size:11px; color:#9aa0a6; }
    .status b { color:#e8eaed; }
    .pill { display:inline-block; padding:1px 6px; border-radius:9px; font-size:10px; margin-left:4px; }
    .pill.confirmed { background:#5c1f1b; color:#f28b82; }
    .pill.not_detected { background:#20302a; color:#81c995; }
    .lbl { font-size:11px; color:#9aa0a6; display:flex; align-items:center; gap:4px; }
    .fld { display:flex; align-items:center; gap:6px; margin-bottom:5px; }
    .fld .lbl { min-width:96px; }
    select, input[type=number], input[type=text], #model-custom {
      flex:1; background:#0f1114; color:#e8eaed; border:1px solid #3c4043;
      border-radius:4px; padding:4px 6px; font:12px/1.3 inherit;
    }
    #model-custom { width:100%; box-sizing:border-box; margin-bottom:4px; }
    .note { font-size:11px; color:#9aa0a6; margin:4px 0; line-height:1.4; }
    .note.warn { color:#fdd663; }
    .note.danger { color:#f28b82; }
    .note.ok { color:#81c995; }
    .chk { display:flex; gap:6px; font-size:11px; padding:1px 0; align-items:baseline; }
    .chk .mark { width:12px; flex:none; }
    .chk.ok .mark { color:#81c995; }
    .chk.warn .mark { color:#fdd663; }
    .chk.blocked .mark { color:#f28b82; }
    .chk.not_required { opacity:.5; }
    .chk .why { color:#9aa0a6; }
    details > summary { cursor:pointer; margin:4px 0; }
    select:disabled, input:disabled, button:disabled { opacity:.45; }
  `;

  const dock = document.createElement('div');
  dock.className = 'dock';
  dock.innerHTML = `
    <div class="bar">
      <span class="dot" id="dot"></span>
      <span class="title">Stealth Prompt workbench</span>
      <button id="hide" title="Collapse">–</button>
    </div>
    <div class="body">
      <div class="sec" id="setup-sec">
        <h4>1 · Session setup</h4>
        <div class="fld">
          <label class="lbl" for="provider">Backend</label>
          <select id="provider"></select>
        </div>
        <div class="note" id="provider-note"></div>
        <div class="fld">
          <label class="lbl" for="model">Model</label>
          <select id="model"></select>
        </div>
        <input id="model-custom" placeholder="custom model name (optional)">
        <div class="note" id="model-note"></div>
        <div class="fld">
          <label class="lbl" for="mode">Run mode</label>
          <select id="mode"></select>
        </div>
        <div class="fld">
          <label class="lbl" for="sharing">Target data sharing</label>
          <select id="sharing"></select>
        </div>
        <div class="note" id="planning-note"></div>
        <textarea id="objective" placeholder="Objective: what should this run establish?"></textarea>
        <div class="fld">
          <label class="lbl" for="max-turns">Max turns</label>
          <input id="max-turns" type="number" min="1" max="100">
          <label class="lbl" for="max-seconds">Max seconds</label>
          <input id="max-seconds" type="number" min="10" max="7200">
        </div>
        <div class="row">
          <button id="validate-config">Validate settings</button>
          <button id="refresh-models">Refresh models</button>
        </div>
        <div class="note" id="config-note"></div>
      </div>
      <div class="sec">
        <h4>2 · Page elements</h4>
        <div class="row">
          <button id="pick-input">Pick input</button>
          <button id="pick-submit">Pick send</button>
          <button id="pick-response">Pick reply</button>
        </div>
        <div class="loc" id="loc-input">input: not set</div>
        <div class="loc" id="loc-submit">send: not set</div>
        <div class="loc" id="loc-response">reply: not set</div>
        <div class="row">
          <label class="lbl"><input type="radio" name="submit-mode" id="mode-click" checked> click button</label>
          <label class="lbl"><input type="radio" name="submit-mode" id="mode-enter"> press Enter in input</label>
        </div>
        <div class="row">
          <button id="save-binding">Save target setup</button>
        </div>
      </div>
      <div class="sec" id="run-section">
        <h4>3 · Run controls</h4>
        <div class="row">
          <button class="primary" id="run-start">Start</button>
          <button class="danger" id="run-stop">Stop</button>
          <button id="run-approve">Approve send</button>
        </div>
        <div class="note" id="start-summary"></div>
        <details id="checklist-box">
          <summary class="lbl">Readiness checklist</summary>
          <div id="checklist"></div>
        </details>
        <div class="loc" id="run-info">mode: manual</div>
      </div>
      <div class="sec">
        <h4>4 · Payload</h4>
        <div class="row">
          <button class="primary" id="generate">Generate first payload</button>
          <button id="interrupt">Stop</button>
        </div>
        <details id="extra-box">
          <summary class="lbl">Additional instruction (optional)</summary>
          <textarea id="ask" placeholder="Optional. Leave empty to author from the objective alone."></textarea>
        </details>
      </div>
      <div class="sec">
        <h4>5 · Review payload</h4>
        <div class="out" id="stream"></div>
        <div class="row">
          <button id="insert">Insert into page</button>
          <button class="primary" id="send">Approve &amp; send</button>
        </div>
      </div>
      <div class="sec">
        <h4>6 · Target reply</h4>
        <div class="out" id="reply"></div>
        <div class="row">
          <button id="capture-reply">Capture reply</button>
        </div>
      </div>
    </div>
    <div class="status" id="status">connecting…</div>
  `;

  shadow.appendChild(style);
  shadow.appendChild(dock);

  const $ = (id) => shadow.getElementById(id);

  function mount() {
    (document.body || document.documentElement).appendChild(host);
  }
  if (document.body) {
    mount();
  } else {
    document.addEventListener('DOMContentLoaded', mount, { once: true });
  }

  /**
   * Render status without ever building markup from text.
   *
   * The old version interpolated into innerHTML. Status text can carry agent
   * output, broker error messages, and locator values derived from the target
   * page -- all attacker-influenced. Nodes are created separately and the text
   * is assigned with textContent, so a string can never become an element.
   */
  function setStatus(text, status) {
    const node = $('status');
    node.textContent = '';
    node.appendChild(document.createTextNode(String(text == null ? '' : text)));
    if (status) {
      const pill = document.createElement('span');
      pill.className = 'pill ' + (String(status).replace(/[^a-z_]/g, '') || 'unknown');
      pill.textContent = String(status);
      node.appendChild(pill);
    }
  }

  function renderLocators() {
    const map = [
      ['loc-input', 'input', state.inputLocator],
      ['loc-submit', 'send', state.submitLocator],
      ['loc-response', 'reply', state.responseLocator],
    ];
    for (const [id, label, loc] of map) {
      const node = $(id);
      node.textContent = `${label}: ${describeLocator(loc)}`;
      node.classList.toggle('set', Boolean(loc));
    }
    const readOnly = state.mode === 'payload_only';
    // Payload-only never mutates the page. The backend refuses these anyway;
    // hiding them keeps the UI honest about what the mode does.
    $('insert').style.display = readOnly ? 'none' : 'inline-block';
    $('send').style.display = readOnly ? 'none' : 'inline-block';
    $('insert').disabled = readOnly || !state.inputLocator || !state.payload;
    $('send').disabled = readOnly || !state.submitLocator || !state.payload;
  }

  // Element picking: one click, captured before the page sees it.
  function startPicking(which) {
    state.picking = which;
    setStatus(`click the ${which} element on the page…`);
    const onClick = (event) => {
      event.preventDefault();
      event.stopPropagation();
      const el = event.composedPath()[0];
      if (host.contains(el)) return;
      const locator = computeLocator(el);
      if (which === 'input') state.inputLocator = locator;
      if (which === 'submit') state.submitLocator = locator;
      if (which === 'response') state.responseLocator = locator;
      state.picking = null;
      document.removeEventListener('click', onClick, true);
      renderLocators();
      setStatus(`${which} set to ${describeLocator(locator)}`);
    };
    document.addEventListener('click', onClick, true);
  }

  $('pick-input').addEventListener('click', () => startPicking('input'));
  $('pick-submit').addEventListener('click', () => startPicking('submit'));
  $('pick-response').addEventListener('click', () => startPicking('response'));

  $('hide').addEventListener('click', () => {
    const body = shadow.querySelector('.body');
    const hidden = body.style.display === 'none';
    body.style.display = hidden ? 'flex' : 'none';
    shadow.querySelector('.status').style.display = hidden ? 'block' : 'none';
    dock.style.height = hidden ? '520px' : 'auto';
  });

  // ------------------------------------------------------------ setup panel
  //
  // Everything here is a *proposal*. The backend re-validates each value
  // against its allowlist and may refuse; the panel only ever renders what the
  // backend confirmed. No provider path, endpoint, or credential exists on
  // this side to leak.

  const capabilities = { providers: [], modes: [], sharing: [], current: {} };

  /** Replace a <select>'s options without touching innerHTML. */
  function fillSelect(id, entries, selected) {
    const node = $(id);
    node.textContent = '';
    for (const entry of entries) {
      const option = document.createElement('option');
      option.value = String(entry.value);
      option.textContent = String(entry.label);
      if (entry.value === selected) option.selected = true;
      node.appendChild(option);
    }
  }

  function setNote(id, text, kind) {
    const node = $(id);
    node.textContent = String(text || '');
    node.className = 'note' + (kind ? ' ' + kind : '');
  }

  const MODE_LABELS = {
    payload_only: 'Payload only (never touches the page)',
    manual: 'Manual (you insert and send)',
    supervised: 'Supervised (approve every send)',
    auto: 'Automatic (bounded loop)',
  };

  const SHARING_LABELS = {
    none: 'None (no reply leaves this machine)',
    redacted: 'Redacted (credential shapes removed)',
    full: 'Full (verbatim replies sent)',
  };

  function currentProviderSpec() {
    const chosen = $('provider').value;
    return capabilities.providers.find((p) => p.kind === chosen) || null;
  }

  function renderProviderNote() {
    const spec = currentProviderSpec();
    if (!spec) return;
    const health = (capabilities.health || {})[spec.kind] || {};
    const bits = [spec.summary];
    if (health.installed === false) {
      bits.push('NOT INSTALLED — ' + (health.remedy || ''));
    } else if (health.authenticated === false) {
      bits.push('installed but NOT AUTHENTICATED — ' + (health.remedy || ''));
    } else if (health.installed) {
      bits.push('installed and authenticated');
    }
    const kind = spec.external
      ? (health.usable ? 'warn' : 'danger')
      : (health.usable ? 'ok' : 'danger');
    setNote('provider-note', bits.join(' · '), kind);

    // Model discovery is a per-provider capability, not a universal one.
    $('model-custom').style.display = spec.custom_model ? 'block' : 'none';
    $('refresh-models').disabled = !spec.model_discovery;
    if (!spec.model_discovery) {
      setNote('model-note', 'This backend does not list models; use Default or type one.');
    }
  }

  function renderPlanningNote() {
    const mode = $('mode').value;
    const sharing = $('sharing').value;
    if (mode === 'payload_only') {
      setNote(
        'planning-note',
        sharing === 'none'
          ? 'Payload-only with sharing=none: the agent works from your objective '
            + 'alone. Adaptive generation from the captured reply is impossible '
            + 'until you choose redacted or full.'
          : 'Payload-only: the captured reply is shared with the agent under the '
            + 'policy above. Nothing is typed or sent.',
        sharing === 'none' ? 'warn' : 'ok'
      );
      return;
    }
    if (sharing === 'none' && (mode === 'supervised' || mode === 'auto')) {
      setNote(
        'planning-note',
        'STATIC payload sequence, not adaptive AI planning: sharing=none means '
          + 'the agent never sees a target reply. Choose redacted or full for '
          + 'reply-adaptive planning.',
        'warn'
      );
      return;
    }
    if (sharing === 'full') {
      setNote('planning-note', 'Adaptive planning. Target replies are sent VERBATIM to the backend.', 'danger');
      return;
    }
    setNote('planning-note', 'Adaptive planning from redacted target replies.', 'ok');
  }

  function renderSetupEnabled() {
    const frozen = Boolean(capabilities.frozen) || !capabilities.allow_ui_configuration;
    for (const id of ['provider', 'model', 'model-custom', 'mode', 'sharing',
                      'objective', 'max-turns', 'max-seconds', 'apply-config']) {
      $(id).disabled = frozen;
    }
    if (frozen) {
      setNote(
        'config-note',
        capabilities.allow_ui_configuration === false
          ? 'Configuration is command-line only for this session.'
          : 'Configuration is frozen for the duration of this run.',
        'warn'
      );
    }
  }

  function applyCapabilities(payload) {
    capabilities.providers = payload.providers || [];
    capabilities.modes = payload.modes || [];
    capabilities.sharing = payload.sharing || [];
    capabilities.current = payload.current || {};
    capabilities.frozen = payload.frozen;
    capabilities.allow_ui_configuration = payload.allow_ui_configuration;

    fillSelect(
      'provider',
      capabilities.providers.map((p) => ({ value: p.kind, label: p.label })),
      capabilities.current.provider
    );
    fillSelect(
      'mode',
      capabilities.modes.map((m) => ({ value: m, label: MODE_LABELS[m] || m })),
      capabilities.current.mode
    );
    fillSelect(
      'sharing',
      capabilities.sharing.map((s) => ({ value: s, label: SHARING_LABELS[s] || s })),
      capabilities.current.target_data_sharing
    );
    // Show the model that is actually configured. Resetting to Default here
    // made a --model on the command line invisible in the UI.
    const current = capabilities.current.model || '';
    fillSelect(
      'model',
      [{ value: '', label: 'Default (backend chooses)' }].concat(
        current ? [{ value: current, label: current + ' (configured)' }] : []
      ),
      current
    );
    if (current) {
      $('model-custom').value = '';
    }
    $('objective').value = capabilities.current.objective || '';
    $('max-turns').value = capabilities.current.max_turns || 8;
    $('max-seconds').value = capabilities.current.max_duration_seconds || 900;
    renderProviderNote();
    renderPlanningNote();
    renderSetupEnabled();
  }

  /** The configuration the operator is currently looking at. */
  function draft() {
    const custom = $('model-custom').value.trim();
    return {
      provider: $('provider').value,
      model: custom || $('model').value,
      mode: $('mode').value,
      target_data_sharing: $('sharing').value,
      objective: $('objective').value.trim(),
      max_turns: Number($('max-turns').value) || 8,
      max_duration_seconds: Number($('max-seconds').value) || 900,
    };
  }

  // Model discovery is asynchronous and the operator may change provider while
  // a reply is in flight. Tag each request and drop anything that comes back
  // for a provider we are no longer showing.
  let modelRequestId = 0;
  let modelRequestProvider = '';

  function requestModels() {
    modelRequestId += 1;
    modelRequestProvider = $('provider').value;
    setNote('model-note', 'discovering models…');
    port.postMessage({
      channel: 'to-broker',
      frame: {
        type: 'model_list_request',
        payload: { provider: modelRequestProvider, request_id: String(modelRequestId) },
      },
    });
  }

  /** Render the readiness checklist and the sentence beside Start. */
  function renderReadiness(readiness) {
    if (!readiness) return;
    const box = $('checklist');
    box.textContent = '';
    for (const check of readiness.checks || []) {
      const row = document.createElement('div');
      row.className = 'chk ' + check.state;
      const mark = document.createElement('span');
      mark.className = 'mark';
      mark.textContent =
        check.state === 'ok' ? '✓'
        : check.state === 'warn' ? '!'
        : check.state === 'blocked' ? '✗' : '–';
      const label = document.createElement('span');
      label.textContent = check.label;
      const why = document.createElement('span');
      why.className = 'why';
      why.textContent = check.action
        ? '— ' + check.action
        : (check.detail ? '— ' + check.detail : '');
      row.appendChild(mark);
      row.appendChild(label);
      row.appendChild(why);
      box.appendChild(row);
    }
    setNote(
      'start-summary',
      readiness.summary || '',
      readiness.ready ? 'ok' : 'warn'
    );
    // Start stays clickable: pressing it re-validates and shows exactly what
    // is missing, which is more useful than a greyed-out button.
    if (!readiness.ready) {
      $('checklist-box').open = true;
    }
  }

  $('provider').addEventListener('change', () => {
    // A model, an effective model, and a health detail all belong to the
    // provider they came from. Carrying them across would misdescribe the run.
    fillSelect('model', [{ value: '', label: 'Default (backend chooses)' }], '');
    $('model-custom').value = '';
    capabilities.current.model = '';
    capabilities.current.effective_model = null;
    setNote('model-note', '');
    renderProviderNote();
    const spec = currentProviderSpec();
    if (spec && spec.model_discovery) {
      requestModels();
    } else {
      setNote('model-note', 'This backend does not list models; use Default or type one.');
    }
  });
  $('mode').addEventListener('change', renderPlanningNote);
  $('sharing').addEventListener('change', renderPlanningNote);
  $('refresh-models').addEventListener('click', requestModels);

  // Optional. Start applies the same draft, so this is only for operators who
  // want to see the plan before committing.
  $('validate-config').addEventListener('click', () => {
    setNote('config-note', 'validating…');
    port.postMessage({
      channel: 'to-broker',
      frame: { type: 'configure_session', payload: draft() },
    });
  });

  $('capture-reply').addEventListener('click', () => {
    port.postMessage({
      channel: 'to-broker',
      frame: { type: 'run_control', payload: { action: 'capture' } },
    });
    setStatus('capturing the current reply…');
  });

  $('mode-click').addEventListener('change', () => {
    if ($('mode-click').checked) state.submitStrategy = 'click_button';
    renderLocators();
  });
  $('mode-enter').addEventListener('change', () => {
    if ($('mode-enter').checked) state.submitStrategy = 'press_key';
    renderLocators();
  });

  $('save-binding').addEventListener('click', () => {
    if (!state.inputLocator || !state.submitLocator || !state.responseLocator) {
      setStatus('pick the input, send, and reply elements before saving');
      return;
    }
    // Validate every locator against the live page before persisting. A binding
    // that cannot resolve now will not resolve on the next run either.
    const checks = [
      ['input', state.inputLocator],
      ['send', state.submitLocator],
      ['reply', state.responseLocator],
    ];
    for (const [label, locator] of checks) {
      const found = resolveAll(locator);
      if (!found.length) {
        setStatus(`cannot save: ${label} locator matches nothing`);
        return;
      }
      if (label !== 'reply' && found.length > 1) {
        setStatus(`cannot save: ${label} locator is ambiguous (${found.length} matches)`);
        return;
      }
    }
    port.postMessage({
      channel: 'to-broker',
      frame: {
        type: 'save_binding',
        payload: {
          binding: {
            input: locatorToBinding(state.inputLocator, 'first'),
            submit: {
              strategy: state.submitStrategy,
              key: state.submitKey,
              locator: locatorToBinding(state.submitLocator, 'first'),
            },
            response: {
              locator: locatorToBinding(state.responseLocator, 'last'),
              pick: 'last',
              stable_ms: state.captureStableMs || DEFAULT_STABILIZE_MS,
              timeout_ms: state.captureTimeoutMs || DEFAULT_CAPTURE_TIMEOUT_MS,
            },
          },
        },
      },
    });
    setStatus('saving target setup…');
  });

  $('run-start').addEventListener('click', () => {
    // One gesture: apply the draft, validate, and start. No separate Apply.
    port.postMessage({
      channel: 'to-broker',
      frame: { type: 'run_control', payload: { action: 'start', config: draft() } },
    });
    setStatus('starting…');
  });

  $('run-stop').addEventListener('click', () => {
    state.cancelCapture = true;
    port.postMessage({
      channel: 'to-broker',
      frame: { type: 'run_control', payload: { action: 'stop' } },
    });
    setStatus('stop requested — no further message will be sent');
  });

  $('run-approve').addEventListener('click', () => {
    if (!state.awaitingApproval) return;
    state.awaitingApproval = false;
    port.postMessage({
      channel: 'to-broker',
      frame: { type: 'run_control', payload: { action: 'approve' } },
    });
    setStatus('send approved');
  });

  $('generate').addEventListener('click', () => {
    // No instruction is required: the objective is the instruction. An extra
    // one is passed through when the operator supplied it.
    $('stream').textContent = '';
    state.payload = '';
    state.streaming = true;
    renderLocators();
    setStatus('asking the agent…');
    port.postMessage({
      channel: 'to-broker',
      frame: {
        type: 'run_control',
        payload: {
          action: 'generate',
          instruction: $('ask').value.trim(),
          config: draft(),
        },
      },
    });
  });

  $('interrupt').addEventListener('click', () => {
    port.postMessage({ channel: 'to-broker', frame: { type: 'operator_interrupt', payload: {} } });
  });

  $('insert').addEventListener('click', () => {
    const result = opFill(state.inputLocator, state.payload);
    setStatus(result.ok ? 'payload inserted — review it in the page' : result.message);
  });

  $('send').addEventListener('click', async () => {
    if (!state.payload) return;
    snapshotResponses();
    setStatus('sending (operator approved)…');
    $('reply').textContent = '';
    port.postMessage({
      channel: 'to-broker',
      frame: {
        type: 'send_approved',
        payload: {
          approved: true,
          payload: state.payload,
          selector: describeLocator(state.submitLocator),
          key: 'Enter',
        },
      },
    });
  });

  // Drag the dock by its title bar.
  (() => {
    const bar = shadow.querySelector('.bar');
    let dragging = false;
    let ox = 0;
    let oy = 0;
    bar.addEventListener('mousedown', (e) => {
      if (e.target.id === 'hide') return;
      dragging = true;
      const rect = dock.getBoundingClientRect();
      ox = e.clientX - rect.left;
      oy = e.clientY - rect.top;
      e.preventDefault();
    });
    window.addEventListener('mousemove', (e) => {
      if (!dragging) return;
      dock.style.left = `${e.clientX - ox}px`;
      dock.style.top = `${e.clientY - oy}px`;
      dock.style.right = 'auto';
      dock.style.bottom = 'auto';
    });
    window.addEventListener('mouseup', () => {
      dragging = false;
    });
  })();

  // ------------------------------------------------------------ broker frames

  port.onMessage.addListener(async (message) => {
    if (message.channel === 'bystander') {
      state.bound = false;
      setStatus('another tab owns this run; this dock is inactive');
      return;
    }
    if (message.channel === 'broker-state') {
      state.connected = Boolean(message.connected);
      if (message.bound === false) {
        state.bound = false;
        setStatus('another tab owns this run; this dock is inactive');
      }
      $('dot').classList.toggle('on', state.connected);
      if (!state.connected) setStatus('broker disconnected');
      return;
    }
    if (message.channel !== 'broker-frame') return;

    const frame = message.frame;
    if (!frame || typeof frame.type !== 'string') return;
    const payload = frame.payload || {};

    switch (frame.type) {
      case 'ready':
        state.maxTurns = payload.max_turns || 0;
        state.requireApproval = payload.require_send_approval !== false;
        state.runId = payload.run_id || '';
        state.mode = payload.mode || 'manual';
        $('run-info').textContent =
          'mode: ' + state.mode +
          (payload.binding_loaded ? ' · binding: ' + payload.binding_summary : ' · no saved binding');
        $('run-section').style.display = 'block';
        $('capture-reply').style.display =
          state.mode === 'payload_only' ? 'inline-block' : 'none';
        port.postMessage({
          channel: 'to-broker',
          frame: { type: 'capabilities_request', payload: {} },
        });
        setStatus(`ready · ${payload.agent} · turn ${payload.turn}/${state.maxTurns}`);
        renderLocators();
        break;

      case 'agent_event': {
        if (payload.kind === 'text_delta') {
          $('stream').textContent += payload.text || '';
          $('stream').scrollTop = $('stream').scrollHeight;
        } else if (payload.kind === 'message_completed') {
          state.payload = payload.text || '';
          state.streaming = false;
          $('stream').textContent = state.payload;
          renderLocators();
          setStatus(payload.truncated ? 'payload ready (truncated)' : 'payload ready — review it');
        } else if (payload.kind === 'interrupted') {
          state.payload = payload.text || '';
          state.streaming = false;
          renderLocators();
          setStatus('agent interrupted');
        } else if (payload.kind === 'error') {
          state.streaming = false;
          setStatus(`agent error: ${(payload.error && payload.error.message) || 'unknown'}`);
        }
        break;
      }

      case 'perform_operation': {
        // Echo the correlation ids back on every reply so the broker can reject
        // a stale, duplicated, or cross-turn result.
        const correlation = {
          run_id: frame.run_id || '',
          turn_id: frame.turn_id || '',
          operation_id: frame.operation_id || '',
          capture_id: frame.capture_id || '',
        };

        // `extract` is the capture command: wait for the correlated reply to
        // settle, then report it -- or report a typed failure.
        if (payload.operation === 'extract') {
          state.cancelCapture = false;
          setStatus('waiting for the target reply…');
          const captured = await captureResponse({
            stableMs: payload.stable_ms,
            timeoutMs: payload.timeout_ms,
          });
          if (captured.ok) {
            $('reply').textContent = captured.text;
            port.postMessage({
              channel: 'to-broker',
              frame: {
                type: 'target_response',
                payload: Object.assign({ text: captured.text }, correlation),
              },
            });
          } else {
            // Never reported as an empty successful reply: the backend must be
            // able to tell "nothing was disclosed" from "nothing was seen".
            $('reply').textContent = captured.text || '(capture failed)';
            port.postMessage({
              channel: 'to-broker',
              frame: {
                type: 'capture_failed',
                payload: Object.assign(
                  {
                    code: captured.code || 'capture_timeout',
                    elapsed_ms: captured.elapsedMs || 0,
                    partial_text: captured.text || '',
                  },
                  correlation
                ),
              },
            });
            setStatus(`capture failed (${captured.code || 'timeout'})`);
          }
          break;
        }

        const isSubmit = payload.target === 'submit' ||
          (payload.operation === 'press' && payload.target === 'input');
        if (isSubmit) {
          snapshotResponses();
        }

        const result = isSubmit
          ? performSubmit()
          : await performOperation(payload);

        port.postMessage({
          channel: 'to-broker',
          frame: {
            type: 'operation_result',
            payload: Object.assign(
              { ok: Boolean(result.ok), message: result.message || '' },
              correlation
            ),
          },
        });
        if (!result.ok) {
          setStatus(`${payload.operation} failed: ${result.message}`);
          break;
        }

        // Manual mode submits and captures in one gesture: the operator pressed
        // "Approve & send" and expects the reply to appear. Automated modes get
        // an explicit `extract` command from the broker instead, so the engine
        // controls when capture starts.
        if (isSubmit && state.mode === 'manual') {
          state.cancelCapture = false;
          setStatus('waiting for the target reply…');
          const captured = await captureResponse({
            stableMs: state.captureStableMs,
            timeoutMs: state.captureTimeoutMs,
          });
          if (captured.ok) {
            $('reply').textContent = captured.text;
            port.postMessage({
              channel: 'to-broker',
              frame: {
                type: 'target_response',
                payload: Object.assign({ text: captured.text }, correlation, {
                  operation_id: '',
                }),
              },
            });
          } else {
            $('reply').textContent = captured.text || '(capture failed)';
            port.postMessage({
              channel: 'to-broker',
              frame: {
                type: 'capture_failed',
                payload: Object.assign(
                  {
                    code: captured.code || 'capture_timeout',
                    elapsed_ms: captured.elapsedMs || 0,
                    partial_text: captured.text || '',
                  },
                  correlation,
                  { operation_id: '' }
                ),
              },
            });
            setStatus(`capture failed (${captured.code || 'timeout'})`);
          }
        }
        break;
      }

      case 'capabilities': {
        applyCapabilities(payload);
        // Health is a separate question from capability; ask for it too.
        port.postMessage({
          channel: 'to-broker',
          frame: { type: 'provider_health_request', payload: {} },
        });
        break;
      }

      case 'provider_health': {
        capabilities.health = {};
        for (const entry of payload.providers || []) {
          capabilities.health[entry.kind] = entry;
        }
        renderProviderNote();
        break;
      }

      case 'model_list': {
        // Drop a reply for a provider the operator has already moved away from.
        if (payload.provider && payload.provider !== $('provider').value) {
          break;
        }
        if (payload.request_id && payload.request_id !== String(modelRequestId)) {
          break;
        }
        const models = payload.models || [];
        if (payload.error) {
          // Recoverable: Default plus a custom name still works.
          setNote('model-note', 'model list unavailable: ' + payload.error
            + ' — use Default or type a model name', 'warn');
          break;
        }
        if (!models.length) {
          setNote('model-note', 'no models listed; use Default or type a name');
          break;
        }
        const options = [{ value: '', label: 'Default (backend chooses)' }].concat(
          models.map((m) => ({
            value: m.id,
            label: m.label + (m.default ? ' (backend default)' : ''),
          }))
        );
        // Keep the operator's current choice selected rather than resetting it.
        const wanted = capabilities.current.model || '';
        const listed = models.some((m) => m.id === wanted);
        fillSelect('model', options, listed ? wanted : '');
        if (wanted && !listed) {
          $('model-custom').value = wanted;
        }
        setNote('model-note', models.length + ' model(s) offered by the backend', 'ok');
        break;
      }

      case 'session_configured': {
        if (!payload.accepted) {
          setNote('config-note', 'rejected: ' + (payload.message || payload.code), 'danger');
          break;
        }
        capabilities.current = payload.current || capabilities.current;
        // Switching into an automated mode reveals the run controls.
        const chosenMode = (payload.current || {}).mode || state.mode;
        state.mode = chosenMode;
        $('capture-reply').style.display =
          chosenMode === 'payload_only' ? 'inline-block' : 'none';
        renderLocators();
        const warnings = payload.warnings || [];
        setNote(
          'config-note',
          warnings.length ? warnings.join(' · ') : 'settings applied',
          warnings.length ? 'warn' : 'ok'
        );
        renderPlanningNote();
        break;
      }

      case 'run_plan': {
        const bits = [
          'backend ' + payload.provider_label,
          'model ' + (payload.effective_model || payload.model || 'default'),
          'mode ' + payload.mode,
          'planning ' + payload.planning,
          'sharing ' + payload.target_data_sharing,
        ];
        if (!payload.mutations_allowed) bits.push('NO page mutations');
        if (payload.external) bits.push('EXTERNAL provider');
        if (payload.health_state === 'not_configured') {
          bits.push('NOT CONFIGURED');
        } else if (payload.health_state === 'unavailable') {
          bits.push('UNAVAILABLE');
        } else if (!payload.authenticated) {
          // Installed CLIs have no free auth probe; say that rather than
          // implying a check ran and failed.
          bits.push('auth unverified');
        }
        if (payload.max_cost_usd && !payload.cost_reporting) {
          bits.push('cost limit set but this backend reports no cost');
        }
        if (payload.needs_start_confirmation) {
          bits.push('pressing Start confirms unattended sending');
        }
        if (payload.effective_model && payload.effective_model !== payload.model) {
          bits.push('requested ' + (payload.model || 'default'));
        }
        $('run-info').textContent = bits.join(' · ');
        renderReadiness(payload.readiness);
        // Start stays enabled on purpose: clicking it re-validates and shows
        // the checklist, which beats a silently greyed-out button.
        $('run-start').disabled = false;
        break;
      }

      case 'binding': {
        if (payload.saved) {
          setStatus('target setup saved');
          port.postMessage({
            channel: 'to-broker',
            frame: { type: 'run_control', payload: { action: 'plan' } },
          });
          break;
        }
        const loaded = payload.binding;
        if (loaded && loaded.input) {
          state.inputLocator = bindingToLocator(loaded.input);
          state.submitLocator = bindingToLocator(loaded.submit && loaded.submit.locator);
          state.responseLocator = bindingToLocator(loaded.response && loaded.response.locator);
          state.submitStrategy = (loaded.submit && loaded.submit.strategy) || 'click_button';
          state.submitKey = (loaded.submit && loaded.submit.key) || 'Enter';
          state.captureStableMs = (loaded.response && loaded.response.stable_ms) || 0;
          state.captureTimeoutMs = (loaded.response && loaded.response.timeout_ms) || 0;
          $('mode-click').checked = state.submitStrategy === 'click_button';
          $('mode-enter').checked = state.submitStrategy === 'press_key';
          renderLocators();
          setStatus('saved target setup loaded');
        }
        break;
      }

      case 'run_state': {
        if (payload.event === 'awaiting_approval') {
          state.awaitingApproval = true;
          state.payload = payload.payload || '';
          $('stream').textContent = state.payload;
          $('run-info').textContent =
            'awaiting approval — ' + (payload.reasoning_summary || '');
          setStatus('review the payload, then Approve send');
        } else if (payload.event === 'payload_ready') {
          $('stream').textContent = payload.payload || '';
          $('run-info').textContent =
            'turn ' + payload.turn + ' — ' + (payload.reasoning_summary || '');
        } else if (payload.event === 'finished') {
          state.awaitingApproval = false;
          $('run-info').textContent = 'finished: ' + (payload.stop_reason || '');
          setStatus('run finished', payload.status);
        } else if (payload.event === 'turn_complete') {
          $('run-info').textContent =
            'turn ' + payload.turn + ' scored ' + payload.status;
        }
        break;
      }

      case 'status':
        if (payload.captured || (payload.turn || 0) > 0) {
          $('generate').textContent = 'Generate next payload';
        }
        state.turn = payload.turn || 0;
        state.status = payload.status || 'not_detected';
        setStatus(`turn ${state.turn}/${payload.max_turns} · evidence ${payload.evidence_count}`, state.status);
        break;

      case 'error':
        setStatus(`error: ${payload.message || payload.code || 'unknown'}`);
        break;

      default:
        break;
    }
  });

  port.postMessage({ channel: 'config-request' });
})();
