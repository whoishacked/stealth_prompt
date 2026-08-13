"""Main entry point for Stealth Prompt (legacy Selenium runner).

This is the compatibility entry point described in ``internal-docs/migration-plan.md``.
The scenario-driven ``stealth-prompt`` command replaces it in a later
milestone; until then ``python main.py`` remains the supported way to run the
legacy tester.
"""

import argparse
import sys
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.config_loader import ConfigLoader
from src.penetration_tester import PenetrationTester

EPILOG = """
Examples:
  # Run with default config.yaml
  python main.py

  # Run with custom config file
  python main.py --config custom_config.yaml

  # Run single test type
  python main.py --test-type prompt_injection

  # Generate payload only (dry run)
  python main.py --dry-run
        """


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the legacy runner."""
    parser = argparse.ArgumentParser(
        description="Stealth Prompt - AI Agent Penetration Testing Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )

    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to configuration file (default: config.yaml)'
    )

    parser.add_argument(
        '--test-type',
        type=str,
        help='Run a single test type instead of all configured tests'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Generate payloads only, do not send to AI agent'
    )

    return parser


def run_dry_run(tester: Any, test_types: list[str], *, out: TextIO) -> None:
    """Generate one payload per test type without contacting the target."""
    print("\n=== DRY RUN MODE ===", file=out)

    for test_type in test_types:
        print(f"\nGenerating payload for: {test_type}", file=out)
        payload = tester.llm_client.generate_payload(test_type)
        print(f"Payload: {payload}\n", file=out)


def run_single_test(tester: Any, test_type: str) -> None:
    """Run one test type against an already-configured target.

    ``PenetrationTester.run_test`` records the result itself, so the caller must
    not append it again.
    """
    tester.web_automation.start()
    try:
        tester.run_test(test_type)
    finally:
        tester.web_automation.close()


def run(
    argv: Sequence[str] | None = None,
    *,
    config_loader_factory: Callable[..., Any] = ConfigLoader,
    tester_factory: Callable[..., Any] = PenetrationTester,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run the legacy CLI and return a process exit code.

    The factory arguments are seams for testing; the defaults preserve the
    original behavior.
    """
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr

    parser = build_parser()
    args = parser.parse_args(argv)

    tester: Any = None
    try:
        # Load configuration
        print("Loading configuration...", file=stdout)
        config_loader = config_loader_factory(args.config)
        config = config_loader.config

        # Initialize penetration tester
        tester = tester_factory(config)

        if args.dry_run:
            test_types = config.get('testing', {}).get('test_types', [])
            if args.test_type:
                test_types = [args.test_type]
            run_dry_run(tester, test_types, out=stdout)
        else:
            # Run actual tests
            if args.test_type:
                print(f"\nRunning single test: {args.test_type}", file=stdout)
                run_single_test(tester, args.test_type)
            else:
                tester.run_all_tests()

            # Save results
            print("\nSaving results...", file=stdout)
            tester.save_results()

            # Print summary
            print("\n" + tester.generate_report(), file=stdout)

        return 0

    except FileNotFoundError as e:
        print(f"Error: {e}", file=stderr)
        return 1
    except ValueError as e:
        print(f"Configuration error: {e}", file=stderr)
        return 1
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Cleaning up...", file=stdout)
        if tester is not None:
            tester.web_automation.close()
        return 0
    except Exception as e:
        print(f"Unexpected error: {e}", file=stderr)
        traceback.print_exc()
        return 1


def main() -> None:
    """Console entry point for the legacy runner."""
    sys.exit(run())


if __name__ == '__main__':
    main()
