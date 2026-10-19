// Сборка: esbuild-бандл (JS + CSS, всё локально, без CDN) + копия index.html -> web/dist.
// Сборка идёт во временную папку dist.build, затем файлы подменяются в dist по одному (запись во временное имя + rename),
// так что работающий сервер никогда не видит отсутствующую dist или наполовину записанный файл.
import { build, context } from "esbuild";
import {
  copyFileSync, mkdirSync, readdirSync, renameSync, rmSync, unlinkSync,
} from "node:fs";
import { join } from "node:path";

const watch = process.argv.includes("--watch");
const OUT = "dist";
const TMP = "dist.build";

const sleepSync = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);

/** Подмена одного файла: temp-копия рядом + rename (на Windows rename может не пройти, пока файл читают — повторяем). */
function replaceFile(src, dst) {
  const tmp = dst + ".tmp";
  copyFileSync(src, tmp);
  for (let i = 0; i < 20; i++) {
    try { renameSync(tmp, dst); return; } catch { sleepSync(50); }
  }
  copyFileSync(src, dst);   // крайний случай: перезапись на месте
  try { unlinkSync(tmp); } catch { /* ignore */ }
}

/** Переносит содержимое TMP в OUT: сначала ассеты, index.html последним; лишние файлы в OUT удаляются. */
function swap() {
  mkdirSync(OUT, { recursive: true });
  const files = readdirSync(TMP).sort((a, b) => (a === "index.html") - (b === "index.html"));
  for (const f of files) replaceFile(join(TMP, f), join(OUT, f));
  for (const f of readdirSync(OUT)) if (!files.includes(f)) rmSync(join(OUT, f), { recursive: true, force: true });
  rmSync(TMP, { recursive: true, force: true });
}

rmSync(TMP, { recursive: true, force: true });
mkdirSync(TMP, { recursive: true });

const opts = {
  entryPoints: { app: "src/main.ts" },
  bundle: true,
  outdir: TMP,
  format: "esm",
  target: "es2022",
  minify: !watch,
  sourcemap: watch,
  loader: { ".css": "css" },
  logLevel: "info",
  plugins: watch ? [{
    name: "swap-on-rebuild",
    setup(b) {
      b.onEnd((r) => {
        if (r.errors.length) return;
        copyFileSync("src/index.html", join(TMP, "index.html"));
        for (const f of readdirSync(TMP)) replaceFile(join(TMP, f), join(OUT, f));
      });
    },
  }] : [],
};

if (watch) {
  mkdirSync(OUT, { recursive: true });
  const ctx = await context(opts);
  await ctx.watch();
} else {
  await build(opts);
  copyFileSync("src/index.html", join(TMP, "index.html"));
  swap();
}
