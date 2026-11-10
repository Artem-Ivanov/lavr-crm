const KEY = 'lavr.checkout-attempt.v1';

function storageKey(actor) {
  if (typeof actor !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(actor)) throw new Error('Некорректная область ключа заказа.');
  return actor === 'guest' ? KEY : `${KEY}:${actor}`;
}

export async function checkoutKey(body, storage = globalThis.sessionStorage, actor = 'guest') {
  const keyName = storageKey(actor);
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(body)));
  const digest = [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, '0')).join('');
  try {
    const saved = JSON.parse(storage.getItem(keyName) || 'null');
    if (saved?.digest === digest && /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(saved.key)) {
      storage.setItem(keyName, JSON.stringify(saved));
      return saved.key;
    }
    const key = crypto.randomUUID();
    storage.setItem(keyName, JSON.stringify({ digest, key }));
    return key;
  } catch {
    throw new Error('Не удалось сохранить ключ повторной отправки. Разрешите хранилище браузера и повторите оформление.');
  }
}

export function clearCheckoutKey(storage = globalThis.sessionStorage, actor = 'guest') {
  const keyName = storageKey(actor);
  storage.setItem(keyName, 'null');
  try { storage.removeItem(keyName); } catch { /* The tombstone already prevents reusing a confirmed key. */ }
}
