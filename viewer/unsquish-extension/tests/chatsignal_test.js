// Tests for chatsignal.js: owner-only parsing, sequence numbers, stale replays, delay, PING.
// Run: node tests/chatsignal_test.js   (exit code 1 on failure)
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "..", "chatsignal.js"), "utf8");

function load(channel, delayMs) {
	const sent = [];
	const sockets = [];
	class FakeWS {
		constructor(url) { this.url = url; this.sent = []; sockets.push(this); }
		send(m) { this.sent.push(m); sent.push(m); }
	}
	const root = { dataset: {} };
	const ctx = {
		document: { documentElement: root },
		location: { pathname: "/" + channel },
		chrome: { storage: { sync: { get: (_d, cb) => cb({ chatSignal: true, chatDelayMs: delayMs }) } } },
		WebSocket: FakeWS,
		setTimeout, clearTimeout, Date, String, Number, Math, console,
	};
	ctx.window = ctx;
	vm.createContext(ctx);
	vm.runInContext(src, ctx);
	const ws = sockets[0];
	const feed = (line) => ws.onmessage({ data: line });
	return { ctx, root, ws, sent, feed };
}

const owner = (seq, on, sentMs, login = "streamer") =>
	`@badge-info=;tmi-sent-ts=${sentMs};user-id=1 :${login}!${login}@${login}.tmi.twitch.tv PRIVMSG #streamer :fsq:${on}:${seq}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("parser: owner on/off with sequence and timestamp", () => {
	const { ctx } = load("streamer", 1);
	const p = ctx.__dbpoFsqParse(owner(7, "on", 1234));
	assert.deepStrictEqual({ on: p.on, seq: p.seq, sentMs: p.sentMs }, { on: true, seq: 7, sentMs: 1234 });
	assert.strictEqual(ctx.__dbpoFsqParse(owner(8, "off", 5)).on, false);
});

test("parser: other accounts are ignored", () => {
	const { ctx } = load("streamer", 1);
	assert.strictEqual(ctx.__dbpoFsqParse(owner(1, "on", 1, "mod_bob")), null);
});

test("parser: normal chat and malformed signals are ignored", () => {
	const { ctx } = load("streamer", 1);
	const pre = `@tmi-sent-ts=1 :streamer!streamer@streamer.tmi.twitch.tv PRIVMSG #streamer :`;
	assert.strictEqual(ctx.__dbpoFsqParse(pre + "hello chat"), null);
	assert.strictEqual(ctx.__dbpoFsqParse(pre + "fsq:maybe:3"), null);
	assert.strictEqual(ctx.__dbpoFsqParse(pre + "fsq:on:"), null);
});

test("parser: signal embedded in longer text still counts", () => {
	const { ctx } = load("streamer", 1);
	const p = ctx.__dbpoFsqParse(owner(4, "on", 9).replace(":fsq:", ":now fsq:"));
	assert.ok(p && p.on && p.seq === 4);
});

test("apply: owner 'on' then 'off' switches data-dbpo-fsq", async () => {
	const { root, feed } = load("streamer", 1);
	assert.strictEqual(root.dataset.dbpoFsq, undefined, "nothing before the first signal");
	feed(owner(1, "on", Date.now()));
	await sleep(20);
	assert.strictEqual(root.dataset.dbpoFsq, "on");
	feed(owner(2, "off", Date.now()));
	await sleep(20);
	assert.strictEqual(root.dataset.dbpoFsq, "off");
});

test("sequence: a replayed or lower number is ignored", async () => {
	const { root, feed } = load("streamer", 1);
	feed(owner(5, "on", Date.now()));
	await sleep(20);
	feed(owner(5, "off", Date.now()));
	feed(owner(3, "off", Date.now()));
	await sleep(20);
	assert.strictEqual(root.dataset.dbpoFsq, "on");
});

test("stale: signals older than 30 s are dropped", async () => {
	const { root, feed } = load("streamer", 1);
	feed(owner(1, "on", Date.now() - 60000));
	await sleep(20);
	assert.strictEqual(root.dataset.dbpoFsq, undefined);
});

test("delay: apply waits for sentMs + delay", async () => {
	const { root, feed } = load("streamer", 300);
	feed(owner(1, "on", Date.now()));
	await sleep(100);
	assert.strictEqual(root.dataset.dbpoFsq, undefined, "still waiting for the delay");
	await sleep(350);
	assert.strictEqual(root.dataset.dbpoFsq, "on");
});

test("delay: 0 ms is honoured, not replaced by the default", async () => {
	const { root, feed } = load("streamer", 0);
	feed(owner(1, "on", Date.now()));
	await sleep(20);
	assert.strictEqual(root.dataset.dbpoFsq, "on");
});

test("multi-line frames and PING/PONG", async () => {
	const { root, feed, ws, sent } = load("streamer", 1);
	ws.onopen();
	feed("PING :tmi.twitch.tv\r\n" + owner(1, "on", Date.now()) + "\r\n");
	await sleep(20);
	assert.ok(sent.includes("PONG :tmi.twitch.tv"));
	assert.strictEqual(root.dataset.dbpoFsq, "on");
	assert.ok(ws.sent.includes("JOIN #streamer"), "joins the channel");
});

let failed = 0;
(async () => {
	for (const [name, fn] of tests) {
		try { await fn(); console.log("PASS", name); }
		catch (e) { failed++; console.log("FAIL", name, "\n   ", e.message); }
	}
	console.log(failed ? `${failed} failed` : "chat signal: all PASS");
	process.exit(failed ? 1 : 0);
})();
