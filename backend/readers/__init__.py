"""
Shared utility: read USB vendor ID from Linux sysfs so each reader module
can filter ports to only the hardware it understands.
"""
import os


def usb_vid(port: str) -> int | None:
    """Return the USB idVendor for a /dev/tty* port, or None if unavailable."""
    dev = os.path.basename(port)
    try:
        real = os.path.realpath(f"/sys/class/tty/{dev}/device")
        path = real
        for _ in range(6):
            path = os.path.dirname(path)
            vid_file = os.path.join(path, "idVendor")
            if os.path.exists(vid_file):
                return int(open(vid_file).read().strip(), 16)
    except Exception:
        pass
    return None
