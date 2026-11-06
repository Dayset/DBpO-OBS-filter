/*
 * DBpO Unsquish: finds the page's main <video>, hides it, and draws it into a
 * WebGL canvas through the inverse squish mapping (squish.js). The canvas sits
 * in the video's own container, so it follows the player into fullscreen.
 *
 * Config comes from the popup (pasted FSQ1 string) or, with auto-detect on,
 * from an "FSQ1:..." tag in the page/stream title.
 *
 * Viewer look (optional, off unless the streamer or the viewer sets it): after
 * unsquishing, Details (sharpen, clarity, HDR toning, bloom) and Sharpen+ run
 * as extra passes through offscreen framebuffers. With no look the picture is
 * drawn in one pass exactly as before.
 */
(() => {
	"use strict";
	const VERT = `
attribute vec2 a_pos;
varying vec2 v_uv;
void main() { v_uv = vec2(a_pos.x * 0.5 + 0.5, 0.5 - a_pos.y * 0.5); gl_Position = vec4(a_pos, 0.0, 1.0); }`;
	// Look passes read framebuffer textures, which are bottom-up.
	const VERT_POST = `
attribute vec2 a_pos;
varying vec2 v_uv;
void main() { v_uv = a_pos * 0.5 + 0.5; gl_Position = vec4(a_pos, 0.0, 1.0); }`;

	// One live copy per page: after an extension update a fresh copy is injected
	// and the old one (cut off from settings) must step aside.
	const GEN = String(Date.now()) + Math.random();
	document.documentElement.dataset.dbpoUnsquishGen = GEN;
	const superseded = () => document.documentElement.dataset.dbpoUnsquishGen !== GEN;
	for (const old of document.querySelectorAll("canvas[data-dbpo-unsquish]")) old.remove();

	// lookMode: "streamer" (the look in the streamer's string), "mine" (myLook) or "off".
	// plainVideos: also run myLook on normal (not squished) videos.
	let settings = {
		enabled: true,
		autoDetect: true,
		config: "",
		quality: "auto", // "light" draws fewer pixels and only new video frames (phones: auto)
		configPage: "", // page the pasted config belongs to ("" = older install: every page)
		lookMode: "streamer",
		myLook: { sharpen: 0, clarity: 0, hdr: 0, bloom: 0, sharpenPlus: 0 }, // more fields are filled in by FSQ.cleanLook
		plainVideos: false,
		enhance: { ...FSQ.NO_ENHANCE }, // viewer-side edge reconstruction of the squeezed periphery (see squish.js)
		debug: false, // overlay with fps / CPU / GPU time, A/B and benchmark
		compare: "off", // "split": left plain, right enhanced (doubles the cost)
	};
	const isLight = () =>
		settings.quality === "light" ||
		(settings.quality === "auto" && /Android|iPhone|iPad|iPod|Mobi/i.test(navigator.userAgent));
	let detected = null; // config found in the page
	let state = null; // { video, canvas, gl, prog, loc, tex, cfg }
	let carry = {}; // debug state (benchmark, A/B bypass, timings) that survives a short detach, e.g. a looping video

	function activeConfig() {
		if (!settings.enabled) return null;
		// Chat signal from the streamer (chatsignal.js): "off" means show the picture as sent.
		if (document.documentElement.dataset.dbpoFsq === "off") return null;
		// A pasted config only applies on the page it was pasted for; on other pages the
		// extension stays idle unless the page itself carries a recognised code.
		const mine = !settings.configPage || settings.configPage === FSQ.pageKey(location.href);
		return (mine && FSQ.parse(settings.config)) || (settings.autoDetect ? detected : null);
	}

	/* The look to apply on top of a squished stream's config. */
	function activeLook(cfg) {
		if (settings.lookMode === "off") return FSQ.NO_LOOK;
		if (settings.lookMode === "mine") return FSQ.cleanLook(settings.myLook);
		return cfg.look || FSQ.NO_LOOK;
	}

	/* Plain videos: only with the option on and a look of the viewer's own. */
	function plainLook() {
		if (!settings.enabled || !settings.plainVideos || settings.lookMode === "off") return null;
		const look = FSQ.cleanLook(settings.myLook);
		return FSQ.lookActive(look) ? look : null;
	}
	const blocked = new WeakSet(); // plain videos we cannot read (cross-origin)

	function findVideo() {
		let best = null, bestArea = 0;
		for (const v of document.querySelectorAll("video")) {
			const r = v.getBoundingClientRect();
			const area = r.width * r.height;
			if (v.readyState >= 2 && area > bestArea) {
				best = v;
				bestArea = area;
			}
		}
		return best;
	}

	function compile(gl, type, src) {
		const s = gl.createShader(type);
		gl.shaderSource(s, src);
		gl.compileShader(s);
		if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
		return s;
	}

	function program(gl, vert, frag, uniforms) {
		const prog = gl.createProgram();
		gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, vert));
		gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, frag));
		gl.bindAttribLocation(prog, 0, "a_pos");
		gl.linkProgram(prog);
		if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
		const loc = {};
		for (const n of uniforms) loc[n] = gl.getUniformLocation(prog, n);
		return { prog, loc };
	}

	function makeTexture(gl) {
		const tex = gl.createTexture();
		gl.bindTexture(gl.TEXTURE_2D, tex);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
		return tex;
	}

	/* Look programs and framebuffers, made the first time a look or an enhancement is used. */
	const AXES = ["u_ax_a", "u_ax_b", "u_ay_a", "u_ay_b", "u_area", "u_nrgn", "u_rgn"];
	function lookSetup(s) {
		if (s.look) return s.look;
		const gl = s.gl;
		s.look = {
			blur: program(gl, VERT_POST, FSQ.LOOK_BLUR_SHADER, ["u_img", "u_step"]),
			details: program(gl, VERT_POST, FSQ.LOOK_DETAILS_SHADER, ["u_img", "u_large", "u_px", "u_look"]),
			cas: program(gl, VERT_POST, FSQ.LOOK_CAS_SHADER, ["u_img", "u_px", "u_sharp"]),
			color: program(gl, VERT_POST, FSQ.LOOK_COLOR_SHADER, ["u_img", "u_a", "u_b", "u_c", "u_n"]),
			deblock: program(gl, VERT_POST, FSQ.ENH_DEBLOCK_SHADER, ["u_img", "u_px", "u_amt", ...AXES]),
			edge: program(gl, VERT_POST, FSQ.ENH_EDGE_SHADER, ["u_img", "u_px", "u_a", "u_b", "u_frame", ...AXES]),
			targets: [0, 1, 2, 3, 4, 5].map(() => ({ tex: makeTexture(gl), fb: gl.createFramebuffer(), w: 0, h: 0 })),
		};
		return s.look;
	}

	function sizeTarget(gl, t, w, h) {
		if (t.w === w && t.h === h) return;
		t.w = w;
		t.h = h;
		gl.bindTexture(gl.TEXTURE_2D, t.tex);
		gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
		gl.bindFramebuffer(gl.FRAMEBUFFER, t.fb);
		gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t.tex, 0);
		gl.bindFramebuffer(gl.FRAMEBUFFER, null);
	}

	/* Output to a target, or to the screen (limited to s.rect when comparing side by side). */
	function bindOutput(s, t, w, h) {
		const gl = s.gl;
		gl.bindFramebuffer(gl.FRAMEBUFFER, t ? t.fb : null);
		gl.viewport(0, 0, w, h);
		if (!t && s.rect) {
			gl.enable(gl.SCISSOR_TEST);
			gl.scissor(s.rect.x, s.rect.y, s.rect.w, s.rect.h);
		} else gl.disable(gl.SCISSOR_TEST);
	}

	function useTex(gl, unit, tex) {
		gl.activeTexture(gl.TEXTURE0 + unit);
		gl.bindTexture(gl.TEXTURE_2D, tex);
	}

	function setAxes(s, p, enh) {
		const gl = s.gl, u = s.u;
		gl.uniform4fv(p.loc.u_ax_a, u.ux.A);
		gl.uniform4fv(p.loc.u_ax_b, u.ux.B);
		gl.uniform4fv(p.loc.u_ay_a, u.uy.A);
		gl.uniform4fv(p.loc.u_ay_b, u.uy.B);
		gl.uniform1f(p.loc.u_area, enh && enh.area === "all" ? 1 : 0);
		gl.uniform1i(p.loc.u_nrgn, u.nrgn);
		gl.uniform4fv(p.loc.u_rgn, u.rgn);
	}

	/*
	 * Draws the unsquished picture through the enhancement and look passes:
	 * unsquish -> T0; then in order Deblock, Edge, Colour, Details, Sharpen+ (only those that are on),
	 * ping-ponging through T3/T4/T5; Details also uses T1/T2 (half size) for its blur.
	 * The last pass draws to the screen.
	 */
	function drawWithLook(s, look, enh, drawUnsquish) {
		const gl = s.gl, L = lookSetup(s);
		const w = s.canvas.width, h = s.canvas.height, hw = Math.max(1, (w + 1) >> 1), hh = Math.max(1, (h + 1) >> 1);
		const [t0, t1, t2, t3, t4, t5] = L.targets;
		const pool = [t3, t4, t5];
		const st = enh ? FSQ.enhanceStages(enh) : {};
		const details = look.sharpen > 0 || look.clarity > 0 || look.hdr > 0 || look.bloom > 0;
		const cas = look.sharpenPlus > 0;
		const color = FSQ.colorActive(look);
		const stages = [];
		if (st.deblock) stages.push("deblock");
		if (st.edge) stages.push("edge");
		if (color) stages.push("color");
		if (details) stages.push("details");
		if (cas) stages.push("cas");
		sizeTarget(gl, t0, w, h);
		if (details) {
			sizeTarget(gl, t1, hw, hh);
			sizeTarget(gl, t2, hw, hh);
		}
		useTex(gl, 0, s.tex); // sizing rebinds textures: the video goes back on unit 0
		bindOutput(s, t0, w, h);
		drawUnsquish();
		let last = t0;
		stages.forEach((stage, i) => {
			const out = i === stages.length - 1 ? null : pool.find((t) => t !== last);
			if (out) sizeTarget(gl, out, w, h);
			if (stage === "deblock") {
				gl.useProgram(L.deblock.prog);
				gl.uniform1i(L.deblock.loc.u_img, 0);
				gl.uniform2f(L.deblock.loc.u_px, 1 / w, 1 / h);
				gl.uniform1f(L.deblock.loc.u_amt, enh.deblock);
				setAxes(s, L.deblock, enh);
			} else if (stage === "edge") {
				gl.useProgram(L.edge.prog);
				gl.uniform1i(L.edge.loc.u_img, 0);
				gl.uniform2f(L.edge.loc.u_px, 1 / w, 1 / h);
				gl.uniform4f(L.edge.loc.u_a, enh.sharpen, enh.snap, enh.vibrance, enh.posterize);
				gl.uniform2f(L.edge.loc.u_b, enh.dither, enh.grain);
				gl.uniform1f(L.edge.loc.u_frame, (s.frameNo % 997) * 1.0);
				setAxes(s, L.edge, enh);
			} else if (stage === "color") {
				gl.useProgram(L.color.prog);
				gl.uniform1i(L.color.loc.u_img, 0);
				gl.uniform4f(L.color.loc.u_a, look.brightness, look.contrast, look.gamma, look.saturation);
				gl.uniform4f(L.color.loc.u_b, look.vibrance, look.temperature, look.tint, look.hue);
				gl.uniform4f(L.color.loc.u_c, look.highlights, look.shadows, look.bw, look.sepia);
				gl.uniform1f(L.color.loc.u_n, look.night);
			} else if (stage === "details") {
				const scale = Math.max(h / 1080, 0.25); // blur size relative to a 1080p picture
				gl.useProgram(L.blur.prog);
				gl.uniform1i(L.blur.loc.u_img, 0);
				bindOutput(s, t1, hw, hh);
				useTex(gl, 0, last.tex);
				gl.uniform2f(L.blur.loc.u_step, scale / w, 0);
				gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
				bindOutput(s, t2, hw, hh);
				useTex(gl, 0, t1.tex);
				gl.uniform2f(L.blur.loc.u_step, 0, scale / h);
				gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
				gl.useProgram(L.details.prog);
				gl.uniform1i(L.details.loc.u_img, 0);
				gl.uniform1i(L.details.loc.u_large, 1);
				gl.uniform2f(L.details.loc.u_px, 1 / w, -1 / h);
				gl.uniform4f(L.details.loc.u_look, look.sharpen, look.clarity, look.hdr, look.bloom);
				useTex(gl, 1, t2.tex);
			} else {
				gl.useProgram(L.cas.prog);
				gl.uniform1i(L.cas.loc.u_img, 0);
				gl.uniform2f(L.cas.loc.u_px, 1 / w, 1 / h);
				gl.uniform1f(L.cas.loc.u_sharp, look.sharpenPlus);
			}
			useTex(gl, 0, last.tex);
			bindOutput(s, out, w, h);
			gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
			last = out;
		});
		// Back to the state the single-pass path expects.
		useTex(gl, 1, null);
		useTex(gl, 0, s.tex);
		gl.useProgram(s.prog);
	}

	function useTexReset(gl, s) {
		gl.bindFramebuffer(gl.FRAMEBUFFER, null);
		gl.viewport(0, 0, s.canvas.width, s.canvas.height);
		useTex(gl, 0, s.tex);
		gl.useProgram(s.prog);
	}

	/*
	 * Debug overlay: render fps, video fps, CPU submit time and GPU time per draw (EXT_disjoint_timer_query when the
	 * browser offers it, otherwise a sparse gl.finish() estimate), pass list and texture reads per pixel, an A/B
	 * bypass (Alt+Shift+U), a split view (left: plain bilinear, right: enhanced) and a benchmark over the options.
	 */
	const BENCH_STEPS = [
		["baseline (bilinear)", {}],
		["lanczos3 unwarp", { kernel: "lanczos" }],
		["lanczos3 + deblock 0.5", { kernel: "lanczos", deblock: 0.5 }],
		["lanczos3 + sharpen 0.5", { kernel: "lanczos", sharpen: 0.5 }],
		["lanczos3 + edge snap 0.8", { kernel: "lanczos", snap: 0.8 }],
		["snap + vibrance + dither", { kernel: "lanczos", snap: 1, vibrance: 0.4, posterize: 0.6, dither: 0.3 }],
		["lanczos3 + grain 0.5", { kernel: "lanczos", grain: 0.5 }],
		["preset light", FSQ.ENHANCE_PRESETS.light],
		["preset balanced", FSQ.ENHANCE_PRESETS.balanced],
		["preset crisp", FSQ.ENHANCE_PRESETS.crisp],
		["your current settings", null],
	];
	const BENCH_WARMUP_MS = 600, BENCH_STEP_MS = 3000;

	const avg = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : NaN);
	const p95 = (a) => (a.length ? [...a].sort((x, y) => x - y)[Math.min(a.length - 1, Math.floor(a.length * 0.95))] : NaN);
	const fmt = (v, d = 2) => (Number.isFinite(v) ? v.toFixed(d) : "n/a");

	function metrics(s) {
		if (!s.m) s.m = { cpu: [], gpu: [], draws: [], pending: [], timer: undefined, syncMs: NaN, lastVideo: null, videoFps: NaN };
		return s.m;
	}

	function gpuBegin(s) {
		const m = metrics(s), gl = s.gl;
		if (m.timer === undefined) m.timer = gl.getExtension("EXT_disjoint_timer_query") || null;
		if (!m.timer || m.pending.length >= 6) return null;
		const q = m.timer.createQueryEXT();
		m.timer.beginQueryEXT(m.timer.TIME_ELAPSED_EXT, q);
		return q;
	}

	function gpuEnd(s, q) {
		const m = metrics(s);
		if (q) m.timer.endQueryEXT(m.timer.TIME_ELAPSED_EXT);
		if (q) m.pending.push(q);
		if (!m.timer) return;
		const gl = s.gl;
		while (m.pending.length && m.timer.getQueryObjectEXT(m.pending[0], m.timer.QUERY_RESULT_AVAILABLE_EXT)) {
			const ok = !gl.getParameter(m.timer.GPU_DISJOINT_EXT);
			const ns = m.timer.getQueryObjectEXT(m.pending[0], m.timer.QUERY_RESULT_EXT);
			m.timer.deleteQueryEXT(m.pending.shift());
			if (ok) {
				m.gpu.push(ns / 1e6);
				if (m.gpu.length > 240) m.gpu.shift();
			}
		}
	}

	function record(s, cpuMs, now) {
		const m = metrics(s);
		m.cpu.push(cpuMs);
		if (m.cpu.length > 240) m.cpu.shift();
		m.draws.push(now);
		while (m.draws.length && now - m.draws[0] > 2000) m.draws.shift();
	}

	function describe(s, enh, look) {
		const e = enh || FSQ.NO_ENHANCE;
		const r = FSQ.readsPerPixel(enh, look);
		const mode = s.bench && s.bench.running ? `benchmark: ${s.bench.steps[s.bench.idx][0]}`
			: s.bypass ? "BYPASS (plain bilinear)" : settings.compare === "split" && enh ? "SPLIT: left plain | right enhanced"
			: enh ? "enhanced" : "plain";
		return { mode, reads: r.reads, parts: r.parts.join(" > "), kernel: e.kernel };
	}

	function overlayText(s) {
		const m = metrics(s), v = s.video, q = v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality() : null;
		const now = performance.now();
		if (q) {
			if (m.lastVideo && now - m.lastVideo.t >= 1000) {
				m.videoFps = ((q.totalVideoFrames - m.lastVideo.n) * 1000) / (now - m.lastVideo.t);
				m.lastVideo = { t: now, n: q.totalVideoFrames };
			} else if (!m.lastVideo) m.lastVideo = { t: now, n: q.totalVideoFrames };
		}
		const fps = m.draws.length > 1 ? ((m.draws.length - 1) * 1000) / (m.draws[m.draws.length - 1] - m.draws[0]) : NaN;
		const d = s.desc || { mode: "plain", reads: 1, parts: "unwarp" };
		const gpu = m.timer ? `${fmt(avg(m.gpu))} ms avg, p95 ${fmt(p95(m.gpu))} (timer query)`
			: `${fmt(m.syncMs)} ms (sync estimate, no timer query)`;
		return [
			`DBpO Unsquish debug: ${d.mode}`,
			`canvas ${s.canvas.width}x${s.canvas.height}   video ${v.videoWidth}x${v.videoHeight} @ ${fmt(m.videoFps, 1)} fps` +
				(q ? ` (dropped ${q.droppedVideoFrames})` : ""),
			`render ${fmt(fps, 1)} fps   CPU submit ${fmt(avg(m.cpu))} ms avg, p95 ${fmt(p95(m.cpu))}`,
			`GPU ${gpu}`,
			`passes: ${d.parts}  ~${d.reads} texture reads/px`,
		].join("\n");
	}

	function benchReport(b) {
		const head = "option\tGPU ms\tCPU ms\tfps\treads/px";
		return [head, ...b.results.map((r) => `${r.name}\t${fmt(r.gpu)}\t${fmt(r.cpu)}\t${fmt(r.fps, 1)}\t${r.reads}`)].join("\n");
	}

	function makeOverlay(s) {
		const box = document.createElement("div");
		box.dataset.dbpoUnsquishDebug = "1";
		Object.assign(box.style, {
			position: "absolute", left: "6px", top: "6px", zIndex: "2147483647", pointerEvents: "auto",
			font: "11px/1.35 ui-monospace, Consolas, monospace", background: "rgba(0,0,0,.74)", color: "#fff",
			padding: "6px 8px", borderRadius: "6px", maxWidth: "92%", userSelect: "text",
		});
		const pre = document.createElement("pre");
		pre.style.cssText = "margin:0;white-space:pre-wrap";
		const row = document.createElement("div");
		row.style.cssText = "margin-top:4px;display:flex;gap:4px;flex-wrap:wrap";
		const btn = (label, fn) => {
			const b = document.createElement("button");
			b.textContent = label;
			b.style.cssText = "font:11px system-ui;padding:2px 6px;border-radius:4px;border:1px solid #888;background:#333;color:#fff;cursor:pointer";
			b.onclick = (e) => { e.stopPropagation(); fn(); };
			row.appendChild(b);
		};
		btn("A/B bypass", () => { s.bypass = !s.bypass; });
		btn("Split view", () => chrome.storage.sync.set({ compare: settings.compare === "split" ? "off" : "split" }));
		btn("Benchmark", () => startBench(s));
		btn("Copy results", () => {
			const text = s.bench && s.bench.results.length ? benchReport(s.bench) : pre.textContent;
			try { navigator.clipboard.writeText(text); } catch (e) { console.log(text); }
		});
		box.append(pre, row);
		s.video.parentElement.appendChild(box);
		s.overlay = { box, pre };
		s.overlayTimer = setInterval(() => {
			if (!s.overlay) return;
			let text = overlayText(s);
			if (s.bench) {
				if (s.bench.running) text += `\nbenchmark ${s.bench.idx + 1}/${s.bench.steps.length} ...`;
				else if (s.bench.results.length) text += "\n" + benchReport(s.bench);
			}
			pre.textContent = text;
		}, 500);
	}

	function removeOverlay(s) {
		if (s.overlayTimer) clearInterval(s.overlayTimer);
		if (s.overlay) s.overlay.box.remove();
		s.overlay = s.overlayTimer = null;
	}

	function startBench(s) {
		const steps = BENCH_STEPS.map(([name, part]) => [name, FSQ.cleanEnhance(part ? { ...FSQ.NO_ENHANCE, ...part } : settings.enhance)]);
		s.bench = { steps, idx: 0, results: [], running: true, t0: performance.now(), warmed: false };
		s.bypass = false;
		const m = metrics(s);
		m.cpu.length = m.gpu.length = m.draws.length = 0;
	}

	/* Called after every draw while a benchmark runs; moves to the next option when one is measured. */
	function benchTick(s, look) {
		const b = s.bench, m = metrics(s), now = performance.now();
		if (!b.warmed && now - b.t0 >= BENCH_WARMUP_MS) {
			b.warmed = true;
			b.tMeasure = now;
			m.cpu.length = m.gpu.length = m.draws.length = 0;
			b.drawsAtStart = 0;
			b.count = 0;
		}
		if (b.warmed) b.count++;
		if (b.warmed && now - b.tMeasure >= BENCH_STEP_MS) {
			const [name, enh] = b.steps[b.idx];
			b.results.push({
				name, gpu: avg(m.gpu), cpu: avg(m.cpu), fps: (b.count * 1000) / (now - b.tMeasure),
				reads: FSQ.readsPerPixel(FSQ.enhanceActive(enh) ? enh : null, look).reads,
			});
			b.idx++;
			if (b.idx >= b.steps.length) {
				b.running = false;
				b.idx = b.steps.length - 1;
				console.log("[DBpO Unsquish] benchmark\n" + benchReport(b));
			} else {
				b.warmed = false;
				b.t0 = now;
			}
		}
	}

	function attach(video) {
		const canvas = document.createElement("canvas");
		canvas.dataset.dbpoUnsquish = "1";
		// Behind everything the player draws (controls, overlays, positioned or not): the
		// parent becomes its own stacking context and the canvas sits at its very bottom,
		// above only the parent's background. The hidden video itself is invisible. Clicks
		// go through to the player.
		Object.assign(canvas.style, { position: "absolute", pointerEvents: "none", zIndex: "-1" });
		const gl = canvas.getContext("webgl", { premultipliedAlpha: false, preserveDrawingBuffer: true });
		if (!gl) return null;
		const prog = gl.createProgram();
		gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT));
		gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FSQ.FRAGMENT_SHADER));
		gl.bindAttribLocation(prog, 0, "a_pos");
		gl.linkProgram(prog);
		if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
		gl.useProgram(prog);
		const buf = gl.createBuffer();
		gl.bindBuffer(gl.ARRAY_BUFFER, buf);
		gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
		const aPos = gl.getAttribLocation(prog, "a_pos");
		gl.enableVertexAttribArray(aPos);
		gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
		const tex = gl.createTexture();
		gl.bindTexture(gl.TEXTURE_2D, tex);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
		const loc = {};
		for (const n of ["u_video", "u_ax_a", "u_ax_b", "u_ay_a", "u_ay_b", "u_box", "u_texel", "u_nrgn", "u_rgn", "u_slot", "u_kernel"]) loc[n] = gl.getUniformLocation(prog, n);
		const parent = video.parentElement;
		if (getComputedStyle(parent).position === "static") parent.style.position = "relative";
		parent.dataset.dbpoIsolation = parent.style.isolation;
		parent.style.isolation = "isolate";
		video.insertAdjacentElement("afterend", canvas);
		video.dataset.dbpoOpacity = video.style.opacity;
		video.style.opacity = "0";
		return { video, canvas, gl, prog, loc, tex };
	}

	function detach() {
		if (!state) return;
		removeOverlay(state);
		carry = { bench: state.bench, bypass: state.bypass, m: state.m };
		state.canvas.remove();
		const par = state.video.parentElement;
		if (par && "dbpoIsolation" in par.dataset) {
			par.style.isolation = par.dataset.dbpoIsolation;
			delete par.dataset.dbpoIsolation;
		}
		state.video.style.opacity = state.video.dataset.dbpoOpacity || "";
		state = null;
	}

	/* Place the canvas over the video's picture area, at the unsquished aspect ratio. */
	function layout(s, cfg) {
		const v = s.video, parent = v.parentElement;
		const vr = v.getBoundingClientRect(), pr = parent.getBoundingClientRect();
		let w = vr.width, h = w / cfg.ar;
		if (h > vr.height) {
			h = vr.height;
			w = h * cfg.ar;
		}
		// Absolute positions are relative to the parent's padding box (inside its border).
		const left = vr.left - pr.left - parent.clientLeft + (vr.width - w) / 2;
		const top = vr.top - pr.top - parent.clientTop + (vr.height - h) / 2;
		Object.assign(s.canvas.style, { left: `${left}px`, top: `${top}px`, width: `${w}px`, height: `${h}px` });
		// Light: never more than 1.5 pixels per CSS pixel and at most 1280 wide (phones report 3x).
		let dpr = window.devicePixelRatio || 1;
		if (isLight()) dpr = Math.min(dpr, 1.5, 1280 / Math.max(w, 1));
		const cw = Math.max(1, Math.round(w * dpr)), ch = Math.max(1, Math.round(h * dpr));
		if (s.canvas.width !== cw || s.canvas.height !== ch) {
			s.canvas.width = cw;
			s.canvas.height = ch;
		}
	}

	/* One complete draw of the picture (optionally into a screen rectangle). Returns whether the look ran. */
	function renderOnce(s, look, enh, rect) {
		const gl = s.gl;
		s.rect = rect;
		gl.uniform1i(s.loc.u_kernel, enh && enh.kernel === "lanczos" ? 1 : 0);
		const st = enh ? FSQ.enhanceStages(enh) : {};
		let lookOn = FSQ.lookActive(look) && !s.lookFailed;
		if ((lookOn || st.deblock || st.edge) && !s.lookFailed) {
			try {
				drawWithLook(s, look, enh, () => gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4));
				return lookOn;
			} catch (e) {
				console.warn("[DBpO Unsquish] viewer look / enhancement unavailable:", e);
				s.lookFailed = true;
				lookOn = false;
				useTexReset(gl, s);
			}
		}
		bindOutput(s, null, s.canvas.width, s.canvas.height);
		gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
		return false;
	}

	/** Level for the frame just uploaded: read the 160x16 marker (same decoded frame), hold on a bad read. */
	function readLevel(s, video, cfg) {
		const STV = self.FSQSTV;
		const vw = video.videoWidth, vh = video.videoHeight;
		if (!STV || !vw || !vh) return null;
		if (!s.stv) {
			const cv = document.createElement("canvas");
			cv.width = 160;
			cv.height = 16;
			s.stv = { ctx: cv.getContext("2d", { willReadFrequently: true }), holder: new STV.Holder(60) };
		}
		let code = null;
		try {
			const [mx, my] = STV.markerPos(vw, vh);
			s.stv.ctx.drawImage(video, mx, my, 160, 16, 0, 0, 160, 16);
			code = STV.decodeImage(s.stv.ctx.getImageData(0, 0, 160, 16).data);
		} catch (e) {
			code = null;
		}
		const level = s.stv.holder.update(code);
		document.documentElement.dataset.dbpoLevel = String(level);
		return STV.levelView(cfg, level, vw, vh);
	}

	function draw() {
		if (superseded()) {
			detach();
			return; // a newer copy of the extension took over
		}
		let cfg = activeConfig();
		let video = cfg ? findVideo() : null;
		let look = cfg ? activeLook(cfg) : null;
		const plain = !cfg && plainLook();
		if (plain) {
			// Normal video: unsquish with the identity mapping, then the viewer's look.
			video = findVideo();
			if (video && !blocked.has(video)) {
				cfg = FSQ.identity(video.videoWidth / Math.max(video.videoHeight, 1));
				look = plain;
			} else video = null;
		}
		if (!cfg || !video) {
			detach();
		} else {
			if (state && state.video !== video) detach();
			if (!state) {
				try {
					state = attach(video);
					if (state) {
						Object.assign(state, carry);
						if (state.m) Object.assign(state.m, { timer: undefined, pending: [] }); // queries belong to the old context
					}
				} catch (e) {
					console.warn("[DBpO Unsquish] WebGL setup failed:", e);
					state = null;
				}
			}
			if (state) {
				const { gl, loc } = state;
				layout(state, cfg);
				if (isLight()) {
					// Only redraw when something changed: a new video frame, size, config or look.
					const key = [video.currentTime, state.canvas.width, state.canvas.height, cfg.text, JSON.stringify(look), JSON.stringify(settings.enhance), settings.compare, settings.debug, !!state.bypass, !!state.bench].join("|");
					if (state.lastKey === key && !(state.bench && state.bench.running)) {
						setTimeout(() => requestAnimationFrame(draw), 8);
						return;
					}
					state.lastKey = key;
				} else state.lastKey = "";
				const frameStart = performance.now();
				const bench = state.bench && state.bench.running ? state.bench : null;
				const enhOn = plain ? null : bench ? bench.steps[bench.idx][1] : state.bypass ? null : FSQ.cleanEnhance(settings.enhance);
				const enh = FSQ.enhanceActive(enhOn) ? enhOn : null;
				const q = settings.debug ? gpuBegin(state) : null;
				gl.viewport(0, 0, state.canvas.width, state.canvas.height);
				try {
					gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video);
				} catch (e) {
					console.warn("[DBpO Unsquish] cannot read this video (cross-origin):", e);
					if (plain) blocked.add(video);
					else settings.enabled = false;
					detach();
					return requestAnimationFrame(draw);
				}
				// Starvation levels: the marker says how small the picture is inside this very frame.
				let lvBox = cfg.box, lvX = cfg.x, lvY = cfg.y;
				if (cfg.starve && !plain) {
					const lv = readLevel(state, video, cfg);
					if (lv) {
						lvBox = lv.box;
						lvX = lv.x;
						lvY = lv.y;
					}
				}
				const ux = FSQ.uniforms(lvX), uy = FSQ.uniforms(lvY);
				gl.uniform1i(loc.u_video, 0);
				gl.uniform4fv(loc.u_ax_a, ux.A);
				gl.uniform4fv(loc.u_ax_b, ux.B);
				gl.uniform4fv(loc.u_ay_a, uy.A);
				gl.uniform4fv(loc.u_ay_b, uy.B);
				gl.uniform4fv(loc.u_box, lvBox);
				gl.uniform2f(loc.u_texel, 1 / Math.max(video.videoWidth, 1), 1 / Math.max(video.videoHeight, 1));
				// Sharp regions (atlas): paste each one from its slot in the frame.
				const rgn = new Float32Array(16), slot = new Float32Array(16);
				let nrgn = 0;
				{
					const vw = Math.max(video.videoWidth, 1), vh = Math.max(video.videoHeight, 1);
					const slots = cfg.regions && cfg.regions.length ? FSQ.atlasLayout(cfg, vw, vh) || [] : [];
					slots.forEach((sl, i) => {
						const r = cfg.regions[i];
						rgn.set([r.x0, r.y0, r.x1, r.y1], i * 4);
						slot.set([sl.x / vw, sl.y / vh, sl.w / vw, sl.h / vh], i * 4);
					});
					nrgn = slots.length;
					gl.uniform1i(loc.u_nrgn, nrgn);
					gl.uniform4fv(loc.u_rgn, rgn);
					gl.uniform4fv(loc.u_slot, slot);
				}
				state.u = { ux, uy, rgn, nrgn };
				state.frameNo = (state.frameNo || 0) + 1;
				let lookOn;
				const cw = state.canvas.width, ch = state.canvas.height;
				if (settings.compare === "split" && enh && !bench) {
					const half = cw >> 1;
					renderOnce(state, look, null, { x: 0, y: 0, w: half, h: ch });
					lookOn = renderOnce(state, look, enh, { x: half, y: 0, w: cw - half, h: ch });
					gl.enable(gl.SCISSOR_TEST);
					gl.scissor(half - 1, 0, 2, ch);
					gl.clearColor(1, 1, 1, 1);
					gl.clear(gl.COLOR_BUFFER_BIT);
					gl.disable(gl.SCISSOR_TEST);
				} else lookOn = renderOnce(state, look, enh, null);
				if (settings.debug) {
					gpuEnd(state, q);
					const m = metrics(state);
					if (!m.timer && state.frameNo % 30 === 0) {
						gl.finish(); // sparse blocking sample: CPU submit + GPU execution
						m.syncMs = performance.now() - frameStart;
					}
				}
				record(state, performance.now() - frameStart, performance.now());
				state.desc = describe(state, enh, lookOn ? look : FSQ.NO_LOOK);
				if (settings.debug && !state.overlay) makeOverlay(state);
				if (!settings.debug && state.overlay) removeOverlay(state);
				if (bench) benchTick(state, lookOn ? look : FSQ.NO_LOOK);
				document.documentElement.dataset.dbpoUnsquish = plain ? "plain" : cfg.text || "custom";
				document.documentElement.dataset.dbpoLook = lookOn
					? FSQ.LOOK_FIELDS.map(([k]) => +look[k].toFixed(3)).join(",")
					: "";
				document.documentElement.dataset.dbpoEnhance = enh
					? [enh.kernel, enh.area, ...FSQ.ENHANCE_FIELDS.map(([k]) => +enh[k].toFixed(3))].join(",")
					: "";
			}
		}
		// Nothing recognised on this page: check back twice a second instead of every frame.
		if (!cfg && !state) setTimeout(() => requestAnimationFrame(draw), 500);
		else requestAnimationFrame(draw);
	}

	/* Auto-detect: an FSQ1 tag in the page title or a stream title element. */
	function scan() {
		const texts = [document.title];
		for (const el of document.querySelectorAll('[data-a-target="stream-title"], h1, h2, #title'))
			texts.push(el.textContent);
		detected = null;
		for (const t of texts) {
			const c = FSQ.parse(t);
			if (c) {
				detected = c;
				break;
			}
		}
		try {
			const key = FSQ.pageKey(location.href);
			chrome.storage.local.get({ detectedBy: {} }, (l) => {
				const map = { ...l.detectedBy };
				if (detected) map[key] = detected.text;
				else delete map[key];
				if ((map[key] || "") !== (l.detectedBy[key] || "")) chrome.storage.local.set({ detectedBy: map });
			});
		} catch (e) {
			// extension was updated/removed; the new copy handles this page
		}
	}

	// A/B: Alt+Shift+U bypasses the viewer-side enhancement (and shows it again).
	window.addEventListener("keydown", (e) => {
		if (superseded() || !state || !(e.altKey && e.shiftKey && e.code === "KeyU")) return;
		state.bypass = !state.bypass;
	});

	chrome.storage.sync.get(settings, (s) => {
		settings = { ...settings, ...s };
		scan();
		setInterval(scan, 3000);
		requestAnimationFrame(draw);
	});
	chrome.storage.onChanged.addListener((changes) => {
		for (const [k, { newValue }] of Object.entries(changes)) if (k in settings) settings[k] = newValue;
	});
})();
