import { appConfig } from './config';
import { normalizeNetworkError, responseToApiError } from './errors';

export interface ChatEvent {
  event: 'session.created' | 'clarification.requested' | 'spec.ready' | 'workflow.error' | 'next_action' | string;
  data: Record<string, unknown>;
}

export async function streamChat(
  message: string,
  onEvent: (event: ChatEvent) => void,
  options: { sessionId?: string; signal?: AbortSignal } = {},
): Promise<string | null> {
  try {
    const response = await fetch(`${appConfig.apiBaseUrl}/chat`, {
      method: 'POST',
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
        ...(options.sessionId ? { 'X-Session-ID': options.sessionId } : {}),
      },
      body: JSON.stringify({ message, session_id: options.sessionId }),
      signal: options.signal,
    });
    if (!response.ok) throw await responseToApiError(response);
    if (!response.body) throw new Error('SSE response body is unavailable');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? '';
      for (const frame of frames) {
        let event = 'message';
        const dataLines: string[] = [];
        for (const line of frame.split(/\r?\n/)) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
        }
        if (dataLines.length) {
          onEvent({ event, data: JSON.parse(dataLines.join('\n')) as Record<string, unknown> });
        }
      }
      if (done) break;
    }
    return response.headers.get('X-Session-ID');
  } catch (error) {
    throw normalizeNetworkError(error);
  }
}
