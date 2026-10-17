import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the UWB live dashboard")
    parser.add_argument(
        "--source", choices=("demo", "tcp", "serial", "dual-serial", "replay"), default="demo"
    )
    parser.add_argument("--tcp-host", default="192.168.1.10")
    parser.add_argument("--tcp-port", type=int, default=3333)
    parser.add_argument("--serial-port", default="/dev/tty.usbmodem1")
    parser.add_argument("--responder-port", help="Responder USB serial port for dual-serial radio control")
    parser.add_argument("--replay-file")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.source == "replay" and not args.replay_file:
        parser.error("--replay-file is required for replay mode")
    if args.source == "dual-serial" and not args.responder_port:
        parser.error("--responder-port is required for dual-serial mode")

    os.environ["UWB_SOURCE"] = args.source
    os.environ["UWB_TCP_HOST"] = args.tcp_host
    os.environ["UWB_TCP_PORT"] = str(args.tcp_port)
    os.environ["UWB_SERIAL_PORT"] = args.serial_port
    if args.responder_port:
        os.environ["UWB_RESPONDER_PORT"] = args.responder_port
    if args.replay_file:
        os.environ["UWB_REPLAY_FILE"] = args.replay_file

    import uvicorn

    from .app import app

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
