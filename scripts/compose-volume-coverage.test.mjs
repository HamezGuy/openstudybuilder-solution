// Compose volume coverage guard. Run: node --test scripts/compose-volume-coverage.test.mjs
// Needs the Docker CLI with the compose plugin (no daemon); with a daemon it
// also inspects the images already present locally (it never pulls).
//
// 2026-09-27: C: reached 0 bytes and Docker Desktop stopped with ~1,627
// dangling volumes (118 GB) on its disk. An image VOLUME that a compose service
// mounts nothing over becomes an anonymous volume, and `docker compose down`
// (without -v) leaves it behind, so every down/up cycle strands one more. The
// neo4j images declare /data and /logs: the root `database` service covered
// only /data, and the api and db_schema_migration databases covered neither.
// (Component directories as renamed by OpenStudyBuilder 2.10.)
//
// The guard resolves every compose stack exactly as Docker Compose merges it
// (`docker compose config`) and requires each VOLUME of each service's image
// to be covered by a named volume, a bind mount or a tmpfs. The VOLUMEs come
// from the repo's own Dockerfiles (the built stage, its parent stages and its
// base image) and, for images built elsewhere, from IMAGE_VOLUMES below.
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

// Every tracked compose file, in the -f order it is used with. Options: env
// (values for variables the files require), contexts (a build context that a
// pipeline fills from another directory of the repo), partial (the files merge
// onto a compose file outside the repo: services that name neither an image
// nor a build only amend services defined there).
const STACKS = [
  { files: ['compose.yaml'] },
  { files: ['compose.yaml', 'compose.production.yaml'] },
  { files: ['api/compose.yaml'] },
  { files: ['api/compose.yaml', 'api/compose.dev.yaml'] },
  { files: ['db_schema_migration/compose.yaml'] },
  { files: ['documentation_portal/compose.yaml'] },
  { files: ['import_standards/compose.yaml'] },
  { files: ['frontend/compose.yaml'] },
  { files: ['frontend/compose.dev.yaml'] },
  { files: ['export/compose.yaml'] },
  { files: ['import_sponsor_data/compose.yaml'] },
  { files: ['import_sponsor_data/compose.yaml', 'import_sponsor_data/compose.override.yaml'] },
  { files: ['system_tests/ui-tests/compose.yaml'] },
  { files: ['system_tests/neodash-test/compose.yaml'] },
];

// VOLUMEs declared by images a service runs without building them here:
// [name pattern (no docker.io/ or library/ prefix), VOLUME targets, or
// { dockerfile } for an image this repo builds under another service].
// Checked with `docker image inspect -f '{{json .Config.Volumes}}' <image>`
// (the Docker-gated test re-checks every image present locally) or the
// registry's image config (hub.docker.com/v2/repositories/<repo>/tags/<tag>/images).
const IMAGE_VOLUMES = [
  [/^neo4j(:|@|$)/, ['/data', '/logs']],
  [/^(python|node|nginx)(:|@|$)/, []],
  [/^cypress\/browsers(:|@|$)/, []],
  [/^openzipkin\/zipkin(:|@|$)/, []],
  [/^clinical-mdr-api(:|$)/, { dockerfile: 'api/Dockerfile' }],
  [/^osb-import(:|$)/, { dockerfile: 'import_sponsor_data/Dockerfile' }],
];

// Images nobody can inspect from here, with the reason. The Docker-gated test
// checks their coverage whenever the image is present locally.
const UNINSPECTABLE = new Map([
  ['novo-allure', 'the upstream OpenStudyBuilder pipeline\'s private Allure report image (ALLURE_IMAGE); not published'],
]);

const COMPOSE_FILE = /(^|\/)(docker-)?compose(\.[\w-]+)*\.ya?ml$/;
const PLACEHOLDER = 'compose-volume-guard-placeholder-0123456789abcdef';

const imageName = (ref) => String(ref).trim().replace(/^docker\.io\//, '').replace(/^library\//, '');
const normalTarget = (target) => String(target).replace(/\/+$/, '') || '/';

function substitute(text, vars) {
  return String(text).replace(/\$\{(\w+)(?::?([-+])([^}]*))?\}|\$(\w+)/g, (_, braced, op, word, bare) => {
    const value = vars.get(braced || bare);
    if (op === '-') return value ? value : word;
    if (op === '+') return value ? word : '';
    return value ?? '';
  });
}

// Dockerfile -> { args: global ARG defaults, stages: [{ from, name, volumes }] }.
export function parseDockerfile(text) {
  const lines = [];
  let current = '';
  for (const raw of text.split(/\r?\n/)) {
    if (/^\s*#/.test(raw)) continue;
    if (/\\\s*$/.test(raw)) { current += `${raw.replace(/\\\s*$/, '')} `; continue; }
    current = `${current}${raw}`.trim();
    if (current) lines.push(current);
    current = '';
  }
  if (current.trim()) lines.push(current.trim());
  const args = [];
  const stages = [];
  for (const line of lines) {
    const [, keyword = '', rest = ''] = line.match(/^(\w+)\s*([\s\S]*)$/) || [];
    if (/^from$/i.test(keyword)) {
      const words = rest.split(/\s+/).filter((word) => !word.startsWith('--'));
      stages.push({ from: words[0], name: /^as$/i.test(words[1] || '') ? words[2].toLowerCase() : undefined, volumes: [] });
    } else if (/^arg$/i.test(keyword) && stages.length === 0) {
      for (const declaration of rest.split(/\s+/).filter(Boolean)) {
        const [name, ...value] = declaration.split('=');
        args.push([name, value.length ? value.join('=').replace(/^(["'])(.*)\1$/, '$2') : undefined]);
      }
    } else if (/^volume$/i.test(keyword) && stages.length) {
      const list = rest.trim().startsWith('[') ? JSON.parse(rest) : rest.split(/\s+/);
      stages.at(-1).volumes.push(...list.filter(Boolean));
    }
  }
  return { args, stages };
}

// VOLUMEs of the image built from `text` (the target stage, its parent stages
// and its base image): { volumes, bases } or { unknown } for an unlisted base.
export function dockerfileVolumes(text, { buildArgs = {}, target, label = 'Dockerfile' } = {}) {
  const { args, stages } = parseDockerfile(text);
  // An empty Dockerfile (upstream's placeholder for the NeoDash report
  // service) builds no image, so it declares no VOLUME.
  if (!stages.length && !String(text).trim()) return { volumes: [], bases: [] };
  const vars = new Map();
  for (const [name, fallback] of args) {
    vars.set(name, buildArgs[name] != null ? String(buildArgs[name]) : fallback === undefined ? undefined : substitute(fallback, vars));
  }
  let index = target ? stages.findIndex((stage) => stage.name === String(target).toLowerCase()) : stages.length - 1;
  assert.ok(index >= 0, `${label}: no build stage ${target}`);
  const volumes = [];
  for (;;) {
    const stage = stages[index];
    volumes.push(...stage.volumes.map((volume) => substitute(volume, vars)));
    const base = substitute(stage.from, vars);
    const parent = stages.findIndex((candidate, at) => at < index && candidate.name === base.toLowerCase());
    if (parent >= 0) { index = parent; continue; }
    const inherited = imageVolumes(base);
    if (inherited.unknown) return inherited;
    return { volumes: [...volumes, ...inherited.volumes], bases: inherited.bases };
  }
}

// VOLUMEs of an image this repo does not build under the service at hand.
export function imageVolumes(ref) {
  const name = imageName(ref);
  if (name === 'scratch') return { volumes: [], bases: [] };
  const hit = IMAGE_VOLUMES.find(([pattern]) => pattern.test(name));
  if (!hit) return { unknown: name };
  if (Array.isArray(hit[1])) return { volumes: hit[1], bases: [name] };
  const file = path.join(REPO, hit[1].dockerfile);
  return dockerfileVolumes(readFileSync(file, 'utf8'), { label: hit[1].dockerfile });
}

function serviceVolumes(service, stack) {
  if (service.build) {
    let context = path.resolve(REPO, service.build.context || '.');
    const alias = stack.contexts?.[path.relative(REPO, context).split(path.sep).join('/')];
    if (alias) context = path.join(REPO, alias);
    const inline = service.build.dockerfile_inline;
    const file = path.resolve(context, service.build.dockerfile || 'Dockerfile');
    if (inline || existsSync(file)) {
      return dockerfileVolumes(inline ?? readFileSync(file, 'utf8'), { buildArgs: service.build.args || {}, target: service.build.target, label: path.relative(REPO, file) });
    }
    if (!service.image) return { unknown: `the build context ${path.relative(REPO, context)} (not in the repo; map it in the stack's contexts)` };
  }
  return imageVolumes(service.image);
}

function mounts(service) {
  const covered = new Set();
  const anonymous = [];
  for (const mount of service.volumes || []) {
    if (mount.type === 'volume' && !mount.source) anonymous.push(mount.target);
    else covered.add(normalTarget(mount.target));
  }
  for (const entry of [service.tmpfs || []].flat()) covered.add(normalTarget(String(entry).split(':')[0]));
  return { covered, anonymous };
}

let envDir;
function composeConfig({ files, env: values = {}, partial = false }) {
  envDir ??= mkdtempSync(path.join(os.tmpdir(), 'compose-volume-guard-'));
  const emptyEnv = path.join(envDir, 'empty.env');
  writeFileSync(emptyEnv, '');
  // Only the committed defaults count: no .env, no COMPOSE_* and no value the
  // shell happens to hold for a variable the files interpolate.
  const text = files.map((file) => readFileSync(path.join(REPO, file), 'utf8')).join('\n');
  const env = { ...process.env };
  for (const name of Object.keys(env)) if (/^COMPOSE_/i.test(name)) delete env[name];
  for (const [, name] of text.matchAll(/\$\{(\w+)/g)) delete env[name];
  for (const [, name] of text.matchAll(/\$\{(\w+):?\?/g)) env[name] = PLACEHOLDER;
  Object.assign(env, values);
  const args = ['compose', '--env-file', emptyEnv, ...files.flatMap((file) => ['--file', file]), '--profile', '*', 'config', '--format', 'json'];
  if (partial) args.push('--no-consistency');
  const result = spawnSync('docker', args, {
    cwd: REPO, env, encoding: 'utf8', windowsHide: true, timeout: 60000,
  });
  assert.equal(result.status, 0, `${files.join(' + ')}: ${result.stderr}`);
  return JSON.parse(result.stdout);
}

const composeCli = spawnSync('docker', ['compose', 'version'], { encoding: 'utf8', windowsHide: true, timeout: 30000 });
const noCompose = composeCli.status !== 0 && 'needs the Docker CLI with the compose plugin';
const listedBases = new Set(); // IMAGE_VOLUMES images the stacks resolved to
const serviceImages = new Map(); // image a service runs -> [{ service, covered }]

test('the Dockerfile reader follows ARG defaults, build args, stage parents and both VOLUME forms', () => {
  const text = [
    '# comment', 'ARG BASE_VERSION=1', 'ARG BASE=example/base:${BASE_VERSION}-x', 'ARG TARGET=dev',
    'FROM example/tool:2 AS build', 'VOLUME /not/in/the/image',
    'FROM $BASE AS common', 'VOLUME ["/extra"]',
    'FROM common AS dev-stage', 'VOLUME /a \\', '  /b',
    'FROM ${TARGET}-stage AS final',
  ].join('\n');
  IMAGE_VOLUMES.push([/^example\/base(:|$)/, ['/data', '/logs']], [/^example\/tool(:|$)/, []]);
  try {
    assert.deepEqual(dockerfileVolumes(text), { volumes: ['/a', '/b', '/extra', '/data', '/logs'], bases: ['example/base:1-x'] });
    assert.deepEqual(dockerfileVolumes(text, { buildArgs: { BASE_VERSION: '7' }, target: 'common' }), { volumes: ['/extra', '/data', '/logs'], bases: ['example/base:7-x'] });
    assert.deepEqual(dockerfileVolumes(text, { target: 'build' }), { volumes: ['/not/in/the/image'], bases: ['example/tool:2'] });
    assert.deepEqual(dockerfileVolumes('ARG IMAGE=private/thing\nFROM $IMAGE\n'), { unknown: 'private/thing' });
  } finally {
    IMAGE_VOLUMES.splice(-2);
  }
});

test('every tracked compose file belongs to a checked stack', () => {
  const tracked = execFileSync('git', ['ls-files'], { cwd: REPO, encoding: 'utf8', windowsHide: true, maxBuffer: 256 * 1024 * 1024 })
    .split(/\r?\n/).filter((file) => COMPOSE_FILE.test(file));
  const listed = new Set(STACKS.flatMap((stack) => stack.files));
  assert.deepEqual(tracked.filter((file) => !listed.has(file)), [], 'add each new compose file to STACKS');
  assert.deepEqual([...listed].filter((file) => !tracked.includes(file)), [], 'STACKS names a file git does not track');
});

for (const stack of STACKS) {
  test(`${stack.files.join(' + ')}: every image VOLUME is covered by a named volume, a bind or a tmpfs`, { skip: noCompose }, () => {
    const definition = composeConfig(stack);
    const problems = [];
    for (const [name, service] of Object.entries(definition.services || {})) {
      if (stack.partial && !service.image && !service.build) continue;
      const { covered, anonymous } = mounts(service);
      for (const target of anonymous) problems.push(`${name}: ${target} is mounted as an anonymous volume`);
      const ref = service.image || `${definition.name}-${name}`;
      serviceImages.set(ref, [...(serviceImages.get(ref) || []), { service: `${stack.files.join(' + ')} ${name}`, covered }]);
      const declared = serviceVolumes(service, stack);
      if (declared.unknown) {
        if (!UNINSPECTABLE.has(declared.unknown)) problems.push(`${name}: image ${declared.unknown} is not in IMAGE_VOLUMES (add it after docker image inspect -f '{{json .Config.Volumes}}' ${declared.unknown})`);
        continue;
      }
      for (const base of declared.bases) listedBases.add(base);
      for (const target of declared.volumes) {
        if (!covered.has(normalTarget(target))) problems.push(`${name}: image VOLUME ${target} would become an anonymous volume that every \`compose down\` leaves behind`);
      }
    }
    assert.deepEqual(problems, []);
  });
}

const daemon = spawnSync('docker', ['version', '--format', '{{.Server.Version}}'], { encoding: 'utf8', windowsHide: true, timeout: 30000 });
test('the images present locally agree: IMAGE_VOLUMES is right and every VOLUME is covered (nothing is pulled)', { skip: (noCompose || daemon.status !== 0) && 'needs a Docker daemon' }, (t) => {
  const cache = new Map();
  const volumesOf = (ref) => {
    if (!cache.has(ref)) {
      const result = spawnSync('docker', ['image', 'inspect', '--format', '{{json .Config.Volumes}}', ref], { encoding: 'utf8', windowsHide: true, timeout: 60000 });
      cache.set(ref, result.status === 0 ? Object.keys(JSON.parse(result.stdout.trim()) || {}).map(normalTarget).sort() : null);
    }
    return cache.get(ref);
  };
  const problems = [];
  for (const ref of listedBases) {
    const actual = volumesOf(ref);
    const listed = imageVolumes(ref).volumes.map(normalTarget).sort();
    if (actual && JSON.stringify(actual) !== JSON.stringify(listed)) problems.push(`${ref}: the image declares ${JSON.stringify(actual)}, IMAGE_VOLUMES says ${JSON.stringify(listed)}`);
  }
  for (const [ref, uses] of serviceImages) {
    for (const target of volumesOf(ref) || []) for (const { service, covered } of uses) if (!covered.has(target)) problems.push(`${service}: ${ref} declares VOLUME ${target}, which nothing covers`);
  }
  t.diagnostic(`inspected locally: ${[...cache].filter(([, volumes]) => volumes).map(([ref]) => ref).join(', ') || 'none'}`);
  assert.deepEqual(problems, []);
});

test.after(() => { if (envDir) rmSync(envDir, { recursive: true, force: true }); });
