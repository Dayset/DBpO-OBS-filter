/*
 * DBpO Unsquish: finds the page's main <video>, hides it, and draws it into a
 * WebGL canvas through the inverse squish mapping (squish.js). The canvas sits
 * in the video's own container, so it follows the player into fullscreen.
 *
 * Config comes from the popup (pasted FSQ1 string) or, with auto-detect on,
 * from an "FSQ1:..." tag in the page/stream title.
 */
(() => {
	"use strict";
	const VERT = `
attribute vec2 a_pos;
varying vec2 v_uv;
void main() { v_uv = vec2(a_pos.x * 0.5 + 0.5, 0.5 - a_pos.y * 0.5); gl_Position = vec4(a_pos, 0.0, 1.0); }`;

	// One live copy per page: after an extension update a fresh copy is injected
	// and the old one (cut off from settings) must step aside.
	const GEN = String(Date.now()) + Math.random();
	document.documentElement.dataset.dbpoUnsquishGen = GEN;
	const superseded = () => document.documentElement.dataset.dbpoUnsquishGen !== GEN;
	for (const old of document.querySelectorAll("canvas[data-dbpo-unsquish]")) old.remove();

	let settings = { enabled: true, autoDetect: true, config: "" };
	let detected = null; // config found in the page
	let state = null; // { video, canvas, gl, prog, loc, tex, cfg }

	function activeConfig() {
		if (!settings.enabled) return null;
		return FSQ.parse(settings.config) || (settings.autoDetect ? detected : null);
	}

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

	function attach(video) {
		const canvas = document.createElement("canvas");
		canvas.dataset.dbpoUnsquish = "1";
		// Directly above the video and below everything the player draws after it
		// (controls, overlays); clicks go through to the player.
		Object.assign(canvas.style, { position: "absolute", pointerEvents: "none" });
		const gl = canvas.getContext("webgl", { premultipliedAlpha: false, preserveDrawingBuffer: true });
		if (!gl) return null;
		const prog = gl.createProgram();
		gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT));
		gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FSQ.FRAGMENT_SHADER));
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
		for (const n of ["u_video", "u_ax_a", "u_ax_b", "u_ay_a", "u_ay_b", "u_box", "u_texel"]) loc[n] = gl.getUniformLocation(prog, n);
		const parent = video.parentElement;
		if (getComputedStyle(parent).position === "static") parent.style.position = "relative";
		video.insertAdjacentElement("afterend", canvas);
		video.dataset.dbpoOpacity = video.style.opacity;
		video.style.opacity = "0";
		return { video, canvas, gl, prog, loc, tex };
	}

	function detach() {
		if (!state) return;
		state.canvas.remove();
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
		const cfg = activeConfig();
		const video = cfg ? findVideo() : null;
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
					settings.enabled = false;
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
				gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
				document.documentElement.dataset.dbpoUnsquish = cfg.text || "custom";
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
