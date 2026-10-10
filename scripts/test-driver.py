#!/usr/bin/env python3
"""Exercise real BMA400/battery code against mocked Zephyr services; install nothing."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile


def compiler(variable, candidates):
    if os.environ.get(variable):
        return shlex.split(os.environ[variable])
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return [path]
    raise SystemExit(f"No host {variable} compiler found; no tools were installed.")


def main():
    project = Path(__file__).resolve().parent.parent
    cc = compiler("CC", ("clang", "cc", "gcc"))
    cxx = compiler("CXX", ("clang++", "c++", "g++"))
    includes = ["-I" + str(project / "tests/native_stubs"),
                "-I" + str(project / "src/bma400"), "-I" + str(project / "src")]
    warnings = ["-Wall", "-Wextra", "-Werror"]
    with tempfile.TemporaryDirectory(prefix="nrf-mailbox-driver-test-") as temporary:
        temporary = Path(temporary)
        api_object = temporary / "bma400.o"
        subprocess.run([*cc, "-std=c99", *warnings, *includes, "-c",
                        str(project / "src/bma400/bma400.c"), "-o", str(api_object)], check=True)
        # Both I2C addresses, switched/permanent dividers, and explicit opt-out.
        for address, switched, installed in ((0x14, 1, True), (0x15, 0, True), (0x14, 0, False)):
            executable = temporary / f"driver-{address:x}-{switched}-{installed}"
            defines = [f"-DTEST_BMA400_ADDRESS={address}", f"-DTEST_BATTERY_SWITCHED={switched}"]
            if installed:
                defines.append("-DCONFIG_MAILBOX_BATTERY_INSTALLED=1")
            subprocess.run([*cxx, "-std=c++17", *warnings, *defines, *includes,
                            str(project / "src/bma400/Bma400Motion.cpp"),
                            str(project / "src/battery_monitor.cpp"),
                            str(project / "tests/test_driver.cpp"), str(api_object),
                            "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
