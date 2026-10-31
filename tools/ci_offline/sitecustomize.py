"""Reject public Python socket access in offline CI, including inherited subprocesses. Loopback/Unix sockets work."""
import ipaddress
import sys

_installed = False


def check(event, args):
    if event == 'socket.connect':
        address = args[1]
        if not isinstance(address, tuple):       # Unix domain socket
            return
        host = address[0]
    elif event == 'socket.getaddrinfo':
        host = args[0]
    else:
        return
    if host in (None, '', 'localhost', b'localhost'):
        return
    try:
        if ipaddress.ip_address(host.decode() if isinstance(host, bytes) else host).is_loopback:
            return
    except ValueError:
        pass
    raise OSError('Offline CI forbids external socket access: ' + str(host))


def install():
    global _installed
    if not _installed:
        sys.addaudithook(check)
        _installed = True


install()
