const $ = (id) => document.getElementById(id);
const defaults = { enabled: true, autoDetect: true, config: "" };

function render(s, detected) {
	$("enabled").checked = s.enabled;
	$("autoDetect").checked = s.autoDetect;
	$("config").value = s.config;
	const pasted = FSQ.parse(s.config);
	const st = $("status");
	st.classList.remove("bad");
	if (s.config && !pasted) {
		st.textContent = "That config string isn't valid (or asks for a squeeze the curve can't do).";
		st.classList.add("bad");
	} else if (pasted) {
		st.textContent = `Using pasted config: ${Math.round(pasted.x.r * 100)}% x ${Math.round(pasted.y.r * 100)}% streamed, sharp center ${Math.round(pasted.x.c * 200)}% x ${Math.round(pasted.y.c * 200)}%.`;
	} else if (s.autoDetect && detected) {
		st.textContent = `Using config from the page: ${detected}`;
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
load();
