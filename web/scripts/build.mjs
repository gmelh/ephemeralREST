// Builds the web app into dist/, which ephemeralREST serves at its root (see web.py).
//   node scripts/build.mjs           production build (minified)
//   node scripts/build.mjs --watch   rebuild on change; run the API alongside (python app.py)
import { build, context } from 'esbuild';
import { watch as watchFile } from 'node:fs';
import { copyFile, mkdir } from 'node:fs/promises';

const watch = process.argv.includes('--watch');
const options = {
  entryPoints: ['src/main.tsx'],
  bundle: true,
  format: 'esm',
  target: 'es2022',
  outfile: 'dist/app.js',
  jsx: 'automatic',
  jsxImportSource: 'preact',
  sourcemap: true,
  minify: !watch,
  logLevel: 'info',
};

const STATIC = [['index.html', 'dist/index.html'], ['src/styles.css', 'dist/styles.css'], ['favicon.svg', 'dist/favicon.svg']];

await mkdir('dist', { recursive: true });
for (const [from, to] of STATIC) await copyFile(from, to);
if (watch) {
  await (await context(options)).watch();
  // esbuild only watches the code, so copy the page and styles again whenever they change.
  for (const [from, to] of STATIC) {
    watchFile(from, () => copyFile(from, to).then(() => console.log(`[watch] copied ${from}`)).catch(() => {}));
  }
} else {
  await build(options);
}
