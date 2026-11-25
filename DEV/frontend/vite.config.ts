import { readdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin } from 'vite';

const WASM_DIR = join(dirname(createRequire(import.meta.url).resolve('pdfjs-dist/package.json')), 'wasm');
const WASM_PATH = 'pdfjs-wasm';
const typeOf = (name: string) => (name.endsWith('.wasm') ? 'application/wasm' : name.endsWith('.js') ? 'text/javascript' : 'text/plain');

/** Serves PDF.js decoders (JPEG2000, JBIG2, colour) from this origin, so no page depends on a CDN. */
function pdfjsWasm(): Plugin {
  return {
    name: 'pdfjs-wasm',
    configureServer(server) {
      server.middlewares.use(`${server.config.base.replace(/\/$/, '')}/${WASM_PATH}`, (req, res, next) => {
        const name = decodeURIComponent((req.url ?? '').split('?')[0]!.replace(/^\//, ''));
        if (!/^[\w.]+$/.test(name) || !readdirSync(WASM_DIR).includes(name)) return next();
        res.setHeader('content-type', typeOf(name));
        res.end(readFileSync(join(WASM_DIR, name)));
      });
    },
    generateBundle() {
      for (const name of readdirSync(WASM_DIR)) {
        this.emitFile({ type: 'asset', fileName: `${WASM_PATH}/${name}`, source: readFileSync(join(WASM_DIR, name)) });
      }
    },
  };
}

export default defineConfig({
  base: process.env.VITE_BASE ?? '/',
  plugins: [react(), pdfjsWasm()],
  server: {
    port: 5173,
    // Let students reach the dev server through an ngrok tunnel.
    allowedHosts: ['.ngrok-free.app', '.ngrok.app'],
    proxy: { '/api': process.env.API_URL ?? 'http://127.0.0.1:3000' },
  },
});
