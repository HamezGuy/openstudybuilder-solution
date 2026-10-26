// Compose platform audit settings guard. Run: node --test scripts/compose-platform-audit.test.mjs
// Needs the Docker CLI with the compose plugin (no daemon).
//
// IR-259 (FX-OSB-04): Neo4jOsbPlatformCommandStore reads PLATFORM_DOMAIN_AUDIT_MODE
// and PLATFORM_REGION from the api container's environment
// (api/clinical_mdr_api/services/integrations/platform_command.py), but no
// compose file mapped them and .dockerignore keeps every .env out of the image,
// so the domain audit chain ran disabled in every compose deployment whatever
// the host held. compose.yaml now maps them from the host names
// OSB_PLATFORM_DOMAIN_AUDIT_MODE and OSB_PLATFORM_REGION, defaulting to the
// code defaults, so a host that still holds the old unprefixed names from an
// earlier .env.production.example keeps today's behaviour (D-01).
// OSB_NATIVE_REGIMEN_WRITES_ENABLED (default true) reaches the api the same way.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PLACEHOLDER = 'compose-platform-audit-placeholder-0123456789abcdef';
const STACKS = [['compose.yaml'], ['compose.yaml', 'compose.production.yaml']];

let envDir;
// The api service's environment as Docker Compose resolves it from the
// committed files and `values` only: no .env, no COMPOSE_* and no value the
// shell happens to hold for a variable the files interpolate.
function apiEnvironment(files, values = {}) {
  envDir ??= mkdtempSync(path.join(os.tmpdir(), 'compose-platform-audit-'));
  const emptyEnv = path.join(envDir, 'empty.env');
  writeFileSync(emptyEnv, '');
  const text = files.map((file) => readFileSync(path.join(REPO, file), 'utf8')).join('\n');
  const env = { ...process.env };
  for (const name of Object.keys(env)) if (/^COMPOSE_/i.test(name)) delete env[name];
  for (const [, name] of text.matchAll(/\$\{(\w+)/g)) delete env[name];
  for (const [, name] of text.matchAll(/\$\{(\w+):?\?/g)) env[name] = PLACEHOLDER;
  Object.assign(env, values);
  const args = ['compose', '--env-file', emptyEnv, ...files.flatMap((file) => ['--file', file]), 'config', '--format', 'json', 'api'];
  const result = spawnSync('docker', args, { cwd: REPO, env, encoding: 'utf8', windowsHide: true, timeout: 60000 });
  assert.equal(result.status, 0, `${files.join(' + ')}: ${result.stderr}`);
  return JSON.parse(result.stdout).services.api.environment;
}

const composeCli = spawnSync('docker', ['compose', 'version'], { encoding: 'utf8', windowsHide: true, timeout: 30000 });
const noCompose = composeCli.status !== 0 && 'needs the Docker CLI with the compose plugin';

for (const files of STACKS) {
  const label = files.join(' + ');

  test(`${label}: the api gets the code defaults for the domain audit settings and native regimen writes`, { skip: noCompose }, () => {
    const environment = apiEnvironment(files);
    assert.equal(environment.PLATFORM_DOMAIN_AUDIT_MODE, 'disabled');
    assert.equal(environment.PLATFORM_REGION, 'local');
    assert.equal(environment.OSB_NATIVE_REGIMEN_WRITES_ENABLED, 'true');
  });

  test(`${label}: explicit OSB_ host values reach the api`, { skip: noCompose }, () => {
    const environment = apiEnvironment(files, {
      OSB_PLATFORM_DOMAIN_AUDIT_MODE: 'enforced',
      OSB_PLATFORM_REGION: 'synthetic-region-1',
      OSB_NATIVE_REGIMEN_WRITES_ENABLED: 'false',
    });
    assert.equal(environment.PLATFORM_DOMAIN_AUDIT_MODE, 'enforced');
    assert.equal(environment.PLATFORM_REGION, 'synthetic-region-1');
    assert.equal(environment.OSB_NATIVE_REGIMEN_WRITES_ENABLED, 'false');
  });

  test(`${label}: the unprefixed host names an older .env.production.example set take no effect`, { skip: noCompose }, () => {
    const environment = apiEnvironment(files, {
      PLATFORM_DOMAIN_AUDIT_MODE: 'enforced',
      PLATFORM_REGION: 'REPLACE_WITH_APPROVED_REGION',
    });
    assert.equal(environment.PLATFORM_DOMAIN_AUDIT_MODE, 'disabled');
    assert.equal(environment.PLATFORM_REGION, 'local');
  });
}

test('.env.production.example names the audit settings by their host names and leaves them commented', () => {
  const lines = readFileSync(path.join(REPO, '.env.production.example'), 'utf8').split(/\r?\n/);
  assert.deepEqual(lines.filter((line) => /^\s*(OSB_)?PLATFORM_(DOMAIN_AUDIT_MODE|REGION)\s*=/.test(line)), []);
  for (const name of ['OSB_PLATFORM_DOMAIN_AUDIT_MODE', 'OSB_PLATFORM_REGION']) {
    assert.ok(lines.some((line) => new RegExp(`^#\\s*${name}=`).test(line)), `${name} is listed, commented`);
  }
});

test.after(() => { if (envDir) rmSync(envDir, { recursive: true, force: true }); });
