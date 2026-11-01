/*
Directional Object Blur
Copyright (C) 2026 dayset <dayset@gmail.com>

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License along
with this program. If not, see <https://www.gnu.org/licenses/>
*/

#include "raw-mouse.h"

#ifdef _WIN32

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <obs-module.h>
#include <plugin-support.h>

#define HID_USAGE_PAGE_GENERIC 0x01
#define HID_USAGE_GENERIC_MOUSE 0x02
#define WM_RAWMOUSE_STOP (WM_APP + 1)

static CRITICAL_SECTION lock;
static INIT_ONCE lock_once = INIT_ONCE_STATIC_INIT;
static int refs;
static HANDLE thread;
static HANDLE ready_event;
static HWND window;
static volatile LONG running_ok;
static volatile LONG total_x, total_y;
static const char *status = "Not started";

static BOOL CALLBACK init_lock(PINIT_ONCE once, PVOID param, PVOID *ctx)
{
	(void)once;
	(void)param;
	(void)ctx;
	InitializeCriticalSection(&lock);
	return TRUE;
}

/* True if something else in this process already receives raw mouse input.
 * Raw Input allows one target window per device type per process, so we
 * must not take it over. */
static bool mouse_already_registered(void)
{
	UINT count = 0;
	if (GetRegisteredRawInputDevices(NULL, &count, sizeof(RAWINPUTDEVICE)) != 0 || count == 0)
		return false;

	RAWINPUTDEVICE *devs = (RAWINPUTDEVICE *)calloc(count, sizeof(RAWINPUTDEVICE));
	if (!devs)
		return false;
	bool found = false;
	UINT n = GetRegisteredRawInputDevices(devs, &count, sizeof(RAWINPUTDEVICE));
	for (UINT i = 0; i < n && n != (UINT)-1; i++) {
		if (devs[i].usUsagePage == HID_USAGE_PAGE_GENERIC && devs[i].usUsage == HID_USAGE_GENERIC_MOUSE)
			found = true;
	}
	free(devs);
	return found;
}

static LRESULT CALLBACK wnd_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
	switch (msg) {
	case WM_INPUT: {
		RAWINPUT raw;
		UINT size = sizeof(raw);
		if (GetRawInputData((HRAWINPUT)lp, RID_INPUT, &raw, &size, sizeof(RAWINPUTHEADER)) != (UINT)-1 &&
		    raw.header.dwType == RIM_TYPEMOUSE && !(raw.data.mouse.usFlags & MOUSE_MOVE_ABSOLUTE)) {
			InterlockedExchangeAdd(&total_x, raw.data.mouse.lLastX);
			InterlockedExchangeAdd(&total_y, raw.data.mouse.lLastY);
		}
		break; /* DefWindowProc must still run to release the input */
	}
	case WM_RAWMOUSE_STOP: {
		RAWINPUTDEVICE rid = {HID_USAGE_PAGE_GENERIC, HID_USAGE_GENERIC_MOUSE, RIDEV_REMOVE, NULL};
		RegisterRawInputDevices(&rid, 1, sizeof(rid));
		DestroyWindow(hwnd);
		return 0;
	}
	case WM_DESTROY:
		PostQuitMessage(0);
		return 0;
	}
	return DefWindowProcW(hwnd, msg, wp, lp);
}

static DWORD WINAPI listener_thread(LPVOID param)
{
	(void)param;
	HINSTANCE inst = GetModuleHandleW(NULL);
	const wchar_t *cls = L"DirectionalObjectBlurRawMouse";

	WNDCLASSEXW wc = {0};
	wc.cbSize = sizeof(wc);
	wc.lpfnWndProc = wnd_proc;
	wc.hInstance = inst;
	wc.lpszClassName = cls;
	RegisterClassExW(&wc);

	window = CreateWindowExW(0, cls, L"", 0, 0, 0, 0, 0, HWND_MESSAGE, NULL, inst, NULL);
	if (!window) {
		status = "Failed to create message window";
		SetEvent(ready_event);
		return 0;
	}

	if (mouse_already_registered()) {
		status = "Another component in OBS already uses raw mouse input; not taking it over";
		DestroyWindow(window);
		window = NULL;
		SetEvent(ready_event);
		return 0;
	}

	/* INPUTSINK: receive movement even while OBS is in the background,
	 * which is the normal case while playing. */
	RAWINPUTDEVICE rid = {HID_USAGE_PAGE_GENERIC, HID_USAGE_GENERIC_MOUSE, RIDEV_INPUTSINK, window};
	if (!RegisterRawInputDevices(&rid, 1, sizeof(rid))) {
		status = "Windows refused the raw input registration";
		DestroyWindow(window);
		window = NULL;
		SetEvent(ready_event);
		return 0;
	}

	InterlockedExchange(&running_ok, 1);
	status = "Active";
	SetEvent(ready_event);

	MSG msg;
	while (GetMessageW(&msg, NULL, 0, 0) > 0) {
		TranslateMessage(&msg);
		DispatchMessageW(&msg);
	}

	InterlockedExchange(&running_ok, 0);
	window = NULL;
	UnregisterClassW(cls, inst);
	return 0;
}

bool raw_mouse_supported(void)
{
	return true;
}

bool raw_mouse_acquire(void)
{
	InitOnceExecuteOnce(&lock_once, init_lock, NULL, NULL);
	EnterCriticalSection(&lock);

	if (refs++ == 0) {
		ready_event = CreateEventW(NULL, TRUE, FALSE, NULL);
		thread = CreateThread(NULL, 0, listener_thread, NULL, 0, NULL);
		if (thread)
			WaitForSingleObject(ready_event, 2000);
		else
			status = "Failed to start listener thread";
		obs_log(LOG_INFO, "raw mouse input: %s", status);
	}
	bool ok = InterlockedCompareExchange(&running_ok, 0, 0) != 0;

	LeaveCriticalSection(&lock);
	return ok;
}

void raw_mouse_release(void)
{
	InitOnceExecuteOnce(&lock_once, init_lock, NULL, NULL);
	EnterCriticalSection(&lock);

	if (refs > 0 && --refs == 0) {
		if (window)
			PostMessageW(window, WM_RAWMOUSE_STOP, 0, 0);
		if (thread) {
			WaitForSingleObject(thread, 2000);
			CloseHandle(thread);
			thread = NULL;
		}
		if (ready_event) {
			CloseHandle(ready_event);
			ready_event = NULL;
		}
		status = "Not started";
		obs_log(LOG_INFO, "raw mouse input stopped");
	}

	LeaveCriticalSection(&lock);
}

void raw_mouse_read_totals(uint32_t *x, uint32_t *y)
{
	*x = (uint32_t)InterlockedCompareExchange(&total_x, 0, 0);
	*y = (uint32_t)InterlockedCompareExchange(&total_y, 0, 0);
}

const char *raw_mouse_status(void)
{
	return status;
}

#else /* !_WIN32 */

bool raw_mouse_supported(void)
{
	return false;
}

bool raw_mouse_acquire(void)
{
	return false;
}

void raw_mouse_release(void) {}

void raw_mouse_read_totals(uint32_t *x, uint32_t *y)
{
	*x = 0;
	*y = 0;
}

const char *raw_mouse_status(void)
{
	return "Only available on Windows";
}

#endif
