# Stealth Prompt

A Python application for automating penetration testing of AI agents with web interfaces. Stealth Prompt uses local Ollama models or OpenAI API to generate test payloads and analyze responses for security vulnerabilities.

## Demo

[![Stealth Prompt Demo](https://img.youtube.com/vi/uIPCG7z5k8g/0.jpg)](https://www.youtube.com/watch?v=uIPCG7z5k8g)

[Watch the demo video on YouTube](https://www.youtube.com/watch?v=uIPCG7z5k8g)

## Features

- **Automated Testing**: Automatically generates and sends penetration testing prompts to AI agents
- **Multiple LLM Support**: Works with Ollama (local) or OpenAI API
- **Web Automation**: Uses Selenium with Chromium for web interface interaction
- **Security Analysis**: Automatically analyzes AI agent responses for security issues
- **Configurable**: Highly configurable via YAML configuration file
- **Multiple Test Types**: Supports various penetration testing scenarios

## Installation

1. **Clone or download this repository**

2. **Install Python dependencies**:
```bash
pip install -r requirements.txt
```

3. **Install Chrome/Chromium and ChromeDriver**:
   - Download Chrome: https://www.google.com/chrome/
   - Download ChromeDriver: https://chromedriver.chromium.org/
   - Ensure ChromeDriver is in your PATH or same directory as Chrome

4. **For Ollama (local models)**:
   - Install Ollama: https://ollama.ai/
   - Pull a model: `ollama pull llama3` (or any other model)

5. **For OpenAI API**:
   - Get an API key from: https://platform.openai.com/
   - **Recommended**: Set it as an environment variable (see [Environment Variables](#environment-variables) below)
   - **Alternative**: Add it directly to `config.yaml` (not recommended for security)

## Configuration

Edit `config.yaml` to configure the tool:

### Environment Variables

The tool supports environment variable substitution in `config.yaml` for sensitive values like API keys. This is the **recommended** approach for security.

**Setting up OpenAI API Key via Environment Variable:**

1. **Using `.env` file (recommended)**:
   - Copy `.env-example` to `.env`:
     ```bash
     cp .env-example .env
     ```
   - Edit `.env` and add your API key:
     ```
     OPENAI_API_KEY=your_actual_api_key_here
     ```
   - The `.env` file is automatically ignored by git (already in `.gitignore`)

2. **Using system environment variables**:
   - **Windows (PowerShell)**:
     ```powershell
     $env:OPENAI_API_KEY="your_actual_api_key_here"
     ```
   - **Windows (CMD)**:
     ```cmd
     set OPENAI_API_KEY=your_actual_api_key_here
     ```
   - **Linux/Mac**:
     ```bash
     export OPENAI_API_KEY="your_actual_api_key_here"
     ```

3. **In `config.yaml`, use the environment variable syntax**:
   ```yaml
   openai:
     api_key: "${OPENAI_API_KEY}"  # Automatically replaced with environment variable
   ```

4. **Optional: Provide a default value**:
   ```yaml
   openai:
     api_key: "${OPENAI_API_KEY:-fallback_key_here}"  # Uses env var if set, otherwise fallback
   ```

**Note**: The config loader automatically substitutes `${VAR_NAME}` syntax with actual environment variable values. This keeps sensitive data out of your config files and version control.

### Connecting to Existing Chrome Instance

You can manually start Chrome, perform authentication, and then connect the script to the existing instance:

1. **Start Chrome with remote debugging:**
   ```bash
   # Windows
   chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\temp\chrome-debug"
   
   # Linux/Mac
   google-chrome --remote-debugging-port=9222 --user-data-dir="/tmp/chrome-debug"
   ```

2. **In Chrome, navigate to your target site and perform authentication/login**

3. **Configure the script to connect:**
   ```yaml
   selenium:
     connect_to_existing: true  # Set to true
     remote_debugging_port: 9222  # Match the port you used
   ```

4. **Run the script** - it will connect to your existing Chrome instance

**Note:** When `connect_to_existing: true`, the script will NOT create a new browser window. It will use your manually opened Chrome instance.

### LLM Configuration
- `provider`: Choose "ollama" or "openai"
- **Ollama settings:**
  - `model`: Model name (e.g., "llama3", "qwen2.5:14b-instruct")
  - `timeout`: Request timeout in seconds
- **OpenAI settings:**
  - `model`: Model name (e.g., "gpt-4o-mini", "gpt-4o", "gpt-4", "gpt-3.5-turbo")
    - Recommended: `gpt-4o-mini` for cost-effective testing
  - `api_key`: Your OpenAI API key
    - **Recommended**: Use environment variable: `"${OPENAI_API_KEY}"`
    - **Alternative**: Hardcode directly (not recommended for security)
    - **With default**: `"${OPENAI_API_KEY:-fallback_key}"` (uses env var if set, otherwise fallback)
  - `timeout`: Request timeout in seconds
  - `use_cache`: Enable/disable response caching (default: true)
    - Caching helps avoid duplicate API calls and reduces costs
  - `cache_dir`: Directory to store cached responses (default: "cache")

### Web Automation
- `url`: Target AI agent URL
- `method`: HTTP method (GET/POST)
- `selenium.selectors`: Configure element selectors for:
  - `input`: Where to enter the prompt
  - `submit`: Submit button
    - `parent`: (Optional) Parent element to narrow down search when multiple submit buttons exist
      - `strategy`: Selector strategy for parent (id, class, css, xpath, name)
      - `value`: Parent element selector value (e.g., "chat-form", "input-container")
  - `response`: Where the AI response appears

**Example for multiple submit buttons:**
```yaml
submit:
  strategy: "css"
  value: "button[type='submit']"
  parent:
    strategy: "class"
    value: "chat-form"  # Only search for submit button within this parent
```

### Proxy Configuration
- `enabled`: Enable/disable proxy usage
- `url`: Proxy URL (format: `http://proxy.example.com:8080` or `socks5://proxy.example.com:1080`)
  - For authenticated proxies: `http://username:password@proxy.example.com:8080`
- `scope`: Where to use proxy
  - `all`: Both web automation (Selenium) and LLM API requests
  - `web`: Only web automation (Selenium)
  - `api`: Only LLM API requests
- `username` / `password`: Alternative proxy authentication (if not in URL)

**Note**: Chrome/Selenium proxy authentication via command line is limited. For authenticated proxies with Selenium, consider using a proxy extension or including credentials in the URL.

### Testing Configuration
- `test_types`: List of test types to run
- `payloads_per_type`: Number of payloads per test type

See `config.yaml` for all available options.

## Usage

### Basic Usage
```bash
python main.py
```

### Run with Custom Config
```bash
python main.py --config custom_config.yaml
```

### Run Single Test Type
```bash
python main.py --test-type prompt_injection
```

### Dry Run (Generate Payloads Only)
```bash
python main.py --dry-run
```

## Test Types

The tool supports various penetration testing scenarios:

- `prompt_injection`: Test for prompt injection vulnerabilities
- `data_extraction`: Attempt to extract sensitive data
- `jailbreak_attempts`: Test for jailbreak vulnerabilities
- `system_prompt_leakage`: Attempt to leak system prompts
- `unauthorized_access`: Test for unauthorized access attempts

## Output

Results are saved in the `results/` directory (configurable) in JSON and/or TXT format, including:
- Generated payloads
- AI agent responses
- Security analysis
- Test status and timestamps

## Example Workflow

1. Configure `config.yaml` with your target AI agent details
2. Set up element selectors for the web interface
3. Choose LLM provider (Ollama or OpenAI)
4. Run: `python main.py`
5. Review results in `results/` directory

## Troubleshooting

### ChromeDriver Issues
- Ensure ChromeDriver version matches your Chrome version
- Add ChromeDriver to PATH or place in project directory

### Ollama Connection Issues
- Ensure Ollama is running: `ollama serve`
- Check if model is available: `ollama list`
- Verify base URL in config matches your Ollama setup

### Selenium Element Not Found
- Use browser developer tools to find correct selectors
- Update `selenium.selectors` in config.yaml
- Increase `implicit_wait` or `response_timeout` if elements load slowly

### OpenAI API Issues
- Verify API key is correct and has credits
- Check rate limits and quotas
- **API Key Not Found**:
  - Ensure `OPENAI_API_KEY` environment variable is set
  - If using `.env` file, verify it's in the project root directory
  - Check that the variable name in `config.yaml` matches: `"${OPENAI_API_KEY}"`
  - Verify the environment variable is accessible: `echo $OPENAI_API_KEY` (Linux/Mac) or `echo %OPENAI_API_KEY%` (Windows)
- **404 Error**: 
  - Verify the model name is correct (e.g., "gpt-4o-mini" not "gpt-4-mini")
  - Check that your API key has access to the selected model
  - Ensure base_url is correct: `https://api.openai.com/v1`
- **Cost Optimization**:
  - Use `gpt-4o-mini` model for lower costs
  - Enable caching (`use_cache: true`) to avoid duplicate API calls
  - Cached responses are stored in the `cache/` directory

### Proxy Issues
- Ensure proxy URL format is correct: `http://host:port` or `socks5://host:port`
- For authenticated proxies, include credentials in URL or use separate username/password fields
- Test proxy connectivity before running tests
- Note: Selenium proxy authentication may require additional setup (extensions)
- For SOCKS proxies with API requests, install: `pip install requests[socks]` or `pip install PySocks`
- **Burp Suite Proxy**: The tool automatically handles SSL certificate warnings. If you see certificate errors, the tool will attempt to bypass them automatically.

### SSL Certificate Warnings
- The tool automatically handles Chrome SSL certificate warnings (including Burp Suite certificates)
- If automatic bypass fails, you may need to manually accept the certificate in the browser window
- Certificate warnings are common when using proxies like Burp Suite

### Timeout Issues
- If Ollama requests timeout, increase the `timeout` value in `config.yaml` under `llm.ollama.timeout`
- Default timeout is 120 seconds, but large models may need 300+ seconds
- The tool now logs all prompts and responses for debugging

## Security Considerations

This tool is designed for authorized penetration testing only. Ensure you have:
- Written permission to test the target AI agent
- Compliance with applicable laws and regulations
- Proper authorization before running tests

### Security Features

The application includes several security measures:
- **Environment variable support**: API keys can be stored in environment variables instead of config files
- **`.env` file support**: Sensitive credentials can be stored in `.env` files (automatically ignored by git)
- URL validation to prevent SSRF attacks
- Input length limits to prevent resource exhaustion
- API key format validation
- Selector value sanitization to prevent XSS
- Proper error handling to avoid information leakage

**Best Practice**: Always use environment variables for API keys and other sensitive data. Never commit API keys to version control.

### Security Scanning

To run security scans on the codebase:
1. Authenticate with Snyk: The tool supports Snyk security scanning
2. Run scans regularly to identify vulnerabilities
3. Keep dependencies updated: `pip install --upgrade -r requirements.txt`

## License

This project is licensed under the MIT License - see the [LICENSE.txt](LICENSE.txt) file for details.

**Important**: This tool is provided for educational and authorized security testing purposes only. Ensure you have proper authorization before testing any AI agent or system.