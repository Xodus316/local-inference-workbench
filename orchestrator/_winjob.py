"""Windows child process. Close the job handle on exit to end the whole test tree."""
import ctypes
from ctypes import wintypes
import subprocess
import sys


def main(argv):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = wintypes.HANDLE
    ulong = wintypes.DWORD
    size = ctypes.c_size_t
    large = ctypes.c_longlong

    class Limits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", large), ("PerJobUserTimeLimit", large),
                    ("LimitFlags", ulong), ("MinimumWorkingSetSize", size), ("MaximumWorkingSetSize", size),
                    ("ActiveProcessLimit", ulong), ("Affinity", size), ("PriorityClass", ulong),
                    ("SchedulingClass", ulong)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Limits), ("IoInfo", ctypes.c_ulonglong * 6),
                    ("ProcessMemoryLimit", size), ("JobMemoryLimit", size),
                    ("PeakProcessMemoryUsed", size), ("PeakJobMemoryUsed", size)]

    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = handle
    kernel.SetInformationJobObject.argtypes = [handle, ctypes.c_int, ctypes.c_void_p, ulong]
    kernel.AssignProcessToJobObject.argtypes = [handle, handle]
    kernel.GetCurrentProcess.restype = handle
    job = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not (job and kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            and kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess())):
        raise ctypes.WinError(ctypes.get_last_error())
    return subprocess.call(argv, close_fds=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
