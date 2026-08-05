/**
 * Why a session can or cannot start.
 *
 * A disabled control with no explanation is the worst outcome for a security
 * tool: the operator cannot tell a misconfiguration from a bug. So readiness is
 * a list of named checks, each with an actionable reason, and Start stays
 * clickable — pressing it surfaces the list rather than doing nothing.
 */

import { bindingComplete, bindingSendComplete } from '../protocol/messages.js';
import type { PanelState } from './state.js';

export interface ReadinessCheck {
  key: string;
  label: string;
  ok: boolean;
  required: boolean;
  action: string;
}

export interface Readiness {
  ready: boolean;
  checks: ReadinessCheck[];
  blockers: ReadinessCheck[];
  summary: string;
}

export function evaluateReadiness(state: PanelState): Readiness {
  const checks: ReadinessCheck[] = [];
  const methodChosen = state.settings.connectionMethod !== 'unset';
  const add = (key: string, label: string, ok: boolean, action: string, required = true): void => {
    checks.push({ key, label, ok, required, action });
  };

  add(
    'core',
    state.settings.connectionMethod === 'direct'
      ? 'Direct API connected'
      : state.settings.connectionMethod === 'core'
        ? 'Local Core connected'
        : 'AI connection selected',
    methodChosen && state.connection === 'connected',
    state.settings.connectionMethod === 'direct'
      ? 'Enter a provider key and connect in the AI step.'
      : state.settings.connectionMethod === 'core'
        ? state.connection === 'pairing_required'
          ? 'Enter the pairing code shown in your terminal.'
          : 'Start the local Core, then connect.'
        : 'Choose Local Core or Direct API.',
  );

  const provider = state.providers.find((entry) => entry.kind === state.settings.provider);
  add('provider', 'Provider selected', Boolean(provider), 'Choose an AI backend.', methodChosen);
  add(
    'model',
    'Model selected',
    state.settings.connectionMethod !== 'direct' || Boolean(state.settings.requestedModel),
    'Load the provider models and choose one.',
    state.settings.connectionMethod === 'direct',
  );

  const health = state.health[state.settings.provider];
  if (health) {
    const installed = health.state !== 'not_installed';
    add('provider_installed', 'Provider installed', installed, health.remedy || 'Install the backend.');
    // "installed but unauthenticated" is a real, distinct state and must read
    // as such rather than as a generic failure.
    add(
      'provider_auth',
      'Provider usable',
      health.usable,
      health.remedy || 'Configure credentials for this backend.',
    );
  }

  // payload_only never touches the page, so it needs no input or send control.
  const payloadOnly = state.settings.mode === 'payload_only';
  const manualResponse = state.settings.responseSource === 'manual';
  const auto = state.settings.mode === 'auto';
  add(
    'input',
    'Input element selected',
    payloadOnly || Boolean(state.binding.input),
    'Select the chat input element.',
    !payloadOnly,
  );
  add(
    'submit',
    'Send control selected',
    payloadOnly || Boolean(state.binding.submit),
    'Select the send control.',
    !payloadOnly,
  );
  add(
    'response',
    'Response container selected',
    payloadOnly || manualResponse || Boolean(state.binding.response),
    'Select the response container.',
    !payloadOnly && !manualResponse,
  );
  add(
    'binding',
    'Interaction saved',
    payloadOnly ||
      (state.bindingSaved &&
        (manualResponse
          ? bindingSendComplete(state.binding)
          : bindingComplete(state.binding))),
    'Save the interaction binding.',
    !payloadOnly,
  );

  add('objective', 'Objective chosen', Boolean(state.settings.objective), 'Choose an objective.');
  add(
    'sharing',
    'Data sharing chosen',
    Boolean(state.settings.sharing),
    'Choose how target data may be shared.',
  );
  add(
    'auto_response',
    'Automatic response capture',
    !auto || state.settings.responseSource === 'page',
    'Choose “Capture from page”; Auto cannot wait for pasted responses.',
    auto,
  );
  add(
    'auto_analysis',
    'Replies available to the AI',
    !auto || state.settings.sharing !== 'none',
    'Choose redacted or full data sharing for an adaptive Auto run.',
    auto,
  );
  add(
    'auto_unbounded',
    'Bounded autonomous policy',
    !auto || state.settings.maxTurns !== 0 || state.settings.potentialFindingAction !== 'continue',
    'Unlimited turns require “Pause for review” or “Stop and save report” when a potential finding appears.',
    auto,
  );

  const blockers = checks.filter((check) => check.required && !check.ok);
  const summary =
    blockers.length === 0
      ? 'Ready to start.'
      : `Cannot start:\n${blockers.map((check) => `- ${check.action}`).join('\n')}`;
  return { ready: blockers.length === 0, checks, blockers, summary };
}
