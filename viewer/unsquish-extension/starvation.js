/*
 * Starvation levels (browser side). Mirrors research/foveation/starvation_math.py and
 * plugins/foveated-squish/src/starvation.c; spec: research/foveation/STARVATION_SPEC.md.
 * tests/starvation.js checks this file against research/foveation/starvation_golden.json.
 *
 * The stream frame keeps its size; at a lower level the picture is drawn smaller in the top-left and a
 * 160x16 marker (5 Manchester bits: 4 code bits + even parity) in the bottom-right says which level.
 */
(function (root) {
	// code -> [name, kind, fraction of the picture rect (plain) / of the frame (squish)]
	const LEVELS = { 0: ["full", "plain", 1], 1: ["720", "plain", 2 / 3], 2: ["540", "plain", 1 / 2], 3: ["360", "plain", 1 / 3], 4: ["squish-810", "squish", 3 / 4], 5: ["squish-540", "squish", 1 / 2] };
	const CELL = 16, BITS = 5, STRIP_W = 160, STRIP_H = 16, MIN_CONTRAST = 4;

	const roundEven = (v) => Math.floor(v + 0.5) & ~1;

	/** Picture size {w, h} of a level inside a pw x ph rect; origin (0, 0). */
	function rect(code, pw, ph) {
		const f = (LEVELS[code] || LEVELS[0])[2];
		return { w: roundEven(f * pw), h: roundEven(f * ph) };
	}

	function markerPos(w, h) {
		return [w - STRIP_W, Math.floor((h - 24) / 16) * 16];
	}

	function markerBits(code) {
		const bits = [0, 1, 2, 3].map((i) => (code >> i) & 1);
		bits.push(bits.reduce((a, b) => a + b, 0) & 1);
		return bits;
	}

	/** Code from the 10 inner-8x8 mean luma values (limited range), or null (parity fail / unreadable). */
	function decodeMeans(m) {
		const bits = [];
		for (let i = 0; i < BITS; i++) {
			const a = m[2 * i], b = m[2 * i + 1];
			if (Math.abs(a - b) < MIN_CONTRAST) return null;
			bits.push(a > b ? 1 : 0);
		}
		if (((bits[0] + bits[1] + bits[2] + bits[3]) & 1) !== bits[4]) return null;
		return bits[0] | (bits[1] << 1) | (bits[2] << 2) | (bits[3] << 3);
	}

	/** Decode the 160x16 strip from RGBA pixels (canvas ImageData.data). */
	function decodeImage(data) {
		const m = [];
		for (let c = 0; c < 2 * BITS; c++) {
			let sum = 0;
			for (let y = 4; y < 12; y++)
				for (let x = c * CELL + 4; x < c * CELL + 12; x++) {
					const i = (y * STRIP_W + x) * 4;
					sum += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
				}
			m.push(16 + (sum / 64) * (219 / 255)); // luma as the encoder stores it
		}
		return decodeMeans(m);
	}

	/** Keeps the last valid level for at most round(0.2 fps) frames (min 3) when the marker is unreadable. */
	class Holder {
		constructor(fps = 60) {
			this.n = Math.max(3, Math.round(0.2 * fps));
			this.last = 0;
			this.age = 0;
		}
		update(code) {
			if (code !== null && code !== undefined) {
				this.last = code in LEVELS ? code : 0; // reserved code -> level 0
				this.age = 0;
			} else if (++this.age > this.n) this.last = 0;
			return this.last;
		}
	}

	/**
	 * What to draw for a level: { box, x, y } (picture rectangle in the frame as fractions, and the axis
	 * configs), or null for level 0. cfg = FSQ.parse result. Plain levels shrink cfg.box (the sharp-region
	 * strip, if any, stays); squish levels (4, 5) re-run the unsquish with r scaled to the smaller picture and
	 * only exist without sharp regions.
	 */
	function levelView(cfg, code, vw, vh) {
		const lv = LEVELS[code];
		if (!lv || code === 0) return null;
		const [ox, oy, bw, bh] = cfg.box;
		if (lv[1] === "plain") {
			const r = rect(code, bw * vw, bh * vh);
			return { box: [ox, oy, r.w / vw, r.h / vh], x: cfg.x, y: cfg.y };
		}
		if (cfg.regions && cfg.regions.length) return null;
		if (!(ox === 0 && oy === 0 && bw === 1 && bh === 1)) return null;
		const r = rect(code, vw, vh);
		return {
			box: [0, 0, r.w / vw, r.h / vh],
			x: { ...cfg.x, r: (r.w * cfg.x.r) / vw },
			y: { ...cfg.y, r: (r.h * cfg.y.r) / vh },
		};
	}

	/* ---------------- marker v2 (spec 9): smooth steps ---------------- */
	const EXT_CODE = 15, A_MAX = 63, Q_MAX = 7, F_MIN = 1 / 6, EXT_CELL = 8, EXT_BITS = 10;
	const frac = (a) => 1 - ((1 - F_MIN) * a) / A_MAX;
	const squeeze = (a, q) => Math.pow(frac(a), q / Q_MAX);
	const aFor = (f) => Math.min(Math.max(Math.floor(((1 - f) / (1 - F_MIN)) * A_MAX + 0.5), 0), A_MAX);
	function rect2(a, pw, ph) {
		const f = frac(a);
		return { w: roundEven(f * pw), h: roundEven(f * ph) };
	}
	function extBits(a, q) {
		const b = [0, 1, 2, 3, 4, 5].map((i) => (a >> i) & 1).concat([0, 1, 2].map((i) => (q >> i) & 1));
		b.push(b.reduce((x, y) => x + y, 0) & 1);
		return b;
	}
	function decodeExtMeans(m) {
		const bits = [];
		for (let i = 0; i < EXT_BITS; i++) {
			const x = m[2 * i], y = m[2 * i + 1];
			if (Math.abs(x - y) < MIN_CONTRAST) return null;
			bits.push(x > y ? 1 : 0);
		}
		if (bits.slice(0, 9).reduce((x, y) => x + y, 0) % 2 !== bits[9]) return null;
		let a = 0, q = 0;
		for (let i = 0; i < 6; i++) a |= bits[i] << i;
		for (let i = 0; i < 3; i++) q |= bits[6 + i] << i;
		return { a, q };
	}
	const lumaLtd = (d, i) => 16 + (0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]) * (219 / 255);
	/**
	 * State {a, q} from the 160x32 strip (RGBA, extension row on top, v1 row below), or null.
	 * v1 codes map to the nearest step (squish codes: q = 7, as their squish ratio is r * f).
	 */
	function decodeStrip(data) {
		const W2 = STRIP_W;
		const v1 = [];
		for (let c = 0; c < 2 * BITS; c++) {
			let sum = 0;
			for (let y = 20; y < 28; y++) for (let x = c * CELL + 4; x < c * CELL + 12; x++) sum += lumaLtd(data, (y * W2 + x) * 4);
			v1.push(sum / 64);
		}
		const code = decodeMeans(v1);
		if (code === null) return null;
		if (code === EXT_CODE) {
			const m = [];
			for (let c = 0; c < 2 * EXT_BITS; c++) {
				let sum = 0;
				for (let y = 4; y < 12; y++) for (let x = c * EXT_CELL + 2; x < c * EXT_CELL + 6; x++) sum += lumaLtd(data, (y * W2 + x) * 4);
				m.push(sum / 32);
			}
			return decodeExtMeans(m);
		}
		if (!(code in LEVELS)) return { a: 0, q: 0 };
		return { a: aFor(LEVELS[code][2]), q: LEVELS[code][1] === "squish" ? Q_MAX : 0 };
	}

	/** Holds the last good state for round(0.2 fps) frames (min 3) when the marker is unreadable, then full size. */
	class StateHolder {
		constructor(fps = 60) {
			this.n = Math.max(3, Math.round(0.2 * fps));
			this.last = { a: 0, q: 0 };
			this.age = 0;
		}
		update(st) {
			if (st) {
				this.last = st;
				this.age = 0;
			} else if (++this.age > this.n) this.last = { a: 0, q: 0 };
			return this.last;
		}
	}

	/** Box and axes for state {a, q}: the picture rect shrinks to the step's size, r is scaled by the squeeze. */
	function stepView(cfg, st, vw, vh) {
		if (!st || (st.a === 0 && st.q === 0)) return null;
		const [ox, oy, bw, bh] = cfg.box;
		const pw = Math.round(bw * vw), ph = Math.round(bh * vh);
		const r = rect2(st.a, pw, ph), g = squeeze(st.a, st.q);
		return { box: [ox, oy, r.w / vw, r.h / vh], x: { ...cfg.x, r: cfg.x.r * g }, y: { ...cfg.y, r: cfg.y.r * g } };
	}

	const api = {
		LEVELS, rect, markerPos, markerBits, decodeMeans, decodeImage, Holder, levelView,
		EXT_CODE, frac, squeeze, aFor, rect2, extBits, decodeExtMeans, decodeStrip, StateHolder, stepView,
	};
	if (typeof module !== "undefined" && module.exports) module.exports = api;
	else root.FSQSTV = api;
})(typeof self !== "undefined" ? self : this);
