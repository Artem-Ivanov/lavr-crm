import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

const command = new URL('../scripts/deploy.sh', import.meta.url).pathname;
function setup(t) {
  const directory = mkdtempSync(join(tmpdir(), 'lavr-deploy-command-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const record = join(directory, 'arguments');
  writeFileSync(join(directory, 'ssh'), '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$DEPLOY_TEST_ARGS"\n', { mode: 0o755 });
  const run = args => spawnSync('sh', [command, ...args], { encoding: 'utf8', env: { ...process.env, PATH: `${directory}:${process.env.PATH}`, DEPLOY_TEST_ARGS: record } });
  return { run, record };
}

test('Git deploy sends only a validated commit to the pinned server command', t => {
  const { run, record } = setup(t);
  const commit = 'a'.repeat(40);
  const result = run([commit]);
  assert.equal(result.status, 0, result.stderr);
  const args = readFileSync(record, 'utf8').trim().split('\n');
  assert.ok(args.includes('StrictHostKeyChecking=yes'));
  assert.ok(args.includes('IdentitiesOnly=yes'));
  assert.ok(args.some(value => value.startsWith('UserKnownHostsFile=') && value.endsWith('/.ssh/lavr_crm_known_hosts')));
  assert.equal(args.at(-2), 'deploy@157.228.139.156');
  assert.equal(args.at(-1), `python3 /home/deploy/lavr-crm-deploy/deploy.py ${commit}`);
});

test('Git deploy rejects branch names, shell text and extra arguments before SSH', t => {
  const { run, record } = setup(t);
  for (const args of [['master'], ['a'.repeat(39)], ['a'.repeat(41)], ['a'.repeat(40) + '; echo unexpected'], ['--help'], ['a'.repeat(40), 'extra']]) {
    const result = run(args);
    assert.equal(result.status, 2, JSON.stringify(args));
    assert.equal(existsSync(record), false, 'invalid input must never reach SSH');
  }
});
