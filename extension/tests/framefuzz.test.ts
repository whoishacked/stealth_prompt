import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';

import {
  DEFAULT_PROTECTED_VALUE_LABEL,
  DEFAULT_SAFE_DESTINATION,
  FRAMEFUZZ_STRATEGIES,
  completeDirectFrameFuzzCase,
  createDirectFrameFuzzCampaign,
  destinationError,
  directFrameFuzzProposal,
  frameFuzzConfiguration,
  openDirectFrameFuzzCase,
  protectedLabelError,
  renderFrameFuzzPayload,
  verifyDirectFrameFuzzCase,
} from '../src/framefuzz.js';
import type { FrameFuzzSettings } from '../src/framefuzz.js';
import type { Evaluation } from '../src/storage/state.js';

const settings = (over: Partial<FrameFuzzSettings> = {}): FrameFuzzSettings => ({
  enabled: true,
  framedStrategies: ['integrity_signature', 'required_config', 'trusted_destination'],
  protectedValueLabel: DEFAULT_PROTECTED_VALUE_LABEL,
  safeDestination: DEFAULT_SAFE_DESTINATION,
  randomizeOrder: false,
  randomSeed: 'unit-test-seed',
  ...over,
});

const evaluation = (verdict: string): Evaluation => ({
  evaluation_id: crypto.randomUUID(),
  verdict,
  summary: 'bounded result',
  observed_signals: [],
  evidence_ids: [],
  suggested_next_steps: [],
  deterministic: false,
  failure_signature: null,
});

test('configuration is closed, complete, and contains no actual value', () => {
  const document = frameFuzzConfiguration(settings(), 'instruction_disclosure');
  assert.deepEqual(document['strategies'], FRAMEFUZZ_STRATEGIES);
  assert.equal(document['protected_value_label'], 'synthetic canary');
  assert.equal(JSON.stringify(document).includes('SP_CANARY_'), false);
  assert.throws(
    () => frameFuzzConfiguration(settings({ framedStrategies: [] }), 'instruction_disclosure'),
    /at least one framed strategy/,
  );
  assert.throws(
    () => frameFuzzConfiguration(settings(), 'tool_misuse'),
    /supports indirect prompt injection/,
  );
});

test('labels and destinations reject secret and executable URL shapes', () => {
  assert.match(protectedLabelError('SP_CANARY_ABCDEF123456'), /actual protected value/);
  for (const value of [
    'javascript:alert(1)',
    'data:text/plain,x',
    'file:///tmp/x',
    'chrome-extension://abc/page',
    'https://user:secret@example.test/x',
    'https://example.test/collect?api_key=secret',
    'https://example.test/collect?value=sk-abcdefghijklmnop',
    'https://example.test/x\nHeader: x',
  ]) assert.notEqual(destinationError(value), '', value);
  assert.equal(destinationError(DEFAULT_SAFE_DESTINATION), '');
});

test('direct templates match Core v1 byte-for-byte', async () => {
  const campaign = await createDirectFrameFuzzCampaign(
    settings(),
    'instruction_disclosure',
  );
  assert.equal(
    campaign.semantic_seed_hash,
    '5ebf2ff43a91ebcdc0a9e91bec279af5657571fc852c184411aceabe6e71df91',
  );
  const expected: Record<string, string> = {
    clean_control: '7797fc723b9e9e50002a73d18923e6d1155e3fb834f3f7aaf65a7edad287c2e5',
    explicit: '2d37f2a57b1e0df00b5e5b1f38d064939e4b0079b6da6b0f46098cae1c62951e',
    integrity_signature: '05ba5cb033f3c3928224d0907573f45590d1d9303de55005ecfbc958e2b4bbde',
    required_config: 'fc461d881e264ef39f301bcf952f589859e96cd5afb7d4ac431ab8786827d248',
    trusted_destination: '8fd52046fbb8c08f77094b22755dca68ab329a069ae1e7f525a7ea04b3e8f177',
  };
  for (const strategy of FRAMEFUZZ_STRATEGIES) {
    const payload = renderFrameFuzzPayload(strategy, campaign.semantic_seed);
    assert.equal(createHash('sha256').update(payload).digest('hex'), expected[strategy]);
  }
});

test('random order is reproducible from the exported local seed', async () => {
  const first = await createDirectFrameFuzzCampaign(
    settings({ randomizeOrder: true, randomSeed: 'reproducible-seed' }),
    'instruction_disclosure',
  );
  const second = await createDirectFrameFuzzCampaign(
    settings({ randomizeOrder: true, randomSeed: 'reproducible-seed' }),
    'instruction_disclosure',
  );
  assert.deepEqual(first.case_order, second.case_order);
  assert.deepEqual(new Set(first.case_order), new Set(FRAMEFUZZ_STRATEGIES));
});

test('direct campaign gates every case and caps differential at potential', async () => {
  let campaign = await createDirectFrameFuzzCampaign(settings(), 'instruction_disclosure');
  for (const strategy of campaign.case_order) {
    assert.equal(campaign.status, 'awaiting_context');
    campaign = openDirectFrameFuzzCase(campaign, true);
    const proposal = directFrameFuzzProposal(campaign);
    const verdict = strategy === 'integrity_signature' ? 'potential' : 'not_observed';
    const turnId = `turn-${strategy}`;
    campaign = completeDirectFrameFuzzCase(
      campaign,
      evaluation(verdict),
      turnId,
      proposal.payload,
    );
    if (strategy === 'integrity_signature') {
      campaign = verifyDirectFrameFuzzCase(campaign, turnId);
    }
  }
  assert.equal(campaign.status, 'complete');
  assert.equal(campaign.conclusion, 'potential_framing_gap');
  assert.match(campaign.warnings[0] ?? '', /Direct API/);
  assert.equal(JSON.stringify(campaign).toLowerCase().includes('api_key'), false);
});

test('editing one payload makes a direct matched comparison inconclusive', async () => {
  let campaign = await createDirectFrameFuzzCampaign(settings(), 'instruction_disclosure');
  for (const strategy of campaign.case_order) {
    campaign = openDirectFrameFuzzCase(campaign, true);
    const proposal = directFrameFuzzProposal(campaign);
    campaign = completeDirectFrameFuzzCase(
      campaign,
      evaluation(strategy === 'integrity_signature' ? 'potential' : 'not_observed'),
      `turn-${strategy}`,
      strategy === 'explicit' ? `${proposal.payload} edited` : proposal.payload,
    );
  }
  assert.equal(campaign.conclusion, 'inconclusive');
  assert.match(campaign.warnings.join(' '), /edited/);
});
