const KEY = 'lavr.checkout-attempt.v1';

export async function checkoutKey(body, storage = globalThis.sessionStorage) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(body)));
  const digest = [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, '0')).join('');
  try {
    const saved = JSON.parse(storage.getItem(KEY) || 'null');
    if (saved?.digest === digest && /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(saved.key)) {
      storage.setItem(KEY, JSON.stringify(saved));
      return saved.key;
    }
    const key = crypto.randomUUID();
    storage.setItem(KEY, JSON.stringify({ digest, key }));
    return key;
  } catch {
    throw new Error('Не удалось сохранить ключ повторной отправки. Разрешите хранилище браузера и повторите оформление.');
  }
}

export function clearCheckoutKey(storage = globalThis.sessionStorage) {
  storage.setItem(KEY, 'null');
  try { storage.removeItem(KEY); } catch { /* The tombstone already prevents reusing a confirmed key. */ }
}
