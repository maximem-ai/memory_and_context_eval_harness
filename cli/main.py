import argparse
import sys
import logging
from bench.runner import Runner
from bench.config import Config

def main():
    parser = argparse.ArgumentParser(description="Eval Framework CLI")
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # Ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest datasets")
    ingest_parser.add_argument("--dataset", required=True, help="Dataset name (longmemeval)")
    ingest_parser.add_argument("--out", required=True, help="Output directory")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run benchmark")
    run_parser.add_argument("--config", required=True, help="Path to config yaml")
    run_parser.add_argument("--adapter", help="Override adapter class")

    args = parser.parse_args()

    if args.command == "ingest":
        print(f"Ingesting {args.dataset} to {args.out}...")
        # Call loader logic here
        pass

    elif args.command == "run":
        print(f"Starting run with config {args.config}...")
        try:
            cfg = Config.from_file(args.config)
            if args.adapter:
                cfg.adapter = args.adapter
            
            runner = Runner(cfg)
            output_dir = runner.run()
            print(f"Run finished. Results in {output_dir}")
        except Exception as e:
            logging.error(f"Run failed: {e}")
            sys.exit(1)

    else:
        parser.print_help()

if __name__ == "__main__":
    main()
