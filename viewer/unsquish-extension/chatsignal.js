/* Chat signal: the streamer's Twitch chat turns the squish on or off.
 *
 * The streamer's own account posts "fsq:on:<n>" or "fsq:off:<n>" in its channel
 * chat. Twitch sets the sender's login server-side, so only messages from the
 * channel owner count. <n> must go up each time, which also stops Twitch from
 * dropping repeated messages. Each signal is applied at the time it was sent
 * plus a delay, so the picture switches when the video, which lags chat, shows
 * that moment. Before any signal arrives nothing changes.
 *
 * The result goes to <html data-dbpo-fsq="on|off">; content.js reads it.
 */
(() => {
	"use strict";
	const root = document.documentElement;
	const channel = (location.pathname.split("/")[1] || "").toLowerCase();
	if (!/^[a-z0-9_]{3,25}$/.test(channel)) return;
	const DEFAULT_DELAY_MS = 1500;
	const STALE_MS = 30000; // ignore signals older than this (reconnect replays)
	let delayMs = DEFAULT_DELAY_MS;
	let lastSeq = -1;
	let timers = [];
	let ws = null;

	/* Pure parser for one IRC line; exported for tests via window.__dbpoFsqParse. */
	function parseLine(line) {
		const m = /^@([^ ]+) :([^!]+)![^ ]+ PRIVMSG #([^ ]+) :(.*)$/.exec(line);
		if (!m) return null;
		const [, tagStr, login, , text] = m;
		if (login.toLowerCase() !== channel) return null; // only the channel owner's account
		const tags = {};
		for (const t of tagStr.split(";")) {
			const i = t.indexOf("=");
			if (i > 0) tags[t.slice(0, i)] = t.slice(i + 1);
		}
		const sig = /\bfsq:(on|off):(\d+)\b/.exec(text);
		if (!sig) return null;
		return { on: sig[1] === "on", seq: Number(sig[2]), sentMs: Number(tags["tmi-sent-ts"]) };
	}
	window.__dbpoFsqParse = parseLine;

	function apply(on) {
		root.dataset.dbpoFsq = on ? "on" : "off";
	}

	function handleSignal(sig) {
		if (!Number.isFinite(sig.sentMs) || sig.seq <= lastSeq) return;
		lastSeq = sig.seq;
		const wait = sig.sentMs + delayMs - Date.now();
		if (wait < -STALE_MS) return;
		for (const t of timers) clearTimeout(t);
		timers = [setTimeout(() => apply(sig.on), Math.max(0, wait))];
	}

	function onLine(line) {
		if (line.startsWith("PING")) {
			ws.send("PONG :tmi.twitch.tv");
			return;
		}
		const sig = parseLine(line);
		if (sig) handleSignal(sig);
	}

	function connect() {
		ws = new WebSocket("wss://irc-ws.chat.twitch.tv:443");
		ws.onopen = () => {
			ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands");
			ws.send("NICK justinfan" + (10000 + Math.floor(Math.random() * 80000)));
			ws.send("JOIN #" + channel);
		};
		ws.onmessage = (ev) => String(ev.data).split("\r\n").forEach(onLine);
		ws.onclose = () => setTimeout(connect, 3000);
	}

	try {
		chrome.storage.sync.get({ chatSignal: true, chatDelayMs: DEFAULT_DELAY_MS }, (s) => {
			if (!s.chatSignal) return;
			delayMs = Number(s.chatDelayMs) || DEFAULT_DELAY_MS;
			connect();
		});
	} catch (e) {
		// extension updated or removed; the new copy handles this page
	}
})();
