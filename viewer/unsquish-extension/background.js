// After install/update, tabs that were already open still run the old content
// script, which loses its connection to the extension (settings stop arriving
// until the page is refreshed). Inject the current one into them right away.
chrome.runtime.onInstalled.addListener(async () => {
	const tabs = await chrome.tabs.query({ url: ["http://*/*", "https://*/*"] });
	for (const tab of tabs) {
		chrome.scripting
			.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ["squish.js", "content.js"] })
			.catch(() => {}); // pages we may not touch (store, settings)
	}
});
