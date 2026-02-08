"""
Name: Shiny App Testing Agent
Purpose: Automated testing agent for Shiny applications using smolagents and Playwright
"""

# Import necessary libraries and tools
import os
from dotenv import load_dotenv
from smolagents import CodeAgent, ToolCallingAgent, tool
from playwright.sync_api import sync_playwright, Page, Browser
import json
from datetime import datetime
from typing import List, Dict, Any
import time

# Load environment variables
load_dotenv()

# Create a tool for navigating to a Shiny app and returning the page title
@tool
def navigate_to_shiny_app(url: str) -> str:
    """
    Navigate to a Shiny application URL and return the page title.
    
    Args:
        url: The URL of the Shiny app to test
    
    Returns:
        Success message with page title
    """
    global browser, page
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=90000)
        title = page.title()
        return f"Successfully navigated to: {title}"

# Create a tool to find all tabs, navigation items, and interactive sections in the Shiny app
@tool
def find_all_tabs_and_sections(url: str) -> str:
    """
    Discover all tabs, navigation items, and interactive sections in a Shiny app.
    
    Args:
        url: The URL of the Shiny app
    
    Returns:
        JSON string containing all discovered navigation elements
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(3000)
        
        navigation_elements = {
            "tabs": [],
            "links": [],
            "buttons": [],
            "sidebars": []
        }
        
        # Find tabs (common Shiny tab patterns)
        tab_selectors = [
            'a[data-toggle="tab"]',
            '.nav-tabs a',
            'li.active a',
            '[role="tab"]',
            '.tabbable a'
        ]
        
        for selector in tab_selectors:
            elements = page.query_selector_all(selector)
            for elem in elements:
                text = elem.inner_text().strip()
                href = elem.get_attribute('href') or elem.get_attribute('data-value')
                if text and text not in [t['text'] for t in navigation_elements['tabs']]:
                    navigation_elements['tabs'].append({
                        'text': text,
                        'href': href,
                        'selector': selector
                    })
        
        # Find navigation links
        nav_links = page.query_selector_all('nav a, .navbar a, .sidebar a')
        for link in nav_links[:20]:  # Limit to prevent overwhelming
            text = link.inner_text().strip()
            if text:
                navigation_elements['links'].append({
                    'text': text,
                    'href': link.get_attribute('href')
                })
        
        # Find interactive buttons
        buttons = page.query_selector_all('button, input[type="button"], .btn')
        for btn in buttons[:15]:
            text = btn.inner_text().strip() or btn.get_attribute('value') or 'Button'
            navigation_elements['buttons'].append({
                'text': text,
                'id': btn.get_attribute('id')
            })
        
        browser.close()
        return json.dumps(navigation_elements, indent=2)

# Create a tool to test a specific page or tab for errors, warnings, and functionality
@tool
def test_shiny_page(url: str, tab_name: str = None) -> str:
    """
    Test a specific page or tab in a Shiny app for errors, warnings, and functionality.
    
    Args:
        url: The URL of the Shiny app
        tab_name: Optional specific tab to test
    
    Returns:
        Test results including errors, warnings, and element checks
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        
        # Collect console messages
        console_messages = []
        errors = []
        warnings = []
        
        page.on("console", lambda msg: console_messages.append({
            "type": msg.type,
            "text": msg.text
        }))
        
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        
        # Navigate to the app
        page.goto(url, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(2000)
        
        test_results = {
            "timestamp": datetime.now().isoformat(),
            "url": url,
            "tab_tested": tab_name or "Main page",
            "status": "success",
            "errors": [],
            "warnings": [],
            "checks": {}
        }
        
        # Click on specific tab if provided
        if tab_name:
            try:
                # Try different tab selectors
                tab_selectors = [
                    f'a:has-text("{tab_name}")',
                    f'[data-value="{tab_name}"]',
                    f'a[href="#{tab_name}"]'
                ]
                
                clicked = False
                for selector in tab_selectors:
                    try:
                        page.click(selector, timeout=5000)
                        clicked = True
                        page.wait_for_timeout(2000)
                        break
                    except:
                        continue
                
                if not clicked:
                    test_results["warnings"].append(f"Could not find tab: {tab_name}")
            except Exception as e:
                test_results["errors"].append(f"Error clicking tab {tab_name}: {str(e)}")
        
        # Check for Shiny errors
        error_elements = page.query_selector_all('.shiny-output-error, .shiny-output-error-validation')
        if error_elements:
            for elem in error_elements:
                test_results["errors"].append(elem.inner_text())
        
        # Check for loading indicators (might indicate stuck processes)
        loading = page.query_selector_all('.shiny-busy, .loading, [class*="spinner"]')
        if loading:
            test_results["warnings"].append(f"Found {len(loading)} loading indicators")
        
        # Check for plots/outputs
        plots = page.query_selector_all('.shiny-plot-output, .plotly, canvas, svg')
        test_results["checks"]["plots_found"] = len(plots)
        
        # Check for tables
        tables = page.query_selector_all('table, .dataTables_wrapper')
        test_results["checks"]["tables_found"] = len(tables)
        
        # Check for inputs
        inputs = page.query_selector_all('input, select, textarea')
        test_results["checks"]["inputs_found"] = len(inputs)
        
        # Categorize console messages
        for msg in console_messages:
            if msg["type"] == "error":
                test_results["errors"].append(f"Console error: {msg['text']}")
            elif msg["type"] == "warning":
                test_results["warnings"].append(f"Console warning: {msg['text']}")
        
        # Add page errors
        test_results["errors"].extend(errors)
        
        # Set overall status
        if test_results["errors"]:
            test_results["status"] = "failed"
        elif test_results["warnings"]:
            test_results["status"] = "warning"
        
        browser.close()
        return json.dumps(test_results, indent=2)

# Create a tool to test interactive elements like buttons, dropdowns, and sliders in the Shiny app
@tool
def test_interactive_elements(url: str, element_type: str = "button") -> str:
    """
    Test interactive elements (buttons, dropdowns, sliders) in a Shiny app.
    
    Args:
        url: The URL of the Shiny app
        element_type: Type of element to test (button, select, slider)
    
    Returns:
        Test results for interactive elements
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(2000)
        
        results = {
            "element_type": element_type,
            "tests": [],
            "errors": []
        }
        
        if element_type == "button":
            buttons = page.query_selector_all('button:not([disabled]), .btn:not([disabled])')
            for i, btn in enumerate(buttons[:5]):  # Test first 5 buttons
                try:
                    text = btn.inner_text() or f"Button {i+1}"
                    btn.click()
                    page.wait_for_timeout(1000)
                    results["tests"].append({
                        "element": text,
                        "status": "clicked successfully"
                    })
                except Exception as e:
                    results["errors"].append({
                        "element": f"Button {i+1}",
                        "error": str(e)
                    })
        
        elif element_type == "select":
            selects = page.query_selector_all('select')
            for i, select in enumerate(selects[:5]):
                try:
                    options = select.query_selector_all('option')
                    if len(options) > 1:
                        select.select_option(index=1)
                        page.wait_for_timeout(1000)
                        results["tests"].append({
                            "element": f"Select {i+1}",
                            "status": "option changed successfully"
                        })
                except Exception as e:
                    results["errors"].append({
                        "element": f"Select {i+1}",
                        "error": str(e)
                    })
        
        browser.close()
        return json.dumps(results, indent=2)

# Create a tool to generate a comprehensive HTML test report from the test results
@tool
def generate_test_report(test_results: str, app_name: str) -> str:
    """
    Generate a comprehensive HTML test report from test results.
    
    Args:
        test_results: JSON string of all test results
        app_name: Name of the app being tested
    
    Returns:
        Path to the generated HTML report
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"test_report_{app_name}_{timestamp}.html"
    
    results = json.loads(test_results) if isinstance(test_results, str) else test_results
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Shiny App Test Report - {app_name}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
            .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; }}
            h1 {{ color: #333; }}
            .status-success {{ color: green; font-weight: bold; }}
            .status-failed {{ color: red; font-weight: bold; }}
            .status-warning {{ color: orange; font-weight: bold; }}
            .section {{ margin: 20px 0; padding: 15px; border: 1px solid #ddd; border-radius: 4px; }}
            .error {{ background: #ffe6e6; padding: 10px; margin: 5px 0; border-left: 4px solid red; }}
            .warning {{ background: #fff3cd; padding: 10px; margin: 5px 0; border-left: 4px solid orange; }}
            .success {{ background: #d4edda; padding: 10px; margin: 5px 0; border-left: 4px solid green; }}
            table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
            th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
            th {{ background: #f8f9fa; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Shiny App Test Report</h1>
            <p><strong>App Name:</strong> {app_name}</p>
            <p><strong>Test Date:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
            <p><strong>Status:</strong> <span class="status-{results.get('status', 'unknown')}">{results.get('status', 'unknown').upper()}</span></p>
            
            <div class="section">
                <h2>Summary</h2>
                <table>
                    <tr><th>Metric</th><th>Count</th></tr>
                    <tr><td>Errors</td><td>{len(results.get('errors', []))}</td></tr>
                    <tr><td>Warnings</td><td>{len(results.get('warnings', []))}</td></tr>
                    <tr><td>Checks Passed</td><td>{len([v for v in results.get('checks', {}).values() if v > 0])}</td></tr>
                </table>
            </div>
            
            <div class="section">
                <h2>Errors</h2>
                {''.join([f'<div class="error">{err}</div>' for err in results.get('errors', [])]) or '<p class="success">No errors found!</p>'}
            </div>
            
            <div class="section">
                <h2>Warnings</h2>
                {''.join([f'<div class="warning">{warn}</div>' for warn in results.get('warnings', [])]) or '<p class="success">No warnings found!</p>'}
            </div>
            
            <div class="section">
                <h2>Checks</h2>
                <table>
                    <tr><th>Check</th><th>Result</th></tr>
                    {''.join([f'<tr><td>{k}</td><td>{v}</td></tr>' for k, v in results.get('checks', {}).items()])}
                </table>
            </div>
        </div>
    </body>
    </html>
    """
    
    with open(filename, 'w') as f:
        f.write(html_content)
    
    return f"Report generated: {filename}"
