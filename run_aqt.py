"""AQT Windows launcher — starts the web UI server and opens the browser."""
import sys
import os


def main():
    # Ensure the project root is on sys.path and set as working directory
    root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root)
    if root not in sys.path:
        sys.path.insert(0, root)

    from aqt.web import run_server

    host = "127.0.0.1"
    port = 8765
    print(f"AQT A-Share Quant Toolkit")
    print(f"Starting at http://{host}:{port}")
    run_server(host=host, port=port, open_browser=True)


if __name__ == "__main__":
    main()
