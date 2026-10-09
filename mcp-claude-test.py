import getpass
import platform
import socket
from datetime import datetime

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

mcp = MCPServer("windows-test")


@mcp.tool()
def windows_info() -> dict[str, str]:
    """Retorna informacoes simples da maquina Windows."""
    return {
        "hostname": socket.gethostname(),
        "usuario": getpass.getuser(),
        "sistema": platform.platform(),
        "data_hora": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8001,
        streamable_http_path="/mcp",
        # Necessario para o teste atraves do reverse proxy HTTPS.
        # Nao usar assim para um servidor publico real.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
