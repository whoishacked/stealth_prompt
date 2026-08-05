/**
 * Build the unpacked extension into dist/.
 *
 * esbuild rather than a bundler with a plugin ecosystem: the output must be
 * auditable, and every byte that ships should be traceable to source in this
 * repository. No CDN, no remote code, no eval.
 */
import { build } from 'esbuild';
import { cp, mkdir, rm } from 'node:fs/promises';

const outdir = 'dist';
await rm(outdir, { recursive: true, force: true });
await mkdir(outdir, { recursive: true });

const common = {
  outdir,
  bundle: true,
  target: 'chrome116',
  platform: 'browser',
  sourcemap: false,
  minify: false,
  legalComments: 'none',
};

// The worker is declared `"type": "module"`, and the panel loads through a
// <script type="module"> tag, so both are ES modules.
await build({
  ...common,
  entryPoints: {
    'service-worker': 'src/service-worker/worker.ts',
    sidepanel: 'src/sidepanel/panel.ts',
  },
  format: 'esm',
});

// The content script is different: `chrome.scripting.executeScript({ files })`
// injects a *classic* script, so an ESM bundle would fail to parse on its
// `export` statement, the listener would never register, and every page
// operation would come back as "Receiving end does not exist". Build it as an
// IIFE, which also keeps its internals off the page's global object.
await build({
  ...common,
  entryPoints: { content: 'src/content/executor.ts' },
  format: 'iife',
});

await cp('manifest.json', `${outdir}/manifest.json`);
await cp('src/sidepanel/sidepanel.html', `${outdir}/sidepanel.html`);
await cp('src/sidepanel/sidepanel.css', `${outdir}/sidepanel.css`);
await mkdir(`${outdir}/icons`, { recursive: true });
for (const size of [16, 32, 48, 128]) {
  await cp(`icons/icon-${size}.png`, `${outdir}/icons/icon-${size}.png`);
}

console.log(`built unpacked extension into ${outdir}/`);
