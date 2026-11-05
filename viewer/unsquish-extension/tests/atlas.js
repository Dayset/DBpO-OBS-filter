/* Atlas layout in the viewer vs research/foveation/atlas_golden.json (1920x1080 stream). */
const fs = require("fs");
const path = require("path");
const FSQ = require("../squish.js");
const golden = JSON.parse(fs.readFileSync(path.join(__dirname, "../../../research/foveation/atlas_golden.json"), "utf8"));
let fails = 0;
for (const c of golden) {
	const cfg = FSQ.parse(c.config);
	if (!cfg) { console.error("parse failed:", c.config.slice(0, 60)); fails++; continue; }
	if (cfg.regions.length !== c.regions.length) { console.error("region count", c.config.slice(0, 40)); fails++; continue; }
	if (!c.slots_1080p) continue;
	const slots = FSQ.atlasLayout(cfg, 1920, 1080);
	const want = c.slots_1080p.map((s) => [s[0], s[1], s[2], s[3]]);
	const got = (slots || []).map((s) => [s.x, s.y, s.w, s.h]);
	if (JSON.stringify(got) !== JSON.stringify(want)) {
		console.error("slots differ:", JSON.stringify(got), "want", JSON.stringify(want));
		fails++;
	}
}
console.error(`atlas layout vs golden: ${golden.length} cases, ${fails ? "FAIL" : "PASS"}`);
process.exit(fails ? 1 : 0);
