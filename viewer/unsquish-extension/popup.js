const $ = (id) => document.getElementById(id);
const NO_LOOK = { sharpen: 0, clarity: 0, hdr: 0, bloom: 0, sharpenPlus: 0 };
const LOOK_KEYS = Object.keys(NO_LOOK);
const defaults = { enabled: true, autoDetect: true, config: "", lookMode: "streamer", myLook: NO_LOOK, plainVideos: false };
let current = null; // the config in use (pasted or detected), for showing the streamer's look

function lookText(l) {
	const names = { sharpen: "Sharpen", clarity: "Clarity", hdr: "HDR toning", bloom: "Bloom", sharpenPlus: "Sharpen+" };
	const on = LOOK_KEYS.filter((k) => l[k] > 0).map((k) => `${names[k]} ${Math.round(l[k] * 100)}%`);
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
	$("config").value = s.config;
	const pasted = FSQ.parse(s.config);
	current = pasted || (s.autoDetect && detected ? FSQ.parse(detected) : null);
	renderLook(s);
	const st = $("status");
	st.classList.remove("bad");
	if (s.config && !pasted) {
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
	chrome.storage.sync.get(defaults, (s) => chrome.storage.local.get({ detected: "" }, (l) => render(s, l.detected)));
}

$("save").onclick = () => chrome.storage.sync.set({ config: $("config").value.trim() }, load);
// Apply as soon as a valid string is pasted or typed (no Save click needed).
$("config").addEventListener("input", () => {
	const text = $("config").value.trim();
	const cfg = FSQ.parse(text);
	if (cfg) chrome.storage.sync.set({ config: cfg.text }, load);
});
$("clear").onclick = () => chrome.storage.sync.set({ config: "" }, load);
$("enabled").onchange = (e) => chrome.storage.sync.set({ enabled: e.target.checked }, load);
$("autoDetect").onchange = (e) => chrome.storage.sync.set({ autoDetect: e.target.checked }, load);
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
