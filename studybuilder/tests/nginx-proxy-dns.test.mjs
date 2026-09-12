import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, unlinkSync, rmdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

// Opt-in Docker integration test; uses only disposable synthetic containers.
// OSB_NGINX_TEST_IMAGE may name an already-built StudyBuilder frontend image.
test('proxy starts without backends and preserves API routes after discovery and address changes', {
  skip: process.env.OSB_RUN_NGINX_DNS_TESTS !== '1',
  timeout: 60000,
}, async () => {
  const image = process.env.OSB_NGINX_TEST_IMAGE || 'opensourcebuilder-frontend';
  const suffix = `${process.pid}-${Date.now()}`;
  const network = `osb-dns-test-${suffix}`;
  const backend = `osb-dns-backend-${suffix}`;
  const replacement = `osb-dns-replacement-${suffix}`;
  const frontend = `osb-dns-frontend-${suffix}`;
  const temp = mkdtempSync(path.join(tmpdir(), 'osb-nginx-dns-'));
  const template = fileURLToPath(new URL('../config/nginx/default.conf.template', import.meta.url));
  const docker = (...args) => execFileSync('docker', args, {
    encoding: 'utf8', timeout: 15000, stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
  const config = path.join(temp, 'backend.conf');
  writeFileSync(config, `server { listen 8080; location / {
    default_type application/json;
    return 200 '{"path":"$request_uri","server":"$server_addr"}';
  } }\n`);
  const created = [];
  let networkCreated = false;
  try {
    docker('network', 'create', network);
    networkCreated = true;
    const runBackend = (name, alias) => {
      docker('run', '--detach', '--name', name, '--network', network,
        ...(alias ? ['--network-alias', 'synthetic-api'] : []),
        '--mount', `type=bind,source=${config},target=/etc/nginx/conf.d/default.conf,readonly`,
        '--entrypoint', 'nginx', image, '-g', 'daemon off;');
      created.push(name);
    };
    const address = name => Object.values(JSON.parse(docker('inspect', name))[0].NetworkSettings.Networks)[0].IPAddress;
    const env = ['NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1', 'PORT=5005'];
    for (const prefix of ['API', 'CONSUMER_API', 'EXTENSIONS_API', 'DOC', 'NEODASH']) {
      env.push(`${prefix}_HOST=synthetic-api`, `${prefix}_PORT=8080`);
    }
    docker('run', '--detach', '--name', frontend, '--network', network,
      '--publish', '127.0.0.1::5005', ...env.flatMap(value => ['--env', value]),
      '--mount', `type=bind,source=${template},target=/etc/nginx/templates/default.conf.template,readonly`,
      image);
    created.push(frontend);
    const inspected = JSON.parse(docker('inspect', frontend))[0];
    const binding = inspected.NetworkSettings.Ports['5005/tcp']?.[0];
    assert.ok(binding, `Synthetic proxy did not bind its test port: ${docker('logs', frontend)}`);
    const origin = `http://127.0.0.1:${binding.HostPort}`;
    // A missing optional service used to stop nginx itself during startup.
    // The main UI must remain available while every upstream is absent.
    const rootDeadline = Date.now() + 10000;
    let root;
    do {
      try {
        root = await fetch(origin + '/', { signal: AbortSignal.timeout(1500) });
        if (root.ok) break;
      } catch { /* nginx may still be processing its entrypoint template. */ }
      await new Promise(resolve => setTimeout(resolve, 150));
    } while (Date.now() < rootDeadline);
    assert.equal(root?.status, 200, `Frontend did not start without backends: ${docker('logs', frontend)}`);
    assert.match(root.headers.get('content-type') || '', /text\/html/);
    const unavailable = await fetch(origin + '/api/system/information', {
      signal: AbortSignal.timeout(7000),
    });
    assert.ok(unavailable.status >= 500, `Unavailable API returned ${unavailable.status}`);

    runBackend(backend, true);
    // Allocate replacement while the original is still connected. It must
    // receive a different address; reusing the same IP would miss the defect.
    runBackend(replacement, false);
    const firstIp = address(backend);
    const secondIp = address(replacement);
    assert.notEqual(firstIp, secondIp);
    const read = async target => {
      const response = await fetch(origin + target, { signal: AbortSignal.timeout(1500) });
      return { response, payload: response.ok ? await response.json() : null };
    };
    const waitFor = async ip => {
      const deadline = Date.now() + 14000;
      do {
        try {
          const result = await read('/api/openapi.json?probe=one%20two&n=2');
          if (result.response.ok && result.payload?.server === ip) return result.payload;
        } catch { /* Initial DNS lookup or address change may be in progress. */ }
        await new Promise(resolve => setTimeout(resolve, 150));
      } while (Date.now() < deadline);
      throw new Error(`Proxy did not observe the expected synthetic backend ${ip}`);
    };
    const initial = await waitFor(firstIp);
    assert.equal(initial.path, '/openapi.json?probe=one%20two&n=2');
    for (const prefix of ['/consumer-api/', '/extensions-api/', '/doc/', '/neodash/']) {
      const { response, payload } = await read(`${prefix}nested/route?a=1`);
      assert.equal(response.status, 200);
      assert.equal(payload.path, '/nested/route?a=1');
    }
    const missing = await fetch(origin + '/missing-static-module.js');
    assert.equal(missing.status, 404);
    // Change only the DNS alias; nginx is neither restarted nor reloaded.
    docker('network', 'disconnect', network, backend);
    docker('network', 'disconnect', network, replacement);
    docker('network', 'connect', '--alias', 'synthetic-api', '--ip', secondIp, network, replacement);
    const changed = await waitFor(secondIp);
    assert.equal(changed.path, initial.path);
    assert.equal(JSON.parse(docker('inspect', frontend))[0].RestartCount, 0);
  } finally {
    for (const name of created.reverse()) {
      try { docker('rm', '--force', name); } catch { /* Preserve the original test error. */ }
    }
    if (networkCreated) {
      try { docker('network', 'rm', network); } catch { /* Preserve the original test error. */ }
    }
    unlinkSync(config);
    rmdirSync(temp);
  }
});
