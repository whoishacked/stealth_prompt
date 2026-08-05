import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  hostPermissionPattern,
  requestHostAccess,
} from '../src/permissions/host-access.js';

test('host permission is limited to the reviewed origin', () => {
  assert.equal(
    hostPermissionPattern('https://chat.example.test'),
    'https://chat.example.test/*',
  );
  assert.equal(
    hostPermissionPattern('http://127.0.0.1:8765'),
    'http://127.0.0.1:8765/*',
  );
});

test('host permission rejects paths and unsupported schemes', () => {
  assert.throws(
    () => hostPermissionPattern('https://example.test/chat'),
    /origin without a path/,
  );
  assert.throws(() => hostPermissionPattern('chrome://extensions'), /HTTP and HTTPS/);
  assert.throws(() => hostPermissionPattern('not a URL'), /valid URL/);
});

test('permission denial is explicit and never reported as success', async () => {
  let requested: chrome.permissions.Permissions | null = null;
  const result = await requestHostAccess('https://example.test', async (permissions) => {
    requested = permissions;
    return false;
  });

  assert.deepEqual(requested, { origins: ['https://example.test/*'] });
  assert.equal(result.granted, false);
  assert.match(result.message, /did not grant access/);
});

test('permission API errors retain an actionable reason', async () => {
  const result = await requestHostAccess('https://example.test', async () => {
    throw new Error('This function must be called during a user gesture');
  });

  assert.equal(result.granted, false);
  assert.match(result.message, /user gesture/);
});
