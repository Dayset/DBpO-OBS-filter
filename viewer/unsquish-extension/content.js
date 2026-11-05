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
		lookMode: "streamer",
		myLook: { sharpen: 0, clarity: 0, hdr: 0, bloom: 0, sharpenPlus: 0 }, // more fields are filled in by FSQ.cleanLook
		plainVideos: false,
	};
	let detected = null; // config found in the page
	let state = null; // { video, canvas, gl, prog, loc, tex, cfg }

	function activeConfig() {
		if (!settings.enabled) return null;
		// Chat signal from the streamer (chatsignal.js): "off" means show the picture as sent.
		if (document.documentElement.dataset.dbpoFsq === "off") return null;
		return FSQ.parse(settings.config) || (settings.autoDetect ? detected : null);
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

	/* Look programs and framebuffers, made the first time a look is used. */
	function lookSetup(s) {
		if (s.look) return s.look;
		const gl = s.gl;
		s.look = {
			blur: program(gl, VERT_POST, FSQ.LOOK_BLUR_SHADER, ["u_img", "u_step"]),
			details: program(gl, VERT_POST, FSQ.LOOK_DETAILS_SHADER, ["u_img", "u_large", "u_px", "u_look"]),
			cas: program(gl, VERT_POST, FSQ.LOOK_CAS_SHADER, ["u_img", "u_px", "u_sharp"]),
			color: program(gl, VERT_POST, FSQ.LOOK_COLOR_SHADER, ["u_img", "u_a", "u_b", "u_c", "u_n"]),
			targets: [0, 1, 2, 3, 4].map(() => ({ tex: makeTexture(gl), fb: gl.createFramebuffer(), w: 0, h: 0 })),
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

	function bindOutput(gl, t, w, h) {
		gl.bindFramebuffer(gl.FRAMEBUFFER, t ? t.fb : null);
		gl.viewport(0, 0, w, h);
	}

	function useTex(gl, unit, tex) {
		gl.activeTexture(gl.TEXTURE0 + unit);
		gl.bindTexture(gl.TEXTURE_2D, tex);
	}

	/*
	 * Draws the unsquished picture through the look passes:
	 * unsquish -> T0; Colour(T0) -> T4; blur H (T0 or T4) -> T1 (half); blur V -> T2 (half);
	 * Details(src, T2) -> screen or T3; Sharpen+(T3 or src) -> screen. The last pass draws to the screen.
	 */
	function drawWithLook(s, look, drawUnsquish) {
		const gl = s.gl, L = lookSetup(s);
		const w = s.canvas.width, h = s.canvas.height, hw = Math.max(1, (w + 1) >> 1), hh = Math.max(1, (h + 1) >> 1);
		const [t0, t1, t2, t3, t4] = L.targets;
		const details = look.sharpen > 0 || look.clarity > 0 || look.hdr > 0 || look.bloom > 0;
		const cas = look.sharpenPlus > 0;
		const color = FSQ.colorActive(look);
		sizeTarget(gl, t0, w, h);
		if (color && (details || cas)) sizeTarget(gl, t4, w, h);
		if (details) {
			sizeTarget(gl, t1, hw, hh);
			sizeTarget(gl, t2, hw, hh);
			if (cas) sizeTarget(gl, t3, w, h);
		}
		useTex(gl, 0, s.tex); // sizing rebinds textures: the video goes back on unit 0
		bindOutput(gl, t0, w, h);
		drawUnsquish();
		let last = t0;
		if (color) {
			gl.useProgram(L.color.prog);
			gl.uniform1i(L.color.loc.u_img, 0);
			gl.uniform4f(L.color.loc.u_a, look.brightness, look.contrast, look.gamma, look.saturation);
			gl.uniform4f(L.color.loc.u_b, look.vibrance, look.temperature, look.tint, look.hue);
			gl.uniform4f(L.color.loc.u_c, look.highlights, look.shadows, look.bw, look.sepia);
			gl.uniform1f(L.color.loc.u_n, look.night);
			useTex(gl, 0, t0.tex);
			const toScreen = !details && !cas;
			bindOutput(gl, toScreen ? null : t4, w, h);
			gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
			last = t4;
		}
		if (details) {
			const scale = Math.max(h / 1080, 0.25); // blur size relative to a 1080p picture
			gl.useProgram(L.blur.prog);
			gl.uniform1i(L.blur.loc.u_img, 0);
			bindOutput(gl, t1, hw, hh);
			useTex(gl, 0, last.tex);
			gl.uniform2f(L.blur.loc.u_step, scale / w, 0);
			gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
			bindOutput(gl, t2, hw, hh);
			useTex(gl, 0, t1.tex);
			gl.uniform2f(L.blur.loc.u_step, 0, scale / h);
			gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
			gl.useProgram(L.details.prog);
			gl.uniform1i(L.details.loc.u_img, 0);
			gl.uniform1i(L.details.loc.u_large, 1);
			gl.uniform2f(L.details.loc.u_px, 1 / w, -1 / h);
			gl.uniform4f(L.details.loc.u_look, look.sharpen, look.clarity, look.hdr, look.bloom);
			useTex(gl, 0, last.tex);
			useTex(gl, 1, t2.tex);
			bindOutput(gl, cas ? t3 : null, w, h);
			gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
			last = t3;
		}
		if (cas) {
			gl.useProgram(L.cas.prog);
			gl.uniform1i(L.cas.loc.u_img, 0);
			gl.uniform2f(L.cas.loc.u_px, 1 / w, 1 / h);
			gl.uniform1f(L.cas.loc.u_sharp, look.sharpenPlus);
			useTex(gl, 0, last.tex);
			bindOutput(gl, null, w, h);
			gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
		}
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
		for (const n of ["u_video", "u_ax_a", "u_ax_b", "u_ay_a", "u_ay_b", "u_box", "u_texel", "u_nrgn", "u_rgn", "u_slot"]) loc[n] = gl.getUniformLocation(prog, n);
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
		const dpr = window.devicePixelRatio || 1;
		const cw = Math.max(1, Math.round(w * dpr)), ch = Math.max(1, Math.round(h * dpr));
		if (s.canvas.width !== cw || s.canvas.height !== ch) {
			s.canvas.width = cw;
			s.canvas.height = ch;
		}
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
				} catch (e) {
					console.warn("[DBpO Unsquish] WebGL setup failed:", e);
					state = null;
				}
			}
			if (state) {
				const { gl, loc } = state;
				layout(state, cfg);
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
				const ux = FSQ.uniforms(cfg.x), uy = FSQ.uniforms(cfg.y);
				gl.uniform1i(loc.u_video, 0);
				gl.uniform4fv(loc.u_ax_a, ux.A);
				gl.uniform4fv(loc.u_ax_b, ux.B);
				gl.uniform4fv(loc.u_ay_a, uy.A);
				gl.uniform4fv(loc.u_ay_b, uy.B);
				gl.uniform4fv(loc.u_box, cfg.box);
				gl.uniform2f(loc.u_texel, 1 / Math.max(video.videoWidth, 1), 1 / Math.max(video.videoHeight, 1));
				// Sharp regions (atlas): paste each one from its slot in the frame.
				{
					const vw = Math.max(video.videoWidth, 1), vh = Math.max(video.videoHeight, 1);
					const slots = cfg.regions && cfg.regions.length ? FSQ.atlasLayout(cfg, vw, vh) || [] : [];
					const rgn = new Float32Array(16), slot = new Float32Array(16);
					slots.forEach((sl, i) => {
						const r = cfg.regions[i];
						rgn.set([r.x0, r.y0, r.x1, r.y1], i * 4);
						slot.set([sl.x / vw, sl.y / vh, sl.w / vw, sl.h / vh], i * 4);
					});
					gl.uniform1i(loc.u_nrgn, slots.length);
					gl.uniform4fv(loc.u_rgn, rgn);
					gl.uniform4fv(loc.u_slot, slot);
				}
				let lookOn = FSQ.lookActive(look) && !state.lookFailed;
				if (lookOn) {
					try {
						drawWithLook(state, look, () => gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4));
					} catch (e) {
						console.warn("[DBpO Unsquish] viewer look unavailable:", e);
						state.lookFailed = true;
						lookOn = false;
						useTexReset(gl, state);
					}
				}
				if (!lookOn) {
					gl.bindFramebuffer(gl.FRAMEBUFFER, null);
					gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
				}
				document.documentElement.dataset.dbpoUnsquish = plain ? "plain" : cfg.text || "custom";
				document.documentElement.dataset.dbpoLook = lookOn
					? FSQ.LOOK_FIELDS.map(([k]) => +look[k].toFixed(3)).join(",")
					: "";
			}
		}
		requestAnimationFrame(draw);
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
			chrome.storage.local.set({ detected: detected ? detected.text : "" });
		} catch (e) {
			// extension was updated/removed; the new copy handles this page
		}
	}

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
