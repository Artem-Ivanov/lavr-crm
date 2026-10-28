import { FEEDBACK_STATUSES, normalizeFeedbackInput, normalizeFeedbackStatus, normalizeFeedbackNote } from '../feedback.js';

const clone = value => structuredClone(value);
const error = (message, status, code) => Object.assign(new Error(message), { status, code });
const validKey = key => typeof key === 'string' && /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(key);

// shortcut: обращения демо живут только в памяти, для сохранения после перезагрузки нужен HTTP API.
export function createDemoFeedbackApi({ getSession, createOrder }) {
  const cases = new Map();
  const submissions = new Map();
  const conversions = new Map();
  const pending = new Map();

  async function staff() {
    const session = await getSession();
    if (!['manager', 'admin'].includes(session?.user?.role)) throw error('Войдите в CRM.', 401, 'SESSION_REQUIRED');
    return session.user;
  }
  function get(id) {
    const feedback = cases.get(id);
    if (!feedback) throw error('Обращение не найдено.', 404, 'FEEDBACK_NOT_FOUND');
    return feedback;
  }
  function requireOwner(feedback, user) {
    if (feedback.assignedTo && feedback.assignedTo.id !== user.id && user.role !== 'admin') throw error('Изменять обращение может ответственный сотрудник или администратор.', 403, 'FEEDBACK_OWNER_REQUIRED');
  }
  function editable(input, user) {
    const feedback = get(input?.id);
    requireOwner(feedback, user);
    if (pending.has(feedback.id)) throw error('Заказ уже создаётся. Обновите обращение после завершения.', 409, 'FEEDBACK_BUSY');
    if (!Number.isSafeInteger(input?.version) || input.version !== feedback.version) throw error('Обращение изменилось. Обновите карточку.', 412, 'VERSION_CONFLICT');
    if (['closed', 'converted'].includes(feedback.status)) throw error('Действие недоступно: обращение завершено.', 409, 'FEEDBACK_TERMINAL');
    return feedback;
  }
  function event(feedback, user, action, text = '', contactMethod = '') {
    const createdAt = new Date().toISOString();
    return { ...feedback, updatedAt: createdAt, version: feedback.version + 1, history: [...feedback.history, {
      id: crypto.randomUUID(), createdAt, staff: user ? { id: user.id, name: user.name } : null, action, text, contactMethod,
    }] };
  }
  function save(feedback) {
    cases.set(feedback.id, feedback);
    return clone(feedback);
  }

  return {
    async submitFeedback(input, key) {
      const body = normalizeFeedbackInput(input);
      if (!validKey(key)) throw new Error('Некорректный ключ повторной отправки обращения.');
      const signature = JSON.stringify(body);
      const previous = submissions.get(key);
      if (previous) {
        if (previous.signature !== signature) throw error('Ключ повторного запроса уже использован для другого обращения.', 409, 'IDEMPOTENCY_CONFLICT');
        return Object.freeze(clone(previous.receipt));
      }
      const id = crypto.randomUUID();
      const createdAt = new Date().toISOString();
      const number = `F-${Date.now()}-${id.slice(0, 6).toUpperCase()}`;
      const receipt = { id, number, createdAt, state: 'accepted', demo: true };
      const feedback = {
        ...body, id, number, createdAt, updatedAt: createdAt, status: 'new', assignedTo: null, version: 1,
        resolution: '', order: null, history: [{ id: crypto.randomUUID(), createdAt, staff: null, action: 'feedback.created', text: '', contactMethod: '' }],
        notification: { state: 'not_configured', code: null }, demo: true,
      };
      cases.set(id, feedback);
      submissions.set(key, { signature, receipt });
      return Object.freeze(clone(receipt));
    },
    async getFeedback({ query = '', status = '' } = {}, { offset = 0, limit = 24 } = {}) {
      await staff();
      if (typeof query !== 'string' || query.length > 120 || status !== '' && !Object.hasOwn(FEEDBACK_STATUSES, status)
        || !Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) throw new Error('Некорректные фильтры или пагинация обращений.');
      const search = query.trim().toLocaleLowerCase('ru');
      const items = [...cases.values()].reverse().filter(item => (!status || item.status === status)
        && (!search || [item.number, item.name, item.phone, item.email, item.messenger.service, item.messenger.address].join(' ').toLocaleLowerCase('ru').includes(search)))
        .sort((a, b) => Number(b.status === 'new') - Number(a.status === 'new'));
      return { items: clone(items.slice(offset, offset + limit)), total: items.length, offset, limit, demo: true };
    },
    async getFeedbackDetail(id) {
      await staff();
      return clone(get(id));
    },
    async updateFeedbackStatus(input, patch) {
      const body = normalizeFeedbackStatus(patch);
      const user = await staff();
      const feedback = editable(input, user);
      if (body.status === 'in_progress' && feedback.status !== 'new') throw error('Обращение уже взято в работу.', 409, 'INVALID_STATUS');
      const next = event({
        ...feedback, status: body.status, resolution: body.resolution,
        assignedTo: body.status === 'in_progress' ? { id: user.id, name: user.name } : feedback.assignedTo,
      }, user, body.status === 'closed' ? 'feedback.closed' : 'feedback.claimed', body.resolution);
      return save(next);
    },
    async addFeedbackNote(input, patch) {
      const body = normalizeFeedbackNote(patch);
      const user = await staff();
      const feedback = editable(input, user);
      if (feedback.status !== 'in_progress') throw error('Сначала возьмите обращение в работу.', 409, 'INVALID_STATUS');
      return save(event(feedback, user, 'feedback.note_added', body.text, body.contactMethod));
    },
    async createFeedbackOrder(input, body, key) {
      if (!validKey(key)) throw new Error('Некорректный ключ повторного создания заказа.');
      const user = await staff();
      const feedback = get(input?.id);
      requireOwner(feedback, user);
      const signature = JSON.stringify(body, [
        'customer', 'name', 'phone', 'email', 'fulfillment', 'method', 'address',
        'items', 'productId', 'quantity', 'expectedUnitPriceKopecks',
      ]);
      const attemptKey = `${feedback.id}:${key}`;
      const previous = conversions.get(attemptKey);
      if (previous) {
        if (previous.signature !== signature) throw error('Ключ повторного запроса уже использован для другого заказа.', 409, 'IDEMPOTENCY_CONFLICT');
        return clone(previous.response);
      }
      const inflight = pending.get(feedback.id);
      if (inflight?.key === key) {
        if (inflight.signature !== signature) throw error('Ключ повторного запроса уже использован для другого заказа.', 409, 'IDEMPOTENCY_CONFLICT');
        return clone(await inflight.promise);
      }
      editable(input, user);
      if (feedback.status !== 'in_progress') throw error('Сначала возьмите обращение в работу.', 409, 'INVALID_STATUS');
      const promise = Promise.resolve().then(async () => {
        const order = await createOrder(body, `feedback:${attemptKey}`);
        const { id, number, createdAt, totalKopecks, status, integrationStatus } = order;
        const response = save(event({ ...feedback, status: 'converted', order: { id, number, createdAt, totalKopecks, status, integrationStatus } }, user, 'feedback.converted'));
        conversions.set(attemptKey, { signature, response: clone(response) });
        return response;
      });
      pending.set(feedback.id, { key, signature, promise });
      try { return clone(await promise); }
      finally { pending.delete(feedback.id); }
    },
  };
}
