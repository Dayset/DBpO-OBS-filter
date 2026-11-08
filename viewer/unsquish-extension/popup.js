const $ = (id) => document.getElementById(id);
const NO_LOOK = FSQ.NO_LOOK;
const LOOK_KEYS = FSQ.LOOK_FIELDS.map(([k]) => k);
const LOOK_SIGNED = Object.fromEntries(FSQ.LOOK_FIELDS.map(([k, , signed]) => [k, signed]));
// One slider per look value: 0..100 %, or -100..100 % for the signed colour values.
$("sliders").innerHTML = FSQ.LOOK_FIELDS.map(
	([k, label, signed]) =>
		`<div class="slider"><span>${label}</span><input type="range" id="${k}" min="${signed ? -100 : 0}" max="100" step="1"><output id="${k}Out"></output></div>`
).join("");
$("enhSliders").innerHTML = FSQ.ENHANCE_FIELDS.map(
	([k, label]) =>
		`<div class="slider"><span>${label}</span><input type="range" id="e_${k}" min="0" max="100" step="1"><output id="e_${k}Out"></output></div>`
).join("");
const ENH_KEYS = FSQ.ENHANCE_FIELDS.map(([k]) => k);
const defaults = { enabled: true, autoDetect: true, config: "", configPage: "", quality: "auto", lookMode: "streamer", lookPreset: "", lookStrength: 1, myLook: NO_LOOK, plainVideos: false, enhance: FSQ.NO_ENHANCE, debug: false, compare: "off" };
let tabKey = ""; // the page this popup was opened on (pasted configs belong to it)
let current = null; // the config in use (pasted or detected), for showing the streamer's look

function lookText(l) {
	const names = Object.fromEntries(FSQ.LOOK_FIELDS.map(([k, label]) => [k, label]));
	const on = LOOK_KEYS.filter((k) => l[k] !== 0).map((k) => `${names[k]} ${Math.round(l[k] * 100)}%`);
	return on.length ? on.join(", ") : "none";
}

/* The Look list: the streamer's look, off, a preset (with a strength) or every value by hand.
 * A preset is stored as lookMode "mine" + lookPreset, so the page only ever reads myLook. */
function renderLook(s) {
	const mode = s.lookMode;
	const preset = mode === "mine" && s.lookPreset && FSQ.LOOK_PRESETS[s.lookPreset] ? s.lookPreset : "";
	$("lookMode").value = preset || mode;
	const mine = FSQ.cleanLook(s.myLook);
	const shown = mode === "streamer" ? (current ? current.look : NO_LOOK) : mine;
	for (const k of LOOK_KEYS) {
		$(k).value = Math.round(shown[k] * 100);
		$(k).disabled = !(mode === "mine" && !preset);
		$(`${k}Out`).textContent = `${Math.round(shown[k] * 100)}%`;
	}
	const k = Number.isFinite(s.lookStrength) ? s.lookStrength : 1;
	$("lookStrength").value = Math.round(k * 100);
	$("lookStrengthOut").textContent = `${Math.round(k * 100)}%`;
	$("strengthRow").style.display = preset ? "" : "none";
	$("lookDetails").style.display = mode === "off" ? "none" : "";
	if (mode === "mine" && !preset) $("lookDetails").open = true;
	$("plainVideos").checked = s.plainVideos;
	$("plainVideos").disabled = mode === "off";
	$("lookHint").textContent =
		mode === "streamer"
			? `The streamer's look: ${current ? lookText(current.look) : "no stream config yet"}.`
			: preset
				? `${FSQ.LOOK_PRESETS[preset][0]} at ${Math.round(k * 100)}%: ${lookText(mine)}.`
				: mode === "mine"
					? "Your own values (open Each value). All at 0 = untouched picture."
					: "No look: you see the stream exactly as sent.";
}

/* Edge enhancement controls: the preset box shows which preset the current values equal, else "Custom". */
function renderEnhance(s) {
	const e = FSQ.cleanEnhance(s.enhance);
	$("enhKernel").value = e.kernel;
	$("enhArea").value = e.area;
	for (const k of ENH_KEYS) {
		$(`e_${k}`).value = Math.round(e[k] * 100);
		$(`e_${k}Out`).textContent = `${Math.round(e[k] * 100)}%`;
	}
	const same = (a, b) => a.kernel === b.kernel && a.area === b.area && ENH_KEYS.every((k) => Math.abs(a[k] - b[k]) < 0.005);
	$("enhPreset").value = Object.keys(FSQ.ENHANCE_PRESETS).find((n) => same(e, FSQ.ENHANCE_PRESETS[n])) || "custom";
	$("debug").checked = !!s.debug;
	$("compare").value = s.compare === "split" ? "split" : "off";
}

/* ", 2 sharp areas" when the config keeps HUD regions sharp (atlas), else "". */
function regionsText(cfg) {
	const n = cfg && cfg.regions ? cfg.regions.length : 0;
	return n ? `, ${n} sharp area${n > 1 ? "s" : ""}` : "";
}

function render(s, detected) {
	$("enabled").checked = s.enabled;
	$("autoDetect").checked = s.autoDetect;
	$("quality").value = s.quality;
	// A pasted config belongs to the page it was pasted on (older installs: every page).
	const here = !s.configPage || s.configPage === tabKey;
	$("config").value = here ? s.config : "";
	const pasted = here ? FSQ.parse(s.config) : null;
	current = pasted || (s.autoDetect && detected ? FSQ.parse(detected) : null);
	renderLook(s);
	renderEnhance(s);
	const st = $("status");
	st.classList.remove("bad");
	if (s.config && here && !pasted) {
		st.textContent = "That config string isn't valid (or asks for a squeeze the curve can't do).";
		st.classList.add("bad");
	} else if (pasted) {
		st.textContent = `Using pasted config: ${Math.round(pasted.x.r * 100)}% x ${Math.round(pasted.y.r * 100)}% streamed, sharp center ${Math.round(pasted.x.c * 200)}% x ${Math.round(pasted.y.c * 200)}%${regionsText(pasted)}.`;
	} else if (s.autoDetect && detected) {
		st.textContent = `Using config from the page: ${detected}${regionsText(current)}`;
	} else {
		st.textContent = "No config yet: paste the streamer's FSQ1 string, or turn on auto-detect.";
	}
}

function load() {
	const go = () =>
		chrome.storage.sync.get(defaults, (s) =>
			chrome.storage.local.get({ detectedBy: {} }, (l) => render(s, l.detectedBy[tabKey] || ""))
		);
	// The userscript panel lives in the page itself; the extension popup asks for the active tab.
	if (chrome.tabs && chrome.tabs.query)
		chrome.tabs.query({ active: true, currentWindow: true }, (t) => {
			// Only a real web page can own a config (not the popup's own tab or a browser page).
			const url = (t[0] && t[0].url) || "";
			tabKey = /^https?:/i.test(url) ? FSQ.pageKey(url) : "";
			go();
		});
	else ((tabKey = FSQ.pageKey(location.href)), go());
}

$("save").onclick = () => chrome.storage.sync.set({ config: $("config").value.trim(), configPage: tabKey }, load);
// Apply as soon as a valid string is pasted or typed (no Save click needed).
$("config").addEventListener("input", () => {
	const text = $("config").value.trim();
	const cfg = FSQ.parse(text);
	if (cfg) chrome.storage.sync.set({ config: cfg.text, configPage: tabKey }, load);
});
$("clear").onclick = () => chrome.storage.sync.set({ config: "", configPage: "" }, load);
$("enabled").onchange = (e) => chrome.storage.sync.set({ enabled: e.target.checked }, load);
$("autoDetect").onchange = (e) => chrome.storage.sync.set({ autoDetect: e.target.checked }, load);
$("quality").onchange = (e) => chrome.storage.sync.set({ quality: e.target.value }, load);
$("lookMode").onchange = (e) => {
	const v = e.target.value;
	if (FSQ.LOOK_PRESETS[v]) {
		const k = Number($("lookStrength").value) / 100 || 1;
		chrome.storage.sync.set({ lookMode: "mine", lookPreset: v, lookStrength: k, myLook: FSQ.presetLook(v, k) }, load);
	} else chrome.storage.sync.set({ lookMode: v, lookPreset: "" }, load);
};
$("plainVideos").onchange = (e) => chrome.storage.sync.set({ plainVideos: e.target.checked }, load);
/* Sliders apply live: every drag step goes to local storage (no write limit, the page follows at once);
 * the final value is saved to sync storage when the slider is released. */
const live = {};
function pushLive(key, value) {
	const first = !(key in live);
	live[key] = value;
	if (first)
		requestAnimationFrame(() => {
			const v = live[key];
			delete live[key];
			chrome.storage.local.set({ [key]: v });
		});
}
function readMyLook() {
	const look = {};
	for (const k of LOOK_KEYS) look[k] = Number($(k).value) / 100;
	return look;
}
for (const k of LOOK_KEYS) {
	$(k).addEventListener("input", () => {
		$(`${k}Out`).textContent = `${$(k).value}%`;
		pushLive("myLook", readMyLook());
	});
	$(k).addEventListener("change", () => chrome.storage.sync.set({ myLook: readMyLook() }));
}
$("lookStrength").addEventListener("input", () => {
	const k = Number($("lookStrength").value) / 100;
	$("lookStrengthOut").textContent = `${Math.round(k * 100)}%`;
	const preset = $("lookMode").value;
	if (FSQ.LOOK_PRESETS[preset]) pushLive("myLook", FSQ.presetLook(preset, k));
});
$("lookStrength").addEventListener("change", () => {
	const k = Number($("lookStrength").value) / 100;
	const preset = $("lookMode").value;
	if (FSQ.LOOK_PRESETS[preset]) chrome.storage.sync.set({ lookStrength: k, myLook: FSQ.presetLook(preset, k) }, load);
});
function readEnhance() {
	const e = { kernel: $("enhKernel").value, area: $("enhArea").value };
	for (const k of ENH_KEYS) e[k] = Number($(`e_${k}`).value) / 100;
	return FSQ.cleanEnhance(e);
}
$("enhPreset").onchange = (e) => {
	if (e.target.value !== "custom") chrome.storage.sync.set({ enhance: FSQ.ENHANCE_PRESETS[e.target.value] }, load);
};
$("enhKernel").onchange = () => chrome.storage.sync.set({ enhance: readEnhance() }, load);
$("enhArea").onchange = () => chrome.storage.sync.set({ enhance: readEnhance() }, load);
$("debug").onchange = (e) => chrome.storage.sync.set({ debug: e.target.checked }, load);
$("compare").onchange = (e) => chrome.storage.sync.set({ compare: e.target.value }, load);
for (const k of ENH_KEYS) {
	$(`e_${k}`).addEventListener("input", () => {
		$(`e_${k}Out`).textContent = `${$(`e_${k}`).value}%`;
		pushLive("enhance", readEnhance());
	});
	$(`e_${k}`).addEventListener("change", () => chrome.storage.sync.set({ enhance: readEnhance() }, load));
}
load();
