import argparse
import json
import csv
import sys
from .scraper import scrape, scrape_investing_gold, scrape_investing_gold_historical, scrape_latest_from_historical


def main():
    p = argparse.ArgumentParser(description="Simple web scraper: fetch items by CSS selector")
    p.add_argument("url", help="URL to scrape")
    p.add_argument("selector", nargs='?', help="CSS selector for target elements (omit with --preset)")
    p.add_argument("--preset", choices=["investing-gold", "investing-gold-historical"], help="Use a built-in parser for a known site")
    p.add_argument("-o", "--output", help="Output file (omit to write to stdout)")
    p.add_argument("--format", choices=["json", "csv", "lines"], default="json", help="Output format")
    p.add_argument("--timeout", type=int, default=10, help="Request timeout seconds")
    args = p.parse_args()

    if args.preset == "investing-gold":
        # prefer investing.com historical page for OHLC
        items = scrape_latest_from_historical(timeout=args.timeout)
    elif args.preset == "investing-gold-historical":
        items = scrape_investing_gold_historical(args.url, timeout=args.timeout)
    else:
        if not args.selector:
            raise SystemExit("selector required when not using --preset")
        items = scrape(args.url, args.selector, timeout=args.timeout)

    def write_out(out_stream):
        if args.preset == "investing-gold":
            # items is a dict
            if args.format == "json":
                json.dump(items, out_stream, ensure_ascii=False, indent=2)
            elif args.format == "csv":
                writer = csv.writer(out_stream)
                for k, v in items.items():
                    writer.writerow([k, v])
            else:
                for k, v in items.items():
                    out_stream.write(f"{k}: {v}\n")
            return

        # default behavior for list results
        if args.format == "json":
            json.dump(items, out_stream, ensure_ascii=False, indent=2)
        elif args.format == "csv":
            writer = csv.writer(out_stream)
            for it in items:
                writer.writerow([it])
        else:  # lines
            for it in items:
                out_stream.write(it + "\n")

    if args.output:
        mode = "w"
        with open(args.output, mode, encoding="utf-8", newline=("" if args.format != "csv" else None)) as f:
            write_out(f)
    else:
        write_out(sys.stdout)


if __name__ == "__main__":
    main()
