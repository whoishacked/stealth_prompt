/**
 * The Side Panel: the product UI.
 *
 * It owns the connection to the Core while it is open, and rebuilds its whole
 * view from persisted state when reopened. Everything it renders goes through
 * `textContent` — a target response, a provider refusal, and a model's prose
 * are all attacker-influenced text, and none of it becomes markup.
 *
 * In Core mode it proposes and the Core decides. In optional direct mode the
 * operator's click authorizes a bounded extension-side action; the provider key
 * stays in this document's memory and is never persisted.
 */

import {
  BINDING_ROLES,
  bindingComplete,
  bindingSendComplete,
  bindingToCore,
  encodeCoreFrame,
  parseBindingSuggestion,
  parseBindingValidation,
  parseCoreFrame,
  parseLocator,
  parseStoredReport,
} from '../protocol/messages.js';
import type {
  BindingRole,
  InteractionBinding,
  Locator,
  StoredReport,
} from '../protocol/messages.js';
import {
  decisionPrompt,
  evaluationPrompt,
  parseDecision,
  parseEvaluation as parseDirectEvaluation,
  parseProposal,
  prepareSharedResponse,
  proposalPrompt,
  unsharedEvaluation,
  withStructuredRetry,
} from '../direct/session.js';
import { requestHostAccess } from '../permissions/host-access.js';
import {
  PRIMARY_WORKSPACES,
  WORKSPACE_LABELS,
  activeWorkspace,
  canEnter,
  initialUi,
  reduceUi,
  reviewPending,
  runEnded,
  workspaceForError,
} from './navigation.js';
import type { ErrorArea, UiAction, UiState, Workspace } from './navigation.js';
import { parseReportList } from '../protocol/messages.js';
import type { ReportSummary } from '../protocol/messages.js';
import { evaluateReadiness } from '../storage/readiness.js';
import type {
  Action,
  PanelState,
  Evaluation,
  Proposal,
  Sharing,
  AssistMode,
  ConnectionMethod,
  ResponseSource,
  PotentialFindingAction,
} from '../storage/state.js';
import { coercePort, initialState, persistable, reduce, restore } from '../storage/state.js';
import {
  deleteDirectReport,
  listDirectReports,
  saveDirectReport,
} from '../storage/direct-reports.js';
import type { DirectReport } from '../storage/direct-reports.js';

const TOKEN_KEY = 'sp.token';

/** The Core's socket, on whatever port the operator gave `serve --port`. */
function coreUrl(): string {
  return `ws://127.0.0.1:${state.settings.corePort}/ws`;
}

let state: PanelState = initialState();
let socket: WebSocket | null = null;
let stageTimer: number | null = null;
let manualResponse = '';
/** The pairing code as typed, kept outside the DOM so a re-render cannot lose it. */
let pairingCode = '';
/** Direct credentials are intentionally session-only: never persisted or exported. */
let directApiKey = '';
let directStartedAt = 0;
let directReportCreatedAt = 0;
let directSentPayloads: string[] = [];
let directTurns: StoredReport['turns'] = [];
let directConfirmedEvaluation: Evaluation | null = null;
let directRequestId = '';
let recoverAfterCancel = false;
let noticeMessage = '';
let noticeTimer: number | null = null;
/**
 * Transient navigation state. Never persisted: which workspace is open belongs
 * to this panel, not to the assessment.
 */
let ui: UiState = initialUi();

/** The report library, listed on demand from the Core. Never persisted. */
let reports: ReportSummary[] = [];
let reportsRoot = '';
let directReports: DirectReport[] = [];
let viewedReport: {
  summary: ReportSummary;
  document: StoredReport;
  directDocument?: Record<string, unknown>;
} | null = null;
let pendingReport: { reportId: string; artifact: string; view: boolean } | null = null;
/** The last scenario export path, and a previewed import awaiting a decision. */
let scenarioExport = '';
let scenarioPreview: Record<string, unknown> | null = null;
let scenarioDocument: Record<string, unknown> | null = null;
interface ObjectiveSpec {
  id: string;
  title: string;
  category: string;
  description: string;
  standards: string[];
  remediation: string[];
}
let objectiveSpecs: ObjectiveSpec[] = [];

const DIRECT_PROVIDERS = [
  {
    kind: 'openai',
    label: 'OpenAI',
    external: true,
    model_discovery: true,
    custom_model: false,
    summary: 'Direct HTTPS API; key stays in panel memory only.',
  },
  {
    kind: 'anthropic',
    label: 'Anthropic',
    external: true,
    model_discovery: true,
    custom_model: false,
    summary: 'Direct HTTPS API; key stays in panel memory only.',
  },
];

const DIRECT_ORIGINS: Record<string, string> = {
  openai: 'https://api.openai.com',
  anthropic: 'https://api.anthropic.com',
};

/* ------------------------------------------------------------- rendering */

function el(tag: string, className = '', text = ''): HTMLElement {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text; // never innerHTML
  return node;
}

function option(value: string, label: string, selected: boolean): HTMLOptionElement {
  const node = document.createElement('option');
  node.value = value;
  node.textContent = label;
  node.selected = selected;
  return node;
}

function boundedNumber(value: unknown, min: number, max: number, fallback: number): number {
  const number = Number(value);
  return Number.isInteger(number) && number >= min && number <= max ? number : fallback;
}

function dispatch(action: Action): void {
  state = reduce(state, action);
  void persist();
  render();
}

/** Apply a navigation/UI action and re-render. Never persisted. */
function uiDispatch(action: UiAction): void {
  ui = reduceUi(ui, action);
  render();
}

/** Move to a workspace at the operator's request. */
function navigate(workspace: Workspace): void {
  if (!canEnter(state, workspace)) return;
  uiDispatch({ type: 'navigate', workspace });
  // The library is read from disk on arrival rather than cached, so it cannot
  // show a report that has since been deleted.
  if (workspace === 'reports') requestReports();
}

/**
 * Report a failure against the control that caused it.
 *
 * The error is filed to an area, and the area decides which workspace shows it,
 * so a setup failure is never announced from the live run and vice versa. When
 * the responsible workspace is not the current one, the panel follows the error
 * rather than hiding it.
 */
function fail(message: string, area: ErrorArea = 'global'): void {
  ui = reduceUi(ui, { type: 'error', area, message });
  const owner = workspaceForError(area);
  if (canEnter(state, owner)) ui = reduceUi(ui, { type: 'navigate', workspace: owner });
  // Setup collapses finished steps, so an error against a collapsed one would
  // be filed correctly and still be invisible. Open the step it belongs to.
  if (SETUP_ERROR_STEPS.has(area)) {
    ui = reduceUi(ui, {
      type: 'open_step',
      step: area === 'ai' && state.settings.connectionMethod === 'direct' ? 'connection' : area,
    });
  }
  dispatch({ type: 'error', message });
}

/** Error areas that correspond to a collapsible Setup step. */
const SETUP_ERROR_STEPS: ReadonlySet<string> = new Set([
  'connection',
  'ai',
  'target',
  'interaction',
]);

/** Clear an error once its own retry succeeded. */
function clearError(area?: ErrorArea): void {
  ui = reduceUi(ui, { type: 'clear_error', area });
  dispatch({ type: 'clear_error' });
}

async function persist(): Promise<void> {
  try {
    await chrome.runtime.sendMessage({
      channel: 'sp-panel',
      kind: 'put-state',
      state: persistable(state),
    });
  } catch {
    /* the worker may be asleep; the next write will land */
  }
}

const SHARING_LABELS: Record<Sharing, string> = {
  none: 'None — no reply leaves this machine',
  redacted: 'Redacted — credential shapes removed',
  full: 'Full — replies sent verbatim',
};

const OBJECTIVE_LABELS: Record<string, string> = {
  prompt_injection: 'Prompt injection',
  indirect_prompt_injection: 'Indirect prompt injection',
  instruction_disclosure: 'Hidden/system instruction disclosure',
  sensitive_data_disclosure: 'Sensitive data disclosure',
  role_confusion: 'Role / instruction hierarchy confusion',
  goal_hijacking: 'Goal hijacking',
  rag_manipulation: 'RAG / retrieval manipulation',
  memory_poisoning: 'Memory poisoning',
  tool_misuse: 'Tool or action misuse',
  excessive_agency: 'Excessive agency',
  approval_bypass: 'Human approval bypass',
  unsafe_output_handling: 'Unsafe output handling',
  custom: 'Custom objective',
};

const MODE_SHORT_LABELS: Record<AssistMode, string> = {
  payload_only: 'Payload only',
  assist: 'Assist',
  guided: 'Guided',
  auto: 'Auto',
};

const MODE_DESCRIPTIONS: Record<AssistMode, string> = {
  payload_only: 'Generate without touching the page',
  assist: 'Approve each generated message',
  guided: 'Prepare the next message automatically',
  auto: 'Run a bounded adaptive loop',
};

function section(title: string): HTMLElement {
  const node = el('section');
  node.appendChild(el('h2', '', title));
  return node;
}

function targetLabel(): string {
  if (!state.origin) return 'No target selected';
  try {
    return new URL(state.origin).host;
  } catch {
    return state.origin;
  }
}

/**
 * The workspace switcher.
 *
 * Real tabs with real ARIA semantics, switching between separately rendered
 * workspaces. It is not an anchor list: nothing here scrolls, and a workspace
 * the assessment cannot support yet is disabled with a reason rather than
 * silently doing nothing.
 */
function renderSwitcher(root: HTMLElement): void {
  const active = activeWorkspace(state, ui);
  const activeTab: Workspace = active === 'review' ? 'test' : active;
  const visible = PRIMARY_WORKSPACES.filter(
    (workspace) =>
      workspace === 'setup' ||
      (workspace === 'test' && canEnter(state, workspace)) ||
      (workspace === 'reports' &&
        (state.settings.connectionMethod === 'direct'
          || state.connection === 'connected'
          || runEnded(state)
          || active === 'reports')),
  );
  // The whole panel re-renders on every change, so a tab that had focus is
  // destroyed and recreated. Remember it and restore focus after the rebuild,
  // or arrow-key navigation silently drops the keyboard user at the top.
  const refocus = document.activeElement?.getAttribute('role') === 'tab';
  const bar = el('nav', 'switcher');
  bar.setAttribute('aria-label', 'Workspace');
  const tabs = el('div', 'workspace-tabs');
  if (visible.length > 1) {
    tabs.setAttribute('role', 'tablist');
    tabs.setAttribute('aria-label', 'Assessment workspace');
  } else {
    tabs.appendChild(el('span', 'workspace-title', WORKSPACE_LABELS[activeTab]));
  }

  for (const workspace of visible.length > 1 ? visible : []) {
    const current = workspace === activeTab;
    const tab = el('button', `tab${current ? ' active' : ''}`) as HTMLButtonElement;
    tab.type = 'button';
    tab.id = `tab-${workspace}`;
    tab.textContent = WORKSPACE_LABELS[workspace];
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-selected', current ? 'true' : 'false');
    tab.setAttribute('aria-controls', 'workspace');
    // Roving tabindex: the tablist is one stop, arrows move within it.
    tab.tabIndex = current ? 0 : -1;
    if (workspace === 'test' && reviewPending(state)) {
      tab.classList.add('attention');
      tab.setAttribute('aria-label', 'Test — finding decision required');
    }
    tab.onclick = () => navigate(workspace);
    tab.onkeydown = (event: KeyboardEvent) => {
      if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
      event.preventDefault();
      const reachable = visible;
      const index = reachable.indexOf(activeTab);
      const step = event.key === 'ArrowRight' ? 1 : -1;
      const next = reachable[(index + step + reachable.length) % reachable.length];
      if (next) {
        navigate(next);
        document.getElementById(`tab-${next}`)?.focus();
      }
    };
    tabs.appendChild(tab);
  }
  bar.appendChild(tabs);

  const settings = el('button', 'tab settings-tab', '\u2699') as HTMLButtonElement;
  settings.type = 'button';
  settings.id = 'tab-settings';
  settings.title = 'Settings';
  settings.setAttribute('aria-label', 'Settings');
  settings.classList.toggle('active', active === 'settings');
  settings.setAttribute('aria-current', active === 'settings' ? 'page' : 'false');
  settings.onclick = () =>
    active === 'settings' ? uiDispatch({ type: 'follow_state' }) : navigate('settings');
  bar.appendChild(settings);

  root.appendChild(bar);
  if (refocus) document.getElementById(`tab-${activeTab}`)?.focus();
}

/**
 * A one-line status strip: what is being tested, with what, and where it stands.
 *
 * Deliberately compact. During a run the operator needs the payload and one
 * action in view, not the configuration that produced them.
 */
function renderSessionHeader(root: HTMLElement): void {
  const provider = state.providers.find((entry) => entry.kind === state.settings.provider);
  const node = el('div', 'session-header');
  const title = el('div', 'session-target');
  title.appendChild(el('strong', '', targetLabel()));
  // No verdict here. Each workspace states it once, where it is actionable;
  // repeating it in the header put the same answer on screen two or three
  // times and, mid-review, two different answers at once.
  node.appendChild(title);

  const model = state.effectiveModel || state.settings.requestedModel;
  const turnLimit = state.maxTurns === 0 ? '∞' : String(state.maxTurns);
  const bits = [
    provider?.label ?? state.settings.provider,
    model,
    MODE_SHORT_LABELS[state.settings.mode],
    `turn ${state.turns}/${turnLimit}`,
  ].filter(Boolean);
  node.appendChild(el('div', 'session-meta', bits.join(' \u00b7 ')));
  root.appendChild(node);
}

function renderConnection(root: HTMLElement): void {
  const pill = document.getElementById('conn');
  if (pill) {
    // `pairing_required` is an internal enum, not product language.
    pill.textContent =
      state.settings.connectionMethod === 'unset'
        ? 'not configured'
        : state.connection.replaceAll('_', ' ');
    pill.className =
      'pill ' + (state.connection === 'connected' ? 'ok' : state.connection === 'error' ? 'bad' : 'warn');
  }
  const node = section('AI connection');
  node.id = 'connection';
  node.appendChild(el('div', 'note connection-intro', 'Choose how the testing AI will run.'));
  const methods = el('div', 'method-grid');
  for (const [method, title, description] of [
    ['core', 'Local Core', 'Claude/Codex CLI, Ollama and local reports'],
    ['direct', 'Direct API', 'OpenAI or Anthropic key in this panel'],
  ] as const) {
    const selected = state.settings.connectionMethod === method;
    const button = el('button', `method-choice${selected ? ' selected' : ''}`) as HTMLButtonElement;
    button.type = 'button';
    button.setAttribute('aria-pressed', selected ? 'true' : 'false');
    button.appendChild(el('strong', '', title));
    button.appendChild(el('span', '', description));
    button.onclick = () => switchConnectionMethod(method);
    methods.appendChild(button);
  }
  node.appendChild(methods);

  if (state.settings.connectionMethod === 'unset') {
    root.appendChild(node);
    return;
  }

  if (state.settings.connectionMethod === 'direct') {
    node.appendChild(
      el(
        'div',
        'note',
        state.connection === 'connected'
          ? `Direct API ready · ${state.settings.provider}`
          : 'No local service is required. Choose OpenAI or Anthropic and enter a key below.',
      ),
    );
    if (state.connection === 'connected') {
      const disconnect = el('button', '', 'Clear key & disconnect') as HTMLButtonElement;
      disconnect.onclick = () => disconnectDirect();
      node.appendChild(disconnect);
    }
    root.appendChild(node);
    return;
  }

  node.appendChild(
    el(
      'div',
      'note',
      state.connectionDetail ||
        (state.connection === 'connected'
          ? `Connected to Core ${state.coreVersion}`
          : `Core: ${coreUrl()}`),
    ),
  );
  if (state.connection === 'connected') {
    root.appendChild(node);
    return;
  }

  const row = el('div', 'field-action');
  // The port must be settable: `serve --port` is a documented option, and a
  // panel that only ever dials the default cannot reach such a Core.
  const port = document.createElement('input');
  port.id = 'core-port';
  port.type = 'number';
  port.min = '1';
  port.max = '65535';
  port.value = String(state.settings.corePort);
  port.title = 'Core port (stealth-prompt serve --port)';
  port.setAttribute('aria-label', 'Local Core port');
  port.onchange = () =>
    dispatch({ type: 'settings', patch: { corePort: coercePort(port.value) } });
  row.appendChild(port);
  const connect = el('button', 'primary', 'Connect') as HTMLButtonElement;
  connect.onclick = () => void connectToCore();
  row.appendChild(connect);
  node.appendChild(row);
  if (state.pairingRequired) {
    node.appendChild(el('div', 'note warn', 'Enter the pairing code shown in your terminal.'));
    const input = document.createElement('input');
    input.id = 'pair-code';
    input.placeholder = 'ABCD-EFGH';
    input.autocapitalize = 'characters';
    input.setAttribute('aria-label', 'Pairing code');
    // The typed code lives outside the DOM. The panel re-renders whenever any
    // background event arrives, and rebuilding the input would otherwise
    // silently discard a code the operator had already typed.
    input.value = pairingCode;
    input.oninput = () => {
      pairingCode = input.value;
    };
    const pairRow = el('div', 'field-action');
    pairRow.appendChild(input);
    const pair = el('button', 'primary', 'Pair') as HTMLButtonElement;
    pair.onclick = () => send('pair', { code: pairingCode, origin: location.origin });
    pairRow.appendChild(pair);
    node.appendChild(pairRow);
  }
  root.appendChild(node);
}

function renderTarget(root: HTMLElement): void {
  const node = section('Target');
  node.id = 'target';
  node.appendChild(el('div', 'note', state.origin || 'No target tab bound yet.'));
  if (!state.origin) {
    node.appendChild(
      el(
        'div',
        'note',
        'Activate the authorized target tab and click the Stealth Prompt toolbar icon.',
      ),
    );
  } else {
    node.appendChild(
      el('div', 'scope-note', 'Host access and element bindings remain scoped to this origin.'),
    );
  }
  const row = el('div', 'row');
  const bind = el('button', '', state.origin ? 'Change target' : 'Use current tab') as HTMLButtonElement;
  bind.onclick = () => void bindTab();
  row.appendChild(bind);
  node.appendChild(row);
  root.appendChild(node);
}

function renderAlert(root: HTMLElement, area: ErrorArea): void {
  if (!ui.error || ui.error.area !== area) return;
  const node = el('div', 'global-alert');
  node.setAttribute('role', 'alert');
  node.appendChild(el('span', 'note bad', ui.error.message));
  if (area === 'test' && /provider operation is already in progress/i.test(ui.error.message)) {
    const recover = el('button', '', 'Cancel operation & recover') as HTMLButtonElement;
    recover.onclick = () => {
      recoverAfterCancel = true;
      send('cancel', {});
      setNotice('Cancelling the stale provider operation…');
    };
    node.appendChild(recover);
  }
  const dismiss = el('button', 'alert-dismiss', '×') as HTMLButtonElement;
  dismiss.type = 'button';
  dismiss.setAttribute('aria-label', 'Dismiss error');
  dismiss.onclick = () => clearError(area);
  node.appendChild(dismiss);
  root.appendChild(node);
}

function renderNotice(root: HTMLElement): void {
  if (!noticeMessage) return;
  const node = el('div', 'note ok global-notice', noticeMessage);
  node.setAttribute('role', 'status');
  root.appendChild(node);
}

function setNotice(message: string, timeoutMs = 6000): void {
  noticeMessage = message;
  if (noticeTimer !== null) clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => {
    noticeMessage = '';
    noticeTimer = null;
    render();
  }, timeoutMs) as unknown as number;
}

const HEALTH_TEXT: Record<string, { label: string; tone: string; detail: string }> = {
  unknown: {
    label: 'Not checked',
    tone: 'note',
    detail: 'Save the interaction to verify it against this page.',
  },
  healthy: {
    label: 'Healthy',
    tone: 'note ok',
    detail: 'Every bound element resolves on the current document.',
  },
  revalidating: {
    label: 'Re-checking',
    tone: 'note',
    detail: 'The page changed; verifying the saved elements still resolve.',
  },
  needs_review: {
    label: 'Needs review',
    tone: 'note bad',
    detail: 'Sending is blocked until the binding resolves again.',
  },
  unsupported: {
    label: 'Unsupported',
    tone: 'note bad',
    detail:
      'This interaction cannot be reached: it may be in a cross-origin iframe, a closed Shadow DOM, or a canvas UI.',
  },
};

/**
 * The binding-health banner.
 *
 * It is a live region because health changes on its own after a navigation, and
 * a screen-reader user must learn that sending is now blocked without having to
 * re-read the panel.
 */
function renderBindingHealth(): HTMLElement {
  const health = HEALTH_TEXT[state.bindingHealth] ?? HEALTH_TEXT['unknown']!;
  const box = el('div', 'health');
  box.setAttribute('role', 'status');
  box.setAttribute('aria-live', 'polite');
  const line = el('div', health.tone);
  line.appendChild(el('span', 'badge', `Binding: ${health.label}`));
  box.appendChild(line);
  if (state.bindingHealth !== 'healthy') box.appendChild(el('div', 'note', health.detail));
  if (state.bindingHealth === 'needs_review') {
    box.appendChild(
      el('div', 'note', 'Use Re-check, Detect elements, or pick the failing element again.'),
    );
  }
  return box;
}

function renderInteraction(root: HTMLElement): void {
  const node = section('Interaction');
  node.id = 'interaction';
  if (state.settings.mode === 'payload_only') {
    node.appendChild(
      el(
        'div',
        'note ok',
        'This mode never fills or submits the page. No interaction binding is required.',
      ),
    );
    root.appendChild(node);
    return;
  }
  if (!state.origin) {
    node.appendChild(
      el('div', 'note warn', 'Select the authorized target tab before choosing page elements.'),
    );
    root.appendChild(node);
    return;
  }
  const needsPageResponse =
    state.settings.responseSource === 'page';
  const roles: Array<['input' | 'submit' | 'response', string]> = [
    ['input', 'Input'],
    ['submit', 'Send control'],
    ['response', 'Response container'],
  ];
  node.appendChild(renderBindingHealth());

  for (const [role, label] of roles) {
    if (role === 'response' && !needsPageResponse) continue;
    const locator = state.binding[role];
    const issue = state.bindingRoleIssues[role];
    const suggested = state.suggestion?.[role];

    const group = el('div', 'role');
    const line = el('div', 'chk ' + (issue ? 'bad' : locator ? 'ok' : 'bad'));
    line.appendChild(el('span', 'mark', issue ? '!' : locator ? '✓' : '✗'));
    line.appendChild(el('span', '', label));
    line.appendChild(
      el(
        'span',
        'note',
        locator ? ` ${locator.strategy}=${locator.value}`.slice(0, 60) : ' not selected',
      ),
    );
    group.appendChild(line);

    // Name the role that broke, rather than reporting one aggregate failure.
    if (issue) group.appendChild(el('div', 'note bad', issue));

    // A suggestion is reviewed and accepted per role, never auto-saved.
    if (suggested?.locator) {
      const already = locator && locator.value === suggested.locator.value;
      const actions = el('div', already ? 'row role-actions' : 'row');
      const show = el('button', '', 'Highlight') as HTMLButtonElement;
      show.setAttribute('aria-label', `Highlight the suggested ${label.toLowerCase()}`);
      show.onclick = () => void highlightRole(suggested.locator);
      actions.appendChild(show);
      if (!already) {
        const accept = el('button', 'primary', 'Accept') as HTMLButtonElement;
        accept.setAttribute('aria-label', `Accept the suggested ${label.toLowerCase()}`);
        accept.onclick = () => acceptSuggestion(role);
        actions.appendChild(accept);
      }
      const replace = el('button', '', 'Pick manually') as HTMLButtonElement;
      replace.setAttribute('aria-label', `Pick the ${label.toLowerCase()} manually`);
      replace.onclick = () => void pickElement(role);
      actions.appendChild(replace);
      if (already) {
        group.appendChild(actions);
      } else {
        const card = el('div', 'suggestion');
        card.appendChild(
          el('div', 'note', `Suggested — ${suggested.confidence}% confidence`),
        );
        if (suggested.reason) card.appendChild(el('div', 'note', suggested.reason));
        card.appendChild(actions);
        group.appendChild(card);
      }
    }
    node.appendChild(group);
  }

  const row = el('div', 'row interaction-primary');
  const detect = el(
    'button',
    'primary',
    state.stage === 'discovering' ? 'Detecting…' : 'Detect elements',
  ) as HTMLButtonElement;
  detect.disabled = state.stage === 'discovering';
  detect.onclick = () => void discoverElements();
  row.appendChild(detect);
  for (const [role, label] of roles) {
    if (role === 'response' && !needsPageResponse) continue;
    if (state.suggestion?.[role]?.locator) continue; // offered in the card above
    const button = el('button', '', `Select ${label.toLowerCase()}`) as HTMLButtonElement;
    button.onclick = () => void pickElement(role);
    row.appendChild(button);
  }
  node.appendChild(row);
  node.appendChild(
    el('div', 'note helper-note', 'Review suggestions before saving. Nothing is sent from this step.'),
  );

  const row2 = el('div', 'row interaction-save');
  const validate = el('button', '', 'Detect again') as HTMLButtonElement;
  validate.setAttribute('aria-label', 'Re-check the saved binding against this page');
  validate.textContent = state.bindingHealth === 'revalidating' ? 'Checking…' : 'Re-check';
  validate.disabled = state.bindingHealth === 'revalidating';
  validate.onclick = () => void validateBinding();
  const save = el('button', 'primary', 'Save interaction') as HTMLButtonElement;
  const complete = needsPageResponse
    ? bindingComplete(state.binding)
    : bindingSendComplete(state.binding);
  save.disabled = !complete;
  save.onclick = () => void saveBinding();
  row2.appendChild(validate);
  row2.appendChild(save);
  node.appendChild(row2);

  if (state.bindingSaved && state.bindingHealth === 'healthy') {
    node.appendChild(el('div', 'note ok', 'Interaction saved and verified against this page.'));
    const draft = el('div', 'suggestion binding-check');
    draft.appendChild(el('div', 'note', 'Optional binding check — fills the input but never presses Send.'));
    const draftRow = el('div', 'row');
    const fillDraft = el('button', '', 'Fill harmless test draft') as HTMLButtonElement;
    fillDraft.onclick = () => void fillTestDraft();
    const clearDraft = el('button', '', 'Clear draft') as HTMLButtonElement;
    clearDraft.onclick = () => void fillDraftValue('');
    draftRow.appendChild(fillDraft);
    draftRow.appendChild(clearDraft);
    draft.appendChild(draftRow);
    node.appendChild(draft);
  }
  if (!needsPageResponse) {
    node.appendChild(
      el(
        'div',
        'note',
        'Manual trigger is selected: only the input and send control are required.',
      ),
    );
  }
  root.appendChild(node);
}

function renderAi(root: HTMLElement): void {
  const node = section('AI');
  node.id = 'ai';

  node.appendChild(el('label', '', 'Provider'));
  const provider = document.createElement('select');
  const providerSpecs =
    state.settings.connectionMethod === 'direct' ? DIRECT_PROVIDERS : state.providers;
  for (const spec of providerSpecs) {
    provider.appendChild(option(spec.kind, spec.label, spec.kind === state.settings.provider));
  }
  provider.onchange = () => {
    if (state.settings.connectionMethod === 'direct') {
      directApiKey = '';
      dispatch({ type: 'connection', state: 'disconnected', detail: 'Enter the provider key.' });
    }
    dispatch({ type: 'settings', patch: { provider: provider.value } });
    if (state.settings.connectionMethod === 'core') requestModels();
  };
  node.appendChild(provider);

  const health = state.health[state.settings.provider];
  if (health) {
    const tone = health.usable ? 'ok' : 'bad';
    node.appendChild(el('div', `note ${tone}`, `${health.state}: ${health.detail}`));
    if (!health.usable && health.remedy) node.appendChild(el('div', 'note warn', health.remedy));
  }

  if (state.settings.connectionMethod === 'direct') {
    const warning = el('div', 'disclosure');
    warning.appendChild(
      el(
        'div',
        'note warn',
        'Direct mode puts your secret key in the browser process. It is kept only in this open panel, never saved, and cleared when the panel closes. Prefer a restricted project key with a spend limit; use Core when browser-held credentials are unacceptable.',
      ),
    );
    if (state.connection !== 'connected') {
      const key = document.createElement('input');
      key.type = 'password';
      key.autocomplete = 'off';
      key.spellcheck = false;
      key.placeholder = state.settings.provider === 'anthropic' ? 'sk-ant-…' : 'sk-…';
      key.setAttribute('aria-label', `${state.settings.provider} API key`);
      key.value = directApiKey;
      key.oninput = () => {
        directApiKey = key.value.trim();
      };
      const keyRow = el('div', 'field-action');
      keyRow.appendChild(key);
      const connect = el('button', 'primary', 'Use key & load models') as HTMLButtonElement;
      connect.onclick = () => void connectDirect();
      keyRow.appendChild(connect);
      warning.appendChild(keyRow);
    } else {
      warning.appendChild(el('div', 'note ok', 'Key active for this panel session.'));
    }
    node.appendChild(warning);
  }

  node.appendChild(el('label', '', 'Model'));
  const model = document.createElement('select');
  model.disabled = state.settings.connectionMethod === 'direct' && state.connection !== 'connected';
  model.appendChild(
    option(
      '',
      state.settings.connectionMethod === 'direct'
        ? state.connection === 'connected'
          ? 'Select a model'
          : 'Connect a key first'
        : 'Default (backend chooses)',
      state.settings.requestedModel === '',
    ),
  );
  for (const entry of state.models) {
    model.appendChild(
      option(entry.id, entry.label + (entry.default ? ' (backend default)' : ''), entry.id === state.settings.requestedModel),
    );
  }
  // A configured model that the backend did not list still belongs in the UI.
  if (state.settings.requestedModel && !state.models.some((m) => m.id === state.settings.requestedModel)) {
    model.appendChild(option(state.settings.requestedModel, `${state.settings.requestedModel} (configured)`, true));
  }
  model.onchange = () => dispatch({ type: 'settings', patch: { requestedModel: model.value } });
  node.appendChild(model);
  if (state.modelsError) node.appendChild(el('div', 'note warn', `Model list unavailable: ${state.modelsError}`));
  if (state.effectiveModel) {
    node.appendChild(el('div', 'note ok', `Effective model: ${state.effectiveModel}`));
  }

  node.appendChild(el('label', '', 'Data sharing'));
  const sharing = document.createElement('select');
  for (const value of ['none', 'redacted', 'full'] as Sharing[]) {
    sharing.appendChild(option(value, SHARING_LABELS[value], value === state.settings.sharing));
  }
  sharing.onchange = () => {
    dispatch({ type: 'settings', patch: { sharing: sharing.value as Sharing } });
  };
  node.appendChild(sharing);

  const spec = providerSpecs.find((entry) => entry.kind === state.settings.provider);
  if (state.settings.sharing === 'none') {
    node.appendChild(
      el('div', 'note warn', 'Replies are never sent to the provider, so response analysis is limited to local deterministic checks.'),
    );
  } else if (spec?.external) {
    node.appendChild(
      el('div', 'note warn', `Target replies will be sent to ${spec.label}, which leaves this machine.`),
    );
  } else {
    node.appendChild(el('div', 'note ok', 'Target replies stay on this machine.'));
  }
  if (spec?.external) {
    const disclosure = el('div', 'disclosure');
    disclosure.appendChild(
      el(
        'div',
        'note warn',
        `${spec.label} is an external provider. It receives the test objective and permitted target context under your provider account.`,
      ),
    );
    node.appendChild(disclosure);
  } else if (spec) {
    const disclosure = el('div', 'disclosure local');
    disclosure.appendChild(
      el('div', 'note ok', 'Local provider path — no target content leaves this machine.'),
    );
    node.appendChild(disclosure);
  }
  root.appendChild(node);
}

function renderMode(root: HTMLElement): void {
  const node = section('Behavior');
  node.id = 'mode';
  const mode = el('div', 'mode-grid');
  for (const value of ['payload_only', 'assist', 'guided', 'auto'] as AssistMode[]) {
    const choice = el(
      'button',
      `mode-choice ${value === state.settings.mode ? 'selected' : ''}`,
    ) as HTMLButtonElement;
    choice.type = 'button';
    choice.setAttribute('aria-pressed', String(value === state.settings.mode));
    choice.appendChild(el('strong', '', MODE_SHORT_LABELS[value]));
    choice.appendChild(el('span', '', MODE_DESCRIPTIONS[value]));
    choice.onclick = () => dispatch({ type: 'settings', patch: { mode: value } });
    mode.appendChild(choice);
  }
  node.appendChild(mode);
  node.appendChild(
    el(
      'div',
      'note',
      state.settings.mode === 'auto'
        ? 'One explicit start authorizes a bounded send → capture → analyze loop. Stop is always available.'
        : state.settings.mode === 'guided'
          ? 'The next payload is generated after analysis, but you approve every send.'
          : state.settings.mode === 'assist'
            ? 'You request payloads and approve every send.'
            : 'Generates payloads only; the target page is never changed.',
    ),
  );

  if (state.settings.mode === 'auto') {
    node.appendChild(el('label', '', 'When a potential finding appears'));
    const action = document.createElement('select');
    for (const [value, label] of [
      ['review', 'Pause for review'],
      ['stop', 'Stop and save report'],
      ['continue', 'Continue and record'],
    ] as Array<[PotentialFindingAction, string]>) {
      const item = option(value, label, value === state.settings.potentialFindingAction);
      item.disabled = value === 'continue' && state.settings.maxTurns === 0;
      action.appendChild(item);
    }
    action.onchange = () => dispatch({
      type: 'settings',
      patch: { potentialFindingAction: action.value as PotentialFindingAction },
    });
    node.appendChild(action);
    node.appendChild(
      el(
        'div',
        'note',
        state.settings.potentialFindingAction === 'review'
          ? 'Pauses before another send so you can confirm or continue.'
          : state.settings.potentialFindingAction === 'stop'
            ? 'Ends the run on the first model-identified signal and preserves it as potential.'
            : 'Keeps every signal in the report and runs until confirmation or a configured limit.',
      ),
    );
    if (state.settings.maxTurns === 0) {
      node.appendChild(
        el(
          'div',
          'note warn',
          'Unlimited turns require Pause or Stop on a potential finding. Continue and record is disabled.',
        ),
      );
    }
  }

  node.appendChild(el('label', '', 'Response trigger'));
  const trigger = el('div', 'segmented');
  for (const [value, label] of [
    ['page', 'Capture from page'],
    ['manual', 'Paste response'],
  ] as Array<[ResponseSource, string]>) {
    const button = el(
      'button',
      state.settings.responseSource === value ? 'selected' : '',
      label,
    ) as HTMLButtonElement;
    button.type = 'button';
    button.disabled = state.settings.mode === 'auto' && value === 'manual';
    button.onclick = () =>
      dispatch({ type: 'settings', patch: { responseSource: value } });
    trigger.appendChild(button);
  }
  node.appendChild(trigger);
  if (state.settings.mode === 'auto') {
    node.appendChild(
      el('div', 'note warn', 'Auto requires response capture from the bound page.'),
    );
  }

  node.appendChild(el('label', '', 'Objective'));
  const objective = document.createElement('select');
  if (objectiveSpecs.length) {
    const groups = new Map<string, ObjectiveSpec[]>();
    for (const spec of objectiveSpecs) {
      const group = groups.get(spec.category) ?? [];
      group.push(spec);
      groups.set(spec.category, group);
    }
    for (const [category, specs] of groups) {
      const group = document.createElement('optgroup');
      group.label = category;
      for (const spec of specs) {
        group.appendChild(option(spec.id, spec.title, spec.id === state.settings.objective));
      }
      objective.appendChild(group);
    }
  } else {
    for (const [value, label] of Object.entries(OBJECTIVE_LABELS)) {
      objective.appendChild(option(value, label, value === state.settings.objective));
    }
  }
  objective.onchange = () => dispatch({ type: 'settings', patch: { objective: objective.value } });
  node.appendChild(objective);
  const objectiveSpec = objectiveSpecs.find((entry) => entry.id === state.settings.objective);
  if (objectiveSpec) {
    node.appendChild(el('div', 'note', objectiveSpec.description));
    node.appendChild(
      el('div', 'scope-note', objectiveSpec.standards.join(' · ')),
    );
  }

  if (state.settings.objective === 'custom') {
    const custom = document.createElement('textarea');
    custom.value = state.settings.customObjective;
    custom.placeholder = 'Describe the authorized objective.';
    custom.onchange = () => dispatch({ type: 'settings', patch: { customObjective: custom.value } });
    node.appendChild(custom);
  }

  root.appendChild(node);
}

function renderSettings(root: HTMLElement): void {
  const node = el('section');
  node.id = 'settings';
  node.appendChild(
    el('div', 'note', 'Bounds and optional steering for this assessment.'),
  );

  const limits = el('div', 'settings-grid');
  const turnsBox = el('div');
  turnsBox.appendChild(el('label', '', 'Turn limit'));
  const turnsRow = el('div', 'limit-row');
  const turns = document.createElement('input');
  turns.type = 'number';
  turns.min = '1';
  turns.max = '100';
  turns.value = state.settings.maxTurns === 0 ? '' : String(state.settings.maxTurns);
  turns.placeholder = 'Unlimited';
  turns.disabled = state.settings.maxTurns === 0;
  turns.onchange = () =>
    dispatch({
      type: 'settings',
      patch: { maxTurns: boundedNumber(turns.value, 1, 100, 20) },
    });
  turnsRow.appendChild(turns);
  const unlimitedTurns = el(
    'button',
    state.settings.maxTurns === 0 ? 'selected' : '',
    'Unlimited',
  ) as HTMLButtonElement;
  unlimitedTurns.type = 'button';
  unlimitedTurns.setAttribute('aria-pressed', String(state.settings.maxTurns === 0));
  unlimitedTurns.onclick = () => dispatch({
    type: 'settings',
    patch: state.settings.maxTurns === 0
      ? { maxTurns: 20 }
      : {
          maxTurns: 0,
          potentialFindingAction:
            state.settings.potentialFindingAction === 'continue'
              ? 'review'
              : state.settings.potentialFindingAction,
        },
  });
  turnsRow.appendChild(unlimitedTurns);
  turnsBox.appendChild(turnsRow);
  limits.appendChild(turnsBox);

  const durationBox = el('div');
  durationBox.appendChild(el('label', '', 'Time limit (seconds)'));
  const durationRow = el('div', 'limit-row');
  const duration = document.createElement('input');
  duration.type = 'number';
  duration.min = '30';
  duration.max = '1800';
  duration.value = state.settings.maxDurationSeconds === 0
    ? ''
    : String(state.settings.maxDurationSeconds);
  duration.placeholder = 'Unlimited';
  duration.disabled = state.settings.maxDurationSeconds === 0;
  duration.onchange = () =>
    dispatch({
      type: 'settings',
      patch: { maxDurationSeconds: boundedNumber(duration.value, 30, 1800, 300) },
    });
  durationRow.appendChild(duration);
  const unlimitedDuration = el(
    'button',
    state.settings.maxDurationSeconds === 0 ? 'selected' : '',
    'Unlimited',
  ) as HTMLButtonElement;
  unlimitedDuration.type = 'button';
  unlimitedDuration.setAttribute(
    'aria-pressed',
    String(state.settings.maxDurationSeconds === 0),
  );
  unlimitedDuration.onclick = () => dispatch({
    type: 'settings',
    patch: { maxDurationSeconds: state.settings.maxDurationSeconds === 0 ? 300 : 0 },
  });
  durationRow.appendChild(unlimitedDuration);
  durationBox.appendChild(durationRow);
  limits.appendChild(durationBox);
  node.appendChild(limits);
  if (state.settings.maxTurns === 0 && state.settings.maxDurationSeconds === 0) {
    node.appendChild(
      el(
        'div',
        'note warn',
        'With both limits unlimited, Auto runs until a potential finding pauses/stops it or you press Stop. Token use is not bounded.',
      ),
    );
  }

  node.appendChild(el('label', '', 'Advanced instruction (optional)'));
  const advanced = document.createElement('textarea');
  advanced.value = state.settings.advancedInstruction;
  advanced.placeholder = 'Optional steering for payload generation and analysis.';
  advanced.onchange = () =>
    dispatch({ type: 'settings', patch: { advancedInstruction: advanced.value } });
  node.appendChild(advanced);
  root.appendChild(node);
}

function renderSettingsPage(root: HTMLElement): void {
  const head = el('div', 'page-heading');
  const copy = el('div');
  copy.appendChild(el('h2', '', 'Settings'));
  copy.appendChild(el('div', 'note', 'Defaults and reusable assessment options.'));
  head.appendChild(copy);
  const back = el('button', '', '\u2190 Back') as HTMLButtonElement;
  back.type = 'button';
  back.onclick = () => uiDispatch({ type: 'follow_state' });
  head.appendChild(back);
  root.appendChild(head);
  renderSettings(root);
  renderScenario(root);
}

function renderManualTrigger(root: HTMLElement): void {
  if (state.settings.responseSource !== 'manual' || state.settings.mode === 'auto') return;
  const node = section('Manual response trigger');
  node.appendChild(
    el(
      'div',
      'note',
      'Paste the latest bot reply when automatic capture is unreliable. It is handled according to the selected data-sharing policy.',
    ),
  );
  const input = document.createElement('textarea');
  input.id = 'manual-response';
  input.placeholder = 'Paste the bot response here…';
  input.value = manualResponse;
  input.maxLength = 262144;
  input.oninput = () => {
    manualResponse = input.value;
    counter.textContent = `${manualResponse.length.toLocaleString()} / 262,144`;
  };
  node.appendChild(input);
  const counter = el('div', 'note counter', `${manualResponse.length.toLocaleString()} / 262,144`);
  node.appendChild(counter);
  const submit = el('button', 'primary', 'Analyze & generate next payload') as HTMLButtonElement;
  submit.disabled = !manualResponse.trim() || ['generating', 'evaluating'].includes(state.stage);
  submit.onclick = () => submitManualResponse();
  node.appendChild(submit);
  root.appendChild(node);
}

function renderProposal(root: HTMLElement): void {
  const node = section('Proposal');
  const actions = el('div', 'row');
  node.id = 'proposal';
  const readiness = evaluateReadiness(state);
  const working = state.stage === 'generating' || state.stage === 'evaluating';
  const inFlight = state.stage === 'sending' || state.stage === 'waiting_for_response';
  const pausedForReview = state.autoStopReason === 'potential_review';
  const autoRecoveryRequired = state.settings.mode === 'auto' && Boolean(state.autoStopReason);
  node.classList.add(
    'proposal-card',
    working
      ? 'status-generating'
      : state.proposal
        ? 'status-ready'
        : inFlight
          ? 'status-active'
          : 'status-idle',
  );

  if (working) {
    const seconds = Math.max(0, Math.round((Date.now() - state.stageStartedAt) / 1000));
    const progress = el('div', 'generation-status');
    const spinner = el('span', 'spinner');
    spinner.setAttribute('aria-hidden', 'true');
    progress.appendChild(spinner);
    progress.appendChild(
      el(
        'span',
        '',
        `${state.stage === 'evaluating' ? 'Analyzing response' : 'Generating next payload'}… ${seconds}s`,
      ),
    );
    progress.setAttribute('role', 'status');
    progress.setAttribute('aria-live', 'polite');
    node.appendChild(progress);
    const cancel = el('button', 'danger', 'Cancel') as HTMLButtonElement;
    cancel.onclick = () => cancelGeneration();
    actions.appendChild(cancel);
  }

  if (inFlight) {
    const message =
      state.stage === 'sending'
        ? 'Sending the authorized payload…'
        : state.settings.responseSource === 'manual'
          ? 'Payload sent. Paste the bot reply above to continue.'
          : 'Waiting for the bound response container…';
    const status = el('div', 'generation-status active', message);
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    node.appendChild(status);
  }

  if (state.stage === 'refused') {
    node.appendChild(el('div', 'note bad', 'The provider declined to create a proposal.'));
    if (state.refusalExcerpt) node.appendChild(el('div', 'out', state.refusalExcerpt));
    node.appendChild(
      el('div', 'note', 'Adjust the objective, edit a payload by hand, or choose another provider.'),
    );
  }

  if (state.proposal && !working) {
    // Only report a duration the operator could act on. "Generated: 0.0s" is
    // decoration, and it competed with the payload for attention.
    if (state.lastLatencyMs >= 1000) {
      node.appendChild(
        el(
          'div',
          'note ok',
          `${state.lastLatencyLabel} in ${(state.lastLatencyMs / 1000).toFixed(1)}s`,
        ),
      );
    }
    node.appendChild(el('div', 'note', `Goal: ${state.proposal.goal}`));
    node.appendChild(el('div', 'note', `Tactic: ${state.proposal.tactic}`));
    node.appendChild(el('div', 'kv').appendChild(el('span', '', 'Hypothesis')).parentElement!);
    node.appendChild(el('div', 'note', state.proposal.hypothesis));
    node.appendChild(el('label', '', 'Payload (editable)'));
    const payload = document.createElement('textarea');
    payload.id = 'payload';
    payload.value = state.editedPayload;
    payload.oninput = () => dispatch({ type: 'proposal_edited', payload: payload.value });
    node.appendChild(payload);
    if (state.proposal.expected_signals.length) {
      node.appendChild(
        el('div', 'note', `Expected signal: ${state.proposal.expected_signals.join('; ')}`),
      );
    }
    node.appendChild(el('div', 'note', `Risk: ${state.proposal.risk}`));

    const row = el('div', 'row');
    const copy = el('button', '', 'Copy') as HTMLButtonElement;
    copy.onclick = () => void navigator.clipboard.writeText(state.editedPayload);
    if (!autoRecoveryRequired) {
      const regenerate = el('button', '', 'Regenerate') as HTMLButtonElement;
      regenerate.onclick = () => requestProposal();
      row.appendChild(regenerate);
    }
    row.appendChild(copy);

    if (pausedForReview) {
      const review = el(
        'button',
        'primary',
        state.evaluation ? 'Review finding' : 'Recover finding review',
      ) as HTMLButtonElement;
      review.onclick = () => openOrRecoverReview();
      row.appendChild(review);
    }

    if (state.settings.mode !== 'payload_only' && state.settings.mode !== 'auto') {
      const approve = el('button', 'primary', 'Approve and send') as HTMLButtonElement;
      approve.id = 'approve-send';
      approve.onclick = () => void approveAndSend();
      row.appendChild(approve);
    } else if (state.settings.mode === 'payload_only') {
      node.appendChild(
        el('div', 'note warn', 'Payload-only mode: nothing is typed or sent. Copy the payload instead.'),
      );
    }
    node.appendChild(row);
    if (state.settings.mode === 'auto') {
      node.appendChild(
        el(
          'div',
          'note ok',
          pausedForReview
            ? 'Auto is paused until the potential finding is reviewed.'
            : state.autoRunning
            ? 'Auto run is active. This payload will be sent without another approval.'
            : 'Auto is ready. Start again to begin a new bounded run.',
        ),
      );
    }
  } else if (!working && !inFlight) {
    // The live workspace never repeats Setup. Readiness blockers and the Start
    // control belong beside the fields that fix them; here the operator either
    // asks for the next payload or is told, in one line, where to go.
    if (pausedForReview) {
      node.appendChild(
        el('div', 'note', 'A potential finding is waiting for your decision.'),
      );
      const review = el(
        'button',
        'primary',
        state.evaluation ? 'Review finding' : 'Recover finding review',
      ) as HTMLButtonElement;
      review.onclick = () => openOrRecoverReview();
      node.appendChild(review);
    } else if (autoRecoveryRequired) {
      node.appendChild(
        el('div', 'note', 'Resolve the paused run below before generating another payload.'),
      );
    } else if (readiness.ready) {
      node.appendChild(
        el('div', 'note', 'No payload is prepared. Generate the next one when you are ready.'),
      );
      const next = el('button', 'primary', 'Generate payload') as HTMLButtonElement;
      next.onclick = () => requestProposal();
      node.appendChild(next);
    } else {
      node.appendChild(
        el('div', 'note warn', 'This run cannot continue until setup is complete.'),
      );
      const fix = el('button', 'primary', 'Open Setup') as HTMLButtonElement;
      fix.onclick = () => navigate('setup');
      node.appendChild(fix);
    }
  }
  if (state.autoRunning) {
    const stop = el('button', 'danger', 'Stop auto run') as HTMLButtonElement;
    stop.onclick = () => stopSession();
    actions.appendChild(stop);
  } else if (state.autoStopReason) {
    node.appendChild(
      el(
        'div',
        'note warn',
        state.autoStopReason === 'potential_review'
          ? 'Auto paused: open the finding review to decide.'
          : `Auto stopped: ${state.autoStopReason.replaceAll('_', ' ').replace(/\.+$/, '')}.`,
      ),
    );
    if (state.autoStopReason === 'max_turns' || state.autoStopReason === 'max_duration') {
      const exhaustedTurns = state.autoStopReason === 'max_turns';
      const more = el(
        'button',
        'primary',
        exhaustedTurns ? `Continue +${state.settings.maxTurns} turns` : 'Continue auto run',
      ) as HTMLButtonElement;
      more.onclick = () => continueAutoRun(exhaustedTurns);
      node.appendChild(more);
    } else if (
      state.autoStopReason === 'analysis failed'
      || state.autoStopReason === 'proposal failed'
      || state.autoStopReason === 'proposal_failed'
      || state.autoStopReason === 'error'
      || state.autoStopReason === 'cancelled'
    ) {
      const retry = el(
        'button',
        'primary',
        state.autoStopReason === 'analysis failed'
          ? 'Retry analysis & continue'
          : 'Retry generation & continue',
      ) as HTMLButtonElement;
      retry.onclick = () => continueAutoRun(false);
      node.appendChild(retry);
    } else if (state.autoStopReason === 'core_session_lost') {
      const recover = el('button', 'primary', 'Recover run') as HTMLButtonElement;
      recover.onclick = () => void recoverCoreRun();
      node.appendChild(recover);
    }
  } else if (
    state.sessionEnded
    && state.settings.mode === 'auto'
    && state.verdict !== 'confirmed'
    && (state.maxTurns === 0 || state.turns < state.maxTurns)
  ) {
    const resume = el('button', 'primary', 'Continue auto run') as HTMLButtonElement;
    resume.onclick = () => continueAutoRun(false);
    node.appendChild(resume);
  }
  if (actions.childElementCount) node.appendChild(actions);
  root.appendChild(node);
}

function renderEvaluation(root: HTMLElement): void {
  if (!state.evaluation) return;
  const node = section('Finding review');
  node.id = 'results';
  const verdict = state.evaluation.verdict;
  const needsReview = verdict === 'potential' && state.autoStopReason === 'potential_review';
  if (needsReview) node.classList.add('review-paused');

  // Evidence first. The decision controls come after the reader has seen what
  // the decision is about; putting "Confirm" above the summary invites
  // confirming a finding nobody read.
  const tone = verdict === 'confirmed' ? 'bad' : verdict === 'potential' ? 'warn' : 'ok';
  const head = el('div', 'verdict-line');
  head.appendChild(el('span', `verdict-dot ${verdict}`, ''));
  head.appendChild(el('strong', '', verdict.replaceAll('_', ' ')));
  if (verdict === 'potential') {
    head.appendChild(el('span', 'note', 'not confirmed'));
  }
  node.appendChild(head);

  if (needsReview) {
    node.appendChild(
      el('div', `note ${tone}`, 'Automatic sending is paused until you decide.'),
    );
  }
  if (state.evaluation.summary) node.appendChild(el('div', 'note', state.evaluation.summary));

  if (state.evaluation.observed_signals.length) {
    node.appendChild(el('h3', 'sub', 'Observed signals'));
    const list = el('ul', 'plain');
    for (const signal of state.evaluation.observed_signals) {
      list.appendChild(el('li', '', signal));
    }
    node.appendChild(list);
  }
  if (state.evaluation.suggested_next_steps.length) {
    node.appendChild(el('h3', 'sub', 'Suggested next steps'));
    const list = el('ul', 'plain');
    for (const step of state.evaluation.suggested_next_steps) {
      list.appendChild(el('li', '', step));
    }
    node.appendChild(list);
  }
  if (needsReview && state.proposal) {
    node.appendChild(el('h3', 'sub', 'Prepared next payload'));
    node.appendChild(el('div', 'out', state.proposal.payload));
  }

  const row = el('div', 'row');
  if (needsReview) {
    // Continuing is the neutral, reversible choice, so it is the default
    // emphasis. Confirmation asserts a finding, so both confirmation choices
    // remain deliberate rather than becoming the biggest button on screen.
    const resume = el('button', 'primary', 'Not confirmed \u2014 continue') as HTMLButtonElement;
    resume.onclick = () => resumeAuto(false);
    row.appendChild(resume);
    const confirmAndContinue = el('button', '', 'Confirm & continue') as HTMLButtonElement;
    confirmAndContinue.onclick = () => resumeAuto(true);
    row.appendChild(confirmAndContinue);
    const confirm = el('button', '', 'Confirm finding & stop') as HTMLButtonElement;
    confirm.onclick = () => {
      if (state.settings.connectionMethod === 'direct') confirmDirectFinding();
      else send('finding.confirm', {});
      stopSession();
    };
    row.appendChild(confirm);
  } else if (!state.autoRunning) {
    const next = el('button', 'primary', 'Generate next payload') as HTMLButtonElement;
    next.onclick = () => requestProposal();
    row.appendChild(next);
  }
  if (!needsReview && verdict !== 'confirmed' && !state.autoRunning) {
    const confirm = el('button', '', 'Confirm finding') as HTMLButtonElement;
    confirm.onclick = () => {
      if (state.settings.connectionMethod === 'direct') confirmDirectFinding();
      else send('finding.confirm', {});
      dispatch({ type: 'session_ended', reason: 'confirmed by operator' });
      uiDispatch({ type: 'follow_state' });
    };
    row.appendChild(confirm);
  }
  const stop = el('button', 'danger', 'Stop without confirming') as HTMLButtonElement;
  stop.onclick = () => stopSession();
  row.appendChild(stop);
  node.appendChild(row);
  root.appendChild(node);
}

function exportSession(): void {
  if (state.settings.connectionMethod === 'direct') exportDirectSession();
  else send('session.export', {});
}

function renderEvidence(root: HTMLElement): void {
  const node = section('Evidence & reports');
  node.id = 'evidence';
  const metrics = el('div', 'evidence-grid');
  // Turns and the model are facts about the run. The verdict is stated once, in
  // the run summary above, and the timeline event count was an internal
  // implementation number dressed up as a metric.
  for (const [label, value] of [
    ['Turns', String(state.turns)],
    ['Model', state.effectiveModel || state.settings.requestedModel || 'default'],
  ]) {
    const metric = el('div', 'evidence-metric');
    metric.appendChild(el('span', '', label));
    metric.appendChild(el('strong', '', value));
    metrics.appendChild(metric);
  }
  node.appendChild(metrics);

  const ready = Boolean(state.evaluation || state.turns || state.timeline.length);
  node.appendChild(
    el(
      'div',
      'note',
      ready
        ? state.settings.connectionMethod === 'direct'
          ? 'Download a key-free JSON record of this assessment.'
          : 'Save the complete HTML and JSON evidence bundle through Local Core.'
        : 'The report becomes available after the first test activity.',
    ),
  );
  const exportButton = el(
    'button',
    ready ? 'primary' : '',
    state.settings.connectionMethod === 'direct' ? 'Download JSON report' : 'Save evidence report',
  ) as HTMLButtonElement;
  exportButton.disabled = !ready;
  exportButton.onclick = () => exportSession();
  node.appendChild(exportButton);
  root.appendChild(node);
}

function confirmDirectFinding(): void {
  if (!state.evaluation) return;
  directConfirmedEvaluation = {
    ...state.evaluation,
    verdict: 'confirmed',
    deterministic: true,
    summary: state.evaluation.summary || 'Confirmed by the operator.',
  };
  dispatch({
    type: 'evaluation',
    evaluation: directConfirmedEvaluation,
  });
  dispatch({ type: 'session', session: { verdict: 'confirmed' } });
  void saveDirectReportSnapshot();
}

function resumeAuto(confirmFinding: boolean): void {
  const proposal = state.proposal;
  if (state.settings.connectionMethod === 'direct') {
    if (confirmFinding) confirmDirectFinding();
  } else {
    send(
      confirmFinding ? 'finding.confirm' : 'auto.start',
      confirmFinding ? { continue: true } : {},
    );
  }
  dispatch({ type: 'auto_stopped', reason: '' });
  dispatch({ type: 'auto_started' });
  uiDispatch({ type: 'follow_state' });
  if (state.settings.connectionMethod === 'direct' && proposal) {
    void performDirectSend(proposal.payload);
  }
}

function directReportDocument(): Record<string, unknown> {
  return {
    kind: 'assistant_session',
    schema_version: 1,
    exported_at: new Date().toISOString(),
    session_id: state.sessionId,
    configuration: {
      runtime: 'direct_api',
      origin: state.origin,
      provider: state.settings.provider,
      provider_label: state.settings.provider,
      requested_model: state.settings.requestedModel,
      effective_model: state.effectiveModel,
      mode: state.settings.mode,
      potential_finding_action: state.settings.potentialFindingAction,
      sharing: state.settings.sharing,
      objective: state.settings.objective,
      objective_text: state.settings.customObjective || state.settings.objective,
    },
    turns: directTurns.map((turn) => ({
      turn_id: turn.turnId,
      started_at: turn.startedAt,
      approved: turn.approved,
      proposal: {
        goal: turn.goal,
        tactic: turn.tactic,
        hypothesis: turn.hypothesis,
        payload: turn.payload,
      },
      approved_payload: turn.payload,
      response: turn.response,
      evaluation: {
        verdict: turn.verdict,
        summary: turn.evaluationSummary,
        observed_signals: turn.observedSignals,
      },
    })),
    verdict: state.verdict,
    confirmed_finding: directConfirmedEvaluation,
    timeline: state.timeline,
  };
}

function directReportSummary(report: DirectReport): ReportSummary {
  return {
    reportId: report.reportId,
    createdAt: report.createdAt,
    targetOrigin: report.parsed.targetOrigin,
    objective: report.parsed.objective,
    verdict: report.parsed.verdict,
    turns: report.parsed.turns.length,
    effectiveModel: report.parsed.effectiveModel,
    provider: report.parsed.provider,
    artifacts: ['session.json'],
  };
}

async function saveDirectReportSnapshot(): Promise<DirectReport | null> {
  if (!state.sessionId.startsWith('direct-')) return null;
  try {
    const report = await saveDirectReport({
      reportId: state.sessionId,
      createdAt: new Date(directReportCreatedAt || Date.now()).toISOString(),
      document: directReportDocument(),
    });
    directReports = [report, ...directReports.filter((item) => item.reportId !== report.reportId)];
    return report;
  } catch (error) {
    setNotice(`${String((error as Error).message)} Use Download JSON report to keep a copy.`, 10000);
    return null;
  }
}

async function exportDirectSession(): Promise<void> {
  const document_ = directReportDocument();
  await saveDirectReportSnapshot();
  downloadText(
    `stealth-prompt-${state.sessionId || 'direct-session'}.json`,
    JSON.stringify(document_, null, 2),
    'application/json',
  );
  setNotice('Direct session saved locally and downloaded. It contains no API key.');
  render();
}

/** Internal event kinds are not product language. */
const TIMELINE_LABELS: Record<string, string> = {
  'session.started': 'Session started',
  'interaction.bound': 'Interaction saved',
  'proposal.generated': 'Payload generated',
  'proposal.approved': 'Payload approved',
  'payload.sent': 'Payload sent',
  'response.captured': 'Response captured',
  'evaluation.completed': 'Response analysed',
  'session.stopped': 'Session stopped',
  error: 'Error',
};

function timelineLabel(kind: string): string {
  return TIMELINE_LABELS[kind] ?? kind.replaceAll('.', ' ').replaceAll('_', ' ');
}

function renderTimeline(root: HTMLElement): void {
  if (!state.timeline.length) return;
  const node = document.createElement('details');
  node.className = 'activity';
  const summary = document.createElement('summary');
  summary.textContent = `Activity · ${state.turns}/${state.maxTurns || '∞'} turns · ${state.timeline.length} events`;
  node.appendChild(summary);
  for (const entry of state.timeline.slice(-12)) {
    const line = el('div', 'timeline-row');
    line.appendChild(el('span', '', timelineLabel(entry.kind)));
    if (entry.detail) line.appendChild(el('span', 'note', entry.detail));
    node.appendChild(line);
  }
  root.appendChild(node);
}

/**
 * Scenario export and import.
 *
 * Import is two steps on purpose. The Core parses and summarises the file, the
 * operator reads that summary -- including an origin mismatch -- and only then
 * chooses to apply it. A one-step import could silently retarget a live
 * assessment at a host the operator never agreed to touch.
 */
function renderScenario(root: HTMLElement): void {
  if (state.settings.connectionMethod === 'direct') return;
  const node = section('Scenario');
  node.appendChild(
    el(
      'div',
      'note',
      'A scenario records the setup so an assessment can be repeated. It carries no credentials, no cookies and no captured replies.',
    ),
  );

  const row = el('div', 'row');
  const save = el('button', '', 'Export scenario') as HTMLButtonElement;
  save.disabled = state.connection !== 'connected';
  save.onclick = () =>
    send('scenario.export', {
      name: `${OBJECTIVE_LABELS[state.settings.objective] ?? state.settings.objective} — ${targetLabel()}`,
    });
  row.appendChild(save);

  const load = el('button', '', 'Import scenario…') as HTMLButtonElement;
  load.disabled = state.connection !== 'connected';
  load.onclick = () => picker.click();
  row.appendChild(load);
  node.appendChild(row);

  // A file input rather than a paste box: a scenario is a file operators keep
  // in a repository next to the rest of an engagement's evidence.
  const picker = document.createElement('input');
  picker.type = 'file';
  picker.accept = 'application/json,.json';
  picker.id = 'scenario-file';
  picker.style.display = 'none';
  picker.onchange = () => {
    const file = picker.files?.[0];
    if (!file) return;
    void file.text().then((text) => {
      send('scenario.preview', { document: text, current_origin: state.origin });
      picker.value = '';
    });
  };
  node.appendChild(picker);

  if (scenarioExport) {
    node.appendChild(el('div', 'note ok', `Exported to ${scenarioExport}`));
  }

  if (scenarioPreview) {
    const preview = el('div', 'suggestion');
    preview.setAttribute('role', 'group');
    preview.setAttribute('aria-label', 'Imported scenario preview');
    preview.appendChild(el('div', '', String(scenarioPreview['name'] ?? 'Scenario')));
    for (const [label, key] of [
      ['Objective', 'objective'],
      ['Provider', 'provider'],
      ['Mode', 'mode'],
      ['Potential finding', 'potential_finding_action'],
      ['Sharing', 'sharing'],
      ['Recorded origin', 'target_origin'],
      ['Interaction', 'binding_summary'],
    ] as const) {
      preview.appendChild(el('div', 'note', `${label}: ${String(scenarioPreview[key] ?? '—')}`));
    }
    const warnings = scenarioPreview['warnings'];
    if (Array.isArray(warnings)) {
      for (const warning of warnings) {
        preview.appendChild(
          el('div', scenarioPreview['origin_mismatch'] ? 'note bad' : 'note warn', String(warning)),
        );
      }
    }
    preview.appendChild(
      el(
        'div',
        'note',
        'Applying a scenario never restores automatic sending, and the binding is revalidated against the current page before anything is sent.',
      ),
    );

    const actions = el('div', 'row');
    const apply = el('button', 'primary', 'Apply settings') as HTMLButtonElement;
    apply.onclick = () => applyScenario();
    actions.appendChild(apply);
    const discard = el('button', '', 'Discard') as HTMLButtonElement;
    discard.onclick = () => {
      scenarioPreview = null;
      scenarioDocument = null;
      render();
    };
    actions.appendChild(discard);
    preview.appendChild(actions);
    node.appendChild(preview);
  }

  root.appendChild(node);
}

/**
 * Apply a previewed scenario's configuration.
 *
 * Only settings and the recorded locators are applied. The binding is marked
 * unsaved so it must be revalidated against the live document, and automatic
 * sending is not restored: a file can describe a run, never authorize one.
 */
function applyScenario(): void {
  const document_ = scenarioDocument;
  if (!document_) return;
  const limits = (document_['limits'] ?? {}) as Record<string, unknown>;
  dispatch({
    type: 'settings',
    patch: {
      provider: String(document_['provider'] ?? state.settings.provider),
      requestedModel: String(document_['requested_model'] ?? ''),
      mode: String(document_['mode'] ?? 'assist') as AssistMode,
      responseSource: String(document_['response_source'] ?? 'page') as ResponseSource,
      potentialFindingAction: String(
        document_['potential_finding_action'] ?? 'review',
      ) as PotentialFindingAction,
      sharing: String(document_['sharing'] ?? 'none') as Sharing,
      objective: String(document_['objective'] ?? state.settings.objective),
      customObjective: String(document_['custom_objective'] ?? ''),
      maxTurns: boundedNumber(limits['max_turns'], 0, 100, state.settings.maxTurns),
      maxDurationSeconds: boundedNumber(
        limits['max_duration_seconds'],
        0,
        1800,
        state.settings.maxDurationSeconds,
      ),
    },
  });

  const recorded = (document_['binding'] ?? null) as Record<string, unknown> | null;
  if (recorded) {
    const submit = (recorded['submit'] ?? {}) as Record<string, unknown>;
    const response = (recorded['response'] ?? {}) as Record<string, unknown>;
    dispatch({
      type: 'binding',
      binding: {
        ...state.binding,
        origin: state.origin,
        input: parseLocator(recorded['input']),
        submit: parseLocator(submit['locator']),
        response: parseLocator(response['locator']),
        submitStrategy:
          submit['strategy'] === 'press_key' ? 'press_key' : 'click_button',
        submitKey: String(submit['key'] ?? 'Enter'),
      },
    });
  }

  scenarioPreview = null;
  scenarioDocument = null;
  setNotice('Scenario applied. Re-check the interaction against this page, then start when ready.');
  render();
}

/* --------------------------------------------------------------- workspaces */

/**
 * Setup: progressive, one step at a time.
 *
 * Rendering all five groups expanded put the primary action several screens
 * below the fold on a 320px panel and repeated every blocker twice -- once
 * beside its own control and again in the readiness summary. A finished step
 * collapses to a single line the operator can reopen, so the screen shows what
 * they have decided plus the one thing they are deciding now.
 */
interface SetupStep {
  id: string;
  title: string;
  done: boolean;
  summary: string;
  render: (root: HTMLElement) => void;
}

function setupSteps(): SetupStep[] {
  const direct = state.settings.connectionMethod === 'direct';
  const methodChosen = state.settings.connectionMethod !== 'unset';
  const provider = state.providers.find((entry) => entry.kind === state.settings.provider);
  const providerHealth = state.health[state.settings.provider];
  const payloadOnly = state.settings.mode === 'payload_only';
  const model = state.effectiveModel || state.settings.requestedModel;

  const steps: SetupStep[] = [
    {
      id: 'connection',
      title: 'AI connection',
      done:
        methodChosen &&
        state.connection === 'connected' &&
        (!direct || Boolean(provider && model)),
      summary: !methodChosen
        ? 'Choose Local Core or Direct API'
        : direct
          ? [provider?.label ?? state.settings.provider, model].filter(Boolean).join(' · ')
          : `Local Core on port ${state.settings.corePort}`,
      render: (root) => {
        renderConnection(root);
        renderAlert(root, 'connection');
        if (direct) {
          renderAi(root);
          renderAlert(root, 'ai');
        }
      },
    },
  ];
  if (state.settings.connectionMethod === 'core') {
    steps.push({
      id: 'ai',
      title: 'AI',
      done: Boolean(provider && (!providerHealth || providerHealth.usable)),
      summary: [provider?.label ?? state.settings.provider, model || 'default model']
        .filter(Boolean)
        .join(' · '),
      render: (root) => {
        renderAi(root);
        renderAlert(root, 'ai');
      },
    });
  }
  steps.push(
    {
      id: 'behavior',
      title: 'Behavior',
      done: true,
      summary: `${MODE_SHORT_LABELS[state.settings.mode]} · ${
        OBJECTIVE_LABELS[state.settings.objective] ?? state.settings.objective
      }`,
      render: (root) => renderMode(root),
    },
    {
      id: 'target',
      title: 'Target',
      done: payloadOnly || Boolean(state.origin),
      summary: payloadOnly ? 'Not needed in payload-only' : targetLabel(),
      render: (root) => {
        renderTarget(root);
        renderAlert(root, 'target');
      },
    },
    {
      id: 'interaction',
      title: 'Interaction',
      done: payloadOnly || state.bindingSaved,
      summary: payloadOnly
        ? 'Not needed in payload-only'
        : state.bindingHealth === 'healthy'
          ? 'Saved and verified on this page'
          : 'Saved',
      render: (root) => {
        renderInteraction(root);
        renderAlert(root, 'interaction');
      },
    },
  );
  return steps;
}

function renderSetup(root: HTMLElement): void {
  renderAlert(root, 'global');
  renderNotice(root);

  const steps = setupSteps();
  const firstOpenIndex = steps.findIndex((step) => !step.done);
  const requestedIndex = steps.findIndex((step) => step.id === ui.openStep);
  const canEdit = (step: SetupStep): boolean =>
    step.id === 'connection' ||
    (state.settings.connectionMethod !== 'unset' &&
      (step.id !== 'ai' || state.connection === 'connected'));
  // The AI provider list comes from Core, so that one step stays locked until
  // Core connects. Target and behavior can be prepared independently once the
  // operator has made the explicit Core/API choice.
  const requestedIsReachable =
    requestedIndex >= 0 && canEdit(steps[requestedIndex]!);
  const activeId = requestedIsReachable
    ? ui.openStep
    : firstOpenIndex >= 0
      ? steps[firstOpenIndex]!.id
      : null;

  for (const step of steps) {
    if (step.id === activeId) {
      const node = el('section');
      step.render(node);
      root.appendChild(node);
      continue;
    }
    if (!step.done) {
      // A step that is neither current nor finished is listed, not expanded, so
      // the operator can see what is still ahead without scrolling past it.
      root.appendChild(stepSummary(step, 'pending', canEdit(step)));
      continue;
    }
    root.appendChild(
      stepSummary(step, 'done', canEdit(step)),
    );
  }

  renderReadiness(root);
}

function stepSummary(
  step: SetupStep,
  tone: 'done' | 'pending',
  editable: boolean,
): HTMLElement {
  const node = el('section', 'step-collapsed');
  const line = el('div', 'group-summary');
  line.appendChild(
    el('span', `group-check ${tone}`, tone === 'done' ? '\u2713' : '\u00b7'),
  );
  const text = el('div', 'group-summary-text');
  text.appendChild(el('strong', '', step.title));
  text.appendChild(el('div', 'value', tone === 'done' ? step.summary : 'Not set yet'));
  line.appendChild(text);
  if (editable) {
    const open = el('button', '', tone === 'done' ? 'Edit' : 'Set up') as HTMLButtonElement;
    open.setAttribute('aria-label', `${tone === 'done' ? 'Edit' : 'Set up'} ${step.title}`);
    open.onclick = () => uiDispatch({ type: 'open_step', step: step.id });
    line.appendChild(open);
  }
  node.appendChild(line);
  return node;
}

/**
 * The readiness summary and the single primary action.
 *
 * Start stays clickable even when something is missing: a disabled button with
 * no explanation cannot be told apart from a bug, so pressing it surfaces the
 * blocking reasons instead of doing nothing.
 */
function renderReadiness(root: HTMLElement): void {
  const readiness = evaluateReadiness(state);
  const node = section('Ready to start');
  node.id = 'readiness';

  if (readiness.ready) {
    node.appendChild(el('div', 'note ok', 'Every requirement is met.'));
  } else {
    // The collapsed steps above already show which groups are unset, so this
    // states only the next thing to do plus how much is left. Repeating every
    // blocker here was the same information twice on one screen.
    const [next, ...rest] = readiness.blockers;
    if (next) node.appendChild(el('div', 'note', next.action));
    if (rest.length) {
      node.appendChild(
        el('div', 'note faint', `${rest.length} more step${rest.length === 1 ? '' : 's'} after this.`),
      );
    }
  }

  const row = el('div', 'row primary-row');
  // Auto states its own bound on the button: the number of sends the operator
  // is authorizing is part of the decision, not a detail to find later.
  const start = el(
    'button',
    'primary wide',
    state.settings.mode === 'auto'
      ? state.settings.maxTurns === 0
        ? 'Start Auto · unlimited turns'
        : `Start Auto · up to ${state.settings.maxTurns} sends`
      : 'Start test',
  ) as HTMLButtonElement;
  // Deliberately always clickable: pressing it explains what is missing.
  start.onclick = () => void startTest();
  row.appendChild(start);
  node.appendChild(row);
  root.appendChild(node);
}

/** Live Test: the current payload, one dominant action, and a concise trail. */
function renderLiveTest(root: HTMLElement): void {
  renderSessionHeader(root);
  renderAlert(root, 'global');
  renderNotice(root);
  renderBindingBanner(root);
  renderProposal(root);
  renderAlert(root, 'test');
  renderManualTrigger(root);
  renderTimeline(root);
}

/**
 * A compact binding warning inside the live run.
 *
 * The full interaction controls stay in Setup; a run only needs to know that
 * sending is blocked and where to fix it.
 */
function renderBindingBanner(root: HTMLElement): void {
  if (state.settings.mode === 'payload_only') return;
  const pausedForBinding = /interaction binding needs review|page interaction failed/i.test(
    state.autoStopReason,
  );
  if (
    !pausedForBinding
    && state.bindingHealth !== 'needs_review'
    && state.bindingHealth !== 'unsupported'
  ) return;
  const node = el('div', 'health');
  node.setAttribute('role', 'status');
  node.appendChild(el('div', 'note bad', 'Sending is blocked: the interaction binding needs review.'));
  for (const [role, issue] of Object.entries(state.bindingRoleIssues)) {
    node.appendChild(el('div', 'note', `${role}: ${issue}`));
  }
  const row = el('div', 'row');
  const recheck = el('button', 'primary', 'Re-check & continue') as HTMLButtonElement;
  recheck.onclick = () => void recheckAndContinue();
  row.appendChild(recheck);
  const fix = el('button', '', 'Fix in Setup') as HTMLButtonElement;
  fix.onclick = () => navigate('setup');
  row.appendChild(fix);
  node.appendChild(row);
  root.appendChild(node);
}

/** Finding Review: the decision that only a human may make. */
function renderReview(root: HTMLElement): void {
  renderSessionHeader(root);
  renderAlert(root, 'global');
  renderEvaluation(root);
}

/** Reports: this session's evidence, plus the stored library in Core mode. */
function renderReports(root: HTMLElement): void {
  if (viewedReport) {
    renderStoredReport(root);
    return;
  }
  renderSessionHeader(root);
  renderAlert(root, 'global');
  renderNotice(root);
  if (runEnded(state)) renderRunSummary(root);
  renderEvidence(root);
  renderAlert(root, 'reports');
  renderReportLibrary(root);
}

/**
 * The terminal summary for a finished run.
 *
 * States why the run ended, which is the difference between "stopped because it
 * found something" and "stopped because it ran out of turns" -- two results a
 * reader must never confuse.
 */
function renderRunSummary(root: HTMLElement): void {
  const node = section('Run summary');
  node.id = 'run-summary';
  const verdict = state.verdict;
  const tone = verdict === 'confirmed' ? 'bad' : verdict === 'potential' ? 'warn' : 'ok';
  node.appendChild(el('div', `note ${tone}`, `Verdict: ${verdict.replaceAll('_', ' ')}`));
  if (state.autoStopReason && state.autoStopReason !== 'potential_review') {
    node.appendChild(el('div', 'note', `Ended: ${state.autoStopReason.replaceAll('_', ' ')}`));
  }
  node.appendChild(
    el('div', 'note', `${state.turns} turn${state.turns === 1 ? '' : 's'} recorded.`),
  );
  if (verdict !== 'confirmed') {
    node.appendChild(
      el(
        'div',
        'note',
        'Nothing here is a confirmed finding: that requires a deterministic check or your explicit confirmation.',
      ),
    );
  }
  const row = el('div', 'row');
  const legacyResume =
    !state.autoStopReason
    && state.verdict !== 'confirmed'
    && (state.maxTurns === 0 || state.turns < state.maxTurns);
  if (
    state.settings.mode === 'auto'
    && (
      state.autoStopReason === 'max_turns'
      || state.autoStopReason === 'max_duration'
      || legacyResume
    )
  ) {
    const exhaustedTurns = state.autoStopReason === 'max_turns';
    const more = el(
      'button',
      'primary',
      exhaustedTurns ? `Continue +${state.settings.maxTurns} turns` : 'Continue auto run',
    ) as HTMLButtonElement;
    more.onclick = () => continueAutoRun(exhaustedTurns);
    row.appendChild(more);
  }
  const again = el('button', '', 'New test') as HTMLButtonElement;
  again.onclick = () => {
    dispatch({ type: 'session_started' });
    navigate('setup');
  };
  row.appendChild(again);
  node.appendChild(row);
  root.appendChild(node);
}

/** Previously exported Core artifacts or browser-local Direct API reports. */
function renderReportLibrary(root: HTMLElement): void {
  const node = section('Report library');
  node.id = 'report-library';

  if (state.settings.connectionMethod === 'direct') {
    const row = el('div', 'row');
    const refresh = el('button', '', ui.reportsLoading ? 'Loading\u2026' : 'Refresh') as HTMLButtonElement;
    refresh.disabled = ui.reportsLoading;
    refresh.onclick = () => requestReports();
    row.appendChild(refresh);
    node.appendChild(row);
    node.appendChild(
      el(
        'div',
        'note',
        'Stored only in this Chrome profile. Reports can contain sensitive target responses; delete them when they are no longer needed.',
      ),
    );
    if (!directReports.length) {
      node.appendChild(
        el(
          'div',
          'note',
          ui.reportsLoading
            ? 'Reading local report history\u2026'
            : 'No Direct API reports stored yet. A report is saved after test activity or an explicit export.',
        ),
      );
    } else {
      const list = el('div', 'report-list');
      for (const report of directReports) {
        list.appendChild(renderReportCard(directReportSummary(report), report));
      }
      node.appendChild(list);
    }
    root.appendChild(node);
    return;
  }

  const row = el('div', 'row');
  const refresh = el('button', '', ui.reportsLoading ? 'Loading\u2026' : 'Refresh') as HTMLButtonElement;
  refresh.disabled = ui.reportsLoading || state.connection !== 'connected';
  refresh.onclick = () => requestReports();
  row.appendChild(refresh);
  node.appendChild(row);

  if (state.connection !== 'connected') {
    node.appendChild(el('div', 'note', 'Connect to the local Core to list stored reports.'));
    root.appendChild(node);
    return;
  }
  if (!reports.length) {
    node.appendChild(
      el(
        'div',
        'note',
        ui.reportsLoading
          ? 'Reading the artifacts directory\u2026'
          : 'No reports stored yet. Exporting a session writes one here.',
      ),
    );
    if (reportsRoot) node.appendChild(el('div', 'note', `Directory: ${reportsRoot}`));
    root.appendChild(node);
    return;
  }

  const list = el('div', 'report-list');
  for (const report of reports) {
    list.appendChild(renderReportCard(report));
  }
  node.appendChild(list);
  root.appendChild(node);
}

function renderReportCard(summary: ReportSummary, direct?: DirectReport): HTMLElement {
  const card = el('div', 'report-row');
  const head = el('div', 'report-head');
  head.appendChild(el('span', `verdict-dot ${summary.verdict}`, ''));
  head.appendChild(el('strong', '', summary.targetOrigin || 'unknown target'));
  card.appendChild(head);
  card.appendChild(
    el(
      'div',
      'note',
      [
        summary.objective.replaceAll('_', ' '),
        `${summary.turns} turn${summary.turns === 1 ? '' : 's'}`,
        summary.effectiveModel,
        summary.createdAt,
      ]
        .filter(Boolean)
        .join(' \u00b7 '),
    ),
  );
  const actions = el('div', 'row');
  if (direct || summary.artifacts.includes('session.json')) {
    const view = el('button', 'primary', 'View results') as HTMLButtonElement;
    view.disabled = pendingReport !== null;
    view.onclick = () => {
      if (direct) {
        viewedReport = {
          summary,
          document: direct.parsed,
          directDocument: direct.document,
        };
        render();
      } else {
        openReport(summary.reportId, 'session.json', true);
      }
    };
    actions.appendChild(view);
  }

  if (direct) {
    const download = el('button', '', 'Download JSON') as HTMLButtonElement;
    download.onclick = () => downloadText(
      `stealth-prompt-${summary.reportId}.json`,
      JSON.stringify(direct.document, null, 2),
      'application/json',
    );
    actions.appendChild(download);
    const remove = el('button', 'danger', 'Delete') as HTMLButtonElement;
    remove.onclick = () => void removeDirectReport(summary.reportId);
    actions.appendChild(remove);
  } else {
    for (const artifact of summary.artifacts) {
      const download = el(
        'button',
        '',
        artifact === 'report.html'
          ? 'Download HTML'
          : artifact === 'session.json'
            ? 'Download JSON'
            : 'Download scenario',
      ) as HTMLButtonElement;
      download.disabled = pendingReport !== null;
      download.onclick = () => openReport(summary.reportId, artifact, false);
      actions.appendChild(download);
    }
  }
  card.appendChild(actions);
  return card;
}

async function removeDirectReport(reportId: string): Promise<void> {
  if (!window.confirm('Delete this browser-local report? This cannot be undone.')) return;
  try {
    await deleteDirectReport(reportId);
    directReports = directReports.filter((report) => report.reportId !== reportId);
    if (viewedReport?.summary.reportId === reportId) viewedReport = null;
    setNotice('Direct API report deleted from this Chrome profile.');
    render();
  } catch (error) {
    fail(String((error as Error).message), 'reports');
  }
}

/** A safe, in-product view of session.json. Stored HTML is never rendered. */
function renderStoredReport(root: HTMLElement): void {
  if (!viewedReport) return;
  const { summary, document: report } = viewedReport;
  renderAlert(root, 'reports');
  const heading = el('div', 'page-heading');
  const copy = el('div');
  copy.appendChild(el('h2', '', 'Report results'));
  copy.appendChild(el('div', 'note', report.targetOrigin || summary.targetOrigin || 'Unknown target'));
  heading.appendChild(copy);
  const back = el('button', '', 'Back') as HTMLButtonElement;
  back.onclick = () => {
    viewedReport = null;
    render();
  };
  heading.appendChild(back);
  root.appendChild(heading);

  const overview = section('Assessment');
  const metrics = el('div', 'evidence-grid');
  const metricsValues: Array<[string, string]> = [
    ['Verdict', report.verdict],
    ['Turns', String(report.turns.length)],
  ];
  for (const [label, value] of metricsValues) {
    const metric = el('div', 'evidence-metric');
    metric.appendChild(el('span', '', label));
    metric.appendChild(el('strong', '', value.replaceAll('_', ' ')));
    metrics.appendChild(metric);
  }
  overview.appendChild(metrics);
  for (const [label, value] of [
    ['Objective', report.objective],
    ['Provider', report.provider],
    ['Model', report.effectiveModel],
    ['Mode', report.mode.replaceAll('_', ' ')],
    ['Potential finding', report.potentialFindingAction.replaceAll('_', ' ')],
    ['Exported', report.exportedAt || summary.createdAt],
  ]) {
    if (!value) continue;
    const item = el('div', 'kv');
    item.appendChild(el('span', '', label));
    item.appendChild(el('span', '', label === 'Objective' ? value.replaceAll('_', ' ') : value));
    overview.appendChild(item);
  }
  root.appendChild(overview);

  const turns = section(`Test turns (${report.turns.length})`);
  if (!report.turns.length) turns.appendChild(el('div', 'note', 'No turns were recorded.'));
  for (const [index, turn] of report.turns.entries()) {
    const detail = el('details', 'report-turn') as HTMLDetailsElement;
    if (index === 0) detail.open = true;
    const title = `Turn ${index + 1}${turn.verdict ? ` \u00b7 ${turn.verdict.replaceAll('_', ' ')}` : ''}`;
    detail.appendChild(el('summary', '', title));
    if (turn.goal) {
      detail.appendChild(el('h3', 'sub', 'Goal'));
      detail.appendChild(el('div', 'note', turn.goal));
    }
    if (turn.tactic) {
      detail.appendChild(el('h3', 'sub', 'Tactic'));
      detail.appendChild(el('div', 'note', turn.tactic));
    }
    if (turn.hypothesis) {
      detail.appendChild(el('h3', 'sub', 'Hypothesis'));
      detail.appendChild(el('div', 'note', turn.hypothesis));
    }
    if (turn.payload) {
      detail.appendChild(el('h3', 'sub', 'Payload sent'));
      detail.appendChild(el('div', 'out', turn.payload));
    }
    if (turn.response) {
      detail.appendChild(el('h3', 'sub', 'Target response'));
      detail.appendChild(el('div', 'out', turn.response));
    } else {
      detail.appendChild(el('div', 'note faint', 'Response text was not stored for this run.'));
    }
    if (turn.evaluationSummary) {
      detail.appendChild(el('h3', 'sub', 'Evaluation'));
      detail.appendChild(el('div', 'note', turn.evaluationSummary));
    }
    if (turn.observedSignals.length) {
      detail.appendChild(el('h3', 'sub', 'Observed signals'));
      const list = el('ul', 'plain');
      for (const signal of turn.observedSignals) list.appendChild(el('li', '', signal));
      detail.appendChild(list);
    }
    turns.appendChild(detail);
  }
  root.appendChild(turns);

  const downloads = section('Export files');
  const actions = el('div', 'row');
  if (viewedReport.directDocument) {
    const download = el('button', 'primary', 'Download JSON') as HTMLButtonElement;
    download.onclick = () => downloadText(
      `stealth-prompt-${summary.reportId}.json`,
      JSON.stringify(viewedReport?.directDocument, null, 2),
      'application/json',
    );
    actions.appendChild(download);
    const remove = el('button', 'danger', 'Delete') as HTMLButtonElement;
    remove.onclick = () => void removeDirectReport(summary.reportId);
    actions.appendChild(remove);
  } else for (const artifact of summary.artifacts) {
    const download = el(
      'button',
      artifact === 'report.html' ? 'primary' : '',
      artifact === 'report.html'
        ? 'Download HTML'
        : artifact === 'session.json'
          ? 'Download JSON'
          : 'Download scenario',
    ) as HTMLButtonElement;
    download.disabled = pendingReport !== null;
    download.onclick = () => openReport(summary.reportId, artifact, false);
    actions.appendChild(download);
  }
  downloads.appendChild(actions);
  root.appendChild(downloads);
}

/**
 * The connection pill in the app header.
 *
 * It lives outside every workspace and every Setup step, so it is refreshed on
 * each render rather than by whichever group happens to be expanded. Tying it
 * to the Connection group left it frozen at a stale value once that group
 * collapsed.
 */
function renderConnectionPill(): void {
  const pill = document.getElementById('conn');
  if (!pill) return;
  pill.textContent =
    state.settings.connectionMethod === 'unset'
      ? 'not configured'
      : state.connection.replaceAll('_', ' ');
  pill.className =
    'pill ' +
    (state.connection === 'connected' ? 'ok' : state.connection === 'error' ? 'bad' : 'warn');
}

export function render(): void {
  const root = document.getElementById('root');
  if (!root) return;
  root.textContent = '';

  renderConnectionPill();
  renderSwitcher(root);

  const workspace = activeWorkspace(state, ui);
  const panel = el('div', 'workspace');
  panel.id = 'workspace';
  const controllingTab = document.getElementById(
    `tab-${workspace === 'review' ? 'test' : workspace}`,
  );
  if (controllingTab) {
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', controllingTab.id);
  } else {
    panel.setAttribute('role', 'region');
    panel.setAttribute('aria-label', WORKSPACE_LABELS[workspace]);
  }
  panel.dataset['workspace'] = workspace;

  if (workspace === 'setup') renderSetup(panel);
  else if (workspace === 'test') renderLiveTest(panel);
  else if (workspace === 'review') renderReview(panel);
  else if (workspace === 'reports') renderReports(panel);
  else renderSettingsPage(panel);

  root.appendChild(panel);
}

/* ------------------------------------------------------------ core client */

function send(type: Parameters<typeof encodeCoreFrame>[0], payload: Record<string, unknown>): void {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    fail('Not connected to the local Core.', 'connection');
    return;
  }
  socket.send(encodeCoreFrame(type, payload));
}

async function storedToken(): Promise<string> {
  const stored = await chrome.storage.local.get(TOKEN_KEY);
  const value = stored[TOKEN_KEY];
  return typeof value === 'string' ? value : '';
}

async function connectToCore(): Promise<void> {
  if (state.settings.connectionMethod !== 'core') return;
  dispatch({ type: 'connection', state: 'connecting' });
  const token = await storedToken();
  const base = coreUrl();
  const url = token ? `${base}?token=${encodeURIComponent(token)}` : `${base}?pairing=1`;
  const connection = new WebSocket(url);
  socket = connection;

  connection.onopen = () => {
    if (socket !== connection || state.settings.connectionMethod !== 'core') return;
    if (token) {
      send('hello', {});
      send('capabilities.request', {});
      send('providers.health', {});
    } else {
      dispatch({ type: 'pairing_required' });
    }
  };
  connection.onclose = () => {
    if (socket === connection && state.settings.connectionMethod === 'core') {
      pendingReport = null;
      dispatch({ type: 'connection', state: 'disconnected', detail: 'Core disconnected.' });
    }
  };
  connection.onerror = () => {
    if (socket !== connection || state.settings.connectionMethod !== 'core') return;
    pendingReport = null;
    dispatch({
      type: 'connection',
      state: 'error',
      detail: 'Could not reach the Core. Run `stealth-prompt serve`.',
    });
  };
  connection.onmessage = (event: MessageEvent<string>) => {
    if (socket !== connection || state.settings.connectionMethod !== 'core') return;
    try {
      handleFrame(parseCoreFrame(event.data));
    } catch (error) {
      fail(String((error as Error).message));
    }
  };
}

function handleFrame(frame: ReturnType<typeof parseCoreFrame>): void {
  const payload = frame.payload as Record<string, any>;
  switch (frame.type) {
    case 'paired':
      // The code is single-use; the token replaces it.
      pairingCode = '';
      void chrome.storage.local.set({ [TOKEN_KEY]: payload['token'] });
      dispatch({ type: 'connection', state: 'connecting', detail: 'Paired. Reconnecting…' });
      socket?.close();
      setTimeout(() => void connectToCore(), 150);
      break;
    case 'reports': {
      reports = parseReportList(payload['reports']);
      reportsRoot = typeof payload['root'] === 'string' ? payload['root'].slice(0, 300) : '';
      ui = reduceUi(ui, { type: 'reports_loading', loading: false });
      ui = reduceUi(ui, { type: 'clear_error', area: 'reports' });
      render();
      break;
    }
    case 'report': {
      const artifact = String(payload['artifact'] ?? 'report.html');
      const content = String(payload['content'] ?? '');
      const reportId = String(payload['report_id'] ?? 'report');
      const request = pendingReport;
      pendingReport = null;
      if (!request || request.reportId !== reportId || request.artifact !== artifact) {
        fail('The Core returned an unexpected report artifact.', 'reports');
        break;
      }
      if (request.view && artifact === 'session.json') {
        let parsed: StoredReport | null = null;
        try {
          parsed = parseStoredReport(JSON.parse(content));
        } catch {
          // Handled below with the same bounded, local error as a wrong schema.
        }
        const summary = reports.find((item) => item.reportId === reportId);
        if (!parsed || !summary) {
          fail('This stored report is not a supported session JSON document.', 'reports');
          break;
        }
        viewedReport = { summary, document: parsed };
        ui = reduceUi(ui, { type: 'clear_error', area: 'reports' });
        render();
        break;
      }
      downloadText(
        `${reportId}-${artifact}`,
        content,
        artifact.endsWith('.json') ? 'application/json' : 'text/html',
      );
      setNotice(`Downloaded ${artifact} for ${reportId}.`);
      render();
      break;
    }
    case 'scenario.exported':
      scenarioExport = String(payload['path'] ?? 'the session directory');
      dispatch({ type: 'clear_error' });
      break;
    case 'scenario.preview':
      scenarioPreview = (payload['preview'] ?? null) as Record<string, unknown> | null;
      scenarioDocument = (payload['document'] ?? null) as Record<string, unknown> | null;
      dispatch({ type: 'clear_error' });
      break;
    case 'pair.rejected':
      fail(String(payload['message'] ?? 'pairing failed'), 'connection');
      break;
    case 'ready':
      if (ui.openStep === 'connection') {
        ui = reduceUi(ui, { type: 'open_step', step: null });
      }
      dispatch({
        type: 'ready',
        coreVersion: String(payload['core_version'] ?? ''),
        session: (payload['session'] ?? null) as Record<string, unknown> | null,
      });
      if (payload['recovery'] && typeof payload['recovery'] === 'object') {
        const recovery = payload['recovery'] as Record<string, any>;
        let recovered = false;
        if (recovery['evaluation']) {
          dispatch({ type: 'evaluation', evaluation: recovery['evaluation'] as Evaluation });
          recovered = true;
        }
        if (recovery['next_proposal']) {
          dispatch({ type: 'proposal', proposal: recovery['next_proposal'] as Proposal });
          recovered = true;
        }
        if (recovery['auto_stopped']) {
          dispatch({ type: 'auto_stopped', reason: String(recovery['auto_stopped']) });
          recovered = true;
        }
        if (recovered) uiDispatch({ type: 'follow_state' });
      }
      requestModels();
      break;
    case 'capabilities':
      objectiveSpecs = Array.isArray(payload['objectives'])
        ? (payload['objectives'] as ObjectiveSpec[])
        : [];
      dispatch({ type: 'providers', providers: payload['providers'] ?? [] });
      break;
    case 'providers.health':
      dispatch({ type: 'health', health: payload['providers'] ?? [] });
      break;
    case 'models':
      dispatch({
        type: 'models',
        provider: String(payload['provider'] ?? ''),
        requestId: String(payload['request_id'] ?? ''),
        models: payload['models'] ?? [],
        error: String(payload['error'] ?? ''),
      });
      break;
    case 'session.configured':
    case 'session.bound':
    case 'session.status':
      dispatch({ type: 'session', session: payload['session'] ?? {} });
      break;
    case 'proposal.pending':
      dispatch({ type: 'stage', stage: 'generating', at: Date.now() });
      startStageTicker();
      break;
    case 'proposal':
      stopStageTicker();
      dispatch({
        type: 'latency',
        milliseconds: Number(payload['elapsed_ms'] ?? 0),
        label: 'Generated',
      });
      dispatch({ type: 'proposal', proposal: payload['proposal'] });
      dispatch({ type: 'timeline', entry: { at: Date.now(), kind: 'proposal.generated', detail: '' } });
      break;
    case 'proposal.refused':
      stopStageTicker();
      dispatch({ type: 'refused', excerpt: String(payload['excerpt'] ?? '') });
      break;
    case 'proposal.failed':
      stopStageTicker();
      fail(String(payload['message'] ?? 'proposal failed'), 'test');
      dispatch({ type: 'stage', stage: 'idle', at: Date.now() });
      break;
    case 'send.authorized':
      dispatch({ type: 'stage', stage: 'sending', at: Date.now() });
      void performSend(payload);
      break;
    case 'evaluation.pending':
      dispatch({ type: 'stage', stage: 'evaluating', at: Date.now() });
      startStageTicker();
      break;
    case 'evaluation':
      stopStageTicker();
      if (payload['session']) dispatch({ type: 'session', session: payload['session'] });
      dispatch({
        type: 'latency',
        milliseconds: Number(payload['elapsed_ms'] ?? 0),
        label:
          payload['planning_strategy'] === 'combined'
            ? 'Analyzed + planned in one AI call'
            : 'Analyzed',
      });
      dispatch({ type: 'evaluation', evaluation: payload['evaluation'] });
      dispatch({ type: 'timeline', entry: { at: Date.now(), kind: 'evaluation.completed', detail: '' } });
      if (payload['auto_finished']) {
        dispatch({ type: 'session_ended', reason: String(payload['auto_finished']) });
      } else if (payload['auto_stopped']) {
        dispatch({ type: 'auto_stopped', reason: String(payload['auto_stopped']) });
      }
      if (payload['next_proposal']) {
        dispatch({ type: 'proposal', proposal: payload['next_proposal'] });
      }
      if (payload['auto_finished']) {
        uiDispatch({ type: 'follow_state' });
        requestReports();
      }
      break;
    case 'auto.started':
      if (payload['session']) dispatch({ type: 'session', session: payload['session'] });
      dispatch({ type: 'auto_started' });
      uiDispatch({ type: 'follow_state' });
      break;
    case 'exported':
      setNotice(`Report written to ${String(payload['html_path'] ?? payload['path'])}`);
      dispatch({ type: 'clear_error' });
      break;
    case 'cancelled':
      stopStageTicker();
      if (recoverAfterCancel) {
        recoverAfterCancel = false;
        dispatch({ type: 'stage', stage: 'idle', at: Date.now() });
        clearError('test');
        send('hello', {});
        break;
      }
      dispatch({ type: 'auto_stopped', reason: 'cancelled' });
      dispatch({ type: 'stage', stage: 'cancelled', at: Date.now() });
      break;
    case 'session.stopped':
      dispatch({ type: 'session_ended', reason: 'stopped by operator' });
      uiDispatch({ type: 'follow_state' });
      stopStageTicker();
      dispatch({ type: 'auto_stopped', reason: 'stopped by operator' });
      break;
    case 'error':
      stopStageTicker();
      if (payload['code'] === 'no_session') {
        dispatch({ type: 'ready', coreVersion: state.coreVersion, session: null });
        dispatch({ type: 'auto_stopped', reason: 'core_session_lost' });
        fail('The Core restarted and no longer has this session. Recover the run to continue.', 'test');
        break;
      }
      if (payload['code'] === 'busy') {
        fail(String(payload['message'] ?? 'A provider operation is already in progress.'), 'test');
        break;
      }
      if (state.autoRunning) dispatch({ type: 'auto_stopped', reason: 'error' });
      if (pendingReport) {
        pendingReport = null;
        fail(String(payload['message'] ?? 'report could not be opened'), 'reports');
      } else {
        fail(String(payload['message'] ?? 'error'), 'test');
      }
      break;
    default:
      break;
  }
}

function startStageTicker(): void {
  stopStageTicker();
  stageTimer = setInterval(render, 1000) as unknown as number;
}
function stopStageTicker(): void {
  if (stageTimer !== null) clearInterval(stageTimer);
  stageTimer = null;
}

function cancelGeneration(): void {
  if (state.settings.connectionMethod === 'direct') {
    const requestId = directRequestId;
    directRequestId = '';
    if (requestId) void callWorker({ kind: 'direct-cancel', requestId });
    stopStageTicker();
    dispatch({ type: 'stage', stage: 'cancelled', at: Date.now() });
    if (state.autoRunning) dispatch({ type: 'auto_stopped', reason: 'cancelled' });
    return;
  }
  send('cancel', {});
}

/* -------------------------------------------------------------- browser */

async function callWorker(request: Record<string, unknown>): Promise<Record<string, any>> {
  return (await chrome.runtime.sendMessage({ channel: 'sp-panel', ...request })) as Record<string, any>;
}

function switchConnectionMethod(method: Exclude<ConnectionMethod, 'unset'>): void {
  if (method === state.settings.connectionMethod) return;
  socket?.close();
  socket = null;
  directApiKey = '';
  directSentPayloads = [];
  dispatch({
    type: 'settings',
    patch: {
      connectionMethod: method,
      provider: method === 'direct' ? 'openai' : 'fake',
      requestedModel: '',
    },
  });
  dispatch({ type: 'providers', providers: method === 'direct' ? DIRECT_PROVIDERS : [] });
  dispatch({ type: 'health', health: [] });
  dispatch({
    type: 'connection',
    state: 'disconnected',
    detail:
      method === 'direct'
        ? 'Choose a provider and enter its key below.'
        : 'Start the local Core, then connect.',
  });
  uiDispatch({ type: 'open_step', step: 'connection' });
}

function disconnectDirect(): void {
  directApiKey = '';
  dispatch({ type: 'health', health: [] });
  dispatch({ type: 'connection', state: 'disconnected', detail: 'The direct API key was cleared.' });
}

async function connectDirect(): Promise<void> {
  const provider = state.settings.provider;
  const origin = DIRECT_ORIGINS[provider];
  if (!origin) {
    fail('Choose OpenAI or Anthropic.', 'ai');
    return;
  }
  if (!directApiKey) {
    fail('Enter an API key. It will not be saved.', 'ai');
    return;
  }
  // Keep this as the first awaited Chrome API in the click handler: optional
  // host permission prompts require the user's gesture.
  const access = await requestHostAccess(origin);
  if (!access.granted) {
    fail(access.message, 'ai');
    return;
  }
  dispatch({ type: 'connection', state: 'connecting', detail: `Checking ${provider}…` });
  const result = await callWorker({
    kind: 'direct-models',
    provider,
    key: directApiKey,
  });
  if (!result?.ok || !Array.isArray(result.models) || result.models.length === 0) {
    dispatch({
      type: 'connection',
      state: 'error',
      detail: String(result?.message ?? 'The provider returned no usable text models.'),
    });
    return;
  }
  const models = result.models.filter((entry: unknown): entry is string => typeof entry === 'string');
  const requestId = String(Date.now());
  dispatch({ type: 'models_requested', requestId });
  dispatch({
    type: 'models',
    provider,
    requestId,
    models: models.map((entry) => ({ id: entry, label: entry, default: false })),
    error: '',
  });
  dispatch({ type: 'settings', patch: { requestedModel: '' } });
  dispatch({
    type: 'providers',
    providers: DIRECT_PROVIDERS,
  });
  dispatch({
    type: 'health',
    health: [
      {
        kind: provider,
        state: 'authenticated',
        detail: 'Direct API ready for this panel session.',
        remedy: '',
        usable: true,
      },
    ],
  });
  ui = reduceUi(ui, { type: 'open_step', step: 'connection' });
  dispatch({ type: 'ready', coreVersion: 'Direct API', session: null });
  setNotice(`${provider} connected. The key will be forgotten when this panel closes.`);
  dispatch({ type: 'clear_error' });
}

async function fillDraftValue(value: string): Promise<void> {
  const result = await callWorker({
    kind: 'operation',
    operation: 'fill',
    binding: bindingLocators(),
    value,
  });
  if (!result?.ok) {
    fail(String(result?.message ?? 'Could not fill the test draft.'), 'interaction');
    return;
  }
  setNotice(value ? 'Harmless draft filled. It was not sent.' : 'Draft cleared.');
  dispatch({ type: 'clear_error' });
}

async function fillTestDraft(): Promise<void> {
  await fillDraftValue('Hello — this is an authorized interaction test. Please reply with TEST_OK.');
}

async function bindTab(): Promise<void> {
  const result = await callWorker({ kind: 'bind-tab' });
  if (!result?.ok) {
    fail(String(result?.message ?? 'no active tab'), 'target');
    return;
  }
  const origin = String(result.origin ?? '');
  dispatch({ type: 'target', origin, tabId: result.tabId ?? null, documentId: '' });
  dispatch({ type: 'clear_error' });
}

async function pickElement(role: 'input' | 'submit' | 'response'): Promise<void> {
  if (!state.origin || state.tabId === null) {
    fail(
      'Choose the target tab first. Open the target, click the Stealth Prompt toolbar icon, then press Use current tab.',
      'target',
    );
    return;
  }

  // This must be the first asynchronous browser API reached from the Select
  // button's click. Moving it behind bind-tab or another awaited message loses
  // Chrome's transient user gesture and the permission prompt may never open.
  const access = await requestHostAccess(state.origin);
  if (!access.granted) {
    fail(access.message, 'interaction');
    return;
  }

  const result = await callWorker({ kind: 'operation', operation: 'pick', role });
  const locator = parseLocator(result?.locator);
  if (!result?.ok || !locator) {
    fail(String(result?.message ?? 'nothing selected'), 'interaction');
    return;
  }
  const binding: InteractionBinding = { ...state.binding, origin: state.origin, [role]: locator };
  dispatch({ type: 'binding', binding });
  if (state.suggestion) {
    dispatch({
      type: 'suggestion',
      suggestion: {
        ...state.suggestion,
        [role]: { ...state.suggestion[role], locator: null },
      },
    });
  }
  dispatch({ type: 'clear_error' });
}

/**
 * Run discovery and hold the result for review.
 *
 * Nothing is saved here. The suggestion is stored beside the binding so the
 * operator can accept roles one at a time; a heuristic that guessed two roles
 * well and one badly should not force an all-or-nothing decision.
 */
async function discoverElements(): Promise<void> {
  const access = await requestHostAccess(state.origin);
  if (!access.granted) {
    fail(access.message, 'interaction');
    return;
  }
  dispatch({ type: 'stage', stage: 'discovering', at: Date.now() });
  const result = await callWorker({ kind: 'operation', operation: 'discover' });
  const suggestion = parseBindingSuggestion(result?.suggestion);
  if (!result?.ok || !suggestion) {
    dispatch({ type: 'stage', stage: 'idle', at: Date.now() });
    fail(String(result?.message ?? 'No chat interaction was detected.'), 'interaction');
    return;
  }
  dispatch({ type: 'suggestion', suggestion });
  setNotice('Review each suggested element, then accept the ones that are right.');
  dispatch({ type: 'stage', stage: 'idle', at: Date.now() });
  dispatch({ type: 'clear_error' });
}

/** Accept one suggested role into the working binding. Never all of them. */
function acceptSuggestion(role: BindingRole): void {
  const suggested = state.suggestion?.[role]?.locator;
  if (!suggested) return;
  dispatch({
    type: 'binding',
    binding: { ...state.binding, origin: state.origin, [role]: suggested },
  });
}

/** Show the operator which element a locator actually resolves to. */
async function highlightRole(locator: Locator | null): Promise<void> {
  if (!locator) return;
  const result = await callWorker({ kind: 'operation', operation: 'highlight', locator });
  if (!result?.ok) {
    fail(String(result?.message ?? 'Element not found.'), 'interaction');
  }
}

let revalidateTimer: number | null = null;

/**
 * Check the saved binding against the current document.
 *
 * `announce` is false for the automatic checks that follow a navigation, so a
 * routine re-check does not read as an error the operator caused.
 */
async function validateBinding(options: { announce?: boolean } = {}): Promise<boolean> {
  const announce = options.announce !== false;
  dispatch({ type: 'binding_revalidating' });
  const result = await callWorker({
    kind: 'operation',
    operation: 'validate',
    binding: bindingLocators(),
  });
  const roles = parseBindingValidation(result?.roles);
  const issues: Record<string, string> = {};
  for (const role of BINDING_ROLES) {
    const check = roles[role];
    if (check && !check.ok) issues[role] = check.reason;
  }

  if (result?.ok) {
    dispatch({ type: 'binding_health', health: 'healthy', issues: {}, at: Date.now() });
    if (state.bindingSaved) dispatch({ type: 'binding_saved', valid: true });
    return true;
  }

  // A page the content script cannot reach at all is a different problem from
  // a locator that stopped matching, and re-detecting will not fix it.
  const message = String(result?.message ?? 'The binding could not be checked.');
  const unreachable = /cannot access|establish connection|no target tab/i.test(message);
  dispatch({
    type: 'binding_health',
    health: unreachable ? 'unsupported' : 'needs_review',
    issues: Object.keys(issues).length ? issues : { binding: message },
    at: Date.now(),
  });
  if (announce) fail(message, 'interaction');
  return false;
}

/**
 * Revalidate after a navigation signal, debounced.
 *
 * A single SPA route change can produce several `onUpdated` events, and each
 * check is a message round trip into the page. Collapsing a burst into one
 * check keeps this event-driven rather than a poll.
 */
function scheduleRevalidation(): void {
  if (!state.bindingSaved) return;
  if (revalidateTimer !== null) clearTimeout(revalidateTimer);
  dispatch({ type: 'binding_revalidating' });
  revalidateTimer = setTimeout(() => {
    revalidateTimer = null;
    void validateBinding({ announce: false });
  }, 400) as unknown as number;
}

function bindingLocators(): Record<string, Locator | null> {
  return {
    input: state.binding.input,
    submit: state.binding.submit,
    response: state.binding.response,
  };
}

async function saveBinding(): Promise<void> {
  // Validate before recording it with the Core, so a binding that never
  // resolved cannot become the session's authorized interaction.
  if (!(await validateBinding())) return;
  dispatch({ type: 'binding_saved', valid: true });
  // Before Start there is no Core session to bind. The reviewed locators are
  // kept locally and sent immediately after session.configure in startTest().
  if (state.settings.connectionMethod === 'core' && state.sessionId) {
    send('session.bind', { binding: bindingToCore({ ...state.binding, origin: state.origin }) });
  }
  dispatch({
    type: 'timeline',
    entry: { at: Date.now(), kind: 'interaction.bound', detail: state.origin },
  });
}

function configurePayload(): Record<string, unknown> {
  return {
    provider: state.settings.provider,
    model: state.settings.requestedModel,
    mode: state.settings.mode,
    response_source: state.settings.responseSource,
    max_turns: state.settings.maxTurns,
    max_duration_seconds: state.settings.maxDurationSeconds,
    potential_finding_action: state.settings.potentialFindingAction,
    sharing: state.settings.sharing,
    objective: state.settings.objective,
    custom_objective: state.settings.customObjective,
  };
}

function directObjective(): string {
  return state.settings.objective === 'custom'
    ? state.settings.customObjective.trim() || 'custom authorized AI security test'
    : OBJECTIVE_LABELS[state.settings.objective] ?? state.settings.objective;
}

function directContext(excludeLatest = false) {
  const history = excludeLatest ? directTurns.slice(0, -1) : directTurns;
  return {
    objective: directObjective(),
    origin: state.origin,
    turn: directSentPayloads.length + 1,
    maxTurns: state.maxTurns,
    instruction: state.settings.advancedInstruction,
    sent: directSentPayloads,
    history: history.slice(-3).map((turn) => ({
      goal: turn.goal,
      tactic: turn.tactic,
      hypothesis: turn.hypothesis,
      payload: turn.payload,
      response: prepareSharedResponse(turn.response, state.settings.sharing),
      verdict: turn.verdict,
      evaluationSummary: prepareSharedResponse(
        turn.evaluationSummary,
        state.settings.sharing,
      ),
      observedSignals: turn.observedSignals
        .map((signal) => prepareSharedResponse(signal, state.settings.sharing))
        .filter(Boolean),
    })),
  };
}

async function askDirect(prompt: string): Promise<{ text: string; model: string }> {
  if (!directApiKey) throw new Error('The direct API key is no longer available. Reconnect it.');
  if (!state.settings.requestedModel) throw new Error('Choose a model.');
  const requestId = crypto.randomUUID();
  directRequestId = requestId;
  let result: Record<string, any>;
  try {
    result = await callWorker({
      kind: 'direct-complete',
      requestId,
      provider: state.settings.provider,
      key: directApiKey,
      model: state.settings.requestedModel,
      prompt,
    });
  } catch (error) {
    if (directRequestId !== requestId) throw new Error('Request cancelled.');
    directRequestId = '';
    throw error;
  }
  if (directRequestId !== requestId) throw new Error('Request cancelled.');
  directRequestId = '';
  if (!result?.ok) throw new Error(String(result?.message ?? 'Direct provider request failed.'));
  return { text: String(result.text ?? ''), model: String(result.model ?? state.settings.requestedModel) };
}

async function requestDirectProposal(automatic = false): Promise<void> {
  dispatch({ type: 'stage', stage: 'generating', at: Date.now() });
  startStageTicker();
  const started = performance.now();
  try {
    const prompt = proposalPrompt(directContext());
    const proposal = await withStructuredRetry(
      askDirect,
      prompt,
      (answer) => parseProposal(
        answer.text,
        state.settings.objective,
        state.settings.provider,
        state.settings.requestedModel,
        answer.model,
      ),
    );
    stopStageTicker();
    dispatch({ type: 'latency', milliseconds: performance.now() - started, label: 'Generated' });
    dispatch({ type: 'proposal', proposal });
    dispatch({ type: 'timeline', entry: { at: Date.now(), kind: 'proposal.generated', detail: '' } });
    if (automatic && state.autoRunning) await performDirectSend(proposal.payload);
  } catch (error) {
    stopStageTicker();
    if ((error as Error).message === 'Request cancelled.') return;
    dispatch({ type: 'stage', stage: 'idle', at: Date.now() });
    fail(String((error as Error).message), 'test');
    if (automatic) dispatch({ type: 'auto_stopped', reason: 'proposal failed' });
  }
}

function directTurnForResponse(response: string): StoredReport['turns'][number] {
  const bounded = response.trim().slice(0, 32_768);
  const pending = directTurns.at(-1);
  if (pending && !pending.evaluationSummary && (!pending.response || pending.response === bounded)) {
    if (!pending.response) pending.response = bounded;
    return pending;
  }
  const turn: StoredReport['turns'][number] = {
    turnId: `direct-turn-${directTurns.length + 1}`,
    startedAt: new Date().toISOString(),
    approved: false,
    goal: state.proposal?.goal.slice(0, 2_000) ?? '',
    tactic: state.proposal?.tactic.slice(0, 2_000) ?? '',
    hypothesis: state.proposal?.hypothesis.slice(0, 2_000) ?? '',
    payload: state.proposal?.payload.slice(0, 16_384) ?? '',
    response: bounded,
    verdict: '',
    evaluationSummary: '',
    observedSignals: [],
  };
  directTurns.push(turn);
  return turn;
}

function recordDirectEvaluation(
  turn: StoredReport['turns'][number],
  evaluation: Evaluation,
): void {
  turn.verdict = evaluation.verdict.slice(0, 40);
  turn.evaluationSummary = evaluation.summary.slice(0, 4_000);
  turn.observedSignals = evaluation.observed_signals.slice(0, 30).map((signal) => signal.slice(0, 500));
}

function directAggregateVerdict(): string {
  const verdicts = new Set(directTurns.map((turn) => turn.verdict));
  for (const verdict of ['confirmed', 'potential', 'inconclusive', 'not_observed']) {
    if (verdicts.has(verdict)) return verdict;
  }
  return 'inconclusive';
}

function applyDirectEvaluation(
  turn: StoredReport['turns'][number],
  evaluation: Evaluation,
  detail = '',
): void {
  recordDirectEvaluation(turn, evaluation);
  dispatch({ type: 'evaluation', evaluation });
  dispatch({
    type: 'session',
    session: {
      verdict: directConfirmedEvaluation ? 'confirmed' : directAggregateVerdict(),
    },
  });
  dispatch({
    type: 'timeline',
    entry: { at: Date.now(), kind: 'evaluation.completed', detail },
  });
}

async function finishDirectAuto(reason: string): Promise<void> {
  dispatch({ type: 'session_ended', reason });
  await saveDirectReportSnapshot();
  uiDispatch({ type: 'follow_state' });
}

async function analyzeDirectResponse(response: string): Promise<void> {
  const reportTurn = directTurnForResponse(response);
  void saveDirectReportSnapshot();
  dispatch({ type: 'stage', stage: 'evaluating', at: Date.now() });
  startStageTicker();
  const shared = prepareSharedResponse(response, state.settings.sharing);
  if (!shared) {
    stopStageTicker();
    const evaluation = unsharedEvaluation();
    applyDirectEvaluation(reportTurn, evaluation, 'local only');
    await saveDirectReportSnapshot();
    return;
  }
  const started = performance.now();
  try {
    const context = directContext(true);
    const combined = state.settings.mode === 'guided' || state.settings.mode === 'auto';
    const prompt = combined ? decisionPrompt(context, shared) : evaluationPrompt(context, shared);
    const parsed = await withStructuredRetry(
      askDirect,
      prompt,
      (answer) => combined
        ? parseDecision(
          answer.text,
          state.settings.objective,
          state.settings.provider,
          state.settings.requestedModel,
          answer.model,
        )
        : parseDirectEvaluation(answer.text),
    );
    stopStageTicker();
    dispatch({ type: 'latency', milliseconds: performance.now() - started, label: combined ? 'Analyzed + planned' : 'Analyzed' });
    if (!combined) {
      const evaluation = parsed as Evaluation;
      applyDirectEvaluation(reportTurn, evaluation);
      await saveDirectReportSnapshot();
      return;
    }
    const decision = parsed as { evaluation: Evaluation; proposal: Proposal };
    applyDirectEvaluation(reportTurn, decision.evaluation);
    dispatch({ type: 'proposal', proposal: decision.proposal });
    await saveDirectReportSnapshot();
    if (state.settings.mode !== 'auto') return;
    if (decision.evaluation.verdict === 'confirmed') {
      await finishDirectAuto('confirmed');
      return;
    }
    if (decision.evaluation.verdict === 'potential') {
      if (state.settings.potentialFindingAction === 'review') {
        dispatch({ type: 'auto_stopped', reason: 'potential_review' });
        // Sending is paused pending a human decision; open that decision.
        uiDispatch({ type: 'follow_state' });
        return;
      }
      if (state.settings.potentialFindingAction === 'stop') {
        await finishDirectAuto('potential_found');
        return;
      }
    }
    const elapsed = (Date.now() - directStartedAt) / 1000;
    if (state.maxTurns > 0 && directSentPayloads.length >= state.maxTurns) {
      await finishDirectAuto('max_turns');
      return;
    }
    if (
      state.settings.maxDurationSeconds > 0
      && elapsed >= state.settings.maxDurationSeconds
    ) {
      await finishDirectAuto('max_duration');
      return;
    }
    if (state.autoRunning) await performDirectSend(decision.proposal.payload);
  } catch (error) {
    stopStageTicker();
    if ((error as Error).message === 'Request cancelled.') return;
    dispatch({ type: 'stage', stage: 'idle', at: Date.now() });
    fail(String((error as Error).message), 'test');
    if (state.settings.mode === 'auto') dispatch({ type: 'auto_stopped', reason: 'analysis failed' });
  }
}

function requestProposal(): void {
  if (state.settings.connectionMethod === 'direct') {
    void requestDirectProposal(false);
    return;
  }
  dispatch({ type: 'stage', stage: 'generating', at: Date.now() });
  startStageTicker();
  // The objective is the instruction. An advanced one is optional.
  send('proposal.request', { instruction: state.settings.advancedInstruction });
}

async function startTest(): Promise<void> {
  const readiness = evaluateReadiness(state);
  if (!readiness.ready) {
    // The blockers are Setup controls, so the message stays with them.
    fail(readiness.summary, 'global');
    return;
  }
  if (state.settings.connectionMethod === 'direct') {
    directStartedAt = Date.now();
    directReportCreatedAt = directStartedAt;
    directSentPayloads = [];
    directTurns = [];
    directConfirmedEvaluation = null;
    dispatch({
      type: 'session',
      session: {
        session_id: `direct-${crypto.randomUUID()}`,
        turns: 0,
        max_turns: state.settings.maxTurns,
        verdict: 'inconclusive',
        effective_model: state.settings.requestedModel,
      },
    });
    dispatch({ type: 'session_started' });
    // Direct mode runs without the Core, but the workspace flow is identical.
    uiDispatch({ type: 'follow_state' });
    dispatch({ type: 'timeline', entry: { at: Date.now(), kind: 'session.started', detail: 'direct API' } });
    if (state.settings.mode === 'auto') dispatch({ type: 'auto_started' });
    await requestDirectProposal(state.settings.mode === 'auto');
    return;
  }
  send('session.configure', configurePayload());
  const bindingReady =
    state.settings.responseSource === 'manual'
      ? bindingSendComplete(state.binding)
      : bindingComplete(state.binding);
  if (bindingReady) {
    send('session.bind', { binding: bindingToCore({ ...state.binding, origin: state.origin }) });
  }
  dispatch({ type: 'session_started' });
  // Hand navigation back to the assessment so the live workspace opens.
  uiDispatch({ type: 'follow_state' });
  dispatch({ type: 'timeline', entry: { at: Date.now(), kind: 'session.started', detail: '' } });
  // WebSocket preserves frame order; a short delay lets the UI render the
  // configured state before the provider starts.
  setTimeout(() => {
    if (state.settings.mode === 'auto') {
      send('auto.start', { instruction: state.settings.advancedInstruction });
    } else {
      requestProposal();
    }
  }, 120);
}

function continueAutoRun(addTurns: boolean): void {
  const additionalTurns = addTurns ? state.settings.maxTurns : 0;
  const stoppedForAnalysis = state.autoStopReason === 'analysis failed';
  if (state.settings.connectionMethod === 'direct') {
    directStartedAt = Date.now();
    if (additionalTurns) {
      dispatch({
        type: 'session',
        session: { max_turns: state.maxTurns + additionalTurns },
      });
    }
    dispatch({ type: 'auto_started' });
    if (stoppedForAnalysis && directTurns.at(-1)?.response) {
      void analyzeDirectResponse(directTurns.at(-1)!.response);
    } else if (state.proposal) void performDirectSend(state.proposal.payload);
    else void requestDirectProposal(true);
  } else {
    send('auto.start', additionalTurns ? { additional_turns: additionalTurns } : {});
  }
  uiDispatch({ type: 'follow_state' });
  setNotice(
    stoppedForAnalysis
      ? 'Retrying the interrupted analysis.'
      : additionalTurns
      ? `Added ${additionalTurns} turns to this run.`
      : 'Started another time window for this run.',
  );
}

function openOrRecoverReview(): void {
  if (state.evaluation) {
    navigate('review');
    return;
  }
  if (state.settings.connectionMethod === 'core') {
    send('hello', {});
    setNotice('Recovering the paused decision from Core…');
    return;
  }
  fail('The paused Direct API decision is no longer in memory. Reconnect the key and recover the run.', 'test');
}

async function recoverCoreRun(): Promise<void> {
  if (state.settings.connectionMethod !== 'core' || state.connection !== 'connected') {
    fail('Reconnect the local Core before recovering this run.', 'connection');
    return;
  }
  if (state.settings.mode !== 'payload_only' && !(await validateBinding())) {
    navigate('setup');
    return;
  }
  clearError('test');
  setNotice('The old Core session was lost. Continuing as a new segment with the same setup.');
  await startTest();
}

async function recheckAndContinue(): Promise<void> {
  if (!(await validateBinding())) {
    navigate('setup');
    return;
  }
  clearError('test');
  if (state.settings.connectionMethod === 'direct') {
    dispatch({ type: 'auto_stopped', reason: '' });
    continueAutoRun(false);
    return;
  }
  if (!state.sessionId) {
    await recoverCoreRun();
    return;
  }
  send('session.bind', { binding: bindingToCore({ ...state.binding, origin: state.origin }) });
  dispatch({ type: 'auto_stopped', reason: '' });
  setTimeout(() => send('auto.start', {}), 120);
  uiDispatch({ type: 'follow_state' });
  setNotice('Interaction restored. Continuing the same run.');
}

async function approveAndSend(): Promise<void> {
  if (state.settings.connectionMethod === 'direct') {
    await performDirectSend(state.editedPayload);
    return;
  }
  send('proposal.approve', { payload: state.editedPayload });
}

interface PageInteractionResult {
  sent: boolean;
  response: string | null;
}

/** The single page-mutation path used by both Core and explicitly-authorized direct mode. */
async function executePageInteraction(
  payload: string,
  submitStrategy = 'click_button',
  submitKey = 'Enter',
  stableMs = 1500,
  timeoutMs = 60000,
): Promise<PageInteractionResult> {
  const binding = bindingLocators();
  await callWorker({ kind: 'operation', operation: 'snapshot', binding });

  const filled = await callWorker({
    kind: 'operation',
    operation: 'fill',
    binding,
    value: payload,
  });
  if (!filled?.ok) {
    // The worker revalidates before every mutation; a `stale` refusal means the
    // binding stopped matching, which is a health problem rather than a fill
    // failure and must pause Auto rather than just print an error.
    if (filled?.['stale']) {
      const roles = parseBindingValidation(filled['roles']);
      const issues: Record<string, string> = {};
      for (const role of BINDING_ROLES) {
        const check = roles[role];
        if (check && !check.ok) issues[role] = check.reason;
      }
      dispatch({
        type: 'binding_health',
        health: 'needs_review',
        issues: Object.keys(issues).length ? issues : { binding: String(filled['message']) },
        at: Date.now(),
      });
      // A stale binding is a Setup problem, so the message travels with the
      // Interaction controls the operator has to fix.
      ui = reduceUi(ui, {
        type: 'error',
        area: 'interaction',
        message: String(filled['message']),
      });
      dispatch({ type: 'binding_invalid', detail: String(filled['message']) });
      return { sent: false, response: null };
    }
    fail(`Could not fill the input: ${String(filled?.message)}`, 'interaction');
    return { sent: false, response: null };
  }
  const sent = await callWorker({
    kind: 'operation',
    operation: 'submit',
    binding,
    submitStrategy,
    submitKey,
  });
  if (!sent?.ok) {
    fail(`Could not send: ${String(sent?.message)}`, 'interaction');
    return { sent: false, response: null };
  }
  dispatch({ type: 'stage', stage: 'waiting_for_response', at: Date.now() });
  dispatch({ type: 'timeline', entry: { at: Date.now(), kind: 'payload.sent', detail: '' } });

  if (state.settings.responseSource === 'manual') {
    setNotice('Payload sent. Paste the bot reply into Manual response trigger to continue.', 10000);
    dispatch({ type: 'clear_error' });
    return { sent: true, response: null };
  }

  const captured = await callWorker({
    kind: 'operation',
    operation: 'capture',
    binding,
    stableMs,
    timeoutMs,
  });
  if (!captured?.ok) {
    // A capture failure is never reported as an empty reply.
    dispatch({ type: 'stage', stage: 'timed_out', at: Date.now() });
    fail(
      'The reply could not be captured. Use Capture current response, or re-select the response container.',
      'interaction',
    );
    return { sent: true, response: null };
  }
  return { sent: true, response: String(captured['text'] ?? '') };
}

/** Run the operations the Core authorized, in order, then report the reply. */
async function performSend(authorized: Record<string, any>): Promise<void> {
  const result = await executePageInteraction(
    String(authorized['payload'] ?? ''),
    String(authorized['submit_strategy'] ?? 'click_button'),
    String(authorized['submit_key'] ?? 'Enter'),
    Number(authorized['stable_ms'] ?? 1500),
    Number(authorized['timeout_ms'] ?? 60000),
  );
  if (!result.sent) return;
  send('payload.sent', {});
  if (result.response !== null) send('response.captured', { text: result.response });
}

async function performDirectSend(payload: string): Promise<void> {
  if (!payload.trim()) {
    fail('The payload is empty.', 'test');
    return;
  }
  dispatch({ type: 'stage', stage: 'sending', at: Date.now() });
  const result = await executePageInteraction(
    payload,
    state.binding.submitStrategy,
    state.binding.submitKey,
  );
  if (!result.sent) {
    if (state.autoRunning) dispatch({ type: 'auto_stopped', reason: 'page interaction failed' });
    return;
  }
  directSentPayloads.push(payload);
  directTurns.push({
    turnId: `direct-turn-${directTurns.length + 1}`,
    startedAt: new Date().toISOString(),
    approved: true,
    goal: state.proposal?.goal.slice(0, 2_000) ?? '',
    tactic: state.proposal?.tactic.slice(0, 2_000) ?? '',
    hypothesis: state.proposal?.hypothesis.slice(0, 2_000) ?? '',
    payload: payload.slice(0, 16_384),
    response: result.response?.trim().slice(0, 32_768) ?? '',
    verdict: '',
    evaluationSummary: '',
    observedSignals: [],
  });
  dispatch({
    type: 'session',
    session: {
      turns: directSentPayloads.length,
      max_turns: state.maxTurns,
      effective_model: state.settings.requestedModel,
    },
  });
  if (result.response !== null) await analyzeDirectResponse(result.response);
  else void saveDirectReportSnapshot();
}

function submitManualResponse(): void {
  const text = manualResponse.trim();
  if (!text) {
    fail('Paste a non-empty bot response first.', 'test');
    return;
  }
  if (state.settings.mode === 'auto') {
    fail('Manual response trigger is unavailable in Auto mode.', 'test');
    return;
  }
  if (state.settings.connectionMethod === 'direct') {
    manualResponse = '';
    void analyzeDirectResponse(text);
    return;
  }
  const submit = (): void => {
    dispatch({ type: 'stage', stage: 'evaluating', at: Date.now() });
    startStageTicker();
    send('response.manual', { text });
    manualResponse = '';
  };
  if (!state.sessionId) {
    send('session.configure', configurePayload());
    setTimeout(submit, 120);
  } else {
    submit();
  }
}

function stopSession(): void {
  if (state.settings.connectionMethod !== 'direct') send('session.stop', {});
  dispatch({ type: 'session_ended', reason: 'stopped by operator' });
  if (state.settings.connectionMethod === 'direct') void saveDirectReportSnapshot();
  // A finished run belongs in Reports, not on a live screen with nothing live.
  uiDispatch({ type: 'follow_state' });
}

/** Read the report library from its runtime-owned durable store. */
function requestReports(): void {
  if (state.settings.connectionMethod === 'direct') {
    uiDispatch({ type: 'reports_loading', loading: true });
    void listDirectReports()
      .then((items) => {
        directReports = items;
        ui = reduceUi(ui, { type: 'reports_loading', loading: false });
        ui = reduceUi(ui, { type: 'clear_error', area: 'reports' });
        render();
      })
      .catch((error) => {
        ui = reduceUi(ui, { type: 'reports_loading', loading: false });
        fail(String((error as Error).message), 'reports');
      });
    return;
  }
  // Asking while disconnected would raise a connection error and drag the
  // operator back to Setup for something they did not do. The library already
  // explains that it needs the Core.
  if (state.connection !== 'connected') return;
  uiDispatch({ type: 'reports_loading', loading: true });
  send('reports.list', { limit: 50 });
}

/**
 * Open one stored report.
 *
 * JSON may be parsed into the safe in-panel viewer. HTML is only downloaded: a
 * stored HTML document contains target output and must never get a script
 * context inside the extension.
 */
function openReport(reportId: string, artifact: string, view: boolean): void {
  pendingReport = { reportId, artifact, view };
  send('reports.open', { report_id: reportId, artifact });
  render();
}

function downloadText(filename: string, text: string, type: string): void {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  // Revoke on the next tick: revoking synchronously can cancel the download.
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

function requestModels(): void {
  if (state.settings.connectionMethod === 'direct') {
    void connectDirect();
    return;
  }
  const requestId = String(Date.now());
  dispatch({ type: 'models_requested', requestId });
  send('models.list', { provider: state.settings.provider, request_id: requestId });
}

/* ------------------------------------------------------------------ boot */

async function boot(): Promise<void> {
  try {
    const stored = await callWorker({ kind: 'get-state' });
    if (stored?.local) state = restore(stored.local);
    if (stored?.session?.origin) {
      state = reduce(state, {
        type: 'target',
        origin: String(stored.session.origin),
        tabId: stored.session.tabId ?? null,
        documentId: String(stored.session.documentId ?? ''),
      });
    }
  } catch {
    /* first run */
  }

  // A navigation in the bound tab is pushed by the worker rather than polled.
  chrome.runtime?.onMessage?.addListener((message: unknown) => {
    if (typeof message !== 'object' || message === null) return;
    const request = message as Record<string, unknown>;
    if (request['channel'] !== 'sp-worker') return;
    if (request['kind'] !== 'target-changed') return;
    dispatch({
      type: 'target',
      origin: String(request['origin'] ?? ''),
      tabId: typeof request['tabId'] === 'number' ? request['tabId'] : null,
      documentId: String(request['documentId'] ?? ''),
    });
    scheduleRevalidation();
  });

  if (state.settings.connectionMethod === 'direct') {
    if (!DIRECT_ORIGINS[state.settings.provider]) {
      state = reduce(state, { type: 'settings', patch: { provider: 'openai' } });
    }
    state = reduce(state, { type: 'providers', providers: DIRECT_PROVIDERS });
    state = reduce(state, {
      type: 'connection',
      state: 'disconnected',
      detail: 'Direct API keys are session-only. Enter the key again to reconnect.',
    });
    render();
  } else {
    render();
    void connectToCore();
  }

  // Reopening the panel is one of the revalidation triggers: the document may
  // have been replaced entirely while the panel was closed, so a stored
  // "healthy" from last time proves nothing.
  if (state.bindingSaved) scheduleRevalidation();
}

if (typeof document !== 'undefined' && document.getElementById('root')) {
  void boot();
}
