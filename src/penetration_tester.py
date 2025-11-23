"""Main penetration testing orchestrator."""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from .llm_client import LLMClient
from .web_automation import WebAutomation
from .prompt_db import PromptDB


class PenetrationTester:
    """Main class for orchestrating AI agent penetration testing."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the penetration tester.
        
        Args:
            config: Full configuration dictionary
        """
        self.config = config
        
        # Merge proxy config into llm and web configs for component access
        proxy_config = config.get('proxy', {})
        llm_config = config['llm'].copy()
        llm_config['proxy'] = proxy_config
        web_config = config['web'].copy()
        web_config['proxy'] = proxy_config
        
        self.llm_client = LLMClient(llm_config)
        self.web_automation = WebAutomation(web_config)
        self.testing_config = config.get('testing', {})
        self.output_config = config.get('output', {})
        self.results: List[Dict[str, Any]] = []
        self.stop_requested = False  # Global flag to stop all testing
        
        # Setup output directory
        self.results_dir = Path(self.output_config.get('results_dir', 'results'))
        self.results_dir.mkdir(exist_ok=True)
        
        # Initialize prompt database
        db_path = self.testing_config.get('prompt_db_path', 'successful_prompts.json')
        self.prompt_db = PromptDB(db_path)
        db_count = len(self.prompt_db.get_all_prompts())
        chain_count = len(self.prompt_db.get_successful_chains())
        if db_count > 0:
            print(f"[DB] Loaded {db_count} successful prompts/chains from database")
            if chain_count > 0:
                print(f"[DB]   - {chain_count} conversation chains available")
    
    def run_test(self, test_type: str, payload: Optional[str] = None) -> Dict[str, Any]:
        """
        Run a single penetration test with conversational approach.
        
        Args:
            test_type: Type of test being run
            payload: Optional pre-generated payload (if None, will be generated)
        
        Returns:
            Test result dictionary with conversation history
        """
        print(f"\n{'='*60}")
        print(f"Running conversational test: {test_type}")
        print(f"{'='*60}")
        
        conversational_mode = self.testing_config.get('conversational_mode', True)
        max_turns = self.testing_config.get('max_turns', 10)
        
        conversation_history = []
        sensitive_data_found = False
        turn = 0
        
        # Start conversation
        while turn < max_turns and not sensitive_data_found and not self.stop_requested:
            turn += 1
            print(f"\n--- Turn {turn}/{max_turns} ---")
            
            # Try to use saved chain/prompt from database first
            current_payload = None
            
            if turn == 1:
                if payload:
                    current_payload = payload
                    print(f"\n[PAYLOAD] Using provided payload")
                else:
                    # Check database for successful initial prompts FIRST
                    saved_prompts = self.prompt_db.get_successful_prompts(test_type)
                    if saved_prompts:
                        # All entries now use conversation_chain structure
                        for saved in saved_prompts:
                            chain = saved.get('conversation_chain', [])
                            if chain and len(chain) > 0:
                                current_payload = chain[0].get('payload', '')
                                entry_id = saved.get('id', 'unknown')
                                chain_length = len(chain)
                                print(f"\n[DB] Using saved prompt from chain (ID: {entry_id[:8] if len(entry_id) > 8 else entry_id}..., {chain_length} turn{'s' if chain_length > 1 else ''})")
                                break
                    
                    if not current_payload:
                        print(f"\n[PAYLOAD GENERATION] No saved prompts found, generating new initial payload...")
                        current_payload = self.llm_client.generate_payload(test_type, conversation_history=None, log=True)
            else:
                # Try to use saved chain continuation
                saved_next = self.prompt_db.try_saved_chain(test_type, conversation_history)
                if saved_next:
                    current_payload = saved_next
                    print(f"\n[DB] Using next prompt from saved chain")
                else:
                    print(f"\n[PAYLOAD GENERATION] Generating follow-up payload based on conversation...")
                    current_payload = self.llm_client.generate_payload(test_type, conversation_history=conversation_history, log=True)
            
            # Send prompt and get response
            print(f"\n[WEB AUTOMATION] Sending prompt to AI agent...")
            success = self.web_automation.send_prompt(current_payload, log=True)
            
            if not success:
                print(f"[ERROR] Failed to send prompt on turn {turn}")
                break
            
            print(f"\n[WEB AUTOMATION] Waiting for response...")
            response = self.web_automation.get_response(log=True)
            
            if not response:
                print(f"[ERROR] Failed to get response on turn {turn}")
                break
            
            # First check database for known successful patterns
            db_match = self.prompt_db.check_response_with_prompts(response, test_type)
            if db_match:
                print(f"\n[DB CHECK] Response matches known successful pattern from database!")
                check_result = {'found': True, 'explanation': 'Matches known successful pattern from database', 'from_db': True}
            else:
                # Check with AI if not in database
                print(f"\n[AI CHECK] Analyzing response with AI...")
                check_result = self.llm_client.check_sensitive_data(response, log=True)
            
            # If sensitive data detected, pause and ask user
            if check_result.get('found', False):
                print(f"\n{'='*60}")
                print(f"[PAUSE] Sensitive data detected!")
                print(f"{'='*60}")
                print(f"\nTurn: {turn}")
                print(f"\nPayload sent:")
                print(f"{current_payload}")
                print(f"\nResponse received:")
                print(f"{response}")
                print(f"\nAI Analysis: {check_result.get('explanation', 'Sensitive data detected')}")
                print(f"\n{'='*60}")
                
                # Ask user if this is real sensitive data
                should_continue = True
                
                while True:
                    user_input = input("\nIs this REAL sensitive data? (yes/no/continue): ").strip().lower()
                    if user_input in ['yes', 'y']:
                        # User confirmed - save full conversation chain to database
                        # Include the full conversation history that led to success
                        self.prompt_db.add_prompt(
                            current_payload, 
                            test_type, 
                            response, 
                            confirmed_by_user=True,
                            conversation_chain=conversation_history + [{
                                'turn': turn,
                                'payload': current_payload,
                                'response': response
                            }]
                        )
                        sensitive_data_found = True
                        print(f"[DB] Conversation chain saved to database")
                        
                        # Ask if user wants to continue
                        continue_input = input("\nContinue testing? (yes/no): ").strip().lower()
                        if continue_input not in ['yes', 'y']:
                            print(f"[STOP] User requested to stop ALL testing")
                            should_continue = False
                            self.stop_requested = True  # Set global stop flag
                        else:
                            print(f"[CONTINUE] Continuing with next turn...")
                        break
                    elif user_input in ['no', 'n', 'false', 'false positive']:
                        # False positive - continue without saving
                        print(f"[FALSE POSITIVE] Marked as false positive, continuing...")
                        check_result['found'] = False
                        break
                    elif user_input in ['continue', 'c']:
                        # Continue without saving
                        print(f"[CONTINUE] Continuing without saving...")
                        check_result['found'] = False
                        break
                    else:
                        print(f"Please enter 'yes', 'no', or 'continue'")
                
                # If user wants to stop, break the main loop
                if not should_continue:
                    break
            
            # Add to conversation history
            conversation_history.append({
                'turn': turn,
                'payload': current_payload,
                'response': response,
                'sensitive_data_found': check_result.get('found', False),
                'check_explanation': check_result.get('explanation', ''),
                'from_db': check_result.get('from_db', False)
            })
            
            # Small delay between turns
            time.sleep(1)
        
        # Build result
        result = {
            'test_type': test_type,
            'conversation_history': conversation_history,
            'total_turns': turn,
            'sensitive_data_found': sensitive_data_found,
            'status': 'success' if sensitive_data_found else 'completed',
            'timestamp': datetime.now().isoformat()
        }
        
        if self.stop_requested:
            print(f"\n[RESULT] Test stopped by user request")
        elif sensitive_data_found:
            print(f"\n[RESULT] Test completed: Sensitive data was extracted!")
        else:
            print(f"\n[RESULT] Test completed: No sensitive data extracted after {turn} turns")
        
        self.results.append(result)
        return result
    
    def run_all_tests(self) -> List[Dict[str, Any]]:
        """
        Run all configured penetration tests.
        
        Returns:
            List of test results
        """
        test_types = self.testing_config.get('test_types', [])
        tests_per_type = self.testing_config.get('tests_per_type', 1)
        conversational_mode = self.testing_config.get('conversational_mode', True)
        
        print(f"\nStarting penetration testing session")
        print(f"Test types: {', '.join(test_types)}")
        print(f"Tests per type: {tests_per_type}")
        print(f"Conversational mode: {conversational_mode}")
        
        # Initialize stop flag
        self.stop_requested = False
        
        # Start web automation
        print("\nInitializing web browser...")
        self.web_automation.start()
        
        try:
            # Run tests for each type
            for test_type in test_types:
                if self.stop_requested:
                    print(f"\n[STOP] Testing stopped by user request")
                    break
                    
                for i in range(tests_per_type):
                    if self.stop_requested:
                        print(f"\n[STOP] Testing stopped by user request")
                        break
                        
                    print(f"\n--- Test {i+1}/{tests_per_type} for {test_type} ---")
                    result = self.run_test(test_type)
                    
                    # Check if stop was requested during the test
                    if self.stop_requested:
                        print(f"\n[STOP] Testing stopped by user request")
                        break
                    
                    # Small delay between tests
                    if not self.stop_requested:
                        time.sleep(2)
            
            if self.stop_requested:
                print(f"\n[SESSION] Testing session stopped by user request")
            
            return self.results
        finally:
            # Always close the browser
            print("\nClosing browser...")
            self.web_automation.close()
    
    def save_results(self, filename: Optional[str] = None):
        """
        Save test results to file.
        
        Args:
            filename: Optional custom filename (defaults to timestamp-based)
        """
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"penetration_test_results_{timestamp}"
        
        output_format = self.output_config.get('format', 'json')
        base_path = self.results_dir / filename
        
        if output_format in ['json', 'both']:
            json_path = base_path.with_suffix('.json')
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, indent=2, ensure_ascii=False)
            print(f"Results saved to: {json_path}")
        
        if output_format in ['txt', 'both']:
            txt_path = base_path.with_suffix('.txt')
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write("AI Agent Penetration Testing Results\n")
                f.write("=" * 60 + "\n\n")
                
                for i, result in enumerate(self.results, 1):
                    f.write(f"Test {i}: {result.get('test_type', 'unknown')}\n")
                    f.write("-" * 60 + "\n")
                    f.write(f"Status: {result.get('status', 'unknown')}\n")
                    f.write(f"Sensitive Data Found: {result.get('sensitive_data_found', False)}\n")
                    f.write(f"Total Turns: {result.get('total_turns', 0)}\n")
                    f.write(f"Timestamp: {result.get('timestamp', 'unknown')}\n\n")
                    
                    # Write conversation history
                    if 'conversation_history' in result:
                        f.write("Conversation History:\n")
                        f.write("-" * 60 + "\n")
                        for turn in result['conversation_history']:
                            f.write(f"\nTurn {turn.get('turn', '?')}:\n")
                            f.write(f"Payload: {turn.get('payload', '')}\n")
                            f.write(f"Response: {turn.get('response', '')}\n")
                            if turn.get('sensitive_data_found'):
                                f.write(f"[SENSITIVE DATA DETECTED]\n")
                            if turn.get('keywords_found'):
                                f.write(f"Keywords found: {', '.join(turn.get('keywords_found', []))}\n")
                            f.write("\n")
                    else:
                        # Legacy format
                        if 'payload' in result:
                            f.write(f"Payload:\n{result['payload']}\n\n")
                        if 'response' in result and self.output_config.get('save_responses', True):
                            f.write(f"Response:\n{result['response']}\n\n")
                    
                    if 'error' in result:
                        f.write(f"Error: {result['error']}\n\n")
                    
                    f.write("\n" + "=" * 60 + "\n\n")
            
            print(f"Results saved to: {txt_path}")
    
    def generate_report(self) -> str:
        """
        Generate a summary report of all test results.
        
        Returns:
            Summary report as string
        """
        total_tests = len(self.results)
        sensitive_data_found_count = sum(1 for r in self.results if r.get('sensitive_data_found', False))
        completed_tests = sum(1 for r in self.results if r.get('status') in ['success', 'completed'])
        failed_tests = total_tests - completed_tests
        
        total_turns = sum(r.get('total_turns', 0) for r in self.results)
        avg_turns = total_turns / total_tests if total_tests > 0 else 0
        
        report = f"""
AI Agent Penetration Testing Report
{'=' * 60}
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Summary:
  Total Tests: {total_tests}
  Sensitive Data Found: {sensitive_data_found_count}
  Completed: {completed_tests}
  Failed: {failed_tests}
  Average Turns per Test: {avg_turns:.1f}

Test Breakdown:
"""
        
        # Group by test type
        by_type = {}
        for result in self.results:
            test_type = result.get('test_type', 'unknown')
            if test_type not in by_type:
                by_type[test_type] = {
                    'total': 0, 
                    'sensitive_data_found': 0, 
                    'completed': 0, 
                    'failed': 0,
                    'total_turns': 0
                }
            
            by_type[test_type]['total'] += 1
            by_type[test_type]['total_turns'] += result.get('total_turns', 0)
            
            if result.get('sensitive_data_found', False):
                by_type[test_type]['sensitive_data_found'] += 1
            if result.get('status') in ['success', 'completed']:
                by_type[test_type]['completed'] += 1
            else:
                by_type[test_type]['failed'] += 1
        
        for test_type, stats in by_type.items():
            avg_turns_type = stats['total_turns'] / stats['total'] if stats['total'] > 0 else 0
            report += f"  {test_type}:\n"
            report += f"    Total: {stats['total']}\n"
            report += f"    Sensitive Data Found: {stats['sensitive_data_found']}\n"
            report += f"    Completed: {stats['completed']}\n"
            report += f"    Failed: {stats['failed']}\n"
            report += f"    Avg Turns: {avg_turns_type:.1f}\n\n"
        
        return report

