"""Package entry point so `python -m quantdeck.mcp_server` starts the server
cleanly (avoids the double-import RuntimeWarning from `-m ...server`)."""
from .server import main

if __name__ == "__main__":
    main()
