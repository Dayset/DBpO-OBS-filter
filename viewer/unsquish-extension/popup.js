const $ = (id) => document.getElementById(id);
const NO_LOOK = FSQ.NO_LOOK;
const LOOK_KEYS = FSQ.LOOK_FIELDS.map(([k]) => k);
const LOOK_SIGNED = Object.fromEntries(FSQ.LOOK_FIELDS.map(([k, , signed]) => [k, signed]));
// One slider per look value: 0..100 %, or -100..100 % for the signed colour values.
$("sliders").innerHTML = FSQ.LOOK_FIELDS.map(
	([k, label, signed]) =>
		`<div class="slider"><span>${label}</span><input type="range" id="${k}" min="${signed ? -100 : 0}" max="100" step="1"><output id="${k}Out"></output></div>`
).join("");
const defaults = { enabled: true, autoDetect: true, config: "", configPage: "", quality: "auto", lookMode: "streamer", myLook: NO_LOOK, plainVideos: false };
let tabKey = ""; // the page this popup was opened on (pasted configs belong to it)
let current = null; // the config in use (pasted or detected), for showing the streamer's look

function lookText(l) {
	const names = Object.fromEntries(FSQ.LOOK_FIELDS.map(([k, label]) => [k, label]));
	const on = LOOK_KEYS.filter((k) => l[k] !== 0).map((k) => `${names[k]} ${Math.round(l[k] * 100)}%`);
	return on.length ? on.join(", ") : "none";
}

/* Sliders show the streamer's look (read-only) or the viewer's own. */
function renderLook(s) {
	const mode = s.lookMode;
	$("lookMode").value = mode;
	const mine = FSQ.cleanLook(s.myLook);
	const shown = mode === "streamer" ? (current ? current.look : NO_LOOK) : mine;
	for (const k of LOOK_KEYS) {
		$(k).value = Math.round(shown[k] * 100);
		$(k).disabled = mode !== "mine";
		$(`${k}Out`).textContent = `${Math.round(shown[k] * 100)}%`;
	}
	$("sliders").style.opacity = mode === "off" ? "0.4" : "1";
	$("plainVideos").checked = s.plainVideos;
	$("plainVideos").disabled = mode === "off";
	$("lookHint").textContent =
		mode === "streamer"
			? `The streamer's look: ${current ? lookText(current.look) : "no stream config yet"}. Pick "My look" to set your own.`
			: mode === "mine"
				? "Your own look, used instead of the streamer's. All at 0 = untouched picture."
				: "No look: you see the stream exactly as sent.";
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
$("lookMode").onchange = (e) => chrome.storage.sync.set({ lookMode: e.target.value }, load);
$("plainVideos").onchange = (e) => chrome.storage.sync.set({ plainVideos: e.target.checked }, load);
function saveMyLook() {
	const look = {};
	for (const k of LOOK_KEYS) look[k] = Number($(k).value) / 100;
	chrome.storage.sync.set({ myLook: look });
}
// Live while dragging, but at most one write per 0.7 s (sync storage allows 120 a minute).
let pending = null;
for (const k of LOOK_KEYS) {
	$(k).addEventListener("input", () => {
		$(`${k}Out`).textContent = `${$(k).value}%`;
		if (!pending) pending = setTimeout(() => ((pending = null), saveMyLook()), 700);
	});
	$(k).addEventListener("change", saveMyLook);
}
load();
