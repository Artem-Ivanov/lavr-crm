const PREFIX = 'lavr.feedback-attempt.v1:';

function storageKey(scope) {
  if (typeof scope !== 'string' || !/^(?:submission|order:[A-Za-z0-9_-]{1,128})$/.test(scope)) throw new Error('Некорректная область ключа повторной отправки.');
  return `${PREFIX}${scope}`;
}

export async function feedbackKey(body, scope = 'submission', storage = globalThis.sessionStorage) {
  const keyName = storageKey(scope);
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(body)));
  const digest = [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, '0')).join('');
  try {
    const saved = JSON.parse(storage.getItem(keyName) || 'null');
    const key = saved?.digest === digest && typeof saved.key === 'string' && /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(saved.key)
      ? saved.key : crypto.randomUUID();
    storage.setItem(keyName, JSON.stringify({ digest, key }));
    return key;
  } catch {
    throw new Error('Не удалось сохранить ключ повторной отправки. Разрешите хранилище браузера и повторите попытку.');
  }
}

export function clearFeedbackKey(scope = 'submission', storage = globalThis.sessionStorage) {
  const keyName = storageKey(scope);
  storage.setItem(keyName, 'null');
  try { storage.removeItem(keyName); } catch { /* The tombstone prevents reusing a confirmed attempt. */ }
}
