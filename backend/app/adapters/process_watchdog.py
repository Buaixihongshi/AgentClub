"""A pipe EOF survives host SIGKILL and terminates its retained CLI group."""
import os
import signal
import sys
import time


def run(fd, pgid):
    try:
        while os.read(fd, 1):
            pass
    finally:
        os.close(fd)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except OSError:
            return
        if sig == signal.SIGTERM:
            time.sleep(0.3)


if __name__ == '__main__':
    run(int(sys.argv[1]), int(sys.argv[2]))
