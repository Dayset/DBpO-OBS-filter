/* Look code (packLook / unpackLook) vs research/foveation/color_math.py golden cases. */
const { execFileSync } = require("child_process");
const path = require("path");
const FSQ = require("../squish.js");
const py = path.join(__dirname, "../../../research/foveation/color_math.py");
const cases = JSON.parse(execFileSync("python3", [py, "--golden"], { encoding: "utf8" }));
let fails = 0;
for (const c of cases) {
	const code = FSQ.packLook(c.look);
	if (code !== c.code) { console.error("code differs:", JSON.stringify(code), "want", JSON.stringify(c.code)); fails++; continue; }
	const back = FSQ.unpackLook(code);
	const want = c.code ? c.decoded : null;
	if (!back && want) { console.error("unpack failed"); fails++; continue; }
	if (back) for (const k of Object.keys(c.decoded)) if (Math.abs(back[k] - c.decoded[k]) > 1e-12) { console.error("value differs", k, back[k], c.decoded[k]); fails++; }
	const cfg = FSQ.parse("FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.7778" + code + " (title tail)");
	if (!cfg || (code && Math.abs(cfg.look.vibrance - c.decoded.vibrance) > 1e-12)) { console.error("parse with code failed"); fails++; }
}
if (FSQ.unpackLook("※abc") !== null) { console.error("garbage accepted"); fails++; }
console.error(`look code vs Python: ${cases.length} cases, ${fails ? "FAIL" : "PASS"}`);
process.exit(fails ? 1 : 0);
