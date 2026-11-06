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
			// Stream uses starvation levels (sl=1): read the level marker every frame (starvation.js).
			starve: g("sl", 0) === 1,
			// Where the squished picture sits in the video frame (black bars around it).
			box: [g("ox", 0), g("oy", 0), g("bw", 1), g("bh", 1)],
			// Viewer look, applied after unsquishing (0..1 each, 0 = off). Older
			// strings have none; older extensions ignore these keys.
			look: {
				...NO_LOOK,
				sharpen: clamp01(g("ls", 0)),
				clarity: clamp01(g("lc", 0)),
				hdr: clamp01(g("lh", 0)),
				bloom: clamp01(g("lb", 0)),
				sharpenPlus: clamp01(g("lp", 0)),
			},
			text: m[0],
		};
		// A look code ("※" + CJK characters) right after the numbers carries the whole look.
		const lc = /\u203b([\u4e00-\u8dff]+)/.exec((text || "").slice(m.index + m[0].length, m.index + m[0].length + 40));
		const packed = lc ? unpackLook(lc[1]) : null;
		if (packed) {
			cfg.look = { ...NO_LOOK, ...packed };
			cfg.text = m[0] + LOOK_MARK + lc[1];
		}
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

	/* Look values in code order (same as research/foveation/color_math.py): [name, label, signed]. */
	const LOOK_FIELDS = [
		["sharpen", "Sharpen", false], ["clarity", "Clarity", false], ["hdr", "HDR toning", false],
		["bloom", "Bloom", false], ["sharpenPlus", "Sharpen+", false],
		["brightness", "Brightness", true], ["contrast", "Contrast", true], ["gamma", "Gamma", true],
		["saturation", "Saturation", true], ["vibrance", "Vibrance", true], ["temperature", "Temperature", true],
		["tint", "Tint", true], ["hue", "Hue", true], ["highlights", "Highlights", true], ["shadows", "Shadows", true],
		["bw", "Black & white", false], ["sepia", "Sepia", false], ["night", "Night mode", false],
	];
	const NO_LOOK = Object.freeze(Object.fromEntries(LOOK_FIELDS.map(([k]) => [k, 0])));
	const COLOR_KEYS = ["brightness", "contrast", "gamma", "saturation", "vibrance", "temperature", "tint", "hue",
		"highlights", "shadows", "bw", "sepia", "night"];

	/** Any look value away from zero? */
	function lookActive(l) {
		return !!l && LOOK_FIELDS.some(([k]) => (Number(l[k]) || 0) !== 0);
	}

	/** Any colour-correction value away from zero (needs the colour pass)? */
	function colorActive(l) {
		return !!l && COLOR_KEYS.some((k) => (Number(l[k]) || 0) !== 0);
	}

	/** Clamped copy of a look from storage or a string (missing values = 0). */
	function cleanLook(l) {
		const out = {};
		for (const [k, , signed] of LOOK_FIELDS) {
			const n = Number(l && l[k]) || 0;
			out[k] = signed ? Math.max(-1, Math.min(1, n)) : clamp01(n);
		}
		return out;
	}

	/*
	 * Look code: "※" + CJK characters (14 bits each, U+4E00..) carrying every look value, so a
	 * title or chat line holds the whole look in about 11 characters. Bits: version (2), then 7 bits
	 * per LOOK_FIELDS entry. Same format as color_math.py.
	 */
	const LOOK_MARK = "※";
	function packLook(l) {
		const c = cleanLook(l);
		const qs = LOOK_FIELDS.map(([k, , signed]) => (signed ? Math.floor(c[k] * 63 + 0.5) + 64 : Math.floor(c[k] * 127 + 0.5)));
		if (qs.every((q, i) => q === (LOOK_FIELDS[i][2] ? 64 : 0))) return "";
		const bits = [0, 1];
		for (const q of qs) for (let i = 6; i >= 0; i--) bits.push((q >> i) & 1);
		while (bits.length % 14) bits.push(0);
		let out = "";
		for (let i = 0; i < bits.length; i += 14) {
			let n = 0;
			for (let j = 0; j < 14; j++) n = (n << 1) | bits[i + j];
			out += String.fromCharCode(0x4e00 + n);
		}
		return LOOK_MARK + out;
	}

	/** Look from a code (the mark is optional), or null if it is not a valid code. */
	function unpackLook(code) {
		code = String(code || "");
		if (code.startsWith(LOOK_MARK)) code = code.slice(1);
		if (code.length * 14 < 2 + 7 * LOOK_FIELDS.length) return null;
		const bits = [];
		for (const ch of code) {
			const n = ch.charCodeAt(0) - 0x4e00;
			if (!(n >= 0 && n < 16384)) return null;
			for (let i = 13; i >= 0; i--) bits.push((n >> i) & 1);
		}
		if (((bits[0] << 1) | bits[1]) !== 1) return null;
		const look = {};
		let pos = 2;
		for (const [k, , signed] of LOOK_FIELDS) {
			let q = 0;
			for (let i = 0; i < 7; i++) q = (q << 1) | bits[pos + i];
			pos += 7;
			look[k] = signed ? (q - 64) / 63 : q / 127;
		}
		return look;
	}

	/** A config that leaves the picture as it is (forward(x) = x): for plain, non-squished videos. */
	function identity(ar) {
		const a = { r: 1, c: 0.5, f: 0.5, p: 1 };
		return { x: { ...a }, y: { ...a }, ar: ar > 0 ? ar : 16 / 9, box: [0, 0, 1, 1], look: { ...NO_LOOK }, text: "", starve: false };
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
uniform int u_kernel;   // 0 bilinear, 1 Lanczos3 (squeezed periphery only; the 1:1 center stays bilinear)
varying vec2 v_uv;
float lz3(float x) {
	x = abs(x);
	if (x < 1e-4) return 1.0;
	if (x >= 3.0) return 0.0;
	float px = 3.14159265 * x;
	return 3.0 * sin(px) * sin(px / 3.0) / (px * px);
}
vec4 sample_lanczos(vec2 u) {
	vec2 p = u / u_texel - 0.5, f = floor(p);
	vec2 lo = u_box.xy + 0.5 * u_texel, hi = u_box.xy + u_box.zw - 0.5 * u_texel;
	vec4 acc = vec4(0.0);
	float wsum = 0.0;
	for (int j = -2; j <= 3; j++) {
		float wy = lz3(p.y - (f.y + float(j)));
		for (int i = -2; i <= 3; i++) {
			float w = lz3(p.x - (f.x + float(i))) * wy;
			vec2 q = clamp((f + vec2(float(i), float(j)) + 0.5) * u_texel, lo, hi);
			acc += texture2D(u_video, q) * w;
			wsum += w;
		}
	}
	return clamp(acc / wsum, 0.0, 1.0);
}
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
	bool center = v_uv.x >= u_ax_a.x && v_uv.x <= u_ax_a.y && v_uv.y >= u_ay_a.x && v_uv.y <= u_ay_a.y;
	if (u_kernel == 1 && !center) gl_FragColor = vec4(sample_lanczos(u).rgb, 1.0);
	else gl_FragColor = texture2D(u_video, u);
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

	/*
	 * Colour correction pass (before the Details and Sharpen+ passes). Math: research/foveation/color_math.py
	 * (apply_color); u_a = brightness, contrast, gamma, saturation; u_b = vibrance, temperature, tint, hue;
	 * u_c = highlights, shadows, bw, sepia; u_n = night.
	 */
	const LOOK_COLOR_SHADER = `
precision highp float;
uniform sampler2D u_img;
uniform vec4 u_a;
uniform vec4 u_b;
uniform vec4 u_c;
uniform float u_n;
varying vec2 v_uv;
const vec3 W = vec3(0.2126, 0.7152, 0.0722);
void main() {
	vec4 src = texture2D(u_img, v_uv);
	vec3 c = src.rgb;
	c.r += u_b.y * 0.10 + u_b.z * 0.04;
	c.b += -u_b.y * 0.10 + u_b.z * 0.04;
	c.g -= u_b.z * 0.08;
	float l = clamp(dot(c, W), 0.0, 1.0);
	c += u_c.x * 0.25 * smoothstep(0.5, 1.0, l) + u_c.y * 0.25 * (1.0 - smoothstep(0.0, 0.5, l));
	c += u_a.x * 0.5;
	c = (c - 0.5) * (1.0 + u_a.y) + 0.5;
	c = clamp(c, 0.0, 1.0);
	c = pow(c, vec3(exp2(-u_a.z)));
	if (u_b.w != 0.0) {
		float ang = u_b.w * 3.14159265358979;
		float ca = cos(ang), sa = sin(ang);
		float y = dot(c, vec3(0.299, 0.587, 0.114));
		float i = dot(c, vec3(0.596, -0.274, -0.322));
		float q = dot(c, vec3(0.211, -0.523, 0.312));
		float i2 = i * ca - q * sa, q2 = i * sa + q * ca;
		c = clamp(vec3(y + 0.956 * i2 + 0.621 * q2, y - 0.272 * i2 - 0.647 * q2, y - 1.106 * i2 + 1.703 * q2), 0.0, 1.0);
	}
	l = dot(c, W);
	float sat = max(max(c.r, c.g), c.b) - min(min(c.r, c.g), c.b);
	float f = max(1.0 + u_a.w + 1.5 * u_b.x * (1.0 - sat), 0.0);
	c = clamp(vec3(l) + (c - vec3(l)) * f, 0.0, 1.0);
	l = dot(c, W);
	c += (vec3(l) - c) * u_c.z;
	vec3 sep = clamp(vec3(dot(c, vec3(0.393, 0.769, 0.189)), dot(c, vec3(0.349, 0.686, 0.168)), dot(c, vec3(0.272, 0.534, 0.131))), 0.0, 1.0);
	c += (sep - c) * u_c.w;
	vec3 night = clamp(pow(c, vec3(0.8)) * vec3(0.7, 0.85, 1.15), 0.0, 1.0);
	c += (night - c) * u_n;
	gl_FragColor = vec4(clamp(c, 0.0, 1.0), src.a);
}`;

	/*
	 * Viewer-side edge enhancement ("reconstruction"): extra passes after unsquishing that make the
	 * stretched periphery look crisper. They cost the streamer no bitrate and change nothing in the
	 * center. Models: research/foveation/viewer_restoration.py and viewer_snap_preview.py (the GPU
	 * versions are cheaper approximations: 5x5 bilateral deblock in sqrt-encoded RGB, one steepening
	 * iteration, 3x3 median flat merge).
	 */
	const PERIPH_GLSL = `
precision highp float;
uniform vec4 u_ax_a; uniform vec4 u_ax_b; uniform vec4 u_ay_a; uniform vec4 u_ay_b;
uniform float u_area;   // 0 = squeezed periphery only, 1 = whole picture
uniform int u_nrgn;
uniform vec4 u_rgn[4];  // sharp regions (atlas) are left alone
float dens(float x, vec4 A, vec4 B) {
	float t = 0.0;
	if (x < A.x) t = (A.x - x) / max(A.z, 1e-6);
	else if (x > A.y) t = (x - A.y) / max(A.w, 1e-6);
	return 1.0 - (1.0 - B.w) * pow(clamp(t, 0.0, 1.0), B.y);
}
// uv: picture position, top-down. 0 in the sharp center, up to 1 at the squeezed edges.
float weight_at(vec2 uv) {
	for (int i = 0; i < 4; i++) {
		if (i >= u_nrgn) break;
		vec4 r = u_rgn[i];
		if (uv.x >= r.x && uv.x <= r.z && uv.y >= r.y && uv.y <= r.w) return 0.0;
	}
	if (u_area > 0.5) return 1.0;
	float wx = (1.0 - dens(uv.x, u_ax_a, u_ax_b)) / max(1.0 - u_ax_b.w, 1e-3);
	float wy = (1.0 - dens(uv.y, u_ay_a, u_ay_b)) / max(1.0 - u_ay_b.w, 1e-3);
	return clamp(max(wx, wy) * 1.5, 0.0, 1.0);
}
varying vec2 v_uv;
uniform sampler2D u_img;
uniform vec2 u_px;
`;

	// Deblock / denoise: 5x5 edge-preserving (bilateral) smoothing, strength = range sigma.
	const ENH_DEBLOCK_SHADER = PERIPH_GLSL + `
uniform float u_amt;
vec3 enc(vec3 c) { return sqrt(max(c, 0.0)); }
void main() {
	vec4 c4 = texture2D(u_img, v_uv);
	float w = weight_at(vec2(v_uv.x, 1.0 - v_uv.y));
	if (w <= 0.0) { gl_FragColor = c4; return; }
	float sig = mix(0.012, 0.05, u_amt);
	vec3 base = enc(c4.rgb), acc = vec3(0.0);
	float ws = 0.0;
	for (int j = -2; j <= 2; j++) {
		for (int i = -2; i <= 2; i++) {
			vec3 s = texture2D(u_img, v_uv + vec2(float(i), float(j)) * u_px).rgb;
			vec3 d = enc(s) - base;
			float wt = exp(-dot(d, d) / (2.0 * sig * sig) - float(i * i + j * j) / 4.5);
			acc += s * wt;
			ws += wt;
		}
	}
	gl_FragColor = vec4(mix(c4.rgb, acc / ws, w), c4.a);
}`;

	// Edge pass: sharpen, edge snap (steepen edges + median-merge flat areas), vibrance,
	// posterize + ordered dither, grain. u_a = sharpen, snap, vibrance, posterize; u_b = dither, grain.
	const ENH_EDGE_SHADER = PERIPH_GLSL + `
uniform vec4 u_a;
uniform vec2 u_b;
uniform float u_frame;
float luma(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }
vec3 tap(float x, float y) { return texture2D(u_img, v_uv + vec2(x, y) * u_px).rgb; }
#define SORT(a, b) { vec3 t_ = min(a, b); b = max(a, b); a = t_; }
float bayer2(vec2 a) { a = floor(a); return fract(a.x * 0.5 + a.y * a.y * 0.75); }
float bayer4(vec2 a) { return bayer2(0.5 * a) * 0.25 + bayer2(a); }
void main() {
	vec4 c4 = texture2D(u_img, v_uv);
	float w = weight_at(vec2(v_uv.x, 1.0 - v_uv.y));
	if (w <= 0.0) { gl_FragColor = c4; return; }
	vec3 c = c4.rgb;
	vec3 p0 = tap(-1.0, -1.0), p1 = tap(0.0, -1.0), p2 = tap(1.0, -1.0);
	vec3 p3 = tap(-1.0, 0.0), p4 = c, p5 = tap(1.0, 0.0);
	vec3 p6 = tap(-1.0, 1.0), p7 = tap(0.0, 1.0), p8 = tap(1.0, 1.0);
	vec3 blur = (p0 + p2 + p6 + p8 + 2.0 * (p1 + p3 + p5 + p7) + 4.0 * p4) / 16.0;
	vec3 o = c + u_a.x * 0.8 * (c - blur); // sharpen
	if (u_a.y > 0.0) {
		float y0 = luma(p0), y1 = luma(p1), y2 = luma(p2), y3 = luma(p3), y5 = luma(p5), y6 = luma(p6), y7 = luma(p7), y8 = luma(p8);
		float g = length(vec2((y2 + 2.0 * y5 + y8) - (y0 + 2.0 * y3 + y6), (y6 + 2.0 * y7 + y8) - (y0 + 2.0 * y1 + y2)));
		vec3 lo = min(min(min(p0, p1), min(p2, p3)), min(min(p4, p5), min(p6, min(p7, p8))));
		vec3 hi = max(max(max(p0, p1), max(p2, p3)), max(max(p4, p5), max(p6, max(p7, p8))));
		vec3 target = (luma(blur) - luma(p4)) > 0.0 ? lo : hi; // steepen: pull toward the nearer extreme
		float a = clamp(g * 6.0, 0.0, 1.0) * u_a.y * 0.8;
		o = o * (1.0 - 0.5 * a) + target * 0.5 * a;
		// flat areas take the 3x3 median (Pixel Snapper "merge")
		vec3 v0 = p0, v1 = p1, v2 = p2, v3 = p3, v4 = p4, v5 = p5, v6 = p6, v7 = p7, v8 = p8;
		SORT(v1, v2) SORT(v4, v5) SORT(v7, v8) SORT(v0, v1) SORT(v3, v4) SORT(v6, v7)
		SORT(v1, v2) SORT(v4, v5) SORT(v7, v8) SORT(v0, v3) SORT(v5, v8) SORT(v4, v7)
		SORT(v3, v6) SORT(v1, v4) SORT(v2, v5) SORT(v4, v7) SORT(v4, v2) SORT(v6, v4) SORT(v4, v2)
		float flatness = 1.0 - smoothstep(0.1, 0.2, clamp(g * 4.0, 0.0, 1.0));
		o = mix(o, v4, flatness * clamp(u_a.y * 2.0, 0.0, 1.0));
	}
	if (u_a.z > 0.0) { // vibrance: low-saturation pixels gain most
		float y = luma(o), sat = max(max(o.r, o.g), o.b) - min(min(o.r, o.g), o.b);
		o = vec3(y) + (o - vec3(y)) * (1.0 + u_a.z * 0.5 * (1.0 - sat));
	}
	if (u_a.w > 0.0 || u_b.x > 0.0) { // posterize (255 -> 31 levels) with ordered dither
		float levels = mix(255.0, 31.0, u_a.w);
		float m = bayer4(gl_FragCoord.xy) - 0.46875;
		o = floor(o * levels + m * u_b.x * 2.0 + 0.5) / levels;
	}
	if (u_b.y > 0.0) {
		float n = fract(sin(dot(gl_FragCoord.xy + u_frame, vec2(12.9898, 78.233))) * 43758.5453);
		o += (n - 0.5) * u_b.y * (8.0 / 255.0);
	}
	gl_FragColor = vec4(clamp(mix(c, o, w), 0.0, 1.0), c4.a);
}`;

	/* Enhancement settings: [name, label]; all 0..1, 0 = off. */
	const ENHANCE_FIELDS = [
		["deblock", "Deblock"], ["sharpen", "Edge sharpen"], ["snap", "Edge snap"], ["vibrance", "Edge vibrance"],
		["posterize", "Posterize"], ["dither", "Dither"], ["grain", "Grain"],
	];
	const NO_ENHANCE = Object.freeze({
		kernel: "bilinear", area: "periphery", ...Object.fromEntries(ENHANCE_FIELDS.map(([k]) => [k, 0])),
	});
	const ENHANCE_PRESETS = {
		off: { ...NO_ENHANCE },
		light: { ...NO_ENHANCE, kernel: "lanczos", deblock: 0.3, sharpen: 0.5 },
		balanced: { ...NO_ENHANCE, kernel: "lanczos", deblock: 0.5, sharpen: 0.4, snap: 0.5, vibrance: 0.2 },
		crisp: { ...NO_ENHANCE, kernel: "lanczos", deblock: 0.5, snap: 1, vibrance: 0.4, posterize: 0.6, dither: 0.3 },
	};

	/** Clamped copy of enhancement settings from storage (missing values = off). */
	function cleanEnhance(e) {
		const out = { kernel: e && e.kernel === "lanczos" ? "lanczos" : "bilinear", area: e && e.area === "all" ? "all" : "periphery" };
		for (const [k] of ENHANCE_FIELDS) out[k] = clamp01(Number(e && e[k]) || 0);
		return out;
	}

	/** Which render stages an enhancement needs. */
	function enhanceStages(e) {
		return {
			lanczos: e.kernel === "lanczos",
			deblock: e.deblock > 0,
			edge: e.sharpen > 0 || e.snap > 0 || e.vibrance > 0 || e.posterize > 0 || e.dither > 0 || e.grain > 0,
		};
	}

	function enhanceActive(e) {
		if (!e) return false;
		const st = enhanceStages(e);
		return st.lanczos || st.deblock || st.edge;
	}

	/** Rough texture reads per pixel of the whole draw (periphery pixels), for the debug overlay. */
	function readsPerPixel(e, look) {
		let n = 1; // unwarp
		const parts = ["unwarp"];
		if (e && e.kernel === "lanczos") { n += 35; parts[0] = "unwarp lanczos3"; }
		if (e && e.deblock > 0) { n += 26; parts.push("deblock"); }
		const st = e ? enhanceStages(e) : {};
		if (st.edge) { n += 10; parts.push("edge"); }
		if (colorActive(look)) { n += 1; parts.push("colour"); }
		if (look && (look.sharpen > 0 || look.clarity > 0 || look.hdr > 0 || look.bloom > 0)) { n += 36; parts.push("details"); }
		if (look && look.sharpenPlus > 0) { n += 10; parts.push("sharpen+"); }
		return { reads: n, parts };
	}

	/** The page a pasted config belongs to: host + first path segment ("www.twitch.tv/somechannel"). */
	function pageKey(href) {
		try {
			const u = new URL(href);
			return u.hostname.toLowerCase() + "/" + (u.pathname.split("/")[1] || "").toLowerCase();
		} catch (e) {
			return "";
		}
	}

	const api = {
		MIN_DENSITY, derive, forward, density, inverse, parse, uniforms, FRAGMENT_SHADER, atlasLayout,
		NO_LOOK, lookActive, colorActive, cleanLook, identity, pageKey, LOOK_BLUR_SHADER, LOOK_DETAILS_SHADER, LOOK_CAS_SHADER,
		LOOK_COLOR_SHADER, LOOK_FIELDS, packLook, unpackLook,
		ENH_DEBLOCK_SHADER, ENH_EDGE_SHADER, ENHANCE_FIELDS, NO_ENHANCE, ENHANCE_PRESETS, cleanEnhance, enhanceStages,
		enhanceActive, readsPerPixel,
	};
	if (typeof module !== "undefined" && module.exports) module.exports = api;
	else root.FSQ = api;
})(typeof self !== "undefined" ? self : this);
