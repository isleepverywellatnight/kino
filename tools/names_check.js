// Every interface module names only what it declares or imports.
//
// A module that uses another's function without importing it loads fine and
// fails only when that line runs (ReferenceError). TypeScript sees it in every
// file, typed or not, when checkJs covers them all (jsconfig.names.json); the
// type errors of the files not yet marked `// @ts-check` are for later, so only
// the errors about names and imports count here.
//
//   node tools/names_check.js            runs TypeScript through npx
//   node tools/names_check.js out.txt    reads a run's output (tsc --pretty false)
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const ROOT = path.resolve(__dirname, '..');
const TYPESCRIPT = 'typescript@5.9.3';
// Cannot find name; … did you mean; no such export; no such module; assigning
// an import; a variable used before its declaration.
const CODES = new Set(['TS2304', 'TS2552', 'TS2305', 'TS2614', 'TS2724', 'TS2307', 'TS2632', 'TS2630', 'TS2448', 'TS2449', 'TS2450']);

function nameErrors(output) {
  return output.split('\n').filter(line => {
    const code = /\berror (TS\d+):/.exec(line);
    return code && CODES.has(code[1]);
  });
}

function runTypeScript() {
  const npx = process.platform === 'win32' ? 'npx.cmd' : 'npx';
  const run = spawnSync(npx, ['--yes', '-p', TYPESCRIPT, 'tsc', '-p', 'jsconfig.names.json', '--pretty', 'false'],
    { cwd: ROOT, encoding: 'utf8', shell: process.platform === 'win32' });
  // 0: no error at all; 2: errors found (the types of unmarked files). Anything
  // else is TypeScript not running, which must not pass for a clean result.
  if (run.status !== 0 && run.status !== 2) {
    throw new Error(`TypeScript did not run (status ${run.status}): ${run.error || run.stderr || run.stdout}`);
  }
  return run.stdout;
}

if (require.main === module) {
  const output = process.argv[2] ? fs.readFileSync(process.argv[2], 'utf8') : runTypeScript();
  const errors = nameErrors(output);
  if (errors.length) {
    console.error(`${errors.length} name or import error(s):\n${errors.join('\n')}`);
    process.exit(1);
  }
  console.log('Every module names only what it declares or imports.');
}

module.exports = { nameErrors, CODES };
