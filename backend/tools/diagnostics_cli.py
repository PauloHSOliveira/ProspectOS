"""CLI for diagnostics: inspect health or export ZIP.

Usage:
  python -m backend.tools.diagnostics_cli inspect
  python -m backend.tools.diagnostics_cli export --output /tmp/diag.zip
"""

import argparse
import json
import sys
from pathlib import Path


def cmd_inspect():
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import health
    report = health.collect_health()
    print(json.dumps(health.health_report_to_dict(report), indent=2, ensure_ascii=False))
    return 0


def cmd_export(args):
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import diagnostics
    output = Path(args.output)
    if output.is_dir():
        output = output / f"prospectos-diagnostics-{diagnostics.health._now_iso().replace(':', '-')}.zip"
    result = diagnostics.export_diagnostics_zip(output, force=args.force)
    print(f"Diagnostics exported to: {result}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="ProspectOS diagnostics CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_parser = sub.add_parser("inspect", help="Print health report to stdout")
    inspect_parser.set_defaults(func=cmd_inspect)

    export_parser = sub.add_parser("export", help="Export diagnostics ZIP")
    export_parser.add_argument("--output", "-o", required=True, help="Output path or directory")
    export_parser.add_argument("--force", "-f", action="store_true", help="Overwrite existing file")
    export_parser.set_defaults(func=cmd_export)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
