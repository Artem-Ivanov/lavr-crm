import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createServer } from 'node:http';
import { setTimeout as delay } from 'node:timers/promises';

const command = promisify(execFile);
const docker = async (...args) => (await command('docker', args)).stdout.trim();

async function container(t, mode, upstream = 'http://host.docker.internal:8000') {
  const name = `lavr-crm-smoke-${process.pid}-${mode}`;
  await docker('run', '--detach', '--pull', 'never', '--name', name,
    '--publish', '127.0.0.1::8080', '--add-host', 'host.docker.internal:host-gateway',
    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
    '--env', `API_PROXY_TARGET=${upstream}`, `lavr-crm:${mode}`);
  t.after(() => docker('rm', '--force', name));
  const port = await docker('inspect', '--format', '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}', name);
  const origin = `http://127.0.0.1:${port}`;
  for (let attempt = 0; attempt < 60; attempt++) {
    const status = await docker('inspect', '--format', '{{.State.Health.Status}}', name);
    if (status === 'healthy') return { name, origin };
    if (status === 'unhealthy') break;
    await delay(500);
  }
  assert.fail(`Container did not become healthy: ${await docker('logs', name)}`);
}

test('demo image serves the compiled storefront as a non-root user without exposing sources', async t => {
  const { name, origin } = await container(t, 'demo');
  assert.notEqual(await docker('exec', name, 'id', '-u'), '0');
  assert.equal(await docker('exec', name, 'test', '!', '-d', '/app/node_modules'), '');
  const response = await fetch(origin);
  assert.equal(response.status, 200);
  assert.match(response.headers.get('cache-control'), /no-store/);
  const html = await response.text();
  const script = html.match(/src="(\/assets\/[^" ]+\.js)"/)[1];
  const asset = await fetch(`${origin}${script}`);
  assert.equal(asset.status, 200);
  assert.match(asset.headers.get('cache-control'), /immutable/);
  const bundle = await asset.text();
  assert.match(bundle, /demo-api-/);
  assert.doesNotMatch(bundle, /http-api-/);
  for (const path of ['/assets/missing.js', '/images/missing.jpg', '/.env', '/src/main.js', '/api']) {
    assert.equal((await fetch(`${origin}${path}`)).status, 404, path);
  }
  assert.equal((await fetch(`${origin}/crm-content`)).status, 200, 'SPA fallback');
  assert.equal((await fetch(`${origin}/healthz`)).status, 200);
});

test('API image preserves paths, query, staff headers, cookies and multipart bodies through Nginx', async t => {
  const requests = [];
  const upstream = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    requests.push({ method: request.method, path: request.url, headers: request.headers, body });
    response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'ETag': '"8"', 'Set-Cookie': 'staff=test; HttpOnly; Path=/api; SameSite=Lax' });
    response.end(JSON.stringify({ path: request.url, bytes: body.length }));
  });
  await new Promise(resolve => upstream.listen(0, '0.0.0.0', resolve));
  t.after(() => new Promise(resolve => { upstream.close(resolve); upstream.closeAllConnections(); }));
  const { name, origin } = await container(t, 'api', `http://host.docker.internal:${upstream.address().port}`);
  assert.notEqual(await docker('exec', name, 'id', '-u'), '0');
  const html = await (await fetch(origin)).text();
  const script = html.match(/src="(\/assets\/[^" ]+\.js)"/)[1];
  const bundle = await (await fetch(`${origin}${script}`)).text();
  assert.match(bundle, /http-api-/);
  assert.doesNotMatch(bundle, /demo-api-/);
  const body = JSON.stringify({ hero: { title: 'Сохранение через proxy' } });
  const response = await fetch(`${origin}/api/admin/site?version=7`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', Origin: origin, Cookie: 'staff=test', 'X-CSRF-Token': 'test-csrf', 'If-Match': '"7"' }, body,
  });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('etag'), '"8"');
  assert.match(response.headers.get('set-cookie'), /Path=\/api/);
  assert.match(response.headers.get('cache-control'), /no-store/);
  const request = requests.at(-1);
  assert.equal(request.method, 'PATCH');
  assert.equal(request.path, '/api/admin/site?version=7');
  assert.equal(request.headers.host, new URL(origin).host);
  assert.equal(request.headers.origin, origin);
  assert.equal(request.headers.cookie, 'staff=test');
  assert.equal(request.headers['x-csrf-token'], 'test-csrf');
  assert.equal(request.headers['if-match'], '"7"');
  assert.equal(request.body.toString(), body);
  const multipart = new FormData();
  multipart.set('file', new File([new Uint8Array(5 * 1024 * 1024)], 'test.png', { type: 'image/png' }));
  multipart.set('alt', 'Изображение через proxy');
  assert.equal((await fetch(`${origin}/api/admin/media`, { method: 'POST', body: multipart })).status, 200);
  assert.equal(requests.at(-1).path, '/api/admin/media');
  assert.match(requests.at(-1).headers['content-type'], /^multipart\/form-data; boundary=/);
  assert.ok(requests.at(-1).body.length > 5 * 1024 * 1024);
  assert.ok(requests.at(-1).body.includes(Buffer.from('Изображение через proxy')));
  const received = requests.length;
  assert.equal((await fetch(`${origin}/api/admin/media`, { method: 'POST', body: new Uint8Array(7 * 1024 * 1024) })).status, 413);
  assert.equal(requests.length, received, 'Oversize body is rejected before the upstream');
});
