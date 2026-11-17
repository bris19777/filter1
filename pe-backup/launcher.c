/* Wrapper EXE: extracts the embedded backup_users.cmd to TEMP and runs it. */
#include <windows.h>
#include <stdio.h>
#include "script.h"

int main(void)
{
    wchar_t tmp[MAX_PATH], path[MAX_PATH], cmd[MAX_PATH * 2], comspec[MAX_PATH];
    DWORD written, code = 1;
    HANDLE h;
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;

    if (!GetTempPathW(MAX_PATH, tmp) || GetFileAttributesW(tmp) == INVALID_FILE_ATTRIBUTES)
        lstrcpyW(tmp, L"X:\\Windows\\Temp\\");
    swprintf(path, MAX_PATH, L"%lsbackup_users_%lu.cmd", tmp, GetCurrentProcessId());

    h = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        printf("Cannot write temp script.\n");
        system("pause");
        return 1;
    }
    WriteFile(h, script_cmd, script_cmd_len, &written, NULL);
    CloseHandle(h);

    if (!GetEnvironmentVariableW(L"ComSpec", comspec, MAX_PATH))
        lstrcpyW(comspec, L"cmd.exe");
    swprintf(cmd, MAX_PATH * 2, L"\"%ls\" /c \"\"%ls\"\"", comspec, path);

    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    if (CreateProcessW(NULL, cmd, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
        WaitForSingleObject(pi.hProcess, INFINITE);
        GetExitCodeProcess(pi.hProcess, &code);
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
    } else {
        printf("Cannot start cmd.exe\n");
        system("pause");
    }
    DeleteFileW(path);
    return (int)code;
}
