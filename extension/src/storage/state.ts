/**
 * Session state and the identities it is keyed by.
 *
 * The bug this design exists to prevent: treating a Chrome tab or a document as
 * *the session*. A page reload creates a new document and a same-origin
 * navigation can change the tab's document id, and if the session is keyed by
 * either, the operator's work vanishes for no reason they can see.
 *
 * So the identities are kept separate and only the durable ones key anything:
 *
 *  - `sessionId`  - the assistant session. Survives reloads and navigation.
 *  - `origin`     - what a binding is scoped to. Survives everything.
 *  - `tabId`      - which tab is currently showing the target. Transient.
 *  - `documentId` - which document is currently loaded. Very transient; used
 *                   only to reject results from a page that has gone away.
 *
 * A reducer keeps the transitions in one testable place rather than scattered
 * across event handlers.
 */

import type { BindingSuggestion, InteractionBinding } from '../protocol/messages.js';
import { emptyBinding } from '../protocol/messages.js';

export const STATE_VERSION = 3;

export type ConnectionState =
  | 'disconnected'
  | 'connecting'
  | 'pairing_required'
  | 'connected'
  | 'error';

export type Stage =
  | 'idle'
  | 'discovering'
  | 'generating'
  | 'awaiting_approval'
  | 'sending'
  | 'waiting_for_response'
  | 'evaluating'
  | 'cancelled'
  | 'timed_out'
  | 'refused'
  | 'binding_invalid'
  | 'core_disconnected';

/**
 * How much the panel currently trusts the saved binding.
 *
 * `needs_review` is deliberately distinct from "no binding": the previously
 * reviewed locators are kept so the operator can see what broke and re-detect,
 * rather than starting from an empty form because a page reloaded.
 *
 * `unsupported` is the terminal case -- a cross-origin iframe, closed Shadow
 * DOM or canvas UI -- where re-detecting cannot help and saying so is more
 * useful than an endless revalidation loop.
 */
export type BindingHealth =
  | 'unknown'
  | 'healthy'
  | 'revalidating'
  | 'needs_review'
  | 'unsupported';

export type AssistMode = 'payload_only' | 'assist' | 'guided' | 'auto';
export type ResponseSource = 'page' | 'manual';
export type Sharing = 'none' | 'redacted' | 'full';
export type ConnectionMethod = 'unset' | 'core' | 'direct';
export type PotentialFindingAction = 'review' | 'stop' | 'continue';

export interface ProviderSpec {
  kind: string;
  label: string;
  external: boolean;
  model_discovery: boolean;
  custom_model: boolean;
  summary: string;
}

export interface ProviderHealth {
  kind: string;
  state: string;
  detail: string;
  remedy: string;
  usable: boolean;
}

export interface ModelOption {
  id: string;
  label: string;
  default: boolean;
}

export interface Proposal {
  proposal_id: string;
  objective: string;
  goal: string;
  tactic: string;
  hypothesis: string;
  payload: string;
  rationale: string;
  expected_signals: string[];
  risk: string;
  provider: string;
  requested_model: string | null;
  effective_model: string | null;
}

export interface Evaluation {
  evaluation_id: string;
  verdict: string;
  summary: string;
  observed_signals: string[];
  evidence_ids: string[];
  suggested_next_steps: string[];
  deterministic: boolean;
}

export interface TimelineEntry {
  at: number;
  kind: string;
  detail: string;
}

/** Settings the operator reviewed. Persisted in `chrome.storage.local`. */
export interface Settings {
  /** Core keeps credentials out of Chrome; direct trades that isolation for zero setup. */
  connectionMethod: ConnectionMethod;
  /** Loopback port the local Core listens on; matches `serve --port`. */
  corePort: number;
  provider: string;
  requestedModel: string;
  mode: AssistMode;
  responseSource: ResponseSource;
  potentialFindingAction: PotentialFindingAction;
  maxTurns: number;
  maxDurationSeconds: number;
  sharing: Sharing;
  objective: string;
  customObjective: string;
  advancedInstruction: string;
}

/** The port `stealth-prompt serve` uses unless told otherwise. */
export const DEFAULT_CORE_PORT = 17371;

export function defaultSettings(): Settings {
  return {
    connectionMethod: 'unset',
    corePort: DEFAULT_CORE_PORT,
    provider: 'fake',
    requestedModel: '',
    mode: 'assist',
    responseSource: 'page',
    potentialFindingAction: 'review',
    maxTurns: 20,
    maxDurationSeconds: 0,
    sharing: 'none',
    objective: 'instruction_disclosure',
    customObjective: '',
    advancedInstruction: '',
  };
}

/** Everything the Side Panel renders. */
export interface PanelState {
  version: number;
  connection: ConnectionState;
  connectionDetail: string;
  pairingRequired: boolean;
  coreVersion: string;

  sessionId: string;
  origin: string;
  tabId: number | null;
  documentId: string;

  settings: Settings;
  effectiveModel: string;

  providers: ProviderSpec[];
  health: Record<string, ProviderHealth>;
  models: ModelOption[];
  modelsError: string;
  modelRequestId: string;

  binding: InteractionBinding;
  bindingSaved: boolean;
  bindingValid: boolean;
  bindingHealth: BindingHealth;
  /** Per-role failure text, so the panel can name what broke. */
  bindingRoleIssues: Record<string, string>;
  bindingCheckedAt: number;
  /** Discovery's per-role confidence and reason, pending operator review. */
  suggestion: BindingSuggestion | null;

  stage: Stage;
  stageStartedAt: number;
  proposal: Proposal | null;
  editedPayload: string;
  evaluation: Evaluation | null;
  autoRunning: boolean;
  autoStopReason: string;
  lastLatencyMs: number;
  lastLatencyLabel: string;
  refusalExcerpt: string;
  errorMessage: string;

  turns: number;
  maxTurns: number;
  verdict: string;
  timeline: TimelineEntry[];
  /**
   * True once the run reached a terminal state (stopped, confirmed, or limit
   * reached). Durable assessment state, not a view flag: it is what lets a
   * reload tell "no run yet" apart from "run finished", which are different
   * screens.
   */
  sessionEnded: boolean;
}

export function initialState(): PanelState {
  return {
    version: STATE_VERSION,
    connection: 'disconnected',
    connectionDetail: '',
    pairingRequired: false,
    coreVersion: '',
    sessionId: '',
    origin: '',
    tabId: null,
    documentId: '',
    settings: defaultSettings(),
    effectiveModel: '',
    providers: [],
    health: {},
    models: [],
    modelsError: '',
    modelRequestId: '',
    binding: emptyBinding(),
    bindingSaved: false,
    bindingValid: false,
    bindingHealth: 'unknown',
    bindingRoleIssues: {},
    bindingCheckedAt: 0,
    suggestion: null,
    stage: 'idle',
    stageStartedAt: 0,
    proposal: null,
    editedPayload: '',
    evaluation: null,
    autoRunning: false,
    autoStopReason: '',
    lastLatencyMs: 0,
    lastLatencyLabel: '',
    refusalExcerpt: '',
    errorMessage: '',
    turns: 0,
    maxTurns: 20,
    verdict: 'inconclusive',
    timeline: [],
    sessionEnded: false,
  };
}

export type Action =
  | { type: 'connection'; state: ConnectionState; detail?: string }
  | { type: 'pairing_required' }
  | { type: 'ready'; coreVersion: string; session: Record<string, unknown> | null }
  | { type: 'settings'; patch: Partial<Settings> }
  | { type: 'providers'; providers: ProviderSpec[] }
  | { type: 'health'; health: ProviderHealth[] }
  | { type: 'models'; provider: string; requestId: string; models: ModelOption[]; error: string }
  | { type: 'models_requested'; requestId: string }
  | { type: 'target'; origin: string; tabId: number | null; documentId: string }
  | { type: 'binding'; binding: InteractionBinding }
  | { type: 'binding_saved'; valid: boolean }
  | { type: 'binding_invalid'; detail: string }
  | { type: 'binding_revalidating' }
  | {
      type: 'binding_health';
      health: BindingHealth;
      issues?: Record<string, string>;
      at: number;
    }
  | { type: 'suggestion'; suggestion: BindingSuggestion | null }
  | { type: 'stage'; stage: Stage; at: number }
  | { type: 'proposal'; proposal: Proposal }
  | { type: 'proposal_edited'; payload: string }
  | { type: 'refused'; excerpt: string }
  | { type: 'evaluation'; evaluation: Evaluation }
  | { type: 'auto_started' }
  | { type: 'session_started' }
  | { type: 'session_ended'; reason?: string }
  | { type: 'auto_stopped'; reason: string }
  | { type: 'latency'; milliseconds: number; label: string }
  | { type: 'session'; session: Record<string, unknown> }
  | { type: 'error'; message: string }
  | { type: 'clear_error' }
  | { type: 'timeline'; entry: TimelineEntry }
  | { type: 'restore'; state: PanelState };

const MAX_TIMELINE = 200;

function applySession(state: PanelState, session: Record<string, unknown>): PanelState {
  const str = (key: string, fallback = ''): string => {
    const value = session[key];
    return typeof value === 'string' ? value : fallback;
  };
  const num = (key: string, fallback: number): number => {
    const value = session[key];
    return typeof value === 'number' ? value : fallback;
  };
  return {
    ...state,
    sessionId: str('session_id', state.sessionId),
    effectiveModel: str('effective_model', state.effectiveModel),
    turns: num('turns', state.turns),
    maxTurns: num('max_turns', state.maxTurns),
    verdict: str('verdict', state.verdict),
    origin: str('origin', state.origin),
  };
}

/**
 * The single place state changes.
 *
 * Note what `target` does *not* do: a new tab or document never clears the
 * session, the settings, the binding, or the timeline. That is the whole fix
 * for work disappearing on reload.
 */
export function reduce(state: PanelState, action: Action): PanelState {
  switch (action.type) {
    case 'connection':
      return {
        ...state,
        connection: action.state,
        connectionDetail: action.detail ?? '',
        errorMessage:
          action.state === 'connecting' || action.state === 'connected' ? '' : state.errorMessage,
        stage: action.state === 'disconnected' ? 'core_disconnected' : state.stage,
      };

    case 'pairing_required':
      return { ...state, connection: 'pairing_required', pairingRequired: true };

    case 'ready': {
      const lostSession = !action.session && Boolean(state.sessionId) && !state.sessionEnded;
      const next: PanelState = {
        ...state,
        connection: 'connected',
        pairingRequired: false,
        coreVersion: action.coreVersion,
        settings:
          state.settings.connectionMethod === 'unset'
            ? { ...state.settings, connectionMethod: 'core' }
            : state.settings,
        connectionDetail: '',
        errorMessage: '',
        stage: state.stage === 'core_disconnected' ? 'idle' : state.stage,
        sessionId: lostSession ? '' : state.sessionId,
        autoRunning: lostSession ? false : state.autoRunning,
        autoStopReason: lostSession ? 'core_session_lost' : state.autoStopReason,
      };
      return action.session ? applySession(next, action.session) : next;
    }

    case 'settings': {
      const settings = { ...state.settings, ...action.patch };
      // A model belongs to the provider it came from; carrying it across would
      // misdescribe the run.
      const providerChanged =
        action.patch.provider !== undefined && action.patch.provider !== state.settings.provider;
      return {
        ...state,
        settings: providerChanged ? { ...settings, requestedModel: '' } : settings,
        models: providerChanged ? [] : state.models,
        modelsError: providerChanged ? '' : state.modelsError,
        effectiveModel: providerChanged ? '' : state.effectiveModel,
        autoRunning: action.patch.mode !== undefined ? false : state.autoRunning,
      };
    }

    case 'providers':
      return { ...state, providers: action.providers };

    case 'health': {
      const health: Record<string, ProviderHealth> = {};
      for (const entry of action.health) health[entry.kind] = entry;
      return { ...state, health };
    }

    case 'models_requested':
      return { ...state, modelRequestId: action.requestId, modelsError: '' };

    case 'models':
      // Drop a reply for a provider or request we have moved past.
      if (action.provider !== state.settings.provider) return state;
      if (action.requestId && state.modelRequestId && action.requestId !== state.modelRequestId) {
        return state;
      }
      return { ...state, models: action.models, modelsError: action.error };

    case 'target': {
      const sameOrigin = action.origin === state.origin;
      const newDocument = action.documentId !== state.documentId;
      return {
        ...state,
        tabId: action.tabId,
        documentId: action.documentId,
        // Only the origin changing invalidates a binding.
        origin: action.origin,
        bindingValid: sameOrigin ? state.bindingValid : false,
        // A new document has not been checked yet. Say so rather than either
        // claiming the old result still holds or discarding reviewed work.
        bindingHealth: !sameOrigin
          ? 'unknown'
          : newDocument && state.bindingSaved
            ? 'revalidating'
            : state.bindingHealth,
        // Auto never continues across a document it has not revalidated.
        autoRunning: sameOrigin && !newDocument ? state.autoRunning : false,
      };
    }

    case 'binding':
      return {
        ...state,
        binding: action.binding,
        bindingSaved: false,
        bindingValid: false,
        bindingHealth: 'unknown',
        bindingRoleIssues: {},
      };

    case 'binding_saved':
      return {
        ...state,
        bindingSaved: true,
        bindingValid: action.valid,
        bindingHealth: action.valid ? 'healthy' : 'needs_review',
        bindingRoleIssues: action.valid ? {} : state.bindingRoleIssues,
      };

    case 'binding_revalidating':
      // The previously reviewed binding is kept. Only the trust level moves.
      return { ...state, bindingHealth: 'revalidating' };

    case 'binding_health': {
      const healthy = action.health === 'healthy';
      return {
        ...state,
        bindingHealth: action.health,
        bindingRoleIssues: action.issues ?? {},
        bindingCheckedAt: action.at,
        bindingValid: healthy && state.bindingSaved,
        // A binding that stopped resolving must not leave an automatic run
        // authorized to keep sending into a page that has changed underneath
        // it. Pausing is the fail-closed direction.
        autoRunning: healthy ? state.autoRunning : false,
        autoStopReason: healthy
          ? state.autoStopReason
          : 'Paused: the interaction binding needs review.',
      };
    }

    case 'binding_invalid':
      return {
        ...state,
        bindingValid: false,
        bindingHealth: 'needs_review',
        stage: 'binding_invalid',
        errorMessage: action.detail,
        autoRunning: false,
        autoStopReason: 'Paused: the interaction binding needs review.',
      };

    case 'suggestion':
      return { ...state, suggestion: action.suggestion };

    case 'stage':
      return {
        ...state,
        stage: action.stage,
        stageStartedAt: action.at,
        errorMessage: '',
        proposal: action.stage === 'sending' ? null : state.proposal,
        editedPayload: action.stage === 'sending' ? '' : state.editedPayload,
      };

    case 'proposal':
      return {
        ...state,
        proposal: action.proposal,
        editedPayload: action.proposal.payload,
        stage: 'awaiting_approval',
        refusalExcerpt: '',
        errorMessage: '',
        effectiveModel: action.proposal.effective_model ?? state.effectiveModel,
      };

    case 'proposal_edited':
      return { ...state, editedPayload: action.payload };

    case 'refused':
      // A refusal is never a payload.
      return {
        ...state,
        stage: 'refused',
        refusalExcerpt: action.excerpt,
        proposal: null,
        editedPayload: '',
      };

    case 'evaluation':
      return { ...state, evaluation: action.evaluation, stage: 'idle', errorMessage: '' };

    case 'auto_started':
      return {
        ...state,
        autoRunning: true,
        autoStopReason: '',
        errorMessage: '',
        sessionEnded: false,
      };

    case 'session_started':
      // Starting again reopens a finished assessment rather than leaving the
      // panel showing a terminal summary over a live run.
      return {
        ...state,
        sessionEnded: false,
        autoStopReason: '',
        evaluation: null,
        errorMessage: '',
      };

    case 'session_ended':
      return {
        ...state,
        sessionEnded: true,
        autoRunning: false,
        autoStopReason: action.reason ?? state.autoStopReason,
        stage: 'idle',
      };

    case 'auto_stopped':
      return {
        ...state,
        autoRunning: false,
        autoStopReason: action.reason,
        stage: 'idle',
      };

    case 'latency':
      return {
        ...state,
        lastLatencyMs: Math.max(0, action.milliseconds),
        lastLatencyLabel: action.label,
      };

    case 'session':
      return applySession(state, action.session);

    case 'error':
      return { ...state, errorMessage: action.message };

    case 'clear_error':
      return { ...state, errorMessage: '' };

    case 'timeline': {
      const previous = state.timeline.at(-1);
      if (previous?.kind === action.entry.kind && previous.detail === action.entry.detail) {
        return state;
      }
      const timeline = [...state.timeline, action.entry];
      return { ...state, timeline: timeline.slice(-MAX_TIMELINE) };
    }

    case 'restore':
      return { ...action.state, version: STATE_VERSION };

    default:
      return state;
  }
}

/** What may be written to durable storage. Never secrets or page content. */
export function persistable(state: PanelState): Record<string, unknown> {
  return {
    version: STATE_VERSION,
    settings: state.settings,
    binding: state.binding,
    bindingSaved: state.bindingSaved,
    origin: state.origin,
    sessionId: state.sessionId,
    turns: state.turns,
    maxTurns: state.maxTurns,
    verdict: state.verdict,
    autoStopReason: state.autoStopReason,
    timeline: state.timeline,
    effectiveModel: state.effectiveModel,
    sessionEnded: state.sessionEnded,
  };
}

/** Rehydrate, tolerating an older or partial record. */
/** A usable loopback port, or the default when the stored value is unusable. */
export function coercePort(value: unknown): number {
  const port = typeof value === 'number' ? value : Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return DEFAULT_CORE_PORT;
  return port;
}

export function restore(raw: unknown): PanelState {
  const base = initialState();
  if (typeof raw !== 'object' || raw === null) return base;
  const record = raw as Record<string, unknown>;
  if (record['version'] !== STATE_VERSION && record['version'] !== 2 && record['version'] !== 1) {
    return base;
  }

  const settings = record['settings'];
  const binding = record['binding'];
  const storedSettings =
    typeof settings === 'object' && settings !== null
      ? (settings as Partial<Settings>)
      : null;
  // Records written before the connection picker existed were Core-based.
  const restoredMethod: ConnectionMethod =
    storedSettings?.connectionMethod === 'direct' || storedSettings?.connectionMethod === 'core'
      ? storedSettings.connectionMethod
      : 'core';
  return {
    ...base,
    settings:
      storedSettings
        ? {
            ...base.settings,
            ...storedSettings,
            connectionMethod: restoredMethod,
            corePort: coercePort(storedSettings.corePort),
            maxTurns: coerceBoundedInt(
              storedSettings.maxTurns,
              0,
              100,
              base.settings.maxTurns,
            ),
            potentialFindingAction:
              storedSettings.potentialFindingAction === 'stop'
              || storedSettings.potentialFindingAction === 'continue'
                ? storedSettings.potentialFindingAction
                : 'review',
            maxDurationSeconds: coerceBoundedInt(
              record['version'] === STATE_VERSION ? storedSettings.maxDurationSeconds : 0,
              0,
              1800,
              base.settings.maxDurationSeconds,
            ),
          }
        : base.settings,
    binding:
      typeof binding === 'object' && binding !== null
        ? ({ ...base.binding, ...(binding as Partial<InteractionBinding>) } as InteractionBinding)
        : base.binding,
    bindingSaved: record['bindingSaved'] === true,
    origin: typeof record['origin'] === 'string' ? record['origin'] : '',
    sessionId: typeof record['sessionId'] === 'string' ? record['sessionId'] : '',
    turns: typeof record['turns'] === 'number' ? record['turns'] : 0,
    maxTurns: typeof record['maxTurns'] === 'number' ? record['maxTurns'] : 20,
    verdict: typeof record['verdict'] === 'string' ? record['verdict'] : 'inconclusive',
    autoStopReason:
      typeof record['autoStopReason'] === 'string'
        ? record['autoStopReason'].slice(0, 80)
        : '',
    effectiveModel:
      typeof record['effectiveModel'] === 'string' ? record['effectiveModel'] : '',
    timeline: Array.isArray(record['timeline'])
      ? (record['timeline'] as TimelineEntry[]).slice(-MAX_TIMELINE)
      : [],
    sessionEnded: record['sessionEnded'] === true,
  };
}

function coerceBoundedInt(
  value: unknown,
  minimum: number,
  maximum: number,
  fallback: number,
): number {
  return typeof value === 'number' &&
    Number.isInteger(value) &&
    value >= minimum &&
    value <= maximum
    ? value
    : fallback;
}
