/**
 * The Core frame parser is a trust boundary: a version skew or a malformed
 * frame must fail loudly rather than be half-understood.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  BROWSER_OPERATIONS,
  MAX_FRAME_BYTES,
  PROTOCOL_VERSION,
  ProtocolError,
  bindingComplete,
  bindingToCore,
  emptyBinding,
  encodeCoreFrame,
  isBrowserOperation,
  parseBindingSuggestion,
  parseBindingValidation,
  parseCoreFrame,
  parseLocator,
  parseStoredReport,
} from '../src/protocol/messages.js';

const frame = (over: Record<string, unknown> = {}): string =>
  JSON.stringify({ protocol_version: PROTOCOL_VERSION, type: 'ready', payload: {}, ...over });

test('parses a well-formed frame', () => {
  const parsed = parseCoreFrame(frame());
  assert.equal(parsed.type, 'ready');
});

test('rejects a version mismatch rather than guessing', () => {
  assert.throws(() => parseCoreFrame(frame({ protocol_version: 99 })), ProtocolError);
});

test('rejects an unknown message type', () => {
  assert.throws(() => parseCoreFrame(frame({ type: 'run_shell' })), ProtocolError);
});

test('rejects malformed frames', () => {
  for (const raw of ['not json', '[]', '"a string"', '123', '{']) {
    assert.throws(() => parseCoreFrame(raw), ProtocolError, raw);
  }
});

test('rejects a non-object payload', () => {
  assert.throws(() => parseCoreFrame(frame({ payload: [1] })), ProtocolError);
});

test('rejects an oversized frame before parsing', () => {
  assert.throws(() => parseCoreFrame('x'.repeat(MAX_FRAME_BYTES + 1)), ProtocolError);
});

test('encodes a request with the protocol version', () => {
  const encoded = JSON.parse(encodeCoreFrame('ping', { a: 1 }));
  assert.equal(encoded.protocol_version, PROTOCOL_VERSION);
  assert.equal(encoded.type, 'ping');
});

test('the operation allowlist is closed', () => {
  assert.deepEqual([...BROWSER_OPERATIONS].sort(), [
    'capture', 'conversation', 'discover', 'fill', 'highlight', 'pick', 'snapshot', 'submit', 'validate',
  ]);
  for (const name of ['evaluate', 'eval', 'navigate', 'goto', 'raw', 'debugger']) {
    assert.equal(isBrowserOperation(name), false, name);
  }
});

test('a page suggestion is bounded and every locator is validated', () => {
  const suggestion = parseBindingSuggestion({
    input: { locator: { strategy: 'css', value: '#message' }, confidence: 84.4, reason: 'ok' },
    submit: { locator: { strategy: 'xpath', value: '//button' }, confidence: 90, reason: 'x' },
    response: { locator: { strategy: 'css', value: '.assistant' }, confidence: 41, reason: '' },
    missing: ['send control'],
  });
  // Confidence is per role, so a weak response cannot hide behind a strong input.
  assert.equal(suggestion?.input.confidence, 84);
  assert.equal(suggestion?.response.confidence, 41);
  assert.equal(suggestion?.input.locator?.value, '#message');
  // An unsupported strategy is dropped rather than trusted.
  assert.equal(suggestion?.submit.locator, null);
  assert.deepEqual(suggestion?.missing, ['send control']);
});

test('a page cannot report an out-of-range or non-numeric confidence', () => {
  const suggestion = parseBindingSuggestion({
    input: { locator: { strategy: 'css', value: '#a' }, confidence: 9000 },
    submit: { locator: { strategy: 'css', value: '#b' }, confidence: -5 },
    response: { locator: { strategy: 'css', value: '#c' }, confidence: 'high' },
  });
  assert.equal(suggestion?.input.confidence, 100);
  assert.equal(suggestion?.submit.confidence, 0);
  assert.equal(suggestion?.response.confidence, 0);
});

test('a suggestion reason is bounded', () => {
  const suggestion = parseBindingSuggestion({
    input: { locator: { strategy: 'css', value: '#a' }, confidence: 50, reason: 'x'.repeat(900) },
  });
  assert.equal(suggestion?.input.reason.length, 200);
});

test('binding validation fails closed for a role it cannot read', () => {
  const validation = parseBindingValidation({
    input: { ok: true, matches: 1, reason: '' },
    submit: { ok: false, matches: 3, reason: 'ambiguous' },
    // `response` is absent entirely.
  });
  assert.equal(validation.input?.ok, true);
  assert.equal(validation.submit?.ok, false);
  assert.equal(validation.submit?.matches, 3);
  // Absent means unknown, and unknown must never read as a pass.
  assert.equal(validation.response, null);
});

test('a truthy-but-not-true ok is not treated as valid', () => {
  const validation = parseBindingValidation({ input: { ok: 'yes', matches: 1 } });
  assert.equal(validation.input?.ok, false);
});

test('a malformed validation result yields no passing role', () => {
  for (const bad of [null, 'ok', 42, undefined]) {
    const validation = parseBindingValidation(bad);
    assert.equal(validation.input, null);
    assert.equal(validation.submit, null);
    assert.equal(validation.response, null);
  }
});

test('a locator from the content script is validated', () => {
  assert.equal(parseLocator(null), null);
  assert.equal(parseLocator({ strategy: 'xpath', value: '//x' }), null);
  assert.equal(parseLocator({ strategy: 'css', value: '' }), null);
  assert.equal(parseLocator({ strategy: 'css', value: 'x'.repeat(600) }), null);
  const ok = parseLocator({ strategy: 'css', value: '#msg' });
  assert.equal(ok?.value, '#msg');
});

test('a binding is incomplete until all three elements exist', () => {
  const binding = emptyBinding('https://example.test');
  assert.equal(bindingComplete(binding), false);
  binding.input = { strategy: 'css', value: '#a' };
  binding.submit = { strategy: 'css', value: '#b' };
  assert.equal(bindingComplete(binding), false);
  binding.response = { strategy: 'css', value: '.c' };
  assert.equal(bindingComplete(binding), true);
});

test('binding serialization carries no page content or credentials', () => {
  const binding = emptyBinding('https://example.test');
  binding.input = { strategy: 'css', value: '#a' };
  const serialized = JSON.stringify(bindingToCore(binding)).toLowerCase();
  for (const forbidden of ['cookie', 'token', 'password', 'storage', 'authorization']) {
    assert.equal(serialized.includes(forbidden), false, forbidden);
  }
});

test('stored reports are bounded data, not trusted markup', () => {
  const report = parseStoredReport({
    schema_version: 1,
    kind: 'assistant_session',
    session_id: 's1',
    exported_at: '2026-08-03T12:00:00Z',
    verdict: 'confirmed',
    configuration: {
      origin: 'https://target.test',
      objective: 'instruction_disclosure',
      provider: 'fake',
      effective_model: 'fake-1',
    },
    turns: [{
      turn_id: 't1',
      approved: true,
      proposal: { payload: '<img src=x onerror=alert(1)>', hypothesis: 'probe' },
      response: 'target reply',
      evaluation: { verdict: 'potential', summary: 'signal', observed_signals: ['one'] },
    }],
  });
  assert.equal(report?.turns[0]?.payload, '<img src=x onerror=alert(1)>');
  assert.equal(report?.turns[0]?.verdict, 'potential');
  assert.deepEqual(report?.turns[0]?.observedSignals, ['one']);
  assert.equal(parseStoredReport({ schema_version: 1, kind: 'other', turns: [] }), null);
});
