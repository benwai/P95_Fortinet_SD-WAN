#!/usr/bin/env python3
"""
95th percentile bandwidth calculator - Fortinet SD-WAN logs.

Logic:
  - Per line              : max(inbandwidthused, outbandwidthused)
  - Single interface      : values grouped by day → P95 per day
  - Multiple interfaces   : values aggregated by timestamp first (summed across
                            interfaces), then grouped by day → P95 per day
  - Per day               : dynamically exclude the top 5% values
                            (based on the actual number of samples for that day)
                            then P95 = maximum of the remaining values

Usage:
    python bandwidth_95th.py <log_file> [options]

Options:
    --output FILE              Output CSV file (default: print to terminal)
    --interface IFACE [IFACE…] One or more interfaces to include (mandatory)
    --healthcheck HC           Healthcheck to filter on (mandatory)
    --verbose                  Print the first 5 parsed/aggregated entries

Examples:
    python bandwidth_95th.py fortigate.log --interface port1 --healthcheck Google
    python bandwidth_95th.py fortigate.log --interface port1 VPN-Cato-1 --healthcheck Google
    python bandwidth_95th.py fortigate.log --interface port1 VPN-Cato-1 VPN-Cato-2 --healthcheck Google --output results.csv
"""

import argparse
import csv
import re
import sys
from collections import defaultdict

P95_PERCENTILE = 0.05   # top fraction of values to exclude

UNIT_TO_KBPS = {
    "kbps": 1,
    "mbps": 1_000,
    "gbps": 1_000_000,
}

# Regex to extract key=value pairs (quoted or unquoted)
RE_KV = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


def parse_bandwidth(value: str) -> float:
    """Convert a bandwidth string to kbps (e.g. '107kbps' -> 107.0, '1000.00Mbps' -> 1_000_000.0)."""
    value = value.strip().strip('"')
    match = re.match(r"([\d.]+)\s*([a-zA-Z]+)", value)
    if not match:
        raise ValueError(f"Unrecognized bandwidth format: {value!r}")
    number     = float(match.group(1))
    unit       = match.group(2).lower()
    multiplier = UNIT_TO_KBPS.get(unit)
    if multiplier is None:
        raise ValueError(f"Unknown unit: {unit!r}")
    return number * multiplier


def parse_line(line: str) -> dict:
    """Parse a Fortinet log line into a key→value dictionary."""
    result = {}
    for key, value in RE_KV.findall(line):
        result[key] = value.strip('"')
    return result


def process_file(
    filepath: str,
    interface_filters: list[str],
    healthcheck_filter: str,
    verbose: bool,
) -> dict:
    """
    Read the log file and return aggregated daily values.

    Single interface  → { "YYYY-MM-DD": [max_bw, ...] }
    Multiple interfaces → values are first accumulated per (date, time) timestamp,
                          summing bandwidth across all matched interfaces at the same
                          second, then flattened to { "YYYY-MM-DD": [agg_bw, ...] }
    """
    multi = len(interface_filters) > 1

    errors        = 0
    parsed        = 0
    skipped_iface = 0
    skipped_hc    = 0

    if multi:
        # { (date, time): { interface: max_bw } }
        ts_buckets: dict[tuple, dict[str, float]] = defaultdict(dict)
    else:
        daily_values: dict[str, list] = defaultdict(list)

    verbose_raw: list[dict] = []   # raw parsed rows for verbose display

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            fields     = parse_line(line)
            date_str   = fields.get("date")
            time_str   = fields.get("time", "")
            in_bw_raw  = fields.get("inbandwidthused")
            out_bw_raw = fields.get("outbandwidthused")
            iface      = fields.get("interface", "")

            # Skip lines with missing required fields
            if not date_str or in_bw_raw is None or out_bw_raw is None:
                errors += 1
                if errors <= 3:
                    print(f"[WARN] Line {line_num}: missing fields (date/inbandwidthused/outbandwidthused)")
                continue

            # Interface filter
            if iface not in interface_filters:
                skipped_iface += 1
                continue

            # Healthcheck filter
            if fields.get("healthcheck", "") != healthcheck_filter:
                skipped_hc += 1
                continue

            try:
                in_bw  = parse_bandwidth(in_bw_raw)
                out_bw = parse_bandwidth(out_bw_raw)
            except ValueError as e:
                errors += 1
                if errors <= 3:
                    print(f"[WARN] Line {line_num}: {e}")
                continue

            max_bw = max(in_bw, out_bw)
            parsed += 1

            if multi:
                # Accumulate per (date, time) bucket — one entry per interface
                ts_buckets[(date_str, time_str)][iface] = max_bw
            else:
                daily_values[date_str].append(max_bw)

            if verbose and len(verbose_raw) < 5:
                verbose_raw.append({
                    "line":        line_num,
                    "date":        date_str,
                    "time":        time_str,
                    "interface":   iface,
                    "healthcheck": fields.get("healthcheck", "?"),
                    "in_kbps":     in_bw,
                    "out_kbps":    out_bw,
                    "max_kbps":    max_bw,
                })

    skipped_total = skipped_iface + skipped_hc
    print(f"[INFO] Lines parsed   : {parsed}")
    print(f"[INFO] Lines skipped  : {skipped_total}"
          + (f"  (interface={skipped_iface}, healthcheck={skipped_hc})" if skipped_total else ""))
    print(f"[INFO] Errors         : {errors}")

    # ── Verbose: raw entries ──────────────────────────────────────────────────
    if verbose and verbose_raw:
        print("\n[VERBOSE] First 5 raw parsed entries (before aggregation):")
        print(f"  {'Line':>6}  {'Date':<12}  {'Time':<10}  {'Interface':<18}  {'Healthcheck':<16}"
              f"  {'In (kbps)':>12}  {'Out (kbps)':>12}  {'Max (kbps)':>12}")
        print("  " + "-" * 112)
        for s in verbose_raw:
            print(f"  {s['line']:>6}  {s['date']:<12}  {s['time']:<10}  {s['interface']:<18}  {s['healthcheck']:<16}"
                  f"  {s['in_kbps']:>12.2f}  {s['out_kbps']:>12.2f}  {s['max_kbps']:>12.2f}")

    # ── Multi-interface aggregation ───────────────────────────────────────────
    if multi:
        # Sum bandwidth across interfaces for each timestamp, then group by date
        daily_values: dict[str, list] = defaultdict(list)
        for (date_str, _time_str), iface_map in ts_buckets.items():
            aggregated_bw = sum(iface_map.values())
            daily_values[date_str].append(aggregated_bw)

        if verbose:
            # Show first 5 aggregated timestamps
            agg_samples = []
            for (date_str, time_str), iface_map in sorted(ts_buckets.items())[:5]:
                agg_samples.append((date_str, time_str, iface_map, sum(iface_map.values())))

            print(f"\n[VERBOSE] First 5 aggregated timestamps (summed across {len(interface_filters)} interfaces):")
            print(f"  {'Date':<12}  {'Time':<10}  {'Interfaces seen':<30}  {'Sum (kbps)':>12}")
            print("  " + "-" * 72)
            for date_str, time_str, iface_map, total in agg_samples:
                ifaces_seen = ", ".join(f"{k}={v:.0f}" for k, v in iface_map.items())
                print(f"  {date_str:<12}  {time_str:<10}  {ifaces_seen:<30}  {total:>12.2f}")
        print()

    return daily_values


def compute_95th(values: list) -> tuple:
    """
    95th percentile (billing method):
      1. Dynamically compute exclude_n = floor(5% x actual sample count for the day)
      2. Sort values in descending order
      3. Remove the top <exclude_n> values
      4. Return (p95, exclude_n)
    """
    n           = len(values)
    exclude_n   = int(n * P95_PERCENTILE)   # floor(5% x actual sample count)
    sorted_vals = sorted(values, reverse=True)

    if exclude_n == 0:
        # Fewer than 20 samples: no exclusion possible, return the maximum
        return sorted_vals[0], 0

    return sorted_vals[exclude_n], exclude_n


def print_results(results: list, interface_filters: list[str]):
    print()
    if len(interface_filters) > 1:
        print(f"  Interfaces : {', '.join(interface_filters)} (aggregated by timestamp)")
    else:
        print(f"  Interface  : {interface_filters[0]}")
    print(f"  P95 method : dynamic exclusion of top {P95_PERCENTILE*100:.0f}% values per day")
    print(f"               (excluded count = floor(5% x actual sample count for the day))")
    print()
    header = (f"{'Date':<14} {'Samples':>10} {'Excluded (5%)':>14} "
              f"{'Max raw (kbps)':>16} {'P95 (kbps)':>12} {'P95 (Mbps)':>12}")
    sep = "-" * len(header)
    print(header)
    print(sep)
    for r in results:
        print(
            f"{r['date']:<14} {r['count']:>10} {r['excluded']:>14} "
            f"{r['max_raw']:>16.2f} {r['p95']:>12.2f} {r['p95']/1000:>12.4f}"
        )

    # Summary row: maximum of all daily P95 values
    max_p95 = max(r["p95"] for r in results)
    print(sep)
    print(
        f"{'MAX P95':<14} {'':>10} {'':>14} "
        f"{'':>16} {max_p95:>12.2f} {max_p95/1000:>12.4f}"
    )
    print()


def write_csv(results: list, output_path: str):
    """Write results to a CSV file, including a MAX P95 summary row."""
    max_p95 = max(r["p95"] for r in results)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "date", "samples", "excluded_5pct",
            "max_raw_kbps", "p95_kbps", "p95_mbps"
        ])
        writer.writeheader()
        for r in results:
            writer.writerow({
                "date":          r["date"],
                "samples":       r["count"],
                "excluded_5pct": r["excluded"],
                "max_raw_kbps":  round(r["max_raw"], 3),
                "p95_kbps":      round(r["p95"], 3),
                "p95_mbps":      round(r["p95"] / 1000, 6),
            })
        # Summary row
        writer.writerow({
            "date":          "MAX P95",
            "samples":       "",
            "excluded_5pct": "",
            "max_raw_kbps":  "",
            "p95_kbps":      round(max_p95, 3),
            "p95_mbps":      round(max_p95 / 1000, 6),
        })
    print(f"[INFO] Results written to: {output_path}")


def scan_available_filters(filepath: str) -> tuple:
    """
    Scan the log file and return the sets of unique values found for
    'interface' and 'healthcheck' fields, without applying any filter.
    """
    interfaces   = set()
    healthchecks = set()

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            fields = parse_line(line.strip())
            if "interface" in fields:
                interfaces.add(fields["interface"])
            if "healthcheck" in fields:
                healthchecks.add(fields["healthcheck"])

    return interfaces, healthchecks


def main():
    parser = argparse.ArgumentParser(
        description="95th percentile bandwidth per day - Fortinet SD-WAN logs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("file",
                        help="Fortinet log file")
    parser.add_argument("--output",      default=None,
                        help="Output CSV file (optional)")
    parser.add_argument("--interface",   default=None, nargs="+",
                        help="One or more interfaces to include (mandatory). "
                             "When multiple interfaces are given, their bandwidth "
                             "is summed per timestamp before P95 is computed.")
    parser.add_argument("--healthcheck", default=None,
                        help="Healthcheck to filter on (mandatory)")
    parser.add_argument("--verbose",     action="store_true",
                        help="Print the first 5 parsed/aggregated entries for verification")

    args = parser.parse_args()

    # If either mandatory filter is missing, scan the file and show available values
    if not args.interface or not args.healthcheck:
        print(f"[INFO] Scanning available filter values in: {args.file}")
        interfaces, healthchecks = scan_available_filters(args.file)
        print()
        if not args.interface:
            print("  --interface is required. Available values found in the file:")
            for iface in sorted(interfaces):
                print(f"      {iface}")
        if not args.healthcheck:
            print("  --healthcheck is required. Available values found in the file:")
            for hc in sorted(healthchecks):
                print(f"      {hc}")
        print()
        ex_iface = " ".join(f'"{i}"' for i in sorted(interfaces)[:2]) if interfaces else '"<interface>"'
        ex_hc    = sorted(healthchecks)[0] if healthchecks else "<healthcheck>"
        print("Example (single interface):")
        ex1 = sorted(interfaces)[0] if interfaces else "<interface>"
        print(f'  python bandwidth_95th.py {args.file} --interface "{ex1}" --healthcheck "{ex_hc}"')
        if len(interfaces) > 1:
            print("Example (multiple interfaces, aggregated):")
            print(f'  python bandwidth_95th.py {args.file} --interface {ex_iface} --healthcheck "{ex_hc}"')
        sys.exit(1)

    interface_filters = args.interface   # already a list thanks to nargs="+"
    multi             = len(interface_filters) > 1

    print(f"[INFO] File           : {args.file}")
    print(f"[INFO] P95 method     : dynamic exclusion of top {P95_PERCENTILE*100:.0f}% samples per day")
    if multi:
        print(f"[INFO] Interfaces     : {', '.join(interface_filters)} → aggregated by timestamp")
    else:
        print(f"[INFO] Interface      : {interface_filters[0]}")
    print(f"[INFO] Healthchk filter: {args.healthcheck}")
    print()

    daily_values = process_file(
        filepath=args.file,
        interface_filters=interface_filters,
        healthcheck_filter=args.healthcheck,
        verbose=args.verbose,
    )

    if not daily_values:
        print("[ERROR] No valid data found. Please check your filters.")
        sys.exit(1)

    results = []
    for date in sorted(daily_values.keys()):
        vals          = daily_values[date]
        p95, excluded = compute_95th(vals)
        results.append({
            "date":     date,
            "count":    len(vals),
            "max_raw":  max(vals),
            "excluded": excluded,
            "p95":      p95,
        })

    print_results(results, interface_filters)

    if args.output:
        write_csv(results, args.output)


if __name__ == "__main__":
    main()
