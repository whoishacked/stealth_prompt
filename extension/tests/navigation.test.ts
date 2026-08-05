/**
 * Workspace routing.
 *
 * The rules that matter are the ones the operator cannot see: that a run in
 * flight cannot be hidden behind a configuration screen, that a paused finding
 * forces a decision, and that an error is never shown away from the control
 * that caused it.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  ERROR_AREA_WORKSPACE,
  WORKSPACES,
  activeWorkspace,
  canEnter,
  derivedWorkspace,
  initialUi,
  reduceUi,
  reviewPending,
  runActive,
  runEnded,
  workspaceForError,
} from '../src/sidepanel/navigation.js';
import type { UiState } from '../src/sidepanel/navigation.js';
import { initialState } from '../src/storage/state.js';
import type { Evaluation, PanelState } from '../src/storage/state.js';

const evaluation = (verdict: string): Evaluation => ({
  evaluation_id: 'e1',
  verdict,
  summary: 'summary',
  observed_signals: [],
  evidence_ids: [],
  suggested_next_steps: [],
  deterministic: false,
});

/** A run that has produced at least one turn. */
function running(over: Partial<PanelState> = {}): PanelState {
  return { ...initialState(), turns: 1, stage: 'awaiting_approval', ...over };
}

function paused(): PanelState {
  return running({ evaluation: evaluation('potential'), autoStopReason: 'potential_review' });
}

test('a fresh panel opens Setup', () => {
  assert.equal(derivedWorkspace(initialState()), 'setup');
  assert.equal(activeWorkspace(initialState(), initialUi()), 'setup');
});

test('the live workspace cannot be entered before a run exists', () => {
  const state = initialState();
  assert.equal(canEnter(state, 'test'), false);
  assert.equal(canEnter(state, 'review'), false);
  // Setup and Reports are always available.
  assert.equal(canEnter(state, 'setup'), true);
  assert.equal(canEnter(state, 'reports'), true);
  assert.equal(canEnter(state, 'settings'), true);
});

test('an unreachable request falls back to the state-derived workspace', () => {
  const ui = reduceUi(initialUi(), { type: 'navigate', workspace: 'test' });
  // Asking for a workspace that has nothing to show must not strand the panel.
  assert.equal(activeWorkspace(initialState(), ui), 'setup');
});

test('starting a run moves to the live workspace', () => {
  assert.equal(derivedWorkspace(running()), 'test');
  assert.equal(runActive(running()), true);
});

test('a potential finding in Auto forces the review workspace', () => {
  const state = paused();
  assert.equal(reviewPending(state), true);
  assert.equal(derivedWorkspace(state), 'review');
});

test('a non-potential evaluation does not hijack navigation', () => {
  const state = running({ evaluation: evaluation('not_observed') });
  assert.equal(reviewPending(state), false);
  assert.equal(derivedWorkspace(state), 'test');
  // But the operator may still open it deliberately.
  assert.equal(canEnter(state, 'review'), true);
});

test('a potential verdict outside an Auto pause is not forced', () => {
  // Same verdict, no paused run: the operator is not interrupted.
  const state = running({ evaluation: evaluation('potential'), autoStopReason: '' });
  assert.equal(reviewPending(state), false);
  assert.equal(derivedWorkspace(state), 'test');
});

test('an ended run lands on Reports', () => {
  const state = running({ sessionEnded: true });
  assert.equal(runActive(state), false);
  assert.equal(runEnded(state), true);
  assert.equal(derivedWorkspace(state), 'reports');
});

test('an ended run can still reopen the live workspace for its record', () => {
  assert.equal(canEnter(running({ sessionEnded: true }), 'test'), true);
});

test('an operator can browse Reports during a live run without being pulled back', () => {
  const state = running();
  const ui = reduceUi(initialUi(), { type: 'navigate', workspace: 'reports' });
  assert.equal(activeWorkspace(state, ui), 'reports');
});

test('follow_state hands navigation back to the assessment', () => {
  const state = paused();
  let ui: UiState = reduceUi(initialUi(), { type: 'navigate', workspace: 'reports' });
  assert.equal(activeWorkspace(state, ui), 'reports');
  ui = reduceUi(ui, { type: 'follow_state' });
  assert.equal(activeWorkspace(state, ui), 'review');
});

test('settings is a workspace and follow_state returns to the assessment', () => {
  let ui = reduceUi(initialUi(), { type: 'navigate', workspace: 'settings' });
  assert.equal(activeWorkspace(initialState(), ui), 'settings');
  ui = reduceUi(ui, { type: 'follow_state' });
  assert.equal(activeWorkspace(initialState(), ui), 'setup');
});

/* ------------------------------------------------------------------ errors */

test('every error area belongs to exactly one workspace', () => {
  for (const [area, workspace] of Object.entries(ERROR_AREA_WORKSPACE)) {
    assert.ok(WORKSPACES.includes(workspace), `${area} -> ${workspace}`);
    assert.equal(workspaceForError(area as never), workspace);
  }
});

test('setup errors belong to Setup and run errors to the live workspace', () => {
  assert.equal(workspaceForError('connection'), 'setup');
  assert.equal(workspaceForError('ai'), 'setup');
  assert.equal(workspaceForError('target'), 'setup');
  assert.equal(workspaceForError('interaction'), 'setup');
  assert.equal(workspaceForError('test'), 'test');
  assert.equal(workspaceForError('reports'), 'reports');
});

test('an error is dismissible', () => {
  let ui = reduceUi(initialUi(), { type: 'error', area: 'ai', message: 'no model' });
  assert.equal(ui.error?.message, 'no model');
  ui = reduceUi(ui, { type: 'clear_error' });
  assert.equal(ui.error, null);
});

test('a successful retry elsewhere does not clear an unrelated error', () => {
  let ui = reduceUi(initialUi(), { type: 'error', area: 'target', message: 'no tab' });
  ui = reduceUi(ui, { type: 'clear_error', area: 'connection' });
  // The target problem is still unresolved, so it must still be visible.
  assert.equal(ui.error?.area, 'target');
  ui = reduceUi(ui, { type: 'clear_error', area: 'target' });
  assert.equal(ui.error, null);
});

test('only one error is shown at a time, and it is the most recent', () => {
  let ui = reduceUi(initialUi(), { type: 'error', area: 'connection', message: 'first' });
  ui = reduceUi(ui, { type: 'error', area: 'ai', message: 'second' });
  assert.equal(ui.error?.area, 'ai');
  assert.equal(ui.error?.message, 'second');
});

/* ------------------------------------------------------- reload behaviour */

test('navigation state is not part of the assessment', () => {
  const ui = reduceUi(initialUi(), { type: 'navigate', workspace: 'reports' });
  // Nothing in UiState belongs in durable storage; it is rebuilt each open.
  assert.deepEqual(Object.keys(initialUi()).sort(), [
    'error',
    'openStep',
    'reportsLoading',
    'requested',
  ]);
  assert.equal(ui.requested, 'reports');
});

test('a reload of a paused run reopens the decision', () => {
  // `initialUi()` is what a reopened panel starts from: no requested workspace.
  assert.equal(activeWorkspace(paused(), initialUi()), 'review');
});

test('a reload mid-run reopens the live workspace', () => {
  assert.equal(activeWorkspace(running(), initialUi()), 'test');
});

test('a reload after a finished run reopens Reports', () => {
  assert.equal(activeWorkspace(running({ sessionEnded: true }), initialUi()), 'reports');
});

test('a reload with no run reopens Setup', () => {
  assert.equal(activeWorkspace(initialState(), initialUi()), 'setup');
});
