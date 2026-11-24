/*
 * ExtendC.exe - extends drive C: into all unallocated space.
 * The actual disk work is done by an embedded PowerShell script (extend-c.ps1).
 * Build: see build.sh
 */
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <stdio.h>
#include <wchar.h>

#define IDR_PS1 101
#define TITLE   L"הגדלת כונן C:"
#define MB_HEB  (MB_RTLREADING | MB_RIGHT)

static wchar_t g_dir[MAX_PATH], g_ps1[MAX_PATH], g_out[MAX_PATH], g_log[MAX_PATH];

static void fail(const wchar_t *msg)
{
    MessageBoxW(NULL, msg, TITLE, MB_OK | MB_ICONERROR | MB_HEB);
    ExitProcess(1);
}

static int write_script(void)
{
    HRSRC r = FindResourceW(NULL, MAKEINTRESOURCEW(IDR_PS1), (LPCWSTR)RT_RCDATA);
    if (!r) return 0;
    HGLOBAL h = LoadResource(NULL, r);
    const BYTE *data = (const BYTE *)LockResource(h);
    DWORD size = SizeofResource(NULL, r), w;
    HANDLE f = CreateFileW(g_ps1, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return 0;
    static const BYTE bom[3] = {0xEF, 0xBB, 0xBF};
    if (size < 3 || memcmp(data, bom, 3) != 0) WriteFile(f, bom, 3, &w, NULL);
    WriteFile(f, data, size, &w, NULL);
    CloseHandle(f);
    return 1;
}

/* Reads a UTF-8 (optionally BOM) text file into a newly allocated wide string. */
static wchar_t *read_utf8(const wchar_t *path)
{
    HANDLE f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return NULL;
    DWORD size = GetFileSize(f, NULL), rd = 0;
    char *buf = (char *)HeapAlloc(GetProcessHeap(), 0, size + 1);
    ReadFile(f, buf, size, &rd, NULL);
    CloseHandle(f);
    char *p = buf;
    if (rd >= 3 && (BYTE)p[0] == 0xEF && (BYTE)p[1] == 0xBB && (BYTE)p[2] == 0xBF) { p += 3; rd -= 3; }
    int n = MultiByteToWideChar(CP_UTF8, 0, p, (int)rd, NULL, 0);
    wchar_t *ws = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, (n + 1) * sizeof(wchar_t));
    MultiByteToWideChar(CP_UTF8, 0, p, (int)rd, ws, n);
    ws[n] = 0;
    HeapFree(GetProcessHeap(), 0, buf);
    return ws;
}

/* ---- small "please wait" window ---- */
static LRESULT CALLBACK wait_proc(HWND h, UINT m, WPARAM wp, LPARAM lp)
{
    if (m == WM_CLOSE) return 0; /* not closable while working */
    return DefWindowProcW(h, m, wp, lp);
}

static HWND show_wait(void)
{
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = wait_proc;
    wc.hInstance = GetModuleHandleW(NULL);
    wc.hCursor = LoadCursor(NULL, IDC_WAIT);
    wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    wc.lpszClassName = L"ExtendCWait";
    RegisterClassW(&wc);

    int W = 460, H = 150;
    int x = (GetSystemMetrics(SM_CXSCREEN) - W) / 2, y = (GetSystemMetrics(SM_CYSCREEN) - H) / 2;
    HWND h = CreateWindowExW(WS_EX_TOPMOST | WS_EX_LAYOUTRTL, wc.lpszClassName, TITLE,
                             WS_POPUP | WS_CAPTION | WS_VISIBLE, x, y, W, H, NULL, NULL, wc.hInstance, NULL);
    HWND s = CreateWindowExW(0, L"STATIC",
                             L"מגדיל את כונן C: ...\n\nנא להמתין ולא לכבות את המחשב.\nהפעולה עשויה להימשך כמה דקות.",
                             WS_CHILD | WS_VISIBLE | SS_CENTER, 10, 20, W - 20, H - 50, h, NULL, wc.hInstance, NULL);
    SendMessageW(s, WM_SETFONT, (WPARAM)GetStockObject(DEFAULT_GUI_FONT), TRUE);
    UpdateWindow(h);
    return h;
}

/* Runs the script in the given mode; returns its exit code (or -1). */
static DWORD run_ps(const wchar_t *mode, int with_wait_window)
{
    wchar_t ps[MAX_PATH], cmd[4 * MAX_PATH];
    GetSystemDirectoryW(ps, MAX_PATH);
    wcscat(ps, L"\\WindowsPowerShell\\v1.0\\powershell.exe");
    _snwprintf(cmd, sizeof(cmd) / sizeof(cmd[0]),
               L"\"%ls\" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"%ls\" -Mode %ls -Out \"%ls\" -Log \"%ls\"",
               ps, g_ps1, mode, g_out, g_log);
    DeleteFileW(g_out);

    STARTUPINFOW si = {sizeof(si)};
    PROCESS_INFORMATION pi;
    if (!CreateProcessW(ps, cmd, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, g_dir, &si, &pi))
        return (DWORD)-1;

    HWND wait = with_wait_window ? show_wait() : NULL;
    for (;;) {
        DWORD r = MsgWaitForMultipleObjects(1, &pi.hProcess, FALSE, INFINITE, QS_ALLINPUT);
        if (r == WAIT_OBJECT_0) break;
        MSG msg;
        while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    }
    if (wait) DestroyWindow(wait);

    DWORD code = (DWORD)-1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return code;
}

static void offer_log(const wchar_t *text, UINT icon)
{
    wchar_t msg[8192];
    _snwprintf(msg, 8192, L"%ls\n\nלפתוח את קובץ הלוג?\n%ls", text, g_log);
    msg[8191] = 0;
    if (MessageBoxW(NULL, msg, TITLE, MB_YESNO | icon | MB_HEB) == IDYES)
        ShellExecuteW(NULL, L"open", L"notepad.exe", g_log, NULL, SW_SHOWNORMAL);
}

int WINAPI wWinMain(HINSTANCE hi, HINSTANCE hp, PWSTR cl, int show)
{
    (void)hi; (void)hp; (void)cl; (void)show;

    GetTempPathW(MAX_PATH, g_dir);
    wcscat(g_dir, L"ExtendC");
    CreateDirectoryW(g_dir, NULL);
    _snwprintf(g_ps1, MAX_PATH, L"%ls\\extend-c.ps1", g_dir);
    _snwprintf(g_out, MAX_PATH, L"%ls\\result.txt", g_dir);
    _snwprintf(g_log, MAX_PATH, L"%ls\\ExtendC-log.txt", g_dir);

    if (!write_script()) fail(L"לא ניתן לכתוב קובץ זמני לתיקיית TEMP.");

    /* Phase 1: analyze (no changes) */
    DWORD rc = run_ps(L"Analyze", 0);
    wchar_t *res = read_utf8(g_out);
    if (rc == (DWORD)-1 || !res) fail(L"לא ניתן להריץ את PowerShell. ודא שהכלי רץ כמנהל (Administrator).");

    if (rc == 2) { MessageBoxW(NULL, res, TITLE, MB_OK | MB_ICONINFORMATION | MB_HEB); return 0; }
    if (rc != 0) { offer_log(res, MB_ICONWARNING); return (int)rc; }

    if (MessageBoxW(NULL, res, TITLE, MB_YESNO | MB_DEFBUTTON2 | MB_ICONQUESTION | MB_HEB) != IDYES)
        return 0;

    /* Phase 2: apply */
    rc = run_ps(L"Apply", 1);
    res = read_utf8(g_out);
    if (!res) res = L"הפעולה הסתיימה ללא תוצאה. בדוק את קובץ הלוג.";
    if (rc == 0) MessageBoxW(NULL, res, TITLE, MB_OK | MB_ICONINFORMATION | MB_HEB);
    else offer_log(res, MB_ICONERROR);
    return (int)rc;
}
