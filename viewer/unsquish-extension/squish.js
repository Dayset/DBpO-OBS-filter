/*
 * Foveated Squish mapping (browser side). Mirrors research/foveation/squish_math.py
 * and plugins/foveated-squish/src/squish-math.c; tests/compare.js checks all three agree.
 *
 * Per axis, normalized: density d(x) = 1 in the sharp center, 1 - (1 - dmin) t^p in the
 * periphery (t: 0 at the center edge .. 1 at the frame edge); U(x) = integral(d) / r.
 */
(function (root) {
	const MIN_DENSITY = 0.08; // plugin default edge density (12.5:1); the viewer accepts any > 0

	function derive(a) {
		const lo = Math.min(Math.max(a.f - a.c, 0), 1);
		const hi = Math.min(Math.max(a.f + a.c, 0), 1);
		const ll = lo, lr = 1 - hi, total = ll + lr;
		const dmin = total <= 1e-9 ? 1 : 1 - (1 - a.r) * (a.p + 1) / total;
		return { lo, hi, ll, lr, dmin, k: (1 - dmin) / (a.p + 1), p: a.p, r: a.r };
	}

	const clamp01 = (v) => Math.min(Math.max(v, 0), 1);
	const periph = (t, len, k, p) => len * (t - k * Math.pow(t, p + 1));

	function forward(a, x) {
		const o = derive(a);
		const leftFull = periph(1, o.ll, o.k, o.p);
		let u;
		if (x < o.lo) u = leftFull - periph(clamp01((o.lo - x) / Math.max(o.ll, 1e-12)), o.ll, o.k, o.p);
		else if (x <= o.hi) u = leftFull + (x - o.lo);
		else u = leftFull + (o.hi - o.lo) + periph(clamp01((x - o.hi) / Math.max(o.lr, 1e-12)), o.lr, o.k, o.p);
		return u / a.r;
	}

	function density(a, x) {
		const o = derive(a);
		let t = 0;
		if (x < o.lo) t = (o.lo - x) / Math.max(o.ll, 1e-12);
		else if (x > o.hi) t = (x - o.hi) / Math.max(o.lr, 1e-12);
		return 1 - (1 - o.dmin) * Math.pow(clamp01(t), o.p);
	}

	/** Squished position -> source position (same safeguarded solver as the plugin). */
	function inverse(a, u) {
		const o = derive(a);
		const solve = (target, len) => {
			if (len <= 1e-12) return 0;
			let lo = 0, hi = 1, t = clamp01(target / len);
			for (let i = 0; i < 20; i++) {
				const f = len * (t - o.k * Math.pow(t, o.p + 1)) - target;
				if (f > 0) hi = t;
				else lo = t;
				const d = len * (1 - o.k * (o.p + 1) * Math.pow(t, o.p));
				let tn = d > 1e-12 ? t - f / d : 0.5 * (lo + hi);
				if (!(tn >= lo && tn <= hi)) tn = 0.5 * (lo + hi);
				t = tn;
			}
			return t;
		};
		const v = clamp01(u) * a.r, leftFull = periph(1, o.ll, o.k, o.p);
		if (v < leftFull) return clamp01(o.lo - o.ll * solve(leftFull - v, o.ll));
		if (v <= leftFull + (o.hi - o.lo)) return o.lo + (v - leftFull);
		return clamp01(o.hi + o.lr * solve(v - leftFull - (o.hi - o.lo), o.lr));
	}

	/** Parses "FSQ1:rx=..,...,ar=..[,ox=..,oy=..,bw=..,bh=..]". Returns null if invalid. */
	function parse(text) {
		const m = /FSQ1:([a-z]{2}=[-0-9.eE]+(?:,[a-z]{2}=[-0-9.eE]+)*)/.exec(text || "");
		if (!m) return null;
		const kv = {};
		for (const item of m[1].split(",")) {
			const [k, v] = item.split("=");
			const n = Number(v);
			if (!Number.isFinite(n)) return null;
			kv[k] = n;
		}
		const g = (k, d) => (k in kv ? kv[k] : d);
		const cfg = {
			x: { r: g("rx", 0.75), c: g("cx", 0.15), f: g("fx", 0.5), p: g("px", 1) },
			y: { r: g("ry", 0.75), c: g("cy", 0.15), f: g("fy", 0.5), p: g("py", 1) },
			ar: g("ar", 16 / 9),
			// Where the squished picture sits in the video frame (black bars around it).
			box: [g("ox", 0), g("oy", 0), g("bw", 1), g("bh", 1)],
			// Viewer look, applied after unsquishing (0..1 each, 0 = off). Older
			// strings have none; older extensions ignore these keys.
			look: {
				sharpen: clamp01(g("ls", 0)),
				clarity: clamp01(g("lc", 0)),
				hdr: clamp01(g("lh", 0)),
				bloom: clamp01(g("lb", 0)),
				sharpenPlus: clamp01(g("lp", 0)),
			},
			text: m[0],
		};
		const [ox, oy, bw, bh] = cfg.box;
		if (!(ox >= 0 && oy >= 0 && bw > 0 && bh > 0 && ox + bw <= 1 + 1e-6 && oy + bh <= 1 + 1e-6)) return null;
		for (const a of [cfg.x, cfg.y]) {
			if (!(a.r > 0 && a.r <= 1 && a.p > 0 && a.c >= 0 && a.f >= 0 && a.f <= 1)) return null;
			// Any positive edge density unfolds correctly; how hard to squeeze is the streamer's choice.
			if (!(derive(a).dmin > 0.001)) return null;
		}
		if (!(cfg.ar > 0)) return null;
		cfg.regions = parseRegions(m[1]);
		if (!cfg.regions) return null;
		return cfg;
	}

	/* Sharp regions (atlas): keys g|h|i|j + x|y|z|v|s, see plugins/foveated-squish/docs/atlas-spec.md. */
	function parseRegions(body) {
		const out = [];
		for (let i = 0; i < 4; i++) {
			const p = "ghij"[i];
			const get = (f) => {
				const mm = new RegExp("(?:^|,)" + p + f + "=([-0-9.eE]+)").exec(body);
				return mm ? Number(mm[1]) : null;
			};
			const v = ["x", "y", "z", "v", "s"].map(get);
			if (v.every((n) => n === null)) break;
			if (v.some((n) => n === null || !Number.isFinite(n))) return null;
			const [x0, y0, x1, y1, sc] = v;
			if (!(x0 >= 0 && y0 >= 0 && x1 <= 1 && y1 <= 1 && x0 < x1 && y0 < y1 && sc > 0 && sc <= 1)) return null;
			out.push({ x0, y0, x1, y1, s: sc });
		}
		if (out.length && !/(^|,)[ghij]x=/.test(body)) return null;
		return out;
	}

	/*
	 * Atlas layout (same packing as research/foveation/atlas_math.py, spec section 3).
	 * Returns slots in stream pixels: { x, y, w, h } of each region's slot (after the
	 * 4 px gutter), or null if the regions do not fit above the density floor.
	 * Slot size depends only on the output size, since the source size cancels.
	 */
	function atlasLayout(cfg, outW, outH) {
		if (!cfg.regions || !cfg.regions.length) return [];
		if (!(outW > 0 && outH > 0)) return null;
		const GUTTER = 4, SNAP = 8;
		const [ox, oy, bw, bh] = cfg.box;
		if (!(bw < 1) === !(bh < 1)) return null; // exactly one axis must be boxed
		const pw = Math.round(bw * outW) & ~1, ph = Math.round(bh * outH) & ~1;
		let sx, sy, sw, sh;
		if (bw < 1) { sx = pw; sy = 0; sw = outW - pw; sh = outH; }
		else { sx = 0; sy = ph; sw = outW; sh = outH - ph; }
		const floorOf = (r) => {
			const dx = density(cfg.x, (r.x0 + r.x1) / 2), dy = density(cfg.y, (r.y0 + r.y1) / 2);
			return Math.max((dx + 1) / 2, (dy + 1) / 2);
		};
		const shelf = (factor) => {
			let cx = sx, cy = sy, rowH = 0;
			const slots = [];
			for (const r of cfg.regions) {
				let s = Math.max(Math.min(r.s * factor, 1), floorOf(r));
				let w = Math.floor((r.x1 - r.x0) * outW * s);
				let h = Math.floor((r.y1 - r.y0) * outH * s);
				w = Math.max(SNAP, Math.ceil(w / SNAP) * SNAP);
				h = Math.max(SNAP, Math.ceil(h / SNAP) * SNAP);
				const cw = w + 2 * GUTTER, ch = h + 2 * GUTTER;
				if (cw > sw || ch > sh) return null;
				if (cx + cw > sx + sw) { cx = sx; cy += rowH; rowH = 0; }
				if (cy + ch > sy + sh) return null;
				slots.push({ x: cx + GUTTER, y: cy + GUTTER, w, h });
				cx += cw;
				rowH = Math.max(rowH, ch);
			}
			return slots;
		};
		let factor = 1;
		for (;;) {
			const slots = shelf(factor);
			if (slots) return slots;
			factor *= 0.95;
			if (cfg.regions.some((r) => factor * r.s < floorOf(r))) return null;
		}
	}

	const NO_LOOK = Object.freeze({ sharpen: 0, clarity: 0, hdr: 0, bloom: 0, sharpenPlus: 0 });

	/** Any look slider above zero? */
	function lookActive(l) {
		return !!l && (l.sharpen > 0 || l.clarity > 0 || l.hdr > 0 || l.bloom > 0 || l.sharpenPlus > 0);
	}

	/** Clamped copy of a look from storage or a string (missing values = 0). */
	function cleanLook(l) {
		const v = (k) => clamp01(Number(l && l[k]) || 0);
		return { sharpen: v("sharpen"), clarity: v("clarity"), hdr: v("hdr"), bloom: v("bloom"), sharpenPlus: v("sharpenPlus") };
	}

	/** A config that leaves the picture as it is (forward(x) = x): for plain, non-squished videos. */
	function identity(ar) {
		const a = { r: 1, c: 0.5, f: 0.5, p: 1 };
		return { x: { ...a }, y: { ...a }, ar: ar > 0 ? ar : 16 / 9, box: [0, 0, 1, 1], look: { ...NO_LOOK }, text: "" };
	}

	/** Uniform values for the unwarp shader: per axis A = (lo, hi, ll, lr), B = (k, p, r, dmin). */
	function uniforms(a) {
		const o = derive(a);
		return { A: [o.lo, o.hi, o.ll, o.lr], B: [o.k, o.p, o.r, o.dmin] };
	}

	const FRAGMENT_SHADER = `
precision highp float;
uniform sampler2D u_video;
uniform vec4 u_ax_a; uniform vec4 u_ax_b;
uniform vec4 u_ay_a; uniform vec4 u_ay_b;
uniform vec4 u_box;   // squished picture rectangle in the video frame (x, y, w, h)
uniform vec2 u_texel; // 1 / video size
uniform int u_nrgn;
uniform vec4 u_rgn[4];  // sharp region in view uv: x0, y0, x1, y1
uniform vec4 u_slot[4]; // its slot in the video frame uv: x, y, w, h
varying vec2 v_uv;
float periph(float t, float len, float k, float p) { return len * (t - k * pow(t, p + 1.0)); }
float sq_forward(float x, vec4 A, vec4 B) {
	float leftFull = periph(1.0, A.z, B.x, B.y);
	float u;
	if (x < A.x) u = leftFull - periph(clamp((A.x - x) / max(A.z, 1e-6), 0.0, 1.0), A.z, B.x, B.y);
	else if (x <= A.y) u = leftFull + (x - A.x);
	else u = leftFull + (A.y - A.x) + periph(clamp((x - A.y) / max(A.w, 1e-6), 0.0, 1.0), A.w, B.x, B.y);
	return u / B.z;
}
void main() {
	for (int i = 0; i < 4; i++) {
		if (i >= u_nrgn) break;
		vec4 r = u_rgn[i];
		if (v_uv.x >= r.x && v_uv.x <= r.z && v_uv.y >= r.y && v_uv.y <= r.w) {
			// Sharp region: read it from its slot, at full detail, instead of the squished picture.
			vec2 t = (v_uv - r.xy) / max(r.zw - r.xy, vec2(1e-6));
			vec4 sl = u_slot[i];
			gl_FragColor = texture2D(u_video, sl.xy + t * sl.zw);
			return;
		}
	}
	vec2 u = vec2(sq_forward(v_uv.x, u_ax_a, u_ax_b), sq_forward(v_uv.y, u_ay_a, u_ay_b));
	u = u_box.xy + u * u_box.zw;
	// Stay half a pixel inside the picture so bilinear filtering never pulls in the bars.
	u = clamp(u, u_box.xy + 0.5 * u_texel, u_box.xy + u_box.zw - 0.5 * u_texel);
	gl_FragColor = texture2D(u_video, u);
}`;

	/*
	 * Viewer look shaders (post-process after unsquishing), the same math as the
	 * OBS preview (plugins/foveated-squish/data/foveated_squish.effect).
	 *
	 * Details: NVIDIA Freestyle "Details" (details.yfx, as ported to ReShade):
	 * one large Gaussian (15 linear-sampled steps per side, taps 2i - 0.5 texels
	 * apart, run at half resolution with offsets scaled to a 1080p reference)
	 * feeds HDR toning, clarity and bloom; an 8-tap box blur feeds sharpen.
	 * Sharpen+: approximated with AMD FidelityFX CAS (contrast adaptive sharpening).
	 * Post passes use GL texture orientation (v_uv.y up), see VERT_POST in content.js.
	 */
	const LOOK_BLUR_SHADER = `
precision highp float;
uniform sampler2D u_img;
uniform vec2 u_step; // one blur step in uv (axis * scale / full size)
varying vec2 v_uv;
void main() {
	float norm = -1.35914091423 / 225.0;
	vec4 acc = texture2D(u_img, v_uv) * 0.5;
	float div = 0.5;
	for (int i = 1; i <= 15; i++) {
		float fi = float(i);
		float o = fi * 2.0 - 0.5;
		float w = exp(fi * fi * norm);
		acc += (texture2D(u_img, v_uv + u_step * o) + texture2D(u_img, v_uv - u_step * o)) * w;
		div += w;
	}
	gl_FragColor = acc / (2.0 * div); // normalization as in the original
}`;

	const LOOK_DETAILS_SHADER = `
precision highp float;
uniform sampler2D u_img;
uniform sampler2D u_large;
uniform vec2 u_px;   // 1 / size, y negated (the original works top-down)
uniform vec4 u_look; // sharpen, clarity, HDR toning, bloom
varying vec2 v_uv;
float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }
vec3 tap(vec2 o) { return texture2D(u_img, v_uv + o * u_px).rgb; }
void main() {
	vec4 src = texture2D(u_img, v_uv);
	vec3 color = src.rgb;
	vec3 big = texture2D(u_large, v_uv).rgb;
	vec3 small = ((tap(vec2(0.5, 1.5)) + tap(vec2(1.5, -0.5)) + tap(vec2(-0.5, -1.5)) + tap(vec2(-1.5, 0.5))) * 1.5 +
		tap(vec2(2.5, 1.5)) + tap(vec2(1.5, -2.5)) + tap(vec2(-2.5, -1.5)) + tap(vec2(-1.5, 2.5))) / 10.0;
	float a = luma(color);
	float b = luma(big);
	float ia = 1.0 / max(a, 1e-6);
	// HDR toning (modified soft light)
	float sa = sqrt(a);
	float hdr = sa * (b > 0.5 ? (2.0 * sa * b - 2.0 * b + 1.0) : sa * (2.0 * a * b - a - 2.0 * b + 2.0));
	color = color * ia * mix(a, hdr, u_look.z);
	// Sharpen
	float lim = mix(0.25, 0.6, u_look.x);
	float sh = clamp(luma(color - small), -lim, lim);
	color = color * ia * mix(a, a + sh, u_look.x);
	// Clarity (modified soft light against the large blur)
	float cl = 0.5 + a - b;
	cl = a > b ? 2.0 * (1.0 - cl) + (2.0 * cl - 1.0) * inversesqrt(max(a, 1e-6)) : 2.0 * cl + a * (1.0 - 2.0 * cl);
	color *= mix(1.0, cl, u_look.y);
	// Bloom (screen blend of the large blur)
	color = 1.0 - (1.0 - color) * (1.0 - big * u_look.w);
	gl_FragColor = vec4(clamp(color, 0.0, 1.0), src.a);
}`;

	const LOOK_CAS_SHADER = `
precision highp float;
uniform sampler2D u_img;
uniform vec2 u_px;
uniform float u_sharp; // Sharpen+ 0..1
varying vec2 v_uv;
vec3 tap(float x, float y) { return texture2D(u_img, v_uv + vec2(x, y) * u_px).rgb; }
void main() {
	vec3 a = tap(-1.0, -1.0), b = tap(0.0, -1.0), c = tap(1.0, -1.0);
	vec3 d = tap(-1.0, 0.0), f = tap(1.0, 0.0);
	vec3 g = tap(-1.0, 1.0), h = tap(0.0, 1.0), i = tap(1.0, 1.0);
	vec4 e4 = texture2D(u_img, v_uv);
	vec3 e = e4.rgb;
	vec3 mn = min(min(min(d, e), min(f, b)), h);
	mn += min(mn, min(min(a, c), min(g, i)));
	vec3 mx = max(max(max(d, e), max(f, b)), h);
	mx += max(mx, max(max(a, c), max(g, i)));
	vec3 amp = sqrt(clamp(min(mn, 2.0 - mx) / max(mx, vec3(1e-5)), 0.0, 1.0));
	vec3 w = amp * (-1.0 / mix(8.0, 5.0, u_sharp));
	vec3 o = ((b + d + f + h) * w + e) / (1.0 + 4.0 * w);
	gl_FragColor = vec4(clamp(o, 0.0, 1.0), e4.a);
}`;

	const api = {
		MIN_DENSITY, derive, forward, density, inverse, parse, uniforms, FRAGMENT_SHADER, atlasLayout,
		NO_LOOK, lookActive, cleanLook, identity, LOOK_BLUR_SHADER, LOOK_DETAILS_SHADER, LOOK_CAS_SHADER,
	};
	if (typeof module !== "undefined" && module.exports) module.exports = api;
	else root.FSQ = api;
})(typeof self !== "undefined" ? self : this);
