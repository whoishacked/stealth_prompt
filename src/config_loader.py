"""Configuration loader for Stealth Prompt."""

import yaml
import os
import re
from typing import Dict, Any
from pathlib import Path

# Try to import python-dotenv, fallback to manual .env loading
try:
    from dotenv import load_dotenv  # type: ignore
    HAS_DOTENV = True
except ImportError:
    HAS_DOTENV = False


class ConfigLoader:
    """Loads and validates configuration from YAML file."""
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        Initialize the configuration loader.
        
        Args:
            config_path: Path to the configuration YAML file
        """
        self.config_path = Path(config_path)
        self.config: Dict[str, Any] = {}
        # Load .env file if it exists
        self._load_env_file()
        self.load()
    
    def load(self) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {self.config_path}. "
                "Please create a config.yaml file."
            )
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        
        # Substitute environment variables in config values
        self.config = self._substitute_env_vars(self.config)
        
        self._validate_config()
        return self.config
    
    def _load_env_file(self):
        """Load environment variables from .env file if it exists."""
        env_file = Path('.env')
        if env_file.exists():
            if HAS_DOTENV:
                # Use python-dotenv if available
                load_dotenv(env_file)
                print(f"[CONFIG] Loaded environment variables from .env file")
            else:
                # Manual .env file parsing (simple implementation)
                try:
                    loaded_count = 0
                    with open(env_file, 'r', encoding='utf-8') as f:
                        for line_num, line in enumerate(f, 1):
                            line = line.strip()
                            # Skip empty lines and comments
                            if not line or line.startswith('#'):
                                continue
                            # Parse KEY=VALUE format
                            if '=' in line:
                                key, value = line.split('=', 1)
                                key = key.strip()
                                value = value.strip()
                                # Remove quotes if present
                                if value.startswith('"') and value.endswith('"'):
                                    value = value[1:-1]
                                elif value.startswith("'") and value.endswith("'"):
                                    value = value[1:-1]
                                # Set the environment variable (override if already set)
                                if key:
                                    os.environ[key] = value
                                    loaded_count += 1
                            else:
                                print(f"[CONFIG] Warning: Skipping malformed line {line_num} in .env: {line}")
                    if loaded_count > 0:
                        print(f"[CONFIG] Loaded {loaded_count} environment variable(s) from .env file (manual parser)")
                except Exception as e:
                    print(f"[CONFIG] Warning: Failed to load .env file: {e}")
    
    def _substitute_env_vars(self, obj: Any) -> Any:
        """
        Recursively substitute environment variables in config values.
        Supports ${VAR_NAME} syntax.
        
        Args:
            obj: Configuration object (dict, list, or string)
        
        Returns:
            Object with environment variables substituted
        """
        if isinstance(obj, dict):
            return {key: self._substitute_env_vars(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._substitute_env_vars(item) for item in obj]
        elif isinstance(obj, str):
            # Pattern to match ${VAR_NAME} or ${VAR_NAME:-default}
            pattern = r'\$\{([^}:]+)(?::-([^}]*))?\}'
            
            def replace_env_var(match):
                var_name = match.group(1)
                default_value = match.group(2) if match.group(2) else None
                env_value = os.getenv(var_name)
                
                if env_value is not None:
                    return env_value
                elif default_value is not None:
                    return default_value
                else:
                    # If variable not found and no default, return the original string
                    # This allows the validation to catch missing required variables
                    return match.group(0)
            
            return re.sub(pattern, replace_env_var, obj)
        else:
            return obj
    
    def _validate_config(self):
        """Validate that required configuration sections exist."""
        required_sections = ['llm', 'web', 'testing']
        
        for section in required_sections:
            if section not in self.config:
                raise ValueError(
                    f"Missing required configuration section: {section}"
                )
        
        # Validate LLM provider
        provider = self.config['llm'].get('provider', 'ollama')
        if provider not in ['ollama', 'openai']:
            raise ValueError(
                f"Invalid LLM provider: {provider}. Must be 'ollama' or 'openai'"
            )
        
        # Validate web method
        method = self.config['web'].get('method', 'GET')
        if method not in ['GET', 'POST']:
            raise ValueError(
                f"Invalid HTTP method: {method}. Must be 'GET' or 'POST'"
            )
        
        # Validate proxy configuration if enabled
        proxy_config = self.config.get('proxy', {})
        if proxy_config.get('enabled', False):
            proxy_url = proxy_config.get('url', '')
            if not proxy_url:
                raise ValueError(
                    "Proxy is enabled but proxy URL is not provided"
                )
            
            # Validate proxy URL format
            from urllib.parse import urlparse
            try:
                parsed = urlparse(proxy_url)
                if not parsed.scheme or not parsed.hostname:
                    raise ValueError(f"Invalid proxy URL format: {proxy_url}")
                if parsed.scheme not in ['http', 'https', 'socks4', 'socks5']:
                    raise ValueError(
                        f"Unsupported proxy scheme: {parsed.scheme}. "
                        "Supported schemes: http, https, socks4, socks5"
                    )
            except Exception as e:
                raise ValueError(f"Invalid proxy URL: {proxy_url}. Error: {str(e)}")
            
            # Validate proxy scope
            scope = proxy_config.get('scope', 'all')
            if scope not in ['all', 'web', 'api']:
                raise ValueError(
                    f"Invalid proxy scope: {scope}. Must be 'all', 'web', or 'api'"
                )
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value using dot notation."""
        keys = key.split('.')
        value = self.config
        
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        
        return value
    
    def reload(self):
        """Reload configuration from file."""
        self.load()

