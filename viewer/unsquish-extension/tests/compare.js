// Prints the JS mapping for the same cases as plugins/foveated-squish/tests/test_squish_math.c,
// so plugins/foveated-squish/tests/compare_reference.py can check it against the Python reference.
const FSQ = require("../squish.js");
const cases = [
	{ r: 0.75, c: 0.15, f: 0.5, p: 1.0 }, { r: 0.75, c: 0.15, f: 0.5, p: 0.5 }, { r: 0.65, c: 0.15, f: 0.5, p: 0.75 },
	{ r: 0.7, c: 0.1, f: 0.35, p: 1.0 }, { r: 0.9, c: 0.3, f: 0.6, p: 2.0 }, { r: 0.25, c: 0.05, f: 0.5, p: 0.1 },
];
cases.forEach((a, c) => {
	for (let i = 0; i <= 20; i++) {
		const x = i / 20;
		const v = FSQ.inverse(a, x); // the extension itself only needs forward
		console.log(`${c} ${x.toFixed(6)} ${FSQ.forward(a, x).toFixed(12)} ${FSQ.density(a, x).toFixed(12)} ${v.toFixed(12)}`);
	}
});
const ok = FSQ.parse("Stream title [FSQ1:rx=0.75,ry=0.7,cx=0.15,cy=0.2,fx=0.5,fy=0.45,px=1,py=0.75,ar=1.7778] gg");
const bad = FSQ.parse("FSQ1:rx=0.5,ry=0.5,cx=0.45,cy=0.45,px=3,py=3"); // edge density would be negative
const user = FSQ.parse("FSQ1:rx=0.6949,ry=0.695,cx=0.025,cy=0.025,fx=0.5,fy=0.5,px=1.865,py=1.865,ar=1.778");
console.error(`parse from title: ${ok && ok.y.f === 0.45 ? "PASS" : "FAIL"}; rejects impossible squeeze: ${bad === null ? "PASS" : "FAIL"}`);
console.error(`accepts strong squeeze near the plugin limit: ${user ? "PASS" : "FAIL"}`);
// Viewer look: written by the plugin as ls/lc/lh/lb/lp (only when above 0), clamped to 0..1.
const lk = FSQ.parse("FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.778,ls=0.6,lc=0.4,lh=0.3,lb=0.15,lp=0.5");
const lookOk = !!lk && lk.look.sharpen === 0.6 && lk.look.clarity === 0.4 && lk.look.hdr === 0.3 &&
	lk.look.bloom === 0.15 && lk.look.sharpenPlus === 0.5 && FSQ.lookActive(lk.look) && lk.x.r === 0.75;
const clampOk = (() => { const c = FSQ.parse("FSQ1:rx=0.75,ar=1.778,lb=7,lc=-1"); return !!c && c.look.bloom === 1 && c.look.clarity === 0; })();
const oldOk = !!ok && !FSQ.lookActive(ok.look) && ok.look.sharpen === 0; // strings from before the look
const id = FSQ.identity(2), idOk = [0, 0.13, 0.5, 0.87, 1].every((x) => Math.abs(FSQ.forward(id.x, x) - x) < 1e-12 && Math.abs(FSQ.forward(id.y, x) - x) < 1e-12);
console.error(`viewer look parse: ${lookOk ? "PASS" : "FAIL"}; clamped: ${clampOk ? "PASS" : "FAIL"}; old strings have no look: ${oldOk ? "PASS" : "FAIL"}; identity for plain videos: ${idOk ? "PASS" : "FAIL"}`);
process.exitCode = ok && bad === null && user && lookOk && clampOk && oldOk && idOk ? 0 : 1;
