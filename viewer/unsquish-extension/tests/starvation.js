// Checks the browser starvation code against research/foveation/starvation_golden.json (written by starvation_math.py).
const fs = require("fs");
const path = require("path");
const STV = require("../starvation.js");
const FSQ = require("../squish.js");
const g = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "..", "research", "foveation", "starvation_golden.json")));
let fails = 0;
const check = (c, m) => { if (!c) { console.error("FAIL: " + m); fails++; } };

for (const [size, rects] of Object.entries(g.rects)) {
	const [w, h] = size.split("x").map(Number);
	for (const [code, r] of Object.entries(rects)) {
		const got = STV.rect(Number(code), w, h);
		check(got.w === r[0] && got.h === r[1], `rect ${size} code ${code}`);
	}
	const mp = STV.markerPos(w, h);
	check(mp[0] === g.marker_pos[size][0] && mp[1] === g.marker_pos[size][1], `marker pos ${size}`);
}
for (const [code, bits] of Object.entries(g.marker_bits)) {
	check(JSON.stringify(STV.markerBits(Number(code))) === JSON.stringify(bits), `marker bits ${code}`);
	const means = [];
	for (const b of bits) means.push(...(b ? [235, 16] : [16, 235]));
	check(STV.decodeMeans(means) === Number(code), `decode ${code}`);
	const bad = means.slice();
	bad[0] = 251 - bad[0]; // flip one cell -> parity fails
	check(STV.decodeMeans(bad) === null, `parity reject ${code}`);
}
check(STV.decodeMeans(Array(10).fill(90)) === null, "blank strip has no marker");

// Holder: keeps the last level 12 frames at 60 fps, then falls back to level 0.
const h = new STV.Holder(60);
const seq = [2, null, null, 2].concat(Array(14).fill(null)).map((c) => h.update(c));
check(seq[1] === 2 && seq[2] === 2 && seq[seq.length - 1] === 0, "holder holds, then falls back");
check(new STV.Holder(60).update(9) === 0, "reserved code -> level 0");

// levelView: plain level shrinks the box, squish level scales r; squish levels need no regions.
const cfg = FSQ.parse("FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778,sl=1");
check(cfg.starve === true && FSQ.parse("FSQ1:rx=0.75,ar=1.778").starve === false, "sl key parsed");
check(STV.levelView(cfg, 0, 1712, 964) === null, "level 0 = no change");
const v2 = STV.levelView(cfg, 2, 1712, 964);
check(Math.abs(v2.box[2] - 856 / 1712) < 1e-12 && Math.abs(v2.box[3] - 482 / 964) < 1e-12, "plain level box");
const v4 = STV.levelView(cfg, 4, 1712, 964);
check(Math.abs(v4.x.r - (1284 * 0.75) / 1712) < 1e-12, "squish level r");
const boxed = FSQ.parse("FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,ar=1.778,ox=0,oy=0,bw=0.8,bh=1,sl=1");
const vb = STV.levelView(boxed, 2, 1712, 964);
check(Math.abs(vb.box[2] - 0.4) < 0.001, "boxed plain level shrinks only the picture rect");
check(STV.levelView(boxed, 4, 1712, 964) === null, "squish level not used with a strip");

// v2
const G2 = g.v2;
G2.steps.forEach(([a, q], i) => {
	check(Math.abs(STV.frac(a) - G2.frac[i]) < 1e-12 && Math.abs(STV.squeeze(a, q) - G2.squeeze[i]) < 1e-12, `v2 frac/squeeze ${a},${q}`);
	check(JSON.stringify(STV.extBits(a, q)) === JSON.stringify(G2.ext_bits[i]), `v2 bits ${a},${q}`);
	for (const [size, rects] of Object.entries(G2.rects)) {
		const [w, h] = size.split("x").map(Number);
		const r = STV.rect2(a, w, h);
		check(r.w === rects[i][0] && r.h === rects[i][1], `v2 rect ${size} ${a},${q}`);
	}
	const m = [];
	for (const b of STV.extBits(a, q)) m.push(...(b ? [235, 16] : [16, 235]));
	const d = STV.decodeExtMeans(m);
	check(d && d.a === a && d.q === q, `v2 decode ${a},${q}`);
});
// Full strip decode from synthetic pixels (v1 row = code 15, extension row = state).
function strip(a, q) {
	const px = new Uint8ClampedArray(160 * 32 * 4);
	const v1 = STV.markerBits(15), ext = STV.extBits(a, q);
	for (let y = 0; y < 32; y++)
		for (let x = 0; x < 160; x++) {
			let on;
			if (y >= 16) { const c = Math.floor(x / 16); on = c % 2 === 0 ? v1[c >> 1] : 1 - v1[c >> 1]; }
			else { const c = Math.floor(x / 8); on = c % 2 === 0 ? ext[c >> 1] : 1 - ext[c >> 1]; }
			px.fill(on ? 255 : 0, (y * 160 + x) * 4, (y * 160 + x) * 4 + 3);
			px[(y * 160 + x) * 4 + 3] = 255;
		}
	return px;
}
for (const [a, q] of [[0, 0], [38, 7], [63, 3], [17, 1]]) {
	const st = STV.decodeStrip(strip(a, q));
	check(st && st.a === a && st.q === q, `v2 strip decode ${a},${q}`);
}
const sv = STV.stepView(cfg, { a: 38, q: 7 }, 1712, 964);
check(Math.abs(sv.box[2] - STV.rect2(38, 1712, 964).w / 1712) < 1e-12 && Math.abs(sv.x.r - cfg.x.r * STV.squeeze(38, 7)) < 1e-12, "v2 step view");
check(STV.stepView(cfg, { a: 0, q: 0 }, 1712, 964) === null, "v2 full size = no change");

console.error(`starvation JS vs golden: ${fails ? "FAIL" : "PASS"}`);
process.exitCode = fails ? 1 : 0;
