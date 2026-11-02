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
process.exitCode = ok && bad === null && user ? 0 : 1;
