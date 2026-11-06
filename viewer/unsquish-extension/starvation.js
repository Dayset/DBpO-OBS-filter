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

	const api = { LEVELS, rect, markerPos, markerBits, decodeMeans, decodeImage, Holder, levelView };
	if (typeof module !== "undefined" && module.exports) module.exports = api;
	else root.FSQSTV = api;
})(typeof self !== "undefined" ? self : this);
