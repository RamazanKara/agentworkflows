// Check the packed tarball the way a consumer sees it: contents, CommonJS and ESM imports of every entry point,
// and type resolution under the module settings consumers use. Run after `npm run build`.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const work = join(root, '.out', 'pack-check');
const consumer = join(work, 'consumer');
const installed = join(consumer, 'node_modules', '@agentworkflows', 'sdk');
const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
rmSync(work, { recursive: true, force: true });
mkdirSync(installed, { recursive: true });

const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const [packed] = JSON.parse(execFileSync(npm, ['pack', '--json', '--ignore-scripts', '--pack-destination', work], {
  cwd: root, encoding: 'utf8', shell: process.platform === 'win32',
}));
const files = packed.files.map(file => file.path).sort();
const unexpected = files.filter(file => !/^dist\/[\w/]+\.(js|d\.ts)$/.test(file) && !['package.json', 'README.md'].includes(file));
assert.deepEqual(unexpected, [], `Unexpected files in the package: ${unexpected.join(', ')}`);
if (!process.env.PACK_CHECK_SKIP_README) assert.ok(files.includes('README.md'), 'The package needs its README.');
for (const entry of Object.values(manifest.exports)) {
  for (const target of typeof entry === 'string' ? [entry] : Object.values(entry)) {
    assert.ok(files.includes(target.replace(/^\.\//, '')), `exports target ${target} is not packed`);
  }
}
for (const sources of Object.values(manifest.typesVersions?.['*'] ?? {})) {
  for (const source of sources) assert.ok(files.includes(source), `typesVersions target ${source} is not packed`);
}
execFileSync('tar', ['-xzf', join(work, packed.filename), '-C', installed, '--strip-components=1']);

const entries = {
  '@agentworkflows/sdk': ['GatewayClient', 'GatewayError', 'GatewayTimeoutError', 'GatewayStreamError', 'firstApprovedRun'],
  '@agentworkflows/sdk/workflows': ['WorkflowGateway', 'ApprovalWorkflow', 'Budget', 'withInputSchema', 'AgentWorkflowsTrigger'],
  '@agentworkflows/sdk/activities': ['GatewayActivities', 'AgentContext'],
  '@agentworkflows/sdk/worker': ['runWorker'],
};
const node = (args) => execFileSync(process.execPath, args, { cwd: consumer, encoding: 'utf8', stdio: 'pipe' });
writeFileSync(join(consumer, 'require.cjs'), Object.entries(entries).map(([specifier, names]) =>
  `{ const m = require(${JSON.stringify(specifier)}); for (const n of ${JSON.stringify(names)}) if (typeof m[n] !== 'function') throw new Error('require ${specifier}: ' + n); }`,
).join('\n'));
writeFileSync(join(consumer, 'import.mjs'), Object.entries(entries).map(([specifier, names], index) =>
  `import { ${names.join(', ')} } from ${JSON.stringify(specifier)};\n` +
  `for (const [n, v] of Object.entries({ ${names.join(', ')} })) if (typeof v !== 'function') throw new Error('import ${index}: ' + n);`,
).join('\n'));
node(['require.cjs']);
node(['import.mjs']);
assert.throws(() => node(['-e', "require('@agentworkflows/sdk/dist/http.js')"]), /ERR_PACKAGE_PATH_NOT_EXPORTED/);

const usage = `
import { GatewayClient, GatewayError } from '@agentworkflows/sdk';
import type { AgentHandler, TeamInfo, WorkerOptions } from '@agentworkflows/sdk';
import { WorkflowGateway, withInputSchema } from '@agentworkflows/sdk/workflows';
import { GatewayActivities } from '@agentworkflows/sdk/activities';
import { runWorker } from '@agentworkflows/sdk/worker';
const client = new GatewayClient('http://127.0.0.1:8080', { apiKey: 'key' });
const team: Promise<TeamInfo> = client.team();
const agent: AgentHandler = async (context, args) => context.tool('search', args);
const options: WorkerOptions = { agents: { agent }, healthPort: 8081 };
const declared = withInputSchema({ type: 'object', properties: {} }, async function Flow() { return new WorkflowGateway().text('hi'); });
export const used = [team, options, declared, GatewayError, GatewayActivities, runWorker, client.registerWorkflow(declared, ['m'])];
`;
const configs = {
  'esm.mts': { module: 'node16', moduleResolution: 'node16' },
  'cjs.cts': { module: 'node16', moduleResolution: 'node16' },
  'bundler.ts': { module: 'esnext', moduleResolution: 'bundler' },
  'node10.ts': { module: 'commonjs', moduleResolution: 'node10', ignoreDeprecations: '6.0' },
};
const tsc = join(root, 'node_modules', 'typescript', 'bin', 'tsc');
for (const [file, options] of Object.entries(configs)) {
  writeFileSync(join(consumer, file), usage);
  writeFileSync(join(consumer, `tsconfig.${file}.json`), JSON.stringify({
    // Temporal's own declarations need skipLibCheck, as in most projects; the usage file is still checked.
    compilerOptions: { target: 'es2022', strict: true, noEmit: true, skipLibCheck: true, types: ['node'], ...options }, files: [file],
  }));
  try {
    node([tsc, '-p', `tsconfig.${file}.json`]);
  } catch (error) {
    throw new Error(`Type check failed for ${file} (${JSON.stringify(options)}):\n${error.stdout}`);
  }
}
console.log(`Package check passed: ${files.length} files, ${packed.size} bytes packed, ${packed.unpackedSize} unpacked; ` +
  `CommonJS, ESM and ${Object.keys(configs).length} TypeScript module settings resolve every entry point.`);
