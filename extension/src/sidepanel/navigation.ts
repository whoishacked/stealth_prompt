/**
 * Which workspace the Side Panel is showing, and why.
 *
 * The panel is a narrow column. Rendering setup, the live run, a finding review
 * and the report library into one scrolling document meant the operator read
 * configuration fields while a run was mid-flight, and the one action that
 * mattered was wherever the scroll happened to be. These are four different
 * jobs, so they are four different rendered workspaces.
 *
 * Two kinds of state meet here and are deliberately kept apart:
 *
 *  - **assessment state** (`PanelState`) is durable. It survives a reload and a
 *    target navigation, and it is what the Core and the evidence agree about.
 *  - **navigation state** (`UiState`) is transient. Which workspace is on
 *    screen and which section owns the current error are properties of *this
 *    open panel*, not of the
 *    assessment. None of it is persisted.
 *
 * The active workspace is therefore never stored. It is computed from the
 * assessment plus whatever the operator last asked for, which is what makes a
 * reload land somewhere sensible instead of somewhere stale.
 */

import type { PanelState } from '../storage/state.js';

export type Workspace = 'setup' | 'test' | 'review' | 'reports' | 'settings';

export const WORKSPACES: readonly Workspace[] = ['setup', 'test', 'review', 'reports', 'settings'];
export const PRIMARY_WORKSPACES: readonly Workspace[] = ['setup', 'test', 'reports'];

export const WORKSPACE_LABELS: Record<Workspace, string> = {
  setup: 'Setup',
  test: 'Test',
  review: 'Review',
  reports: 'Reports',
  settings: 'Settings',
};

/**
 * Where an error is displayed.
 *
 * An error attached to the wrong control is worse than no error: it sends the
 * operator to fix something that was never broken. Each area maps to exactly
 * one control group, and an error outlives only its own retry.
 */
export type ErrorArea =
  | 'global'
  | 'connection'
  | 'ai'
  | 'target'
  | 'interaction'
  | 'test'
  | 'reports';

/** Which workspace owns each error area, so an error is never orphaned. */
export const ERROR_AREA_WORKSPACE: Record<ErrorArea, Workspace> = {
  global: 'setup',
  connection: 'setup',
  ai: 'setup',
  target: 'setup',
  interaction: 'setup',
  test: 'test',
  reports: 'reports',
};

export interface PanelError {
  area: ErrorArea;
  message: string;
}

export interface UiState {
  /** The workspace the operator explicitly asked for, if it is still reachable. */
  requested: Workspace | null;
  error: PanelError | null;
  /** Report metadata last listed by the Core. Never persisted. */
  reportsLoading: boolean;
  /** A completed Setup step the operator reopened to change. */
  openStep: string | null;
}

export function initialUi(): UiState {
  return {
    requested: null,
    error: null,
    reportsLoading: false,
    openStep: null,
  };
}

export type UiAction =
  | { type: 'navigate'; workspace: Workspace }
  | { type: 'follow_state' }
  | { type: 'error'; area: ErrorArea; message: string }
  | { type: 'clear_error'; area?: ErrorArea }
  | { type: 'reports_loading'; loading: boolean }
  | { type: 'open_step'; step: string | null };

export function reduceUi(ui: UiState, action: UiAction): UiState {
  switch (action.type) {
    case 'navigate':
      return { ...ui, requested: action.workspace };

    // Used after an event that should hand control back to the assessment, so
    // the next automatic transition is not blocked by a stale operator choice.
    case 'follow_state':
      return { ...ui, requested: null };

    case 'error':
      return { ...ui, error: { area: action.area, message: action.message } };

    case 'clear_error':
      // Clearing is scoped: a successful retry in one section must not wipe an
      // unrelated error the operator has not dealt with yet.
      if (action.area && ui.error && ui.error.area !== action.area) return ui;
      return { ...ui, error: null };

    case 'reports_loading':
      return { ...ui, reportsLoading: action.loading };

    case 'open_step':
      return { ...ui, openStep: action.step };

    default:
      return ui;
  }
}

/** True while a run is mid-flight and the live workspace is the useful one. */
const ACTIVE_STAGES = new Set([
  'generating',
  'awaiting_approval',
  'sending',
  'waiting_for_response',
  'evaluating',
]);

export function runActive(state: PanelState): boolean {
  if (state.sessionEnded) return false;
  return state.autoRunning || ACTIVE_STAGES.has(state.stage) || state.turns > 0;
}

/**
 * True when a potential finding has paused automatic sending.
 *
 * This is the one transition the operator must not be able to miss: sending is
 * halted and only an explicit decision resumes or ends it, so the panel opens
 * the review rather than leaving it below the fold.
 */
export function reviewPending(state: PanelState): boolean {
  return (
    state.evaluation !== null &&
    state.evaluation.verdict === 'potential' &&
    state.autoStopReason === 'potential_review'
  );
}

/** True once a run has reached a terminal state worth summarising. */
export function runEnded(state: PanelState): boolean {
  return state.sessionEnded && state.turns > 0;
}

/**
 * Whether a workspace can be shown at all.
 *
 * This is what makes "incomplete configuration returns to Setup" a property of
 * the model rather than a special case in an event handler: the live workspace
 * simply has nothing to show before a run exists, so it cannot be entered.
 */
export function canEnter(state: PanelState, workspace: Workspace): boolean {
  switch (workspace) {
    case 'setup':
    case 'reports':
    case 'settings':
      return true;
    case 'test':
      return runActive(state) || runEnded(state);
    case 'review':
      return state.evaluation !== null;
    default:
      return false;
  }
}

/** The workspace the assessment state alone implies. */
export function derivedWorkspace(state: PanelState): Workspace {
  if (reviewPending(state)) return 'review';
  if (runActive(state)) return 'test';
  if (runEnded(state)) return 'reports';
  return 'setup';
}

/**
 * The workspace to render.
 *
 * An explicit choice wins while it remains reachable, so the operator can look
 * at Reports during a run without being yanked back. When it stops being
 * reachable -- or when an event hands control back with `follow_state` -- the
 * assessment decides again.
 */
export function activeWorkspace(state: PanelState, ui: UiState): Workspace {
  if (ui.requested && canEnter(state, ui.requested)) return ui.requested;
  return derivedWorkspace(state);
}

/**
 * Where an error should send the operator.
 *
 * Errors raised against a setup control must not be reported from the live
 * workspace, and vice versa: the message and the control it refers to belong on
 * the same screen.
 */
export function workspaceForError(area: ErrorArea): Workspace {
  return ERROR_AREA_WORKSPACE[area];
}
