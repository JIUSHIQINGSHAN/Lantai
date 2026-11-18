"""Real stdio bridge with in-memory synthetic config; no deployed conf is read."""

import importlib.util
import ipaddress
import sys
import urllib.request


def loopback_only(event, args):
    if event == "socket.connect":
        address = args[1]
        if not isinstance(address, tuple) or not ipaddress.ip_address(address[0]).is_loopback:
            raise RuntimeError("verification is limited to loopback")


sys.addaudithook(loopback_only)
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
spec = importlib.util.spec_from_file_location("synthetic_stdio_bridge", sys.argv[1])
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
bridge._conf = {"host": "127.0.0.1", "port": int(sys.argv[2]), "api_key": "synthetic-chain-key"}
bridge.main()
