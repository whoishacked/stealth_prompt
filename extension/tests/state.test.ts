/**
 * The reducer is where "my work disappeared on reload" is prevented, so the
 * navigation cases are the important ones here.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { emptyBinding } from '../src/protocol/messages.js';
import { evaluateReadiness } from '../src/storage/readiness.js';
import type { PanelState } from '../src/storage/state.js';
import {
  DEFAULT_CORE_PORT,
  coercePort,
  initialState,
  persistable,
  reduce,
  restore,
} from '../src/storage/state.js';

const withBinding = (state: PanelState): PanelState => {
  const binding = emptyBinding('https://example.test');
  binding.input = { strategy: 'css', value: '#a' };
  binding.submit = { strategy: 'css', value: '#b' };
  binding.response = { strategy: 'css', value: '.c' };
  return reduce(reduce(state, { type: 'binding', binding }), { type: 'binding_saved', valid: true });
};

test('a new document does not erase the session', () => {
  let state = initialState();
  state = reduce(state, { type: 'ready', coreVersion: '1', session: { session_id: 's1', turns: 2 } });
  // The real order: bind the target tab first, then pick and save elements.
  state = reduce(state, { type: 'target', origin: 'https://example.test', tabId: 7, documentId: 'doc-1' });
  state = withBinding(state);
  state = reduce(state, { type: 'timeline', entry: { at: 1, kind: 'proposal.generated', detail: '' } });

  // Reload: same origin, new document id, possibly new tab id.
  state = reduce(state, { type: 'target', origin: 'https://example.test', tabId: 42, documentId: 'doc-2' });

  assert.equal(state.sessionId, 's1');
  assert.equal(state.turns, 2);
  assert.equal(state.bindingSaved, true);
  assert.equal(state.bindingValid, true, 'a same-origin reload keeps the binding valid');
  assert.equal(state.timeline.length, 1);
});

test('a different origin invalidates the binding but keeps settings', () => {
  let state = withBinding(reduce(initialState(), { type: 'target', origin: 'https://example.test', tabId: 1, documentId: 'd' }));
  state = reduce(state, { type: 'settings', patch: { provider: 'codex' } });
  state = reduce(state, { type: 'target', origin: 'https://other.test', tabId: 1, documentId: 'd2' });

  assert.equal(state.bindingValid, false);
  assert.equal(state.settings.provider, 'codex');
});

test('changing provider clears the model and effective model', () => {
  let state = reduce(initialState(), { type: 'settings', patch: { provider: 'codex', requestedModel: 'gpt-5' } });
  state = reduce(state, { type: 'models', provider: 'codex', requestId: '', models: [{ id: 'gpt-5', label: 'GPT-5', default: true }], error: '' });
  state = reduce(state, { type: 'settings', patch: { provider: 'ollama' } });

  assert.equal(state.settings.requestedModel, '');
  assert.deepEqual(state.models, []);
  assert.equal(state.effectiveModel, '');
});

test('a stale model list is ignored', () => {
  let state = reduce(initialState(), { type: 'settings', patch: { provider: 'codex' } });
  state = reduce(state, { type: 'models_requested', requestId: '2' });
  // Reply for a request we already superseded.
  state = reduce(state, { type: 'models', provider: 'codex', requestId: '1', models: [{ id: 'stale', label: 'stale', default: false }], error: '' });
  assert.deepEqual(state.models, []);
  // Reply for a provider we moved away from.
  state = reduce(state, { type: 'models', provider: 'ollama', requestId: '2', models: [{ id: 'wrong', label: 'w', default: false }], error: '' });
  assert.deepEqual(state.models, []);
  // The matching reply lands.
  state = reduce(state, { type: 'models', provider: 'codex', requestId: '2', models: [{ id: 'ok', label: 'ok', default: false }], error: '' });
  assert.equal(state.models[0]?.id, 'ok');
});

test('a refusal is never kept as a payload', () => {
  let state = reduce(initialState(), {
    type: 'proposal',
    proposal: { proposal_id: 'p', objective: 'o', goal: 'g', tactic: 't', hypothesis: 'h', payload: 'the payload', rationale: '', expected_signals: [], risk: 'low', provider: 'fake', requested_model: null, effective_model: null },
  });
  assert.equal(state.editedPayload, 'the payload');
  state = reduce(state, { type: 'refused', excerpt: 'I will not do that.' });
  assert.equal(state.proposal, null);
  assert.equal(state.editedPayload, '');
  assert.equal(state.stage, 'refused');
});

test('an authorized send cannot leave a stale payload clickable', () => {
  let state = reduce(initialState(), {
    type: 'proposal',
    proposal: { proposal_id: 'p', objective: 'o', goal: 'g', tactic: 't', hypothesis: 'h', payload: 'send once', rationale: '', expected_signals: [], risk: 'low', provider: 'fake', requested_model: null, effective_model: null },
  });
  state = reduce(state, { type: 'stage', stage: 'sending', at: 10 });

  assert.equal(state.proposal, null);
  assert.equal(state.editedPayload, '');
  assert.equal(state.stage, 'sending');
});

test('persisted state round-trips and holds no secrets', () => {
  const state = withBinding(reduce(initialState(), { type: 'settings', patch: { provider: 'codex' } }));
  const stored = persistable(state);
  const text = JSON.stringify(stored).toLowerCase();
  for (const forbidden of ['token', 'cookie', 'password', 'api_key', 'apikey']) {
    assert.equal(text.includes(forbidden), false, forbidden);
  }
  const restored = restore(stored);
  assert.equal(restored.settings.provider, 'codex');
  assert.equal(restored.bindingSaved, true);
});

test('direct API can satisfy connection readiness without a Core', () => {
  let state = reduce(initialState(), {
    type: 'settings',
    patch: { connectionMethod: 'direct', provider: 'openai', mode: 'payload_only' },
  });
  state = reduce(state, { type: 'settings', patch: { requestedModel: 'gpt-test' } });
  state = reduce(state, { type: 'ready', coreVersion: 'Direct API', session: null });
  state = reduce(state, {
    type: 'providers',
    providers: [{ kind: 'openai', label: 'OpenAI', external: true, model_discovery: true, custom_model: true, summary: '' }],
  });
  state = reduce(state, {
    type: 'health',
    health: [{ kind: 'openai', state: 'authenticated', detail: 'Direct API ready', remedy: '', usable: true }],
  });
  assert.equal(evaluateReadiness(state).ready, true);
  assert.equal(JSON.stringify(persistable(state)).includes('apiKey'), false);
});

test('a successful connection clears stale failure messages', () => {
  let state = reduce(initialState(), { type: 'error', message: 'Previous attempt failed.' });
  state = reduce(state, { type: 'connection', state: 'error', detail: 'Core unavailable.' });
  state = reduce(state, { type: 'connection', state: 'connecting', detail: 'Retrying…' });
  assert.equal(state.errorMessage, '');
  state = reduce(state, { type: 'ready', coreVersion: 'test', session: null });
  assert.equal(state.connectionDetail, '');
  assert.equal(state.errorMessage, '');
});

test('a restarted Core clears a stale session and makes recovery explicit', () => {
  let state = reduce(initialState(), {
    type: 'ready',
    coreVersion: '1',
    session: { session_id: 'old-session', turns: 4 },
  });
  state = reduce(state, { type: 'settings', patch: { mode: 'auto' } });

  state = reduce(state, { type: 'ready', coreVersion: '2', session: null });

  assert.equal(state.sessionId, '');
  assert.equal(state.autoRunning, false);
  assert.equal(state.autoStopReason, 'core_session_lost');
  assert.equal(state.connection, 'connected');
});

test('an unknown stored version falls back to defaults', () => {
  const restored = restore({ version: 999, settings: { provider: 'codex' } });
  assert.equal(restored.settings.provider, 'fake');
});

test('auto defaults to 20 turns with no time limit', () => {
  const settings = initialState().settings;
  assert.equal(settings.maxTurns, 20);
  assert.equal(settings.maxDurationSeconds, 0);
});

test('readiness names every missing prerequisite', () => {
  const readiness = evaluateReadiness(initialState());
  assert.equal(readiness.ready, false);
  assert.ok(readiness.summary.startsWith('Cannot start:'));
  for (const blocker of readiness.blockers) {
    assert.ok(blocker.action.length > 0, `${blocker.key} has no action`);
  }
  assert.ok(readiness.blockers.some((b) => b.key === 'core'));
  assert.ok(readiness.blockers.some((b) => b.key === 'response'));
});

test('payload-only needs no page elements', () => {
  let state = reduce(initialState(), { type: 'settings', patch: { mode: 'payload_only' } });
  state = reduce(state, { type: 'ready', coreVersion: '1', session: null });
  state = reduce(state, { type: 'providers', providers: [{ kind: 'fake', label: 'Fake', external: false, model_discovery: false, custom_model: false, summary: '' }] });
  state = reduce(state, { type: 'health', health: [{ kind: 'fake', state: 'authenticated', detail: '', remedy: '', usable: true }] });

  const readiness = evaluateReadiness(state);
  assert.equal(readiness.ready, true, JSON.stringify(readiness.blockers));
});

test('manual response trigger makes the response container optional', () => {
  let state = initialState();
  state = reduce(state, { type: 'ready', coreVersion: '1', session: null });
  state = reduce(state, {
    type: 'providers',
    providers: [{ kind: 'fake', label: 'Fake', external: false, model_discovery: false, custom_model: false, summary: '' }],
  });
  state = reduce(state, {
    type: 'health',
    health: [{ kind: 'fake', state: 'authenticated', detail: '', remedy: '', usable: true }],
  });
  state = reduce(state, { type: 'settings', patch: { responseSource: 'manual' } });
  const binding = emptyBinding('https://example.test');
  binding.input = { strategy: 'css', value: '#input' };
  binding.submit = { strategy: 'css', value: '#send' };
  state = reduce(state, { type: 'binding', binding });
  state = reduce(state, { type: 'binding_saved', valid: true });

  const readiness = evaluateReadiness(state);
  assert.equal(readiness.ready, true, JSON.stringify(readiness.blockers));
  assert.equal(readiness.checks.find((check) => check.key === 'response')?.required, false);
});

test('auto requires page capture and adaptive response sharing', () => {
  let state = withBinding(initialState());
  state = reduce(state, { type: 'ready', coreVersion: '1', session: null });
  state = reduce(state, {
    type: 'providers',
    providers: [{ kind: 'fake', label: 'Fake', external: false, model_discovery: false, custom_model: false, summary: '' }],
  });
  state = reduce(state, {
    type: 'health',
    health: [{ kind: 'fake', state: 'authenticated', detail: '', remedy: '', usable: true }],
  });
  state = reduce(state, {
    type: 'settings',
    patch: { mode: 'auto', responseSource: 'manual', sharing: 'none' },
  });

  let readiness = evaluateReadiness(state);
  assert.ok(readiness.blockers.some((check) => check.key === 'auto_response'));
  assert.ok(readiness.blockers.some((check) => check.key === 'auto_analysis'));

  state = reduce(state, {
    type: 'settings',
    patch: { responseSource: 'page', sharing: 'redacted' },
  });
  readiness = evaluateReadiness(state);
  assert.equal(readiness.ready, true, JSON.stringify(readiness.blockers));
});

test('unlimited turns cannot silently continue past potential findings', () => {
  let state = withBinding(initialState());
  state = reduce(state, { type: 'ready', coreVersion: '1', session: null });
  state = reduce(state, {
    type: 'providers',
    providers: [{ kind: 'fake', label: 'Fake', external: false, model_discovery: false, custom_model: false, summary: '' }],
  });
  state = reduce(state, {
    type: 'health',
    health: [{ kind: 'fake', state: 'authenticated', detail: '', remedy: '', usable: true }],
  });
  state = reduce(state, {
    type: 'settings',
    patch: {
      mode: 'auto',
      responseSource: 'page',
      sharing: 'redacted',
      maxTurns: 0,
      maxDurationSeconds: 0,
      potentialFindingAction: 'continue',
    },
  });

  const readiness = evaluateReadiness(state);
  assert.equal(readiness.ready, false);
  assert.equal(readiness.blockers.some((check) => check.key === 'auto_unbounded'), true);

  state = reduce(state, {
    type: 'settings',
    patch: { potentialFindingAction: 'review' },
  });
  assert.equal(evaluateReadiness(state).ready, true);
});

test('version-one settings migrate without losing the reviewed binding', () => {
  const stored = persistable(withBinding(initialState())) as Record<string, unknown>;
  stored.version = 1;
  const oldSettings = { ...(stored.settings as Record<string, unknown>) };
  delete oldSettings.responseSource;
  delete oldSettings.maxTurns;
  delete oldSettings.maxDurationSeconds;
  stored.settings = oldSettings;

  const restored = restore(stored);
  assert.equal(restored.bindingSaved, true);
  assert.equal(restored.settings.responseSource, 'page');
  assert.equal(restored.settings.maxTurns, 20);
  assert.equal(restored.settings.maxDurationSeconds, 0);
  assert.equal(restored.settings.potentialFindingAction, 'review');
});

test('legacy profiles migrate the old 300-second default to unlimited time', () => {
  const stored = persistable(initialState()) as Record<string, unknown>;
  stored.version = 2;
  stored.settings = {
    ...(stored.settings as Record<string, unknown>),
    maxTurns: 15,
    maxDurationSeconds: 300,
  };

  const restored = restore(stored);
  assert.equal(restored.settings.maxTurns, 15);
  assert.equal(restored.settings.maxDurationSeconds, 0);
});

test('a bounded autonomous finding policy and 100-turn budget survive reload', () => {
  const state = reduce(initialState(), {
    type: 'settings',
    patch: { mode: 'auto', potentialFindingAction: 'continue', maxTurns: 100 },
  });
  const restored = restore(persistable(state));
  assert.equal(restored.settings.potentialFindingAction, 'continue');
  assert.equal(restored.settings.maxTurns, 100);
});

test('a complete assist configuration is ready', () => {
  let state = initialState();
  state = reduce(state, { type: 'ready', coreVersion: '1', session: null });
  state = reduce(state, { type: 'providers', providers: [{ kind: 'fake', label: 'Fake', external: false, model_discovery: false, custom_model: false, summary: '' }] });
  state = reduce(state, { type: 'health', health: [{ kind: 'fake', state: 'authenticated', detail: '', remedy: '', usable: true }] });
  state = reduce(state, { type: 'target', origin: 'https://example.test', tabId: 1, documentId: 'd' });
  state = withBinding(state);

  const readiness = evaluateReadiness(state);
  assert.equal(readiness.ready, true, JSON.stringify(readiness.blockers));
  assert.equal(readiness.summary, 'Ready to start.');
});

test('an unusable provider blocks with its remedy', () => {
  let state = initialState();
  state = reduce(state, { type: 'ready', coreVersion: '1', session: null });
  state = reduce(state, { type: 'settings', patch: { provider: 'openai' } });
  state = reduce(state, { type: 'providers', providers: [{ kind: 'openai', label: 'OpenAI', external: true, model_discovery: true, custom_model: true, summary: '' }] });
  state = reduce(state, { type: 'health', health: [{ kind: 'openai', state: 'not_configured', detail: 'no API key', remedy: 'export STEALTH_PROMPT_OPENAI_API_KEY', usable: false }] });

  const readiness = evaluateReadiness(state);
  const blocker = readiness.blockers.find((b) => b.key === 'provider_auth');
  assert.ok(blocker);
  assert.match(blocker!.action, /STEALTH_PROMPT_OPENAI_API_KEY/);
});

test('core disconnection is a recoverable state, not a reset', () => {
  let state = withBinding(reduce(initialState(), { type: 'ready', coreVersion: '1', session: { session_id: 's9' } }));
  state = reduce(state, { type: 'connection', state: 'disconnected', detail: 'gone' });
  assert.equal(state.stage, 'core_disconnected');
  assert.equal(state.sessionId, 's9');
  state = reduce(state, { type: 'ready', coreVersion: '1', session: { session_id: 's9' } });
  assert.equal(state.stage, 'idle');
  assert.equal(state.bindingSaved, true);
});

test('a successful browser action can clear a visible error', () => {
  let state = reduce(initialState(), { type: 'error', message: 'permission denied' });
  assert.equal(state.errorMessage, 'permission denied');
  state = reduce(state, { type: 'clear_error' });
  assert.equal(state.errorMessage, '');
});


test('the core port defaults to what serve uses', () => {
  assert.equal(initialState().settings.corePort, DEFAULT_CORE_PORT);
  assert.equal(DEFAULT_CORE_PORT, 17371);
});

test('a fresh install requires an explicit connection method', () => {
  assert.equal(initialState().settings.connectionMethod, 'unset');
  const legacy = persistable(initialState()) as Record<string, unknown>;
  const settings = { ...(legacy.settings as Record<string, unknown>) };
  delete settings.connectionMethod;
  legacy.settings = settings;
  assert.equal(restore(legacy).settings.connectionMethod, 'core');
});

test('consecutive duplicate activity is recorded once', () => {
  const entry = { at: 1, kind: 'interaction.bound', detail: 'https://example.test' };
  let state = reduce(initialState(), { type: 'timeline', entry });
  state = reduce(state, { type: 'timeline', entry: { ...entry, at: 2 } });
  assert.equal(state.timeline.length, 1);
});

test('a chosen core port survives a reload', () => {
  const state = reduce(initialState(), { type: 'settings', patch: { corePort: 9000 } });
  assert.equal(state.settings.corePort, 9000);
  assert.equal(restore(persistable(state)).settings.corePort, 9000);
});

test('an unusable stored port falls back to the default', () => {
  // Storage is untrusted input: a corrupt value must not produce a bad URL.
  for (const bad of ['not-a-port', 0, -1, 70000, 1.5, null, undefined, {}]) {
    assert.equal(coercePort(bad), DEFAULT_CORE_PORT, `coercePort(${JSON.stringify(bad)})`);
  }
  assert.equal(coercePort('9000'), 9000, 'a numeric string is usable');
  assert.equal(coercePort(65535), 65535);

  const stored = { ...persistable(initialState()), settings: { corePort: 'nonsense' } };
  assert.equal(restore(stored).settings.corePort, DEFAULT_CORE_PORT);
});

/* ------------------------------------------------------- binding health */

const savedState = (): PanelState => {
  let state = initialState();
  state = reduce(state, {
    type: 'target',
    origin: 'https://app.test',
    tabId: 7,
    documentId: 'doc-1',
  });
  state = reduce(state, { type: 'binding_saved', valid: true });
  return state;
};

test('a saved and verified binding reads as healthy', () => {
  assert.equal(savedState().bindingHealth, 'healthy');
});

test('a reload marks the binding for revalidation without discarding it', () => {
  let state = savedState();
  state.binding.input = { strategy: 'css', value: '#message' };
  state = reduce(state, {
    type: 'target',
    origin: 'https://app.test',
    tabId: 7,
    documentId: 'doc-2',
  });
  assert.equal(state.bindingHealth, 'revalidating');
  // The reviewed work survives; only the trust level moved.
  assert.equal(state.bindingSaved, true);
  assert.equal(state.binding.input?.value, '#message');
});

test('an SPA document replacement pauses an automatic run', () => {
  let state = savedState();
  state = reduce(state, { type: 'auto_started' });
  assert.equal(state.autoRunning, true);
  state = reduce(state, {
    type: 'target',
    origin: 'https://app.test',
    tabId: 7,
    documentId: 'doc-2',
  });
  assert.equal(state.autoRunning, false);
});

test('a failed revalidation pauses Auto and names the failing role', () => {
  let state = savedState();
  state = reduce(state, { type: 'auto_started' });
  state = reduce(state, {
    type: 'binding_health',
    health: 'needs_review',
    issues: { submit: 'The submit locator is ambiguous (3 elements match).' },
    at: 1000,
  });
  assert.equal(state.bindingHealth, 'needs_review');
  assert.equal(state.bindingValid, false);
  assert.equal(state.autoRunning, false);
  assert.match(state.autoStopReason, /needs review/);
  assert.match(state.bindingRoleIssues['submit'] ?? '', /ambiguous/);
});

test('a stale binding blocks sending until it is healthy again', () => {
  let state = savedState();
  state = reduce(state, { type: 'binding_invalid', detail: 'the input no longer matches' });
  assert.equal(state.bindingValid, false);
  assert.equal(state.bindingHealth, 'needs_review');
  assert.equal(state.autoRunning, false);

  state = reduce(state, { type: 'binding_health', health: 'healthy', issues: {}, at: 2000 });
  assert.equal(state.bindingValid, true);
  assert.equal(state.bindingHealth, 'healthy');
  assert.deepEqual(state.bindingRoleIssues, {});
});

test('recovering health does not silently resume an automatic run', () => {
  let state = savedState();
  state = reduce(state, { type: 'auto_started' });
  state = reduce(state, { type: 'binding_health', health: 'needs_review', at: 1 });
  state = reduce(state, { type: 'binding_health', health: 'healthy', at: 2 });
  // Auto must be re-authorized explicitly; health alone never restores it.
  assert.equal(state.autoRunning, false);
});

test('an unreachable interaction is unsupported, not merely unhealthy', () => {
  let state = savedState();
  state = reduce(state, {
    type: 'binding_health',
    health: 'unsupported',
    issues: { binding: 'cannot access contents of the page' },
    at: 5,
  });
  assert.equal(state.bindingHealth, 'unsupported');
  assert.equal(state.bindingValid, false);
});

test('changing origin resets health to unknown', () => {
  let state = savedState();
  state = reduce(state, {
    type: 'target',
    origin: 'https://other.test',
    tabId: 7,
    documentId: 'doc-9',
  });
  assert.equal(state.bindingHealth, 'unknown');
  assert.equal(state.bindingValid, false);
});

test('editing a role clears health so it cannot be sent unverified', () => {
  let state = savedState();
  state = reduce(state, {
    type: 'binding',
    binding: { ...state.binding, input: { strategy: 'css', value: '#new' } },
  });
  assert.equal(state.bindingSaved, false);
  assert.equal(state.bindingValid, false);
  assert.equal(state.bindingHealth, 'unknown');
});

test('binding health is never restored from durable storage', () => {
  const state = savedState();
  const restored = restore(persistable(state));
  // A stored "healthy" would be a claim about a document that may no longer
  // exist, so the panel starts unknown and revalidates.
  assert.equal(restored.bindingHealth, 'unknown');
  assert.equal(restored.bindingSaved, true);
});

test('a discovery suggestion is held for review and never auto-applied', () => {
  let state = initialState();
  state = reduce(state, {
    type: 'suggestion',
    suggestion: {
      input: { locator: { strategy: 'css', value: '#m' }, confidence: 90, reason: 'r' },
      submit: { locator: null, confidence: 0, reason: '' },
      response: { locator: null, confidence: 0, reason: '' },
      missing: ['send control'],
    },
  });
  assert.equal(state.suggestion?.input.confidence, 90);
  // The binding itself is untouched until the operator accepts a role.
  assert.equal(state.binding.input, null);
  assert.equal(state.bindingSaved, false);
});

/* ------------------------------------------- durable vs transient state */

test('only safe fields are persisted', () => {
  const state = initialState();
  state.settings.advancedInstruction = 'steer the payload';
  state.sessionId = 'session-1';
  state.proposal = {
    proposal_id: 'p1',
    objective: 'instruction_disclosure',
    goal: 'establish disclosure',
    tactic: 'direct probe',
    hypothesis: 'h',
    payload: 'PAYLOAD TEXT',
    rationale: 'r',
    expected_signals: [],
    risk: 'low',
    provider: 'openai',
    requested_model: 'gpt-x',
    effective_model: 'gpt-x',
  };
  state.evaluation = {
    evaluation_id: 'e1',
    verdict: 'potential',
    summary: 'TARGET RESPONSE SUMMARY',
    observed_signals: ['signal'],
    evidence_ids: [],
    suggested_next_steps: [],
    deterministic: false,
  };
  state.editedPayload = 'EDITED PAYLOAD';
  state.autoRunning = true;

  const stored = persistable(state);
  const serialized = JSON.stringify(stored);

  // Configuration and assessment identity survive a reload.
  assert.equal(stored['sessionId'], 'session-1');
  assert.ok(stored['settings']);
  assert.ok(stored['binding'] !== undefined);

  // Model output, target-derived text and run authorization do not.
  for (const key of ['proposal', 'evaluation', 'editedPayload', 'autoRunning', 'models']) {
    assert.equal(stored[key], undefined, key);
  }
  assert.equal(serialized.includes('PAYLOAD TEXT'), false);
  assert.equal(serialized.includes('EDITED PAYLOAD'), false);
  assert.equal(serialized.includes('TARGET RESPONSE SUMMARY'), false);
});

test('no credential-shaped field is ever persisted', () => {
  const serialized = JSON.stringify(persistable(initialState())).toLowerCase();
  for (const forbidden of ['apikey', 'api_key', 'token', 'secret', 'password', 'authorization']) {
    assert.equal(serialized.includes(forbidden), false, forbidden);
  }
});

test('a run authorization never survives a reload', () => {
  let state = initialState();
  state = reduce(state, { type: 'session_started' });
  state = reduce(state, { type: 'auto_started' });
  assert.equal(state.autoRunning, true);

  const restored = restore(persistable(state));
  // Auto must be re-authorized by hand after reopening the panel.
  assert.equal(restored.autoRunning, false);
});

test('the run lifecycle is durable so a reload knows which screen to open', () => {
  let state = reduce(initialState(), { type: 'session_started' });
  assert.equal(state.sessionEnded, false);
  state = reduce(state, { type: 'session_ended', reason: 'stopped by operator' });
  assert.equal(state.sessionEnded, true);
  assert.equal(state.autoRunning, false);
  const restored = restore(persistable(state));
  assert.equal(restored.sessionEnded, true);
  assert.equal(restored.autoStopReason, 'stopped by operator');

  // Starting again reopens the assessment rather than stranding it as finished.
  state = reduce(state, { type: 'session_started' });
  assert.equal(state.sessionEnded, false);
  assert.equal(state.evaluation, null);
});
