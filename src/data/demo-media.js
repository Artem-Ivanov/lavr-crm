import { validateMediaUpload } from '../content.js';

export const DEMO_MEDIA = [
  ['demo-hero', 'hero-car', 1800, 1200, 151857],
  ['demo-motor-oil', 'motor-oil', 928, 1321, 676647],
  ['demo-oil-filter', 'oil-filter', 1108, 1108, 124524],
  ['demo-air-filter', 'air-filter', 800, 640, 82050],
  ['demo-brake-disc', 'brake-disc', 870, 870, 96219],
  ['demo-spark-plug', 'spark-plug', 1660, 480, 263589],
].map(([id, name, width, height, sizeBytes]) => ({
  id, path: `/images/${name}.jpg`, alt: '', width, height, sizeBytes,
  mimeType: 'image/jpeg', state: 'ready', createdAt: '2026-10-11T00:00:00Z',
}));

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('lavr-media-v1', 1);
    let blocked = false;
    request.onupgradeneeded = () => request.result.createObjectStore('files', { keyPath: 'id' });
    request.onsuccess = () => { if (blocked) request.result.close(); else resolve(request.result); };
    request.onerror = () => reject(new Error('Не удалось открыть библиотеку изображений в браузере.'));
    request.onblocked = () => { blocked = true; reject(new Error('Закройте другие вкладки, чтобы открыть библиотеку изображений.')); };
  });
}

async function storeRequest(mode, operation) {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    let result;
    let transaction;
    try {
      transaction = database.transaction('files', mode);
      const request = operation(transaction.objectStore('files'));
      request.onsuccess = () => { result = request.result; };
    } catch {
      database.close(); reject(new Error('Не удалось сохранить или прочитать изображение.')); return;
    }
    transaction.oncomplete = () => { database.close(); resolve(result); };
    transaction.onerror = transaction.onabort = () => {
      database.close(); reject(new Error('Не удалось сохранить или прочитать изображение. Проверьте хранилище браузера.'));
    };
  });
}

function readyFile(row) {
  return row && /^[\da-f-]{36}$/i.test(row.id) && row.blob instanceof Blob
    && ['image/jpeg', 'image/png', 'image/webp'].includes(row.mimeType) && row.blob.type === row.mimeType
    && row.blob.size === row.sizeBytes && row.sizeBytes > 0 && row.sizeBytes <= 5242880
    && typeof row.alt === 'string' && row.alt.length <= 240 && row.state === 'ready'
    && [row.width, row.height].every(size => Number.isSafeInteger(size) && size > 0 && size <= 4096)
    && Number.isFinite(Date.parse(row.createdAt));
}

// shortcut: файлы принадлежат демо этого браузера, рабочая библиотека использует серверный media volume.
export function createDemoMediaStore() {
  const urls = new Map();
  function metadata(row) {
    if (!readyFile(row)) throw new Error('Библиотека изображений повреждена. Сохранённые файлы не изменены.');
    if (!urls.has(row.id)) urls.set(row.id, URL.createObjectURL(row.blob));
    const { blob, ...info } = row;
    return { ...info, path: urls.get(row.id) };
  }
  return {
    async list() {
      if (!globalThis.indexedDB) return [];
      return (await storeRequest('readonly', store => store.getAll())).map(metadata);
    },
    async save(file, alt) {
      validateMediaUpload(file, alt);
      if (!globalThis.indexedDB) throw new Error('Хранилище изображений недоступно в этом браузере.');
      const header = new Uint8Array(await file.slice(0, 12).arrayBuffer());
      const signatures = {
        'image/jpeg': () => header[0] === 255 && header[1] === 216 && header[2] === 255,
        'image/png': () => [137, 80, 78, 71, 13, 10, 26, 10].every((byte, index) => header[index] === byte),
        'image/webp': () => String.fromCharCode(...header.slice(0, 4)) === 'RIFF' && String.fromCharCode(...header.slice(8, 12)) === 'WEBP',
      };
      if (!signatures[file.type]()) throw new Error('Содержимое файла не соответствует JPG, PNG или WebP.');
      let bitmap;
      try { bitmap = await createImageBitmap(file); }
      catch { throw new Error('Не удалось прочитать изображение. Выберите исправный JPG, PNG или WebP.'); }
      const { width, height } = bitmap;
      bitmap.close();
      if (width < 1 || height < 1 || width > 4096 || height > 4096) throw new Error('Изображение должно быть не больше 4096×4096 пикселей.');
      const row = {
        id: crypto.randomUUID(), blob: file, alt, width, height,
        mimeType: file.type, sizeBytes: file.size, state: 'ready', createdAt: new Date().toISOString(),
      };
      await storeRequest('readwrite', store => store.add(row));
      return metadata(row);
    },
  };
}
