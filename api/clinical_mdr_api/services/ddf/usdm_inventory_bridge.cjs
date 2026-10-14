'use strict';

// Private, bounded transport for the installed kernel. No semantic resolver here.
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const { TextDecoder } = require('node:util');
const MAX_INPUT = 16 * 1024 * 1024;
const MAX_OUTPUT = 64 * 1024 * 1024;
const fail = () => { process.stderr.write('OSB_INVENTORY_BRIDGE_REFUSED\n'); process.exit(1); };

async function main() {
  if (process.argv.length !== 4 || process.env.NODE_OPTIONS || process.env.NODE_PATH) fail();
  const nodeRoot = fs.realpathSync(process.argv[2]);
  if (process.version !== process.argv[3]) fail();
  const packageRoot = path.join(nodeRoot, 'node_modules', '@accura-trial', 'shared-types');
  // All loaded files, including Ajv's runtime helpers, must come from the seal.
  const resolve = Module._resolveFilename;
  Module._resolveFilename = function (...args) {
    const filename = Reflect.apply(resolve, this, args);
    if (!Module.isBuiltin(filename)) {
      const relative = path.relative(nodeRoot, fs.realpathSync(filename));
      if (relative.startsWith('..' + path.sep) || relative === '..' || path.isAbsolute(relative)) fail();
    }
    return filename;
  };
  const requireInstalled = Module.createRequire(path.join(packageRoot, 'package.json'));
  const { canonicalJson } = requireInstalled('@accura-trial/shared-types/dist/study-exchange-amendment/contract/content-hash.js');
  const { inventoryUsdmV4Document } = requireInstalled('@accura-trial/shared-types/usdm/validation');
  const { USDM_V4_REFERENCE: referenceDictionary } = requireInstalled('@accura-trial/shared-types/dist/usdm/generated/usdm-v4.reference.js');
  let size = 0, count = 0;
  const chunks = [];
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_INPUT || ++count > 4096) fail();
    chunks.push(chunk);
  }
  const input = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(Buffer.concat(chunks));
  const document = JSON.parse(input);
  // The caller uses the existing platform canonicalizer. Exact comparison also
  // rejects duplicate names, BOM, unsafe numeric rounding and trailing values.
  if (canonicalJson(document) !== input) fail();
  const inventory = inventoryUsdmV4Document(document);
  const output = JSON.stringify({ inventory, referenceDictionary });
  if (Buffer.byteLength(output) > MAX_OUTPUT) fail();
  process.stdout.write(output);
}

const timer = setTimeout(fail, 15000);
main().then(() => clearTimeout(timer), fail);
