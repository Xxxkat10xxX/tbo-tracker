// Рендер ролика из promo/launch.html.
//   node render.js beats            — по кадру на каждую долю (120 BPM) + контактный лист
//   node render.js at 2.0 2.25 ...  — отдельные кадры
//   node render.js audio            — саундтрек (OfflineAudioContext) → out/soundtrack.wav
//   node render.js full             — 60 fps, 3 субкадра на кадр → ffmpeg tmix → MP4 со звуком
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const OUT = path.join(ROOT, 'out');
const HTML = 'file://' + path.join(ROOT, 'launch.html');
const FPS = 60, SUB = 3, DUR = 15;
fs.mkdirSync(OUT, { recursive: true });

async function open() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(HTML);
  await page.waitForFunction(() => window.__ready === true, null, { timeout: 30000 });
  return { browser, page, errors };
}
const shot = async (page, t) => { await page.evaluate((x) => window.seek(x), t); return page.screenshot({ type: 'png' }); };

async function frames(times, dir) {
  fs.mkdirSync(dir, { recursive: true });
  const { browser, page, errors } = await open();
  for (const [i, t] of times.entries()) fs.writeFileSync(path.join(dir, `f${String(i).padStart(2, '0')}_${t.toFixed(2)}.png`), await shot(page, t));
  await browser.close();
  if (errors.length) console.log('ОШИБКИ:', errors);
}

async function audio() {
  const { browser, page, errors } = await open();
  const b64 = await page.evaluate(() => window.renderSoundtrack());
  fs.writeFileSync(path.join(OUT, 'soundtrack.wav'), Buffer.from(b64, 'base64'));
  await browser.close();
  if (errors.length) console.log('ОШИБКИ:', errors);
  console.log('soundtrack.wav готов');
}

async function full() {
  const wav = path.join(OUT, 'soundtrack.wav');
  if (!fs.existsSync(wav)) await audio();
  const silent = path.join(OUT, 'video_noaudio.mp4');
  // субкадры t = k/60 + j/180: склейки (кратные 1/60) никогда не попадают внутрь одного кадра
  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS * SUB), '-c:v', 'png', '-i', '-',
    '-vf', `tmix=frames=${SUB}:weights='1 1 1',select='eq(mod(n\\,${SUB})\\,${SUB - 1})',setpts=N/(${FPS}*TB)`,
    '-r', String(FPS), '-c:v', 'libx264', '-preset', 'slow', '-crf', '15', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', silent],
  { stdio: ['pipe', 'inherit', 'inherit'] });
  const done = new Promise((res, rej) => ff.on('close', (c) => (c === 0 ? res() : rej(new Error('ffmpeg ' + c)))));
  const { browser, page, errors } = await open();
  const total = FPS * DUR;
  const t0 = Date.now();
  for (let k = 0; k < total; k++) {
    for (let j = 0; j < SUB; j++) {
      const buf = await shot(page, k / FPS + j / (FPS * SUB));
      if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
    }
    if (k % 60 === 0) console.log(`кадр ${k}/${total}  ${((Date.now() - t0) / 1000).toFixed(0)} с`);
  }
  ff.stdin.end();
  await done;
  await browser.close();
  if (errors.length) console.log('ОШИБКИ:', errors);
  const final = path.join(ROOT, 'tbo-tracker-launch.mp4');
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', silent, '-i', wav, '-c:v', 'copy', '-c:a', 'aac', '-b:a', '256k',
    '-shortest', '-movflags', '+faststart', final]);
  console.log('готово:', final);
}

(async () => {
  const [mode, ...rest] = process.argv.slice(2);
  if (mode === 'beats') {
    const times = Array.from({ length: 30 }, (_, k) => k * 0.5 + 0.4);
    const dir = path.join(OUT, 'beats');
    await frames(times, dir);
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-pattern_type', 'glob', '-i', path.join(dir, 'f*.png'),
      '-vf', 'scale=480:-1,tile=5x6:padding=6:color=white', '-frames:v', '1', path.join(OUT, 'beats_sheet.png')]);
    console.log('контактный лист: out/beats_sheet.png');
  } else if (mode === 'at') {
    await frames(rest.map(Number), path.join(OUT, 'at'));
  } else if (mode === 'audio') await audio();
  else if (mode === 'full') await full();
  else if (mode !== 'blend') console.log('режим: beats | at t… | audio | full | blend k…');
})();

// node render.js blend 134 228 381 … — кадры итогового вида (3 субкадра → mix), без полного рендера
if (process.argv[2] === 'blend') (async () => {
  const dir = path.join(OUT, 'blend'); fs.mkdirSync(dir, { recursive: true });
  const { browser, page, errors } = await open();
  for (const k of process.argv.slice(3).map(Number)) {
    const subs = [];
    for (let j = 0; j < SUB; j++) { const f = path.join(dir, `s${k}_${j}.png`); fs.writeFileSync(f, await shot(page, k / FPS + j / (FPS * SUB))); subs.push(f); }
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', ...subs.flatMap((s) => ['-i', s]), '-filter_complex', `mix=inputs=${SUB}`, path.join(dir, `k${String(k).padStart(3, '0')}.png`)]);
    subs.forEach((s) => fs.unlinkSync(s));
  }
  await browser.close();
  if (errors.length) console.log('ОШИБКИ:', errors);
})();
