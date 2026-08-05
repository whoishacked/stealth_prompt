import assert from 'node:assert/strict';
import test from 'node:test';

import { parseDirectReport } from '../src/storage/direct-reports.js';

test('browser-local Direct API reports are validated and keep credentials out', () => {
  const reportId = 'direct-123e4567-e89b-42d3-a456-426614174000';
  const value = {
    reportId,
    createdAt: '2026-08-04T12:00:00.000Z',
    document: {
      kind: 'assistant_session',
      schema_version: 1,
      session_id: reportId,
      exported_at: '2026-08-04T12:01:00.000Z',
      verdict: 'potential',
      configuration: {
        runtime: 'direct_api',
        origin: 'https://target.example',
        objective_text: 'Instruction disclosure',
        provider: 'openai',
        effective_model: 'gpt-test',
        mode: 'assist',
      },
      turns: [{
        turn_id: 'direct-turn-1',
        started_at: '2026-08-04T12:00:10.000Z',
        approved: true,
        approved_payload: 'TEST_PAYLOAD',
        response: 'TARGET_RESPONSE',
        evaluation: { verdict: 'potential', summary: 'Review this response.' },
      }],
    },
  };

  const parsed = parseDirectReport(value);
  assert.equal(parsed?.parsed.turns[0]?.response, 'TARGET_RESPONSE');
  assert.equal(parsed?.parsed.turns[0]?.payload, 'TEST_PAYLOAD');
  assert.equal(JSON.stringify(value).includes('api_key'), false);
  assert.equal(parseDirectReport({ ...value, reportId: '../report' }), null);
  assert.equal(
    parseDirectReport({ ...value, document: { ...value.document, session_id: 'direct-other' } }),
    null,
  );
});
