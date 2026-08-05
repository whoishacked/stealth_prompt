/**
 * The wire contract with the local Core, and the internal extension contract.
 *
 * Two separate trust boundaries meet in this file, and keeping them apart is
 * the point:
 *
 *  - frames from the **Core** are trusted for content but still version- and
 *    shape-checked, because a version skew should fail loudly rather than half
 *    work;
 *  - messages from the **content script** are not trusted at all. That script
 *    runs inside a page the target controls, so anything it reports is treated
 *    as a claim about the page, never as a command.
 *
 * Nothing in the Core or page-operation contract can carry a provider path,
 * endpoint, or credential. Optional direct API messages are an internal
 * panel-to-worker contract with fixed endpoints and never reach the page.
 */

export const PROTOCOL_VERSION = 1;

/** Frames the extension may send to the Core. */
export type CoreRequestType =
  | 'pair'
  | 'hello'
  | 'capabilities.request'
  | 'providers.health'
  | 'models.list'
  | 'session.configure'
  | 'session.bind'
  | 'session.conversation'
  | 'proposal.request'
  | 'proposal.approve'
  | 'payload.sent'
  | 'response.captured'
  | 'response.manual'
  | 'auto.start'
  | 'finding.confirm'
  | 'session.export'
  | 'scenario.export'
  | 'scenario.preview'
  | 'reports.list'
  | 'reports.open'
  | 'session.stop'
  | 'cancel'
  | 'ping';

/** Frames the Core may send back. */
export const CORE_RESPONSE_TYPES = [
  'paired',
  'pair.rejected',
  'ready',
  'capabilities',
  'providers.health',
  'models',
  'session.configured',
  'session.bound',
  'session.status',
  'proposal.pending',
  'proposal',
  'proposal.refused',
  'proposal.failed',
  'send.authorized',
  'evaluation.pending',
  'evaluation',
  'auto.started',
  'exported',
  'scenario.exported',
  'scenario.preview',
  'reports',
  'report',
  'session.stopped',
  'cancelled',
  'error',
  'pong',
] as const;

export type CoreResponseType = (typeof CORE_RESPONSE_TYPES)[number];

export interface CoreFrame<T = Record<string, unknown>> {
  protocol_version: number;
  type: CoreResponseType;
  payload: T;
}

export class ProtocolError extends Error {}

/** Longest frame we will parse. Mirrors the Core's own cap. */
export const MAX_FRAME_BYTES = 1024 * 1024;

const RESPONSE_SET: ReadonlySet<string> = new Set(CORE_RESPONSE_TYPES);

/**
 * Parse one frame from the Core, failing closed.
 *
 * A version mismatch is an error rather than a best-effort read: silently
 * ignoring unknown fields is how a client ends up acting on a message it only
 * partly understood.
 */
export function parseCoreFrame(raw: string): CoreFrame {
  if (raw.length > MAX_FRAME_BYTES) {
    throw new ProtocolError('frame exceeds the size limit');
  }
  let document: unknown;
  try {
    document = JSON.parse(raw);
  } catch {
    throw new ProtocolError('frame is not valid JSON');
  }
  if (typeof document !== 'object' || document === null || Array.isArray(document)) {
    throw new ProtocolError('frame must be a JSON object');
  }
  const frame = document as Record<string, unknown>;

  const version = frame['protocol_version'];
  if (version !== PROTOCOL_VERSION) {
    throw new ProtocolError(
      `unsupported protocol version ${String(version)}; this extension speaks ${PROTOCOL_VERSION}`,
    );
  }
  const type = frame['type'];
  if (typeof type !== 'string' || !RESPONSE_SET.has(type)) {
    throw new ProtocolError(`unknown message type ${String(type)}`);
  }
  const payload = frame['payload'];
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) {
    throw new ProtocolError("'payload' must be an object");
  }
  return {
    protocol_version: PROTOCOL_VERSION,
    type: type as CoreResponseType,
    payload: payload as Record<string, unknown>,
  };
}

export function encodeCoreFrame(
  type: CoreRequestType,
  payload: Record<string, unknown> = {},
): string {
  return JSON.stringify({ protocol_version: PROTOCOL_VERSION, type, payload });
}

/* ------------------------------------------------------------------ internal */

/**
 * Operations the content script may be asked to perform.
 *
 * A closed list. There is no `evaluate`, no `navigate`, no `raw`. The Core
 * names one of these; a model never does.
 */
export const BROWSER_OPERATIONS = [
  'discover',
  'pick',
  'validate',
  'fill',
  'submit',
  'capture',
  'snapshot',
  'conversation',
  'highlight',
] as const;

export type BrowserOperation = (typeof BROWSER_OPERATIONS)[number];

const OPERATION_SET: ReadonlySet<string> = new Set(BROWSER_OPERATIONS);

export function isBrowserOperation(value: unknown): value is BrowserOperation {
  return typeof value === 'string' && OPERATION_SET.has(value);
}

/** A locator the operator produced by picking an element. */
export interface Locator {
  strategy: 'role' | 'label' | 'placeholder' | 'test_id' | 'css';
  value: string;
  name?: string | null;
  css_fallback?: string | null;
}

const LOCATOR_STRATEGIES: ReadonlySet<string> = new Set([
  'role',
  'label',
  'placeholder',
  'test_id',
  'css',
]);

/**
 * Validate a locator arriving from the content script.
 *
 * The content script is not trusted, so a locator it reports is checked before
 * being stored or sent to the Core.
 */
export function parseLocator(value: unknown): Locator | null {
  if (typeof value !== 'object' || value === null) return null;
  const record = value as Record<string, unknown>;
  const strategy = record['strategy'];
  const raw = record['value'];
  if (typeof strategy !== 'string' || !LOCATOR_STRATEGIES.has(strategy)) return null;
  if (typeof raw !== 'string' || raw.trim() === '' || raw.length > 512) return null;
  const name = record['name'];
  const fallback = record['css_fallback'];
  return {
    strategy: strategy as Locator['strategy'],
    value: raw,
    name: typeof name === 'string' ? name.slice(0, 200) : null,
    css_fallback: typeof fallback === 'string' ? fallback.slice(0, 512) : null,
  };
}

export type BindingRole = 'input' | 'submit' | 'response';

export const BINDING_ROLES: readonly BindingRole[] = ['input', 'submit', 'response'];

/** Discovery's finding for one role, with its own confidence and reason. */
export interface RoleSuggestion {
  locator: Locator | null;
  confidence: number;
  reason: string;
}

export interface BindingSuggestion {
  input: RoleSuggestion;
  submit: RoleSuggestion;
  response: RoleSuggestion;
  missing: string[];
}

function parseRoleSuggestion(value: unknown): RoleSuggestion {
  if (typeof value !== 'object' || value === null) {
    return { locator: null, confidence: 0, reason: '' };
  }
  const record = value as Record<string, unknown>;
  const confidence = record['confidence'];
  const reason = record['reason'];
  return {
    locator: parseLocator(record['locator']),
    // A page-supplied number is clamped, not trusted: it is rendered as a
    // percentage and drives what the operator accepts.
    confidence:
      typeof confidence === 'number' && Number.isFinite(confidence)
        ? Math.max(0, Math.min(100, Math.round(confidence)))
        : 0,
    reason: typeof reason === 'string' ? reason.slice(0, 200) : '',
  };
}

/** Validate the page's read-only binding suggestion before the panel uses it. */
export function parseBindingSuggestion(value: unknown): BindingSuggestion | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const missing = record['missing'];
  return {
    input: parseRoleSuggestion(record['input']),
    submit: parseRoleSuggestion(record['submit']),
    response: parseRoleSuggestion(record['response']),
    missing: Array.isArray(missing)
      ? missing
          .filter((item): item is string => typeof item === 'string')
          .slice(0, 6)
          .map((item) => item.slice(0, 160))
      : [],
  };
}

/** One role's answer to "does this still resolve?". */
export interface RoleValidation {
  ok: boolean;
  reason: string;
  matches: number;
}

export type BindingValidation = Record<BindingRole, RoleValidation | null>;

/**
 * Parse a validation result from the content script.
 *
 * Fails closed by design: an unreadable or absent result for a role becomes
 * "not ok", never an assumed pass. This runs immediately before a mutation, so
 * an ambiguous answer must stop the send rather than wave it through.
 */
export function parseBindingValidation(value: unknown): BindingValidation {
  const result: BindingValidation = { input: null, submit: null, response: null };
  if (typeof value !== 'object' || value === null) return result;
  const record = value as Record<string, unknown>;
  for (const role of BINDING_ROLES) {
    const entry = record[role];
    if (typeof entry !== 'object' || entry === null) continue;
    const item = entry as Record<string, unknown>;
    const matches = item['matches'];
    const reason = item['reason'];
    result[role] = {
      ok: item['ok'] === true,
      reason: typeof reason === 'string' ? reason.slice(0, 200) : '',
      matches: typeof matches === 'number' && Number.isFinite(matches) ? matches : 0,
    };
  }
  return result;
}

export interface InteractionBinding {
  origin: string;
  input: Locator | null;
  submit: Locator | null;
  response: Locator | null;
  submitStrategy: 'click_button' | 'press_key';
  submitKey: string;
  stableMs: number;
  timeoutMs: number;
}

export function emptyBinding(origin = ''): InteractionBinding {
  return {
    origin,
    input: null,
    submit: null,
    response: null,
    submitStrategy: 'click_button',
    submitKey: 'Enter',
    stableMs: 1500,
    timeoutMs: 60000,
  };
}

export function bindingComplete(binding: InteractionBinding): boolean {
  return Boolean(binding.input && binding.submit && binding.response);
}

export function bindingSendComplete(binding: InteractionBinding): boolean {
  return Boolean(binding.input && binding.submit);
}

/** Serialize a binding for the Core. Never includes page content. */
export function bindingToCore(binding: InteractionBinding): Record<string, unknown> {
  return {
    origin: binding.origin,
    input: binding.input,
    submit: {
      strategy: binding.submitStrategy,
      key: binding.submitKey,
      locator: binding.submit,
    },
    response: {
      locator: binding.response,
      stable_ms: binding.stableMs,
      timeout_ms: binding.timeoutMs,
    },
  };
}

/* ------------------------------------------------------------------ reports */

/**
 * One row in the report library.
 *
 * The Core derives these from stored session artifacts. They are still parsed
 * and bounded here: the fields carry a target origin and a model name that
 * ultimately came from a page or a provider, and they are rendered into a list.
 */
export interface ReportSummary {
  reportId: string;
  createdAt: string;
  targetOrigin: string;
  objective: string;
  verdict: string;
  turns: number;
  effectiveModel: string;
  provider: string;
  artifacts: string[];
}

export interface StoredReportTurn {
  turnId: string;
  startedAt: string;
  approved: boolean;
  goal: string;
  tactic: string;
  hypothesis: string;
  payload: string;
  response: string;
  verdict: string;
  evaluationSummary: string;
  observedSignals: string[];
}

export interface StoredReport {
  sessionId: string;
  exportedAt: string;
  targetOrigin: string;
  objective: string;
  verdict: string;
  provider: string;
  effectiveModel: string;
  mode: string;
  potentialFindingAction: string;
  turns: StoredReportTurn[];
}

/** Artifacts the panel will ask for. A closed list, matching the Core's. */
const REPORT_ARTIFACTS: ReadonlySet<string> = new Set([
  'report.html',
  'session.json',
  'scenario.json',
]);

/** Report ids are Core-generated; the shape is fixed, so it is checked. */
const REPORT_ID = /^assistant-\d{8}T\d{6}Z-[0-9a-f]{6}$/;

export const MAX_REPORT_ROWS = 200;

function boundedLine(value: unknown, limit: number): string {
  if (typeof value !== 'string') return '';
  return value.replace(/\s+/g, ' ').trim().slice(0, limit);
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function boundedBlock(value: unknown, limit = 65_536): string {
  return typeof value === 'string' ? value.trim().slice(0, limit) : '';
}

function boundedLines(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 30).map((item) => boundedLine(item, 500)).filter(Boolean);
}

/** Parse the stored evidence document before any of it reaches the DOM. */
export function parseStoredReport(value: unknown): StoredReport | null {
  const document_ = record(value);
  if (document_['kind'] !== 'assistant_session' || document_['schema_version'] !== 1) return null;
  const configuration = record(document_['configuration']);
  const rawTurns = document_['turns'];
  if (!Array.isArray(rawTurns)) return null;
  return {
    sessionId: boundedLine(document_['session_id'], 120),
    exportedAt: boundedLine(document_['exported_at'], 40),
    targetOrigin: boundedLine(configuration['origin'], 300),
    objective: boundedLine(configuration['objective_text'], 500)
      || boundedLine(configuration['objective'], 80),
    verdict: boundedLine(document_['verdict'], 40) || 'inconclusive',
    provider: boundedLine(configuration['provider_label'], 80)
      || boundedLine(configuration['provider'], 60),
    effectiveModel: boundedLine(configuration['effective_model'], 120)
      || boundedLine(configuration['requested_model'], 120),
    mode: boundedLine(configuration['mode'], 40),
    potentialFindingAction: boundedLine(configuration['potential_finding_action'], 40),
    turns: rawTurns.slice(0, 100).map((value): StoredReportTurn => {
      const turn = record(value);
      const proposal = record(turn['proposal']);
      const evaluation = record(turn['evaluation']);
      return {
        turnId: boundedLine(turn['turn_id'], 120),
        startedAt: boundedLine(turn['started_at'], 40),
        approved: turn['approved'] === true,
        goal: boundedBlock(proposal['goal'], 2_000),
        tactic: boundedBlock(proposal['tactic'], 2_000),
        hypothesis: boundedBlock(proposal['hypothesis'], 2_000),
        payload: boundedBlock(turn['approved_payload'] ?? proposal['payload']),
        response: boundedBlock(turn['response']),
        verdict: boundedLine(evaluation['verdict'], 40),
        evaluationSummary: boundedBlock(evaluation['summary'], 4_000),
        observedSignals: boundedLines(evaluation['observed_signals']),
      };
    }),
  };
}

/**
 * Parse one report row, or null when it is unusable.
 *
 * A row without a well-formed id is dropped rather than shown: the id is what
 * the panel would send back to open the report, so an unverifiable one has no
 * safe use.
 */
export function parseReportSummary(value: unknown): ReportSummary | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const reportId = record['report_id'];
  if (typeof reportId !== 'string' || !REPORT_ID.test(reportId)) return null;
  const turns = record['turns'];
  const artifacts = record['artifacts'];
  return {
    reportId,
    createdAt: boundedLine(record['created_at'], 40),
    targetOrigin: boundedLine(record['target_origin'], 300),
    objective: boundedLine(record['objective'], 80),
    verdict: boundedLine(record['verdict'], 40) || 'inconclusive',
    turns: typeof turns === 'number' && Number.isFinite(turns) ? Math.max(0, Math.round(turns)) : 0,
    effectiveModel: boundedLine(record['effective_model'], 120),
    provider: boundedLine(record['provider'], 60),
    artifacts: Array.isArray(artifacts)
      ? artifacts.filter((name): name is string => typeof name === 'string' && REPORT_ARTIFACTS.has(name))
      : [],
  };
}

/** Parse a whole listing, dropping unusable rows and bounding the total. */
export function parseReportList(value: unknown): ReportSummary[] {
  if (!Array.isArray(value)) return [];
  const rows: ReportSummary[] = [];
  for (const entry of value.slice(0, MAX_REPORT_ROWS)) {
    const parsed = parseReportSummary(entry);
    if (parsed) rows.push(parsed);
  }
  return rows;
}

export function isReportArtifact(value: unknown): value is string {
  return typeof value === 'string' && REPORT_ARTIFACTS.has(value);
}
