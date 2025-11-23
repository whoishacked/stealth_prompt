"""Main entry point for AI Agent Penetration Testing Tool."""

import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.config_loader import ConfigLoader
from src.penetration_tester import PenetrationTester


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="AI Agent Penetration Testing Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
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
    
    args = parser.parse_args()
    
    try:
        # Load configuration
        print("Loading configuration...")
        config_loader = ConfigLoader(args.config)
        config = config_loader.config
        
        # Initialize penetration tester
        tester = PenetrationTester(config)
        
        if args.dry_run:
            # Dry run: just generate payloads
            print("\n=== DRY RUN MODE ===")
            test_types = config.get('testing', {}).get('test_types', [])
            if args.test_type:
                test_types = [args.test_type]
            
            for test_type in test_types:
                print(f"\nGenerating payload for: {test_type}")
                payload = tester.llm_client.generate_payload(test_type)
                print(f"Payload: {payload}\n")
        else:
            # Run actual tests
            if args.test_type:
                # Run single test type
                print(f"\nRunning single test: {args.test_type}")
                tester.web_automation.start()
                try:
                    result = tester.run_test(args.test_type)
                    tester.results.append(result)
                finally:
                    tester.web_automation.close()
            else:
                # Run all tests
                tester.run_all_tests()
            
            # Save results
            print("\nSaving results...")
            tester.save_results()
            
            # Print summary
            print("\n" + tester.generate_report())
    
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Cleaning up...")
        if 'tester' in locals():
            tester.web_automation.close()
        sys.exit(0)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

