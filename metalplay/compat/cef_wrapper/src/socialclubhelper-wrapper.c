/*
 * SPDX-License-Identifier: MIT
 *
 * SocialClubHelper wrapper — inject Chromium GPU workarounds for Rockstar
 * Launcher CEF under Wine on macOS (same approach as Steam steamwebhelper).
 */

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif

#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

#define BASE_FLAGS   L"--disable-gpu --disable-gpu-compositing --disable-dev-shm-usage --use-angle=swiftshader --in-process-gpu --single-process"
#define CHILD_FLAGS  L"--disable-gpu --disable-gpu-compositing --use-angle=swiftshader --single-process"
#define REAL_BINARY  L"SocialClubHelper_real.exe"

static wchar_t g_extra_flags[384];

static const wchar_t *extra_flags(void)
{
    wcscpy(g_extra_flags, BASE_FLAGS);
    const wchar_t *scale = _wgetenv(L"METALPLAY_CEF_DEVICE_SCALE_FACTOR");
    if (scale && *scale) {
        wchar_t lower[32];
        size_t n = wcslen(scale);
        if (n >= sizeof(lower) / sizeof(wchar_t)) n = (sizeof(lower) / sizeof(wchar_t)) - 1;
        wcsncpy(lower, scale, n);
        lower[n] = L'\0';
        for (wchar_t *p = lower; *p; ++p) {
            if (*p >= L'A' && *p <= L'Z') *p += (L'a' - L'A');
        }
        if (wcscmp(lower, L"auto") != 0 && wcscmp(lower, L"dynamic") != 0) {
            wchar_t suffix[64];
            _snwprintf(suffix, 64, L" --force-device-scale-factor=%ls", scale);
            wcsncat(g_extra_flags, suffix, (sizeof(g_extra_flags) / sizeof(wchar_t)) - wcslen(g_extra_flags) - 1);
        }
    }
    return g_extra_flags;
}

static wchar_t *resolve_real_binary(void)
{
    wchar_t self[MAX_PATH];
    DWORD len = GetModuleFileNameW(NULL, self, MAX_PATH);
    if (len == 0 || len >= MAX_PATH) return NULL;

    wchar_t *slash = wcsrchr(self, L'\\');
    if (!slash) return NULL;
    *(slash + 1) = L'\0';

    size_t cap = wcslen(self) + wcslen(REAL_BINARY) + 1;
    wchar_t *real = (wchar_t *)calloc(cap, sizeof(wchar_t));
    if (!real) return NULL;
    wcscpy(real, self);
    wcscat(real, REAL_BINARY);
    return real;
}

static const wchar_t *args_tail(void)
{
    const wchar_t *cmd = GetCommandLineW();
    if (!cmd) return L"";

    int in_quotes = 0;
    while (*cmd) {
        wchar_t c = *cmd;
        if (c == L'"') in_quotes = !in_quotes;
        else if (c == L' ' && !in_quotes) break;
        ++cmd;
    }
    while (*cmd == L' ') ++cmd;
    return cmd;
}

int wmain(void)
{
    wchar_t *real = resolve_real_binary();
    if (!real) return 1;

    const wchar_t *tail = args_tail();
    int is_child = (wcsstr(tail, L"--type=") != NULL);
    size_t cap = wcslen(real) + wcslen(tail) + 128;
    wchar_t *cmdline = (wchar_t *)calloc(cap, sizeof(wchar_t));
    if (!cmdline) {
        free(real);
        return 1;
    }
    if (is_child) {
        _snwprintf(cmdline, cap, L"\"%ls\" %ls %ls", real, CHILD_FLAGS, tail);
    } else {
        _snwprintf(cmdline, cap, L"\"%ls\" %ls %ls", real, extra_flags(), tail);
    }

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));

    BOOL ok = CreateProcessW(real, cmdline, NULL, NULL, TRUE, 0, NULL, NULL, &si, &pi);
    if (!ok) {
        free(cmdline);
        free(real);
        return 1;
    }

    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    free(cmdline);
    free(real);
    return (int)code;
}
