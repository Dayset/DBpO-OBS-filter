/*
 * Foveated Squish mapping (browser side). Mirrors research/foveation/squish_math.py
 * and plugins/foveated-squish/src/squish-math.c; tests/compare.js checks all three agree.
 *
 * Per axis, normalized: density d(x) = 1 in the sharp center, 1 - (1 - dmin) t^p in the
 * periphery (t: 0 at the center edge .. 1 at the frame edge); U(x) = integral(d) / r.
 */
(function (root) {
	const MIN_DENSITY = 0.08;

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
			text: m[0],
		};
		const [ox, oy, bw, bh] = cfg.box;
		if (!(ox >= 0 && oy >= 0 && bw > 0 && bh > 0 && ox + bw <= 1 + 1e-6 && oy + bh <= 1 + 1e-6)) return null;
		for (const a of [cfg.x, cfg.y]) {
			if (!(a.r > 0 && a.r <= 1 && a.p > 0 && a.c >= 0 && a.f >= 0 && a.f <= 1)) return null;
			if (derive(a).dmin < MIN_DENSITY - 1e-6) return null;
		}
		if (!(cfg.ar > 0)) return null;
		return cfg;
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
	vec2 u = vec2(sq_forward(v_uv.x, u_ax_a, u_ax_b), sq_forward(v_uv.y, u_ay_a, u_ay_b));
	u = u_box.xy + u * u_box.zw;
	// Stay half a pixel inside the picture so bilinear filtering never pulls in the bars.
	u = clamp(u, u_box.xy + 0.5 * u_texel, u_box.xy + u_box.zw - 0.5 * u_texel);
	gl_FragColor = texture2D(u_video, u);
}`;

	const api = { MIN_DENSITY, derive, forward, density, parse, uniforms, FRAGMENT_SHADER };
	if (typeof module !== "undefined" && module.exports) module.exports = api;
	else root.FSQ = api;
})(typeof self !== "undefined" ? self : this);
