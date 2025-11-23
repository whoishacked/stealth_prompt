"""Web automation using Selenium for interacting with AI agent web interfaces."""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    TimeoutException, 
    NoSuchElementException,
    ElementClickInterceptedException,
    ElementNotInteractableException
)
from typing import Dict, Any, Optional
from urllib.parse import urlparse
import time
import re


class WebAutomation:
    """Handles web automation using Selenium for AI agent interaction."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the web automation client.
        
        Args:
            config: Configuration dictionary containing web automation settings
        """
        self.config = config
        url = config.get('url', '')
        self.url = self._validate_url(url)
        self.method = config.get('method', 'GET')
        self.selenium_config = config.get('selenium', {})
        self.selectors = self.selenium_config.get('selectors', {})
        self.driver: Optional[webdriver.Chrome] = None
        
        # Check if connecting to existing Chrome instance
        self.connect_to_existing = self.selenium_config.get('connect_to_existing', False)
        
        # Get proxy configuration
        proxy_config = config.get('proxy', {})
        self.proxy_enabled = proxy_config.get('enabled', False)
        proxy_scope = proxy_config.get('scope', 'all')
        self.use_proxy = self.proxy_enabled and proxy_scope in ['all', 'web']
        self.proxy_url = proxy_config.get('url', '') if self.use_proxy else None
    
    @staticmethod
    def _validate_url(url: str) -> str:
        """
        Validate URL format and scheme.
        
        Args:
            url: URL to validate
        
        Returns:
            Validated URL
        
        Raises:
            ValueError: If URL is invalid
        """
        if not url:
            raise ValueError("URL cannot be empty")
        
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError(f"Invalid URL format: {url}")
            
            # Only allow http and https schemes
            if parsed.scheme not in ['http', 'https']:
                raise ValueError(f"Unsupported URL scheme: {parsed.scheme}. Only http and https are allowed.")
            
            return url
        except Exception as e:
            raise ValueError(f"Invalid URL: {url}. Error: {str(e)}")
    
    def _create_driver(self) -> webdriver.Chrome:
        """Create and configure Chrome WebDriver."""
        chrome_options = Options()
        
        # Check if we should connect to existing Chrome instance
        connect_to_existing = self.selenium_config.get('connect_to_existing', False)
        
        if connect_to_existing:
            # Connect to existing Chrome instance with remote debugging
            remote_port = self.selenium_config.get('remote_debugging_port', 9222)
            debugger_address = f"localhost:{remote_port}"
            chrome_options.add_experimental_option("debuggerAddress", debugger_address)
            print(f"[WEB AUTOMATION] Connecting to existing Chrome instance on port {remote_port}...")
            print(f"[WEB AUTOMATION] Make sure Chrome is started with: --remote-debugging-port={remote_port}")
        else:
            # Create new Chrome instance
            if self.selenium_config.get('headless', False):
                chrome_options.add_argument('--headless')
            
            window_size = self.selenium_config.get('window_size', '1920,1080')
            chrome_options.add_argument(f'--window-size={window_size}')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            
            # Ignore SSL certificate errors (useful for testing environments)
            chrome_options.add_argument('--ignore-certificate-errors')
            chrome_options.add_argument('--ignore-ssl-errors')
            chrome_options.add_argument('--ignore-certificate-errors-spki-list')
            chrome_options.add_argument('--allow-running-insecure-content')
            
            # User agent to appear more like a real browser
            chrome_options.add_argument(
                'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            
            # Configure proxy if enabled
            if self.use_proxy and self.proxy_url:
                self._configure_proxy(chrome_options)
        
        driver = webdriver.Chrome(options=chrome_options)
        
        # Set timeouts
        implicit_wait = self.selenium_config.get('implicit_wait', 10)
        page_load_timeout = self.selenium_config.get('page_load_timeout', 30)
        driver.implicitly_wait(implicit_wait)
        driver.set_page_load_timeout(page_load_timeout)
        
        return driver
    
    def _configure_proxy(self, chrome_options: Options):
        """
        Configure proxy settings for Chrome.
        
        Args:
            chrome_options: Chrome options object to configure
        """
        if not self.proxy_url:
            return
        
        try:
            parsed = urlparse(self.proxy_url)
            proxy_server = f"{parsed.hostname}:{parsed.port or 8080}"
            
            # Add proxy argument
            chrome_options.add_argument(f'--proxy-server={parsed.scheme}://{proxy_server}')
            
            # Handle authentication if provided in URL
            if parsed.username and parsed.password:
                # Note: Chrome doesn't support proxy auth via command line directly
                # We'll need to use an extension or handle it differently
                # For now, we'll set it and log a warning
                print(f"Warning: Proxy authentication in URL is not fully supported by Chrome.")
                print(f"Consider using a proxy extension or setting up authentication separately.")
        except Exception as e:
            print(f"Warning: Failed to configure proxy: {str(e)}")
            print("Continuing without proxy...")
    
    def _find_submit_button(self, submit_config: Dict[str, Any], wait: WebDriverWait):
        """
        Find submit button, optionally within a parent element.
        
        Args:
            submit_config: Submit button configuration
            wait: WebDriverWait instance
        
        Returns:
            Submit button WebElement or None
        """
        submit_by_type = self._get_by_type(submit_config.get('strategy', 'css'))
        submit_value = submit_config.get('value', '')
        
        # Check if parent element is specified
        parent_config = submit_config.get('parent')
        if parent_config and parent_config.get('value'):
            # Find parent element first
            parent_strategy = parent_config.get('strategy', 'class')
            parent_value = parent_config.get('value', '')
            
            if not parent_value:
                # If parent is specified but value is empty, search globally
                parent_element = None
            else:
                parent_by_type = self._get_by_type(parent_strategy)
                try:
                    parent_element = wait.until(
                        EC.presence_of_element_located((parent_by_type, parent_value))
                    )
                    print(f"[SELECTOR] Found parent element using {parent_strategy}: {parent_value}")
                except TimeoutException:
                    print(f"[WARNING] Parent element not found: {parent_strategy}={parent_value}")
                    print("[WARNING] Searching for submit button globally...")
                    parent_element = None
            
            # Find submit button within parent (if parent found) or globally
            if parent_element:
                try:
                    # Search within parent element
                    submit_element = parent_element.find_element(submit_by_type, submit_value)
                    
                    # Wait for it to be clickable
                    wait.until(EC.element_to_be_clickable(submit_element))
                    print(f"[SELECTOR] Found submit button within parent using {submit_config.get('strategy')}: {submit_value}")
                    return submit_element
                except (NoSuchElementException, TimeoutException) as e:
                    print(f"[WARNING] Submit button not found within parent: {str(e)}")
                    print("[WARNING] Searching globally...")
                    # Fall back to global search
                    pass
        
        # Global search (no parent or parent not found)
        try:
            submit_element = wait.until(
                EC.element_to_be_clickable((submit_by_type, submit_value))
            )
            print(f"[SELECTOR] Found submit button using {submit_config.get('strategy')}: {submit_value}")
            return submit_element
        except TimeoutException:
            print(f"[ERROR] Submit button not found using {submit_config.get('strategy')}: {submit_value}")
            return None
    
    def _safe_click(self, element, max_attempts: int = 3, timeout: float = 10.0) -> bool:
        """
        Safely click an element with multiple fallback strategies.
        
        Args:
            element: WebElement to click
            max_attempts: Maximum number of click attempts
            timeout: Maximum total time to spend on clicking (seconds)
        
        Returns:
            True if click succeeded, False otherwise
        """
        import time as time_module
        start_time = time_module.time()
        
        for attempt in range(max_attempts):
            # Check if we've exceeded the timeout
            elapsed = time_module.time() - start_time
            if elapsed >= timeout:
                print(f"[ERROR] Click timeout exceeded ({timeout}s). Aborting click attempts.")
                return False
            
            try:
                # Wait a bit for any overlays to disappear (shorter wait)
                time.sleep(0.2)
                
                # Scroll element into view (center of viewport)
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                    element
                )
                time.sleep(0.2)
                
                # Try regular click first
                element.click()
                print(f"[CLICK] Successfully clicked element (attempt {attempt + 1}, {elapsed:.1f}s)")
                return True
                
            except ElementClickInterceptedException as e:
                elapsed = time_module.time() - start_time
                remaining_time = timeout - elapsed
                if remaining_time <= 0:
                    print(f"[ERROR] Click timeout exceeded ({timeout}s). Aborting.")
                    return False
                
                print(f"[WARNING] Click intercepted (attempt {attempt + 1}/{max_attempts}, {elapsed:.1f}s elapsed)")
                
                if attempt < max_attempts - 1:
                    # Try to dismiss any overlays or modals (quick attempt)
                    self._dismiss_overlays()
                    
                    # Try JavaScript click as fallback immediately
                    try:
                        self.driver.execute_script("arguments[0].click();", element)
                        elapsed = time_module.time() - start_time
                        print(f"[CLICK] Successfully clicked using JavaScript (attempt {attempt + 1}, {elapsed:.1f}s)")
                        return True
                    except Exception as js_error:
                        print(f"[WARNING] JavaScript click also failed: {str(js_error)}")
                        # Shorter wait before next attempt
                        time.sleep(0.5)
                else:
                    # Last attempt - try JavaScript click
                    try:
                        self.driver.execute_script("arguments[0].click();", element)
                        elapsed = time_module.time() - start_time
                        print(f"[CLICK] Successfully clicked using JavaScript (final attempt, {elapsed:.1f}s)")
                        return True
                    except Exception as js_error:
                        elapsed = time_module.time() - start_time
                        print(f"[ERROR] All click attempts failed after {elapsed:.1f}s. Last error: {str(js_error)}")
                        return False
                        
            except (ElementNotInteractableException, Exception) as e:
                elapsed = time_module.time() - start_time
                remaining_time = timeout - elapsed
                if remaining_time <= 0:
                    print(f"[ERROR] Click timeout exceeded ({timeout}s). Aborting.")
                    return False
                
                print(f"[WARNING] Click failed (attempt {attempt + 1}/{max_attempts}, {elapsed:.1f}s elapsed): {str(e)}")
                
                if attempt < max_attempts - 1:
                    # Try JavaScript click immediately
                    try:
                        self.driver.execute_script("arguments[0].click();", element)
                        elapsed = time_module.time() - start_time
                        print(f"[CLICK] Successfully clicked using JavaScript (attempt {attempt + 1}, {elapsed:.1f}s)")
                        return True
                    except Exception as js_error:
                        print(f"[WARNING] JavaScript click failed: {str(js_error)}")
                        time.sleep(0.5)  # Shorter wait
                else:
                    elapsed = time_module.time() - start_time
                    print(f"[ERROR] All click attempts failed after {elapsed:.1f}s: {str(e)}")
                    return False
        
        return False
    
    def _dismiss_overlays(self):
        """
        Try to dismiss common overlays, modals, or popups that might block clicks.
        """
        try:
            # Common overlay/modal selectors
            overlay_selectors = [
                (By.CSS_SELECTOR, ".overlay"),
                (By.CSS_SELECTOR, ".modal"),
                (By.CSS_SELECTOR, "[role='dialog']"),
                (By.CSS_SELECTOR, ".popup"),
                (By.CSS_SELECTOR, ".backdrop"),
                (By.CSS_SELECTOR, "[data-overlay]"),
                (By.CSS_SELECTOR, ".close-button"),
                (By.CSS_SELECTOR, "[aria-label*='close' i]"),
                (By.CSS_SELECTOR, "[aria-label*='dismiss' i]"),
            ]
            
            # Try to find and close overlays
            for by_type, selector in overlay_selectors:
                try:
                    elements = self.driver.find_elements(by_type, selector)
                    for element in elements:
                        if element.is_displayed():
                            try:
                                # Try clicking close button or pressing Escape
                                element.click()
                                print(f"[OVERLAY] Dismissed overlay using selector: {selector}")
                                time.sleep(0.5)
                            except:
                                pass
                except:
                    pass
            
            # Try pressing Escape key to close modals
            try:
                from selenium.webdriver.common.action_chains import ActionChains
                ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
                time.sleep(0.5)
            except:
                pass
                
        except Exception as e:
            # Silently fail - overlays might not exist
            pass
    
    def _find_element_by_strategy(self, strategy: str, value: str):
        """
        Find element using specified strategy.
        
        Args:
            strategy: Selector strategy (id, class, css, xpath, name)
            value: Selector value
        
        Returns:
            WebElement
        
        Raises:
            ValueError: If strategy is unsupported or value is empty
        """
        if not value or not value.strip():
            raise ValueError("Selector value cannot be empty")
        
        # Basic XSS prevention for selector values
        if '<' in value or '>' in value or 'script' in value.lower():
            raise ValueError("Invalid characters in selector value")
        
        if strategy == 'id':
            return self.driver.find_element(By.ID, value)
        elif strategy == 'class':
            return self.driver.find_element(By.CLASS_NAME, value)
        elif strategy == 'css':
            return self.driver.find_element(By.CSS_SELECTOR, value)
        elif strategy == 'xpath':
            return self.driver.find_element(By.XPATH, value)
        elif strategy == 'name':
            return self.driver.find_element(By.NAME, value)
        else:
            raise ValueError(f"Unsupported selector strategy: {strategy}")
    
    def _handle_security_warning(self):
        """Handle Chrome security warning page (SSL certificate errors)."""
        try:
            # Check if we're on a Chrome security warning page
            current_url = self.driver.current_url
            page_source = self.driver.page_source.lower()
            
            # Look for Chrome security warning indicators
            if 'your connection is not private' in page_source or 'net::err_cert' in page_source:
                print("Detected SSL certificate warning. Attempting to proceed...")
                
                # Try to find and click "Advanced" button
                try:
                    advanced_button = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((By.ID, "details-button"))
                    )
                    advanced_button.click()
                    time.sleep(1)
                except:
                    # Try alternative selectors
                    try:
                        advanced_links = self.driver.find_elements(By.XPATH, "//a[contains(text(), 'Advanced')]")
                        if advanced_links:
                            advanced_links[0].click()
                            time.sleep(1)
                    except:
                        pass
                
                # Try to find and click "Proceed" link
                try:
                    proceed_link = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((By.ID, "proceed-link"))
                    )
                    proceed_link.click()
                    time.sleep(2)
                    print("Successfully bypassed security warning.")
                except:
                    try:
                        proceed_links = self.driver.find_elements(By.XPATH, "//a[contains(text(), 'Proceed') or contains(text(), 'proceed')]")
                        if proceed_links:
                            proceed_links[0].click()
                            time.sleep(2)
                            print("Successfully bypassed security warning.")
                    except Exception as e:
                        print(f"Warning: Could not automatically bypass security warning: {str(e)}")
                        print("You may need to manually accept the certificate in the browser.")
        except Exception as e:
            print(f"Warning: Error handling security warning: {str(e)}")
    
    def start(self):
        """Start the browser and navigate to the target URL."""
        self.driver = self._create_driver()
        
        # If connecting to existing Chrome, don't navigate (user may have already navigated)
        if self.connect_to_existing:
            print(f"[WEB AUTOMATION] Connected to existing Chrome instance")
            print(f"[WEB AUTOMATION] Current URL: {self.driver.current_url}")
            print(f"[WEB AUTOMATION] Skipping navigation - using current page in Chrome")
            return
        
        # Add cookies if specified
        cookies = self.config.get('http', {}).get('cookies', {})
        if cookies:
            self.driver.get(self.url)
            for name, cookie_value in cookies.items():
                self.driver.add_cookie({'name': name, 'value': cookie_value})
        
        # Navigate to the URL
        self.driver.get(self.url)
        
        # Handle security warnings (SSL certificate errors)
        self._handle_security_warning()
        
        # Wait for page to be ready
        wait = WebDriverWait(self.driver, self.selenium_config.get('page_load_timeout', 30))
        wait.until(lambda driver: driver.execute_script('return document.readyState') == 'complete')
        
        # Additional wait for dynamic content
        time.sleep(2)
    
    def send_prompt(self, prompt: str, log: bool = True) -> bool:
        """
        Send a prompt to the AI agent via the web interface.
        
        Args:
            prompt: The prompt to send
        
        Returns:
            True if successful, False otherwise
        """
        if not self.driver:
            raise RuntimeError("WebDriver not started. Call start() first.")
        
        # Input validation
        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty")
        
        # Length limit to prevent issues
        max_length = 100000  # characters
        if len(prompt) > max_length:
            raise ValueError(f"Prompt exceeds maximum length of {max_length} characters")
        
        # Log the prompt being sent
        if log:
            print(f"\n[PROMPT] Sending to AI agent:")
            print(f"{'='*60}")
            print(prompt)
            print(f"{'='*60}\n")
        
        try:
            # Find input element
            input_config = self.selectors.get('input', {})
            if not input_config:
                raise ValueError("Input selector not configured")
            
            # Validate selector value
            selector_value = input_config.get('value', '')
            if not selector_value:
                raise ValueError("Input selector value cannot be empty")
            
            # Wait for input element to be present and interactable
            wait = WebDriverWait(self.driver, self.selenium_config.get('implicit_wait', 10))
            by_type = self._get_by_type(input_config.get('strategy', 'id'))
            
            # Wait for element to be visible and clickable
            input_element = wait.until(
                EC.element_to_be_clickable((by_type, selector_value))
            )
            
            # Scroll element into view if needed
            self.driver.execute_script("arguments[0].scrollIntoView(true);", input_element)
            time.sleep(0.5)  # Brief pause after scrolling
            
            # Clear and enter prompt
            try:
                input_element.clear()
            except Exception:
                # If clear fails, select all and delete
                input_element.send_keys(Keys.CONTROL + "a")
                input_element.send_keys(Keys.DELETE)
            
            input_element.send_keys(prompt)
            
            # Find and click submit button
            submit_config = self.selectors.get('submit', {})
            if submit_config:
                submit_element = self._find_submit_button(submit_config, wait)
                if submit_element:
                    # Get click timeout from config (default 10 seconds)
                    click_timeout = self.selenium_config.get('click_timeout', 10.0)
                    # Try to click the submit button with fallback methods
                    if not self._safe_click(submit_element, timeout=click_timeout):
                        raise ValueError(f"Could not click submit button after multiple attempts (timeout: {click_timeout}s)")
                else:
                    raise ValueError("Could not find submit button")
            else:
                # Try pressing Enter if no submit button configured
                input_element.send_keys(Keys.RETURN)
            
            return True
        except (NoSuchElementException, TimeoutException, ValueError) as e:
            print(f"Error sending prompt: {str(e)}")
            return False
        except Exception as e:
            print(f"Unexpected error sending prompt: {str(e)}")
            return False
    
    def get_response(self, timeout: Optional[int] = None, log: bool = True) -> Optional[str]:
        """
        Get the response from the AI agent.
        
        Args:
            timeout: Maximum time to wait for response (uses config default if None)
            log: Whether to log the response
        
        Returns:
            The response text, or None if not found
        """
        if not self.driver:
            raise RuntimeError("WebDriver not started. Call start() first.")
        
        try:
            response_config = self.selectors.get('response', {})
            if not response_config:
                raise ValueError("Response selector not configured")
            
            wait_timeout = timeout or self.selenium_config.get('response_timeout', 60)
            wait = WebDriverWait(self.driver, wait_timeout)
            
            response_element = wait.until(
                EC.presence_of_element_located((
                    self._get_by_type(response_config.get('strategy', 'class')),
                    response_config.get('value', '')
                ))
            )
            
            # Wait a bit more for content to fully load
            time.sleep(2)
            
            response_text = response_element.text
            
            # Log the response
            if log and response_text:
                print(f"\n[RESPONSE] Received from AI agent ({len(response_text)} characters):")
                print(f"{'='*60}")
                # Truncate very long responses for display
                display_text = response_text[:1000] + "..." if len(response_text) > 1000 else response_text
                print(display_text)
                print(f"{'='*60}\n")
            
            return response_text
        except (TimeoutException, NoSuchElementException, ValueError) as e:
            print(f"Error getting response: {str(e)}")
            return None
    
    def _get_by_type(self, strategy: str):
        """Get Selenium By type from strategy string."""
        mapping = {
            'id': By.ID,
            'class': By.CLASS_NAME,
            'css': By.CSS_SELECTOR,
            'xpath': By.XPATH,
            'name': By.NAME
        }
        return mapping.get(strategy, By.CSS_SELECTOR)
    
    def close(self):
        """Close the browser and cleanup."""
        if self.driver:
            self.driver.quit()
            self.driver = None
    
    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

