export type DirectProvider = 'openai' | 'anthropic';

export function parseDirectProvider(value: unknown): DirectProvider | null {
  return value === 'openai' || value === 'anthropic' ? value : null;
}

export function parseDirectModels(provider: DirectProvider, document: unknown): string[] {
  if (typeof document !== 'object' || document === null) throw new Error('Invalid model list.');
  const data = (document as Record<string, unknown>)['data'];
  if (!Array.isArray(data)) throw new Error('Invalid model list.');
  const excluded = /embedding|moderation|realtime|audio|transcri|tts|image|search|whisper/i;
  return data
    .map((entry) =>
      typeof entry === 'object' && entry !== null
        ? (entry as Record<string, unknown>)['id']
        : null,
    )
    .filter((id): id is string => {
      if (typeof id !== 'string' || id.length < 1 || id.length > 160 || excluded.test(id)) return false;
      return provider === 'anthropic' ? id.startsWith('claude-') : /^(gpt-|o\d|chatgpt-)/.test(id);
    })
    .sort()
    .slice(0, 200);
}

export function parseDirectCompletion(
  provider: DirectProvider,
  document: unknown,
  requestedModel: string,
): { text: string; model: string; usage: unknown } {
  if (typeof document !== 'object' || document === null) throw new Error('Invalid provider reply.');
  const record = document as Record<string, unknown>;
  const output = provider === 'openai' ? record['output'] : record['content'];
  if (!Array.isArray(output)) throw new Error('Provider reply contained no text.');
  const chunks: string[] = [];
  for (const item of output) {
    if (typeof item !== 'object' || item === null) continue;
    const part = item as Record<string, unknown>;
    if (provider === 'openai' && Array.isArray(part['content'])) {
      for (const content of part['content']) {
        if (typeof content !== 'object' || content === null) continue;
        const text = (content as Record<string, unknown>)['text'];
        if (typeof text === 'string') chunks.push(text);
      }
    } else if (provider === 'anthropic' && typeof part['text'] === 'string') {
      chunks.push(part['text']);
    }
  }
  const text = chunks.join('').slice(0, 32 * 1024);
  if (!text) throw new Error('Provider reply contained no text.');
  return {
    text,
    model: typeof record['model'] === 'string' ? record['model'] : requestedModel,
    usage: record['usage'] ?? null,
  };
}
