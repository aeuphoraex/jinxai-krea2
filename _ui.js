
const $ = (id) => document.getElementById(id);
const state = { ratio: "1:1", base: 1328, mode: "t2i", frames: 81, quality: 480, upscale: 0 };
const timeline = [];
let initData = null;   // data URL of the init frame
let initRatio = 1;

const RATIOS = { "1:1": 1, "4:5": 0.8, "3:4": 0.75, "16:9": 16 / 9, "9:16": 9 / 16, "21:9": 21 / 9 };
const snap16 = (v) => Math.max(512, Math.min(2048, Math.round(v / 16) * 16));

function sizeFor() {
  const r = state.mode === "i2i" && initData ? initRatio : RATIOS[state.ratio];
  let w = state.base * Math.sqrt(r);
  let h = state.base / Math.sqrt(r);
  const m = Math.max(w, h);
  if (m > 2048) { w = w * 2048 / m; h = h * 2048 / m; }
  return { width: snap16(w), height: snap16(h) };
}
const snap32 = (v) => Math.max(256, Math.round(v / 32) * 32);
function videoDims() {
  const long = state.quality >= 720 ? 1280 : 832;
  let w = initRatio >= 1 ? long : long * initRatio;
  let h = initRatio >= 1 ? long / initRatio : long;
  w = snap32(w); h = snap32(h);
  const budget = 1280 * 704;
  if (w * h > budget) { const k = Math.sqrt(budget / (w * h)); w = snap32(w * k); h = snap32(h * k); }
  return { width: w, height: h };
}
function paintSize() {
  if (state.mode === "i2v") {
    if (!initData) { $("sizeLabel").textContent = "add a frame"; return; }
    const { width, height } = videoDims();
    $("sizeLabel").textContent = `${width} × ${height}`;
    return;
  }
  const { width, height } = sizeFor();
  $("sizeLabel").textContent = `${width} × ${height}`;
}

function setMode(m, reset = true) {
  state.mode = m;
  const video = m === "i2v";
  [...$("tabs").children].forEach((b) => b.classList.toggle("on", b.dataset.m === m));
  $("panel-init").style.display = m === "t2i" ? "none" : "block";
  $("panel-video").style.display = video ? "block" : "none";
  $("panel-stitch").style.display = video ? "block" : "none";
  $("panel-frame").style.display = video ? "none" : "block";
  $("kreaOnly").style.display = video ? "none" : "block";
  $("strengthWrap").style.display = m === "i2i" ? "block" : "none";
  $("initHint").textContent = video
    ? "The clip begins on this frame, so that frame is what the video depicts."
    : "Krea 2 encodes this frame and re-denoises it from the strength below.";
  $("steps").min = video ? 8 : 4;
  $("steps").max = video ? 50 : 20;
  $("cfg").min = video ? 1 : 0;
  $("cfg").max = video ? 10 : 5;
  if (reset) {
    const st = video ? 24 : 8, cf = video ? 5 : 0;
    $("steps").value = st; $("stepsVal").textContent = st;
    $("cfg").value = cf; $("cfgVal").textContent = Number(cf).toFixed(1);
  }
  $("stepsHint").textContent = video
    ? "Wan 2.2 is not distilled: 20–30 steps is the useful range, CFG around 5."
    : "8 steps / CFG 0 is what Turbo was distilled for. Raising CFG trades speed for prompt obedience (and can burn highlights).";
  $("goLabel").textContent = video ? "Make video" : m === "i2i" ? "Re-render" : "Generate";
  paintSize();
  persist();
}

function chipGroup(el, attr, key) {
  el.addEventListener("click", (e) => {
    const b = e.target.closest(".chip");
    if (!b) return;
    [...el.children].forEach((c) => c.classList.remove("on"));
    b.classList.add("on");
    state[key] = attr === "data-b" ? Number(b.dataset.b) : b.dataset.r;
    paintSize();
    persist();
  });
}
chipGroup($("ratios"), "data-r", "ratio");
chipGroup($("resos"), "data-b", "base");

$("tabs").addEventListener("click", (e) => {
  const b = e.target.closest(".tab");
  if (b) setMode(b.dataset.m);
});
$("framesCh").addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (!b) return;
  [...$("framesCh").children].forEach((c) => c.classList.remove("on"));
  b.classList.add("on");
  state.frames = Number(b.dataset.f);
  $("durLabel").textContent = `${state.frames} frames · ${(state.frames / 24).toFixed(1)} s`;
  paintSize();
  persist();
});
$("upsCh").addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (!b) return;
  [...$("upsCh").children].forEach((c) => c.classList.remove("on"));
  b.classList.add("on");
  state.upscale = Number(b.dataset.u);
  $("upLabel").textContent = state.upscale ? `4K · ${state.upscale}px` : "native";
  persist();
});
$("qualityCh").addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (!b) return;
  [...$("qualityCh").children].forEach((c) => c.classList.remove("on"));
  b.classList.add("on");
  state.quality = Number(b.dataset.q);
  $("qLabel").textContent = `${state.quality}p`;
  paintSize();
  persist();
});

$("prompt").addEventListener("input", (e) => { $("chars").textContent = e.target.value.length; persist(); });
$("steps").addEventListener("input", (e) => { $("stepsVal").textContent = e.target.value; persist(); });
$("cfg").addEventListener("input", (e) => { $("cfgVal").textContent = Number(e.target.value).toFixed(1); persist(); });
$("strength").addEventListener("input", (e) => { $("strengthVal").textContent = Number(e.target.value).toFixed(2); persist(); });
["seed", "count", "variant", "encoder", "negative", "enhance", "styleLora"].forEach((id) =>
  $(id).addEventListener("change", persist)
);
$("styleLora").addEventListener("change", () => {
  const spec = loraCatalog[$("styleLora").value];
  if (spec && spec.default_scale != null) {
    $("styleScale").value = spec.default_scale;
    $("styleVal").textContent = Number(spec.default_scale).toFixed(2);
  }
  syncStyle();
  persist();
});
$("styleGet").addEventListener("click", getLora);
$("vlora").addEventListener("change", () => {
  const spec = videoLoraCatalog[$("vlora").value];
  if (spec && spec.default_scale != null) {
    $("vloraScale").value = spec.default_scale;
    $("vloraVal").textContent = Number(spec.default_scale).toFixed(2);
  }
  syncVideoLora();
  persist();
});
$("vloraScale").addEventListener("input", (e) => {
  $("vloraVal").textContent = Number(e.target.value).toFixed(2);
  persist();
});
$("styleAll").addEventListener("click", getAllLoras);
$("saveToken").addEventListener("click", saveToken);
$("styleScale").addEventListener("input", (e) => {
  $("styleVal").textContent = Number(e.target.value).toFixed(2);
  persist();
});
$("dice").addEventListener("click", () => {
  $("seed").value = Math.floor(Math.random() * 2 ** 31);
  persist();
});

function ingest(file) {
  if (!file || !file.type.startsWith("image/")) return;
  const fr = new FileReader();
  fr.onload = () => {
    const im = new Image();
    im.onload = () => {
      // 1280 on the long side keeps the JSON payload small without hurting the result.
      const k = Math.min(1, 1280 / Math.max(im.width, im.height));
      const c = document.createElement("canvas");
      c.width = Math.round(im.width * k);
      c.height = Math.round(im.height * k);
      c.getContext("2d").drawImage(im, 0, 0, c.width, c.height);
      initData = c.toDataURL("image/jpeg", 0.95);
      initRatio = c.width / c.height;
      $("initPreview").src = initData;
      $("drop").classList.add("has");
      $("initMeta").textContent = `${c.width}×${c.height}`;
      paintSize();
    };
    im.src = fr.result;
  };
  fr.readAsDataURL(file);
}
$("file").addEventListener("change", (e) => ingest(e.target.files[0]));
$("drop").addEventListener("dragover", (e) => { e.preventDefault(); $("drop").classList.add("over"); });
$("drop").addEventListener("dragleave", () => $("drop").classList.remove("over"));
$("drop").addEventListener("drop", (e) => {
  e.preventDefault();
  $("drop").classList.remove("over");
  ingest(e.dataTransfer.files[0]);
});
document.addEventListener("paste", (e) => {
  const f = [...((e.clipboardData && e.clipboardData.files) || [])].find((x) => x.type.startsWith("image/"));
  if (f) ingest(f);
});

function persist() {
  const s = {
    prompt: $("prompt").value, ratio: state.ratio, base: state.base,
    steps: $("steps").value, cfg: $("cfg").value, seed: $("seed").value,
    count: $("count").value, variant: $("variant").value, encoder: $("encoder").value,
    negative: $("negative").value, enhance: $("enhance").checked, lora: $("lora").checked,
    styleLora: $("styleLora").value, styleScale: $("styleScale").value, stack: loraStack,
    mode: state.mode, strength: $("strength").value,
    frames: state.frames, quality: state.quality, upscale: state.upscale,
    vmodel: $("vmodel").value,
    vlora: $("vlora").value, vloraScale: $("vloraScale").value,
  };
  localStorage.setItem("krea2", JSON.stringify(s));
}
function restore() {
  let s = null;
  try { s = JSON.parse(localStorage.getItem("krea2") || "null"); } catch (e) {}
  if (!s) { paintSize(); return; }
  if (s.prompt) { $("prompt").value = s.prompt; $("chars").textContent = s.prompt.length; }
  if (s.ratio && RATIOS[s.ratio]) {
    state.ratio = s.ratio;
    [...$("ratios").children].forEach((c) => c.classList.toggle("on", c.dataset.r === s.ratio));
  }
  if (s.base) {
    state.base = Number(s.base);
    [...$("resos").children].forEach((c) => c.classList.toggle("on", Number(c.dataset.b) === state.base));
  }
  if (s.steps) { $("steps").value = s.steps; $("stepsVal").textContent = s.steps; }
  if (s.cfg !== undefined) { $("cfg").value = s.cfg; $("cfgVal").textContent = Number(s.cfg).toFixed(1); }
  if (s.seed !== undefined) $("seed").value = s.seed;
  if (s.count) $("count").value = s.count;
  if (s.variant) $("variant").value = s.variant;
  if (s.encoder) $("encoder").value = s.encoder;
  if (s.negative) $("negative").value = s.negative;
  if (s.enhance) $("enhance").checked = true;
  if (s.lora === false) $("lora").checked = false;
  if (s.styleLora) {
    const opt = document.createElement("option");
    opt.value = s.styleLora;
    opt.textContent = s.styleLora;
    $("styleLora").appendChild(opt);
    $("styleLora").value = s.styleLora;
  }
  if (s.styleScale) {
    $("styleScale").value = s.styleScale;
    $("styleVal").textContent = Number(s.styleScale).toFixed(2);
  }
  loraStack = Array.isArray(s.stack) ? s.stack.filter((it) => it && it.name) : [];
  if (s.strength) { $("strength").value = s.strength; $("strengthVal").textContent = Number(s.strength).toFixed(2); }
  if (s.frames) {
    state.frames = Number(s.frames);
    [...$("framesCh").children].forEach((c) => c.classList.toggle("on", Number(c.dataset.f) === state.frames));
    $("durLabel").textContent = `${state.frames} frames · ${(state.frames / 24).toFixed(1)} s`;
  }
  if (s.quality) {
    state.quality = Number(s.quality);
    [...$("qualityCh").children].forEach((c) => c.classList.toggle("on", Number(c.dataset.q) === state.quality));
    $("qLabel").textContent = `${state.quality}p`;
  }
  if (s.vmodel) $("vmodel").value = s.vmodel;
  if (s.vlora) {
    const opt = document.createElement("option");
    opt.value = s.vlora;
    opt.textContent = s.vlora;
    $("vlora").appendChild(opt);
    $("vlora").value = s.vlora;
  }
  if (s.vloraScale) {
    $("vloraScale").value = s.vloraScale;
    $("vloraVal").textContent = Number(s.vloraScale).toFixed(2);
  }
  if (s.upscale) {
    state.upscale = Number(s.upscale);
    [...$("upsCh").children].forEach((c) => c.classList.toggle("on", Number(c.dataset.u) === state.upscale));
    $("upLabel").textContent = state.upscale ? `4K · ${state.upscale}px` : "native";
  }
  setMode(s.mode || "t2i", false);
  paintSize();
}

$("lightbox").addEventListener("click", () => $("lightbox").classList.remove("on"));
$("results").addEventListener("click", (e) => {
  const img = e.target.closest("img.shot");
  if (!img) return;
  $("lightbox").querySelector("img").src = img.src;
  $("lightbox").classList.add("on");
});

function fail(msg) {
  $("status").className = "status err";
  $("status").textContent = msg;
}

async function runVideo() {
  const prompt = $("prompt").value.trim();
  if (!initData) { fail("load an init frame first — video is image → video."); return; }
  if (!prompt) { $("prompt").focus(); return; }
  const { width, height } = videoDims();
  const payload = {
    prompt,
    negative_prompt: $("negative").value,
    init_image: initData,
    frames: state.frames,
    quality: state.quality,
    model: $("vmodel").value,
    lora: $("vlora").value,
    lora_scale: Number($("vloraScale").value),
    steps: Number($("steps").value),
    cfg: Number($("cfg").value),
    fps: 24,
    seed: Number($("seed").value),
  };

  $("go").disabled = true;
  $("go").classList.add("busy");
  $("goLabel").textContent = "Rendering…";
  $("empty").style.display = "none";
  $("promptbox").innerHTML = "";
  $("results").innerHTML = '<div class="skeleton" style="aspect-ratio:16/9"></div>';

  const t0 = performance.now();
  const tick = setInterval(() => {
    const s = ((performance.now() - t0) / 1000).toFixed(0);
    $("status").className = "status";
    $("status").textContent = `${s}s · ${width}×${height} · ${state.frames} frames @ ${payload.steps} steps` +
      (s > 30 ? " · a clip takes minutes; the first one also loads the 5B model" : "");
  }, 500);

  try {
    const res = await fetch("/api/video", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    clearInterval(tick);
    if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
    renderVideo(data);
    timeline.push(data);
    paintTimeline();
    const total = ((performance.now() - t0) / 1000).toFixed(1);
    $("status").className = "status";
    $("status").innerHTML = `<b>done</b> · ${total}s wall · ${data.width}×${data.height} · ${data.frames}f @ ${data.fps}fps · ${data.seconds}s render`;
  } catch (err) {
    clearInterval(tick);
    $("results").innerHTML = "";
    $("empty").style.display = "block";
    fail("failed · " + err.message);
  } finally {
    $("go").disabled = false;
    $("go").classList.remove("busy");
    $("goLabel").textContent = "Make video";
  }
}

function renderVideo(data) {
  const url = `data:video/mp4;base64,${data.video}`;
  $("results").innerHTML = `<div class="card wide">
    <video id="clip" src="${url}" poster="data:image/jpeg;base64,${data.thumb}" controls autoplay loop muted playsinline></video>
    <div class="meta"><span>${data.width}×${data.height}</span><span>${data.frames}f @ ${data.fps}fps</span>
      <span>seed ${data.seed}</span><span>${data.seconds}s</span>
      <a download="krea2_${data.seed}.mp4" href="${url}">mp4</a>
    </div></div>`;
}

async function run() {
  if (state.mode === "i2v") return runVideo();
  const prompt = $("prompt").value.trim();
  if (!prompt) { $("prompt").focus(); return; }
  if (state.mode === "i2i" && !initData) {
    fail("load an init image first — image → image needs a frame.");
    return;
  }
  const { width, height } = sizeFor();
  const count = Number($("count").value);
  const payload = {
    prompt,
    negative_prompt: $("negative").value,
    width, height,
    steps: Number($("steps").value),
    cfg: Number($("cfg").value),
    seed: Number($("seed").value),
    num_images: count,
    variant: $("variant").value,
    text_encoder: $("encoder").value,
    enhance: $("enhance").checked,
    init_image: state.mode === "i2i" ? initData : undefined,
    strength: Number($("strength").value),
    upscale: state.upscale,
    lora: $("lora").checked,
    style_lora: $("styleLora").value,
    style_scale: Number($("styleScale").value),
    style_loras: loraStack.map((it) => ({ name: it.name, scale: Number(it.scale) })),
  };

  $("go").disabled = true;
  $("go").classList.add("busy");
  $("goLabel").textContent = "Rendering…";
  $("empty").style.display = "none";
  $("promptbox").innerHTML = "";
  $("results").innerHTML = new Array(count).fill('<div class="skeleton"></div>').join("");

  const t0 = performance.now();
  const tick = setInterval(() => {
    const s = ((performance.now() - t0) / 1000).toFixed(0);
    $("status").className = "status";
    $("status").textContent = `${s}s · ${width}×${height} · ${payload.steps} steps` +
      (s > 25 ? " · cold start can take ~2 min (weights + compile)" : "");
  }, 400);

  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
    clearInterval(tick);
    render(data);
    const total = ((performance.now() - t0) / 1000).toFixed(1);
    $("status").className = "status";
    $("status").innerHTML = `<b>done</b> · ${total}s wall · ${data.elapsed}s in worker · ${data.variant} / ${data.text_encoder}` +
      (data.enhanced ? " · prompt expanded" : "") +
      ((data.style_loras || []).length
        ? ` · ${data.style_loras.map((l) => `${l.name} @ ${l.scale}`).join(" + ")} (${data.style_adapters} layers)`
        : "");
  } catch (err) {
    clearInterval(tick);
    $("results").innerHTML = "";
    $("empty").style.display = "block";
    $("status").className = "status err";
    $("status").textContent = "failed · " + err.message;
  } finally {
    $("go").disabled = false;
    $("go").classList.remove("busy");
    $("goLabel").textContent = state.mode === "i2v" ? "Make video" : state.mode === "i2i" ? "Re-render" : "Generate";
  }
}
$("go").addEventListener("click", run);
$("prompt").addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") run();
});

function render(data) {
  if (data.enhanced && data.prompt) {
    $("promptbox").innerHTML =
      `<div class="promptbox"><div class="lbl">expanded prompt</div>${escapeHtml(data.prompt)}</div>`;
  }
  $("results").innerHTML = data.images
    .map(
      (im, i) => `<div class="card" style="animation-delay:${i * 40}ms">
        <img class="shot" src="data:image/png;base64,${im.data}" alt="generated image" loading="lazy" />
        <div class="meta"><span>${im.width}×${im.height}</span>${data.mode === "img2img" ? `<span>str ${data.strength}</span>` : ""}<span>seed ${im.seed}</span>
          <span>${im.seconds}s</span>
          ${loraSlot(data, i)}
          <a download="krea2_${im.seed}.png" href="data:image/png;base64,${im.data}">png</a>
        </div></div>`
    )
    .join("");
  (data.lora_jobs || []).forEach(watchLora);
}
function loraSlot(data, i) {
  const job = (data.lora_jobs || []).find((j) => j.index === i);
  return job ? `<span class="lorajob" data-lora="${job.id}">lora …</span>` : "";
}

async function watchLora(job) {
  const el = document.querySelector(`[data-lora="${job.id}"]`);
  if (!el) return;
  for (let i = 0; i < 120; i++) {
    let d = null;
    try {
      const r = await fetch(`/api/lora/${job.id}`);
      d = await r.json();
    } catch (e) {}
    if (d && d.status === "ready") {
      el.innerHTML = `<a download="krea2_${job.seed}.safetensors" href="${d.url}">` +
        `lora ${d.rank}r · ${(d.bytes / 1e6).toFixed(1)} MB</a>`;
      return;
    }
    if (d && d.status === "error") { el.textContent = "lora failed"; return; }
    await new Promise((r) => setTimeout(r, 4000));
  }
  el.textContent = "lora timed out";
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function paintTimeline() {
  const total = timeline.reduce((a, c) => a + c.frames / c.fps, 0);
  $("tlMeta").textContent = `${timeline.length} clip${timeline.length === 1 ? "" : "s"} · ${total.toFixed(1)} s`;
  $("tlEmpty").style.display = timeline.length ? "none" : "block";
  [...$("timeline").querySelectorAll(".tlitem")].forEach((n) => n.remove());
  timeline.forEach((c, i) => {
    const el = document.createElement("div");
    el.className = "tlitem";
    el.innerHTML = `<img src="data:image/jpeg;base64,${c.thumb}" alt="clip ${i + 1}" />` +
      `<button data-i="${i}" title="remove">×</button>`;
    $("timeline").appendChild(el);
  });
}

$("timeline").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-i]");
  if (!b) return;
  timeline.splice(Number(b.dataset.i), 1);
  paintTimeline();
});

$("extendBtn").addEventListener("click", async () => {
  if (!timeline.length) { fail("render a clip first, then extend it."); return; }
  const last = timeline[timeline.length - 1];
  initData = `data:image/jpeg;base64,${last.last_frame}`;
  $("initPreview").src = initData;
  $("drop").classList.add("has");
  const im = new Image();
  im.onload = () => {
    initRatio = im.width / im.height;
    $("initMeta").textContent = `${im.width}×${im.height}`;
    paintSize();
  };
  im.src = initData;
  await runVideo();
});

$("stitchBtn").addEventListener("click", async () => {
  if (timeline.length < 2) { fail("stitch needs at least two clips."); return; }
  $("go").disabled = true;
  $("status").className = "status";
  $("status").textContent = `stitching ${timeline.length} clips…`;
  try {
    const res = await fetch("/api/video/stitch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clips: timeline.map((c) => c.video), fps: timeline[0].fps || 24 }),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
    const url = `data:video/mp4;base64,${data.video}`;
    $("results").innerHTML = `<div class="card wide">
      <video src="${url}" controls autoplay loop muted playsinline></video>
      <div class="meta"><span>stitched ${data.clips} clips</span><span>${data.width}×${data.height}</span>
        <span>${data.fps} fps</span><span>${data.mb} MB</span>
        <a download="krea2_stitch.mp4" href="${url}">mp4</a></div></div>`;
    $("status").className = "status";
    $("status").innerHTML = `<b>stitched</b> · ${data.clips} clips · ${data.mb} MB`;
  } catch (err) {
    fail("stitch failed · " + err.message);
  } finally {
    $("go").disabled = false;
  }
});

var loraStack = [];
restore();
let loraCatalog = {};
function paintLoras(d) {
  loraCatalog = d.custom_loras || {};
  const sel = $("styleLora");
  const keep = sel.value;
  sel.innerHTML = '<option value="">none</option>';
  Object.entries(loraCatalog).forEach(([name, v]) => {
    const opt = document.createElement("option");
    opt.value = name;
    const trig = v.trigger ? ` · "${v.trigger}"` : "";
    opt.textContent = (v.available ? v.label : `${v.label} (server will fetch)`) + trig;
    sel.appendChild(opt);
  });
  if (keep) sel.value = keep;
  paintVideoLoras(d.video_loras || {});
  syncStyle();
  paintStack();
}

let videoLoraCatalog = {};
function paintVideoLoras(cat) {
  videoLoraCatalog = cat || {};
  const sel = $("vlora");
  const keep = sel.value;
  sel.innerHTML = '<option value="">none</option>';
  Object.entries(videoLoraCatalog).forEach(([name, v]) => {
    const opt = document.createElement("option");
    opt.value = name;
    const wrongBase = v.models && v.models.length === 0;
    opt.textContent = `${v.label}${v.available ? "" : " (server will fetch)"}${wrongBase ? " !" : ""}`;
    sel.appendChild(opt);
  });
  if (keep) sel.value = keep;
  syncVideoLora();
}
function syncVideoLora() {
  const name = $("vlora").value;
  const spec = videoLoraCatalog[name];
  $("vloraWrap").style.display = name ? "block" : "none";
  $("vloraHint").textContent = !name
    ? ""
    : spec && spec.models && !spec.models.length
      ? `trained for ${spec.base}: this pipeline will refuse it`
      : spec && !spec.available
        ? "not in the volume yet: the server downloads it on first use"
        : "";
}
function syncStyle() {
  const v = $("styleLora").value;
  $("styleWrap").style.display = v || loraStack.length ? "block" : "none";
  const ready = v && loraCatalog[v] && loraCatalog[v].available;
  $("styleGet").style.display = v && !ready ? "inline-block" : "none";
}
function paintStack() {
  const box = $("loraStack");
  if (!box) return;
  box.innerHTML = loraStack.length
    ? loraStack
        .map(
          (it, i) =>
            `<span class="chip" data-i="${i}">${escapeHtml(it.name)} @ ${Number(it.scale).toFixed(2)}<b data-rm="${i}" style="margin-left:6px">×</b></span>`
        )
        .join("")
    : '<span class="hint" style="margin:0">no LoRA stacked — generate with one, or add several to combine them</span>';
  persist();
}
function addLora() {
  const name = $("styleLora").value;
  if (!name) return;
  const scale = Number($("styleScale").value);
  const at = loraStack.findIndex((it) => it.name === name);
  if (at >= 0) loraStack[at].scale = scale;
  else loraStack.push({ name, scale });
  paintStack();
  syncStyle();
}
$("styleAdd").addEventListener("click", addLora);
$("loraStack").addEventListener("click", (e) => {
  const rm = e.target.closest("[data-rm]");
  if (rm) {
    loraStack.splice(Number(rm.dataset.rm), 1);
    paintStack();
    return;
  }
  const chip = e.target.closest("[data-i]");
  if (chip) {
    const it = loraStack[Number(chip.dataset.i)];
    if (!it) return;
    $("styleLora").value = it.name;
    $("styleScale").value = it.scale;
    $("styleVal").textContent = Number(it.scale).toFixed(2);
    syncStyle();
    persist();
  }
});
async function getLora() {
  const name = $("styleLora").value;
  if (!name) return;
  const btn = $("styleGet");
  btn.disabled = true;
  btn.textContent = "downloading…";
  $("styleMsg").textContent = "the server is fetching the weights — about a minute";
  try {
    const r = await fetch("/api/lora/download", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const d = await r.json();
    if (d.error) {
      $("styleMsg").textContent = d.error;
    } else {
      paintLoras(await (await fetch("/api/info")).json());
      $("styleMsg").textContent = `${d.file} ready (${d.mb} MB${d.cached ? ", cached" : ""})`;
    }
  } catch (e) {
    $("styleMsg").textContent = "download failed: " + e;
  } finally {
    btn.disabled = false;
    btn.textContent = "Download LoRA";
    syncStyle();
  }
}

async function saveToken() {
  const token = $("civitaiToken").value.trim();
  try {
    const r = await fetch("/api/lora/token", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const d = await r.json();
    $("styleMsg").textContent = d.error
      ? d.error
      : d.stored
        ? `token stored (${d.chars} chars) — press Download LoRA`
        : "token cleared";
    if (d.stored) $("civitaiToken").value = "";
  } catch (e) {
    $("styleMsg").textContent = "could not save the token: " + e;
  }
}

async function getAllLoras() {
  $("styleMsg").textContent = "the server is downloading every registry LoRA — a few minutes";
  try {
    const r = await fetch("/api/lora/download_all", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}",
    });
    const d = await r.json();
    paintLoras(await (await fetch("/api/info")).json());
    if (d.error) {
      $("styleMsg").textContent = d.error;
    } else {
      const lines = Object.entries(d).map(([k, v]) =>
        v.error ? `${k}: ${v.error}` : `${k}: ${v.mb} MB`
      );
      $("styleMsg").textContent = lines.join(" · ");
    }
  } catch (e) {
    $("styleMsg").textContent = "download failed: " + e;
  }
}

fetch("/api/info")
  .then((r) => r.json())
  .then((d) => {
    if (d.gpu) $("gpu").textContent = `${d.gpu} · ${d.vram_gb} GB` + (d.loaded ? ` · ${d.loaded[0]} loaded` : " · cold");
    paintLoras(d);
  })
  .catch(() => {});
