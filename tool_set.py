"""
Name: Shiny App Testing Agent
Purpose: Automated testing agent for Shiny applications using smolagents and Playwright
"""

import os
from dotenv import load_dotenv
from smolagents import CodeAgent, ToolCallingAgent, tool
from playwright.sync_api import sync_playwright, Page, Browser
import json
from datetime import datetime
from typing import List, Dict, Any
import time
import logging
from helper_fun import is_critical_error

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# Constants
SHINY_NAVIGATION_WAIT = 6000  # Wait for Shiny JS to stabilize
SHINY_LOAD_TIMEOUT = 40000    # Max page load time
INTERACTION_WAIT = 5000       # Wait after user interactions
TAB_CLICK_TIMEOUT = 10000     # Timeout for tab click operations

@tool
def navigate_to_shiny_app(url: str) -> str:
    """
    Navigate to a Shiny application.
    
    Args:
        url: The URL of the Shiny application to navigate to
    
    Returns:
        str: Confirmation message or result
    """
    logger.info(f"🌐 Navigating to Shiny app: {url}")
    global browser, page
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()
            
            page.goto(url, wait_until="domcontentloaded", timeout=SHINY_LOAD_TIMEOUT)
            page.wait_for_timeout(SHINY_NAVIGATION_WAIT)
            
            title = page.title()
            logger.info(f"✓ Successfully navigated to: {title}")
            return f"Successfully navigated to: {title}"
    except Exception as e:
        logger.error(f"✗ Navigation failed: {str(e)}")
        raise

@tool
def find_all_tabs_and_sections(url: str) -> str:
    """Discover all tabs, navigation items, and interactive sections in a Shiny app.
    
    Args:
        url: The URL of the Shiny app to analyze
    
    Returns:
        str: JSON string containing all discovered navigation elements (tabs, buttons, inputs, sliders, radio buttons)
    """
    logger.info(f"🔍 Finding tabs and sections in: {url}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()
            
            page.goto(url, wait_until="domcontentloaded", timeout=SHINY_LOAD_TIMEOUT)
            page.wait_for_timeout(SHINY_NAVIGATION_WAIT)
            
            navigation_elements = {
                "tabs": [],
                "links": [],
                "buttons": [],
                "inputs": [],
                "sliders": [],
                "radio_buttons": []
            }
            
            # Find tabs
            tab_selectors = [
                'a[data-toggle="tab"]',
                '.nav-tabs a',
                '.nav-link',
                '[role="tab"]',
                '.tabbable a',
                '.nav li a',
                'li.nav-item a',
                '[data-bs-toggle="tab"]'
            ]
            
            found_tabs = set()
            logger.info("🔎 Searching for tabs...")
            
            for selector in tab_selectors:
                try:
                    elements = page.query_selector_all(selector)
                    if elements:
                        logger.info(f"  Found {len(elements)} element(s) with selector: {selector}")
                        for elem in elements:
                            try:
                                text = elem.inner_text().strip()
                                href = elem.get_attribute('href') or elem.get_attribute('data-value')
                                
                                if text and text not in found_tabs:
                                    navigation_elements['tabs'].append({
                                        'text': text,
                                        'href': href,
                                        'selector': selector
                                    })
                                    found_tabs.add(text)
                            except:
                                continue
                except:
                    continue
            
            logger.info(f"✓ Found {len(navigation_elements['tabs'])} unique tabs: {list(found_tabs)}")
            
            # Find buttons
            logger.info("🔎 Searching for buttons...")
            try:
                buttons = page.query_selector_all('button, input[type="button"], .btn, [role="button"]')
                logger.info(f"  Found {len(buttons)} buttons (limiting to 20)")
                for btn in buttons[:20]:
                    try:
                        text = btn.inner_text().strip() or btn.get_attribute('value') or btn.get_attribute('aria-label') or 'Button'
                        navigation_elements['buttons'].append({
                            'text': text,
                            'id': btn.get_attribute('id'),
                            'class': btn.get_attribute('class'),
                            'visible': btn.is_visible()
                        })
                    except:
                        continue
            except Exception as e:
                logger.warning(f"Error finding buttons: {str(e)}")
            
            logger.info(f"✓ Found {len(navigation_elements['buttons'])} buttons")
            
            # Find input fields
            logger.info("🔎 Searching for input fields...")
            try:
                inputs = page.query_selector_all('input:not([type="hidden"]), textarea, select')
                logger.info(f"  Found {len(inputs)} input elements")
                for inp in inputs[:20]:
                    try:
                        input_type = inp.get_attribute('type') or inp.tag_name.lower()
                        navigation_elements['inputs'].append({
                            'type': input_type,
                            'id': inp.get_attribute('id'),
                            'name': inp.get_attribute('name'),
                            'value': inp.get_attribute('value')
                        })
                    except:
                        continue
            except Exception as e:
                logger.warning(f"Error finding inputs: {str(e)}")
            
            logger.info(f"✓ Found {len(navigation_elements['inputs'])} input fields")
            
            # Find sliders - comprehensive detection for all types
            logger.info("🔎 Searching for sliders...")
            try:
                # Single numeric sliders
                single_sliders = page.query_selector_all('input[type="range"]:not([multiple])')
                logger.info(f"  Found {len(single_sliders)} single numeric sliders")
                
                # Range sliders (dual-handle via ionRangeSlider)
                range_sliders = page.query_selector_all('.irs--from, .irs--to')
                logger.info(f"  Found {len(range_sliders)} range slider handles")
                
                # Shiny slider containers
                shiny_sliders = page.query_selector_all('.shiny-input-slider')
                logger.info(f"  Found {len(shiny_sliders)} Shiny slider containers")
                
                # Date/DateTime sliders
                date_sliders = page.query_selector_all('input[type="date"], input[type="datetime-local"], input[type="datetime"]')
                logger.info(f"  Found {len(date_sliders)} date/datetime inputs")
                
                # Unique slider collection
                seen_ids = set()
                
                # Process numeric sliders
                for slider in single_sliders:
                    slider_id = slider.get_attribute('id') or 'unknown'
                    if slider_id not in seen_ids:
                        try:
                            navigation_elements['sliders'].append({
                                'id': slider_id,
                                'type': 'numeric',
                                'min': slider.get_attribute('min') or '0',
                                'max': slider.get_attribute('max') or '100',
                                'step': slider.get_attribute('step') or '1',
                                'value': slider.get_attribute('value'),
                                'class': slider.get_attribute('class')
                            })
                            seen_ids.add(slider_id)
                        except:
                            continue
                
                # Process date/datetime inputs
                for date_input in date_sliders:
                    date_id = date_input.get_attribute('id') or 'unknown'
                    if date_id not in seen_ids:
                        try:
                            input_type = date_input.get_attribute('type')
                            navigation_elements['sliders'].append({
                                'id': date_id,
                                'type': f'date_{input_type}',
                                'min': date_input.get_attribute('min'),
                                'max': date_input.get_attribute('max'),
                                'value': date_input.get_attribute('value'),
                                'class': date_input.get_attribute('class')
                            })
                            seen_ids.add(date_id)
                        except:
                            continue
                            
            except Exception as e:
                logger.warning(f"Error finding sliders: {str(e)}")
            
            logger.info(f"✓ Found {len(navigation_elements['sliders'])} sliders")
            
            # Find radio buttons
            logger.info("🔎 Searching for radio buttons...")
            try:
                radio_buttons = page.query_selector_all('input[type="radio"], .shiny-input-radiogroup')
                logger.info(f"  Found {len(radio_buttons)} radio button elements")
                for rb in radio_buttons[:10]:
                    try:
                        navigation_elements['radio_buttons'].append({
                            'id': rb.get_attribute('id'),
                            'value': rb.get_attribute('value'),
                            'name': rb.get_attribute('name')
                        })
                    except:
                        continue
            except Exception as e:
                logger.warning(f"Error finding radio buttons: {str(e)}")
            
            logger.info(f"✓ Found {len(navigation_elements['radio_buttons'])} radio buttons")
            
            browser.close()
            
            summary = (
                f"Found: {len(navigation_elements['tabs'])} tabs, "
                f"{len(navigation_elements['buttons'])} buttons, "
                f"{len(navigation_elements['inputs'])} inputs, "
                f"{len(navigation_elements['sliders'])} sliders, "
                f"{len(navigation_elements['radio_buttons'])} radio buttons"
            )
            logger.info(f"✓ Summary: {summary}")
            
            return json.dumps(navigation_elements, indent=2)
    except Exception as e:
        logger.error(f"✗ Tab discovery failed: {str(e)}")
        raise

@tool
def test_tabs_navigation(url: str) -> str:
    """
    Click through all tabs in the Shiny app and test their functionality.
    
    Args:
        url: The URL of the Shiny app
    
    Returns:
        JSON string with tab click test results
    """
    logger.info(f"📂 Testing tab navigation: {url}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()
            
            page.goto(url, wait_until="domcontentloaded", timeout=SHINY_LOAD_TIMEOUT)
            page.wait_for_timeout(SHINY_NAVIGATION_WAIT)
            
            tab_results = {
                "url": url,
                "timestamp": datetime.now().isoformat(),
                "tabs_tested": [],
                "errors": [],
                "total_tabs": 0
            }
            
            # Find all tabs
            tab_selectors = [
                'a[data-toggle="tab"]',
                '.nav-tabs a',
                '.nav-link',
                '[role="tab"]',
                'li.nav-item a'
            ]
            
            all_tabs = []
            for selector in tab_selectors:
                try:
                    elements = page.query_selector_all(selector)
                    for elem in elements:
                        try:
                            text = elem.inner_text().strip()
                            if text and text not in [t['text'] for t in all_tabs]:
                                all_tabs.append({
                                    'text': text,
                                    'selector': selector,
                                    'element': elem
                                })
                        except:
                            continue
                except:
                    continue
            
            tab_results["total_tabs"] = len(all_tabs)
            logger.info(f"🔘 Found {len(all_tabs)} tabs to test: {[t['text'] for t in all_tabs]}")
            
            # Test each tab
            for i, tab_info in enumerate(all_tabs):
                tab_text = tab_info['text']
                tab_selector = tab_info['selector']
                
                logger.info(f"\n  Testing tab {i+1}/{len(all_tabs)}: '{tab_text}'")
                
                try:
                    # Try to find and click the tab
                    tab_element = page.query_selector(f'{tab_selector}:has-text("{tab_text}")')
                    
                    if not tab_element:
                        # Try alternative selector
                        all_tabs_on_page = page.query_selector_all(tab_selector)
                        for t in all_tabs_on_page:
                            if tab_text in t.inner_text():
                                tab_element = t
                                break
                    
                    if tab_element:
                        logger.info(f"    ✓ Found tab element")
                        logger.info(f"    Clicking tab...")
                        
                        # Scroll into view before clicking
                        tab_element.scroll_into_view_if_needed()
                        page.wait_for_timeout(1000)
                        
                        # Click the tab
                        tab_element.click()
                        logger.info(f"    ✓ Tab clicked")
                        
                        # Wait for tab content to load
                        page.wait_for_timeout(INTERACTION_WAIT)
                        logger.info(f"    ⏳ Waiting for tab content to load...")
                        
                        # Check if any errors appeared
                        error_elements = page.query_selector_all('.shiny-output-error, .alert-danger, .error')
                        
                        if error_elements:
                            error_text = " | ".join([e.inner_text() for e in error_elements[:3]])
                            logger.warning(f"    ⚠️  Errors found: {error_text}")
                            tab_results["tabs_tested"].append({
                                'text': tab_text,
                                'status': 'clicked_with_errors',
                                'errors': error_text
                            })
                            tab_results["errors"].append(f"Tab '{tab_text}' has errors: {error_text}")
                        else:
                            logger.info(f"    ✓ Tab loaded successfully, no errors")
                            tab_results["tabs_tested"].append({
                                'text': tab_text,
                                'status': 'success',
                                'errors': None
                            })
                    else:
                        logger.error(f"    ✗ Could not find tab element")
                        tab_results["tabs_tested"].append({
                            'text': tab_text,
                            'status': 'not_found',
                            'errors': 'Tab element not found'
                        })
                        tab_results["errors"].append(f"Could not find tab: {tab_text}")
                
                except Exception as e:
                    logger.error(f"    ✗ Error testing tab: {str(e)}")
                    tab_results["tabs_tested"].append({
                        'text': tab_text,
                        'status': 'error',
                        'errors': str(e)
                    })
                    tab_results["errors"].append(f"Error testing tab '{tab_text}': {str(e)}")
            
            browser.close()
            
            logger.info(f"\n✓ Tab testing complete: {len(tab_results['tabs_tested'])} tabs tested")
            logger.info(f"  Success: {len([t for t in tab_results['tabs_tested'] if t['status'] == 'success'])}")
            logger.info(f"  Errors: {len(tab_results['errors'])}")
            
            return json.dumps(tab_results, indent=2)
    except Exception as e:
        logger.error(f"✗ Tab navigation testing failed: {str(e)}")
        raise

@tool
def test_sliders(url: str) -> str:
    """
    Comprehensive test of slider controls in Shiny app.
    Supports: numeric sliders, range sliders, date sliders, datetime sliders.
    
    Args:
        url: The URL of the Shiny app
    
    Returns:
        JSON string with comprehensive slider test results
    """
    logger.info(f"🎚️  Testing sliders: {url}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()
            
            page.goto(url, wait_until="domcontentloaded", timeout=SHINY_LOAD_TIMEOUT)
            page.wait_for_timeout(SHINY_NAVIGATION_WAIT)
            
            slider_results = {
                "url": url,
                "timestamp": datetime.now().isoformat(),
                "sliders_tested": [],
                "slider_types_found": {
                    "numeric": 0,
                    "range": 0,
                    "date": 0,
                    "datetime": 0
                },
                "errors": [],
                "total_sliders": 0
            }
            
            # Comprehensive slider finding
            logger.info("🔎 Finding all slider types...")
            
            # Numeric sliders (single handle)
            numeric_sliders = page.query_selector_all('input[type="range"]:not([multiple])')
            logger.info(f"  Found {len(numeric_sliders)} numeric sliders")
            slider_results["slider_types_found"]["numeric"] = len(numeric_sliders)
            
            # Date/DateTime sliders
            date_sliders = page.query_selector_all('input[type="date"]')
            datetime_sliders = page.query_selector_all('input[type="datetime-local"], input[type="datetime"]')
            logger.info(f"  Found {len(date_sliders)} date sliders")
            logger.info(f"  Found {len(datetime_sliders)} datetime sliders")
            slider_results["slider_types_found"]["date"] = len(date_sliders)
            slider_results["slider_types_found"]["datetime"] = len(datetime_sliders)
            
            all_sliders = numeric_sliders + date_sliders + datetime_sliders
            slider_results["total_sliders"] = len(all_sliders)
            logger.info(f"\n  Total sliders found: {len(all_sliders)}")
            
            # Test all sliders
            for i, slider in enumerate(all_sliders):
                slider_type = slider.get_attribute('type')
                logger.info(f"\n  Testing slider {i+1}/{len(all_sliders)} (type: {slider_type})")
                
                try:
                    slider_id = slider.get_attribute('id') or f"slider_{i}_{slider_type}"
                    min_val = slider.get_attribute('min')
                    max_val = slider.get_attribute('max')
                    step_val = slider.get_attribute('step') or '1'
                    current_val = slider.get_attribute('value')
                    
                    logger.info(f"    ID: {slider_id}")
                    logger.info(f"    Type: {slider_type}")
                    logger.info(f"    Range: {min_val} - {max_val}")
                    logger.info(f"    Step: {step_val}")
                    logger.info(f"    Current value: {current_val}")
                    
                    test_result = {
                        'id': slider_id,
                        'type': slider_type,
                        'min': min_val,
                        'max': max_val,
                        'step': step_val,
                        'original_value': current_val
                    }
                    
                    # Determine test strategy based on type
                    new_val = None
                    
                    if slider_type == 'range':
                        # Numeric range slider - calculate midpoint
                        try:
                            min_num = int(min_val) if min_val else 0
                            max_num = int(max_val) if max_val else 100
                            new_val = str((min_num + max_num) // 2)
                        except:
                            new_val = '50'
                        
                    elif slider_type == 'date':
                        # Date slider - set to midpoint date if possible
                        try:
                            from datetime import datetime as dt
                            min_date = dt.fromisoformat(min_val)
                            max_date = dt.fromisoformat(max_val)
                            mid_date = min_date + (max_date - min_date) / 2
                            new_val = mid_date.date().isoformat()
                        except:
                            new_val = max_val or min_val
                        
                    elif slider_type in ['datetime-local', 'datetime']:
                        # DateTime slider - set to midpoint
                        try:
                            from datetime import datetime as dt
                            min_dt = dt.fromisoformat(min_val.replace('Z', '+00:00'))
                            max_dt = dt.fromisoformat(max_val.replace('Z', '+00:00'))
                            mid_dt = min_dt + (max_dt - min_dt) / 2
                            new_val = mid_dt.isoformat().split('+')[0]
                        except:
                            new_val = max_val or min_val
                    
                    if new_val:
                        logger.info(f"    Setting value to: {new_val}")
                        
                        # Try multiple methods to set value
                        try:
                            # Method 1: Direct fill
                            slider.fill(new_val)
                            page.wait_for_timeout(INTERACTION_WAIT)
                            updated_val = slider.get_attribute('value')
                        except:
                            try:
                                # Method 2: Clear and type
                                slider.triple_click()
                                slider.type(new_val)
                                page.wait_for_timeout(INTERACTION_WAIT)
                                updated_val = slider.get_attribute('value')
                            except:
                                # Method 3: JavaScript execution for ionRangeSlider
                                try:
                                    slider.evaluate(f"el => el.value = '{new_val}'")
                                    page.wait_for_timeout(INTERACTION_WAIT)
                                    updated_val = slider.get_attribute('value')
                                except:
                                    updated_val = None
                        
                        test_result['new_value'] = updated_val
                        test_result['status'] = 'tested'
                        logger.info(f"    ✓ Value updated to: {updated_val}")
                    else:
                        test_result['status'] = 'skipped'
                        test_result['reason'] = 'Could not determine test value'
                    
                    slider_results["sliders_tested"].append(test_result)
                
                except Exception as e:
                    logger.error(f"    ✗ Error testing slider: {str(e)}")
                    slider_results["sliders_tested"].append({
                        'id': f"slider_{i}",
                        'type': slider_type,
                        'status': 'error',
                        'error': str(e)
                    })
                    slider_results["errors"].append(f"Error testing slider {i+1}: {str(e)}")
            
            browser.close()
            
            logger.info(f"\n✓ Slider testing complete: {len(slider_results['sliders_tested'])} sliders tested")
            
            return json.dumps(slider_results, indent=2)
    except Exception as e:
        logger.error(f"✗ Slider testing failed: {str(e)}")
        raise

@tool
def test_radio_buttons(url: str) -> str:
    """
    Test radio buttons and checkboxes in the Shiny app.
    
    Args:
        url: The URL of the Shiny app
    
    Returns:
        JSON string with radio button test results
    """
    logger.info(f"🔘 Testing radio buttons: {url}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()
            
            page.goto(url, wait_until="domcontentloaded", timeout=SHINY_LOAD_TIMEOUT)
            page.wait_for_timeout(SHINY_NAVIGATION_WAIT)
            
            radio_results = {
                "url": url,
                "timestamp": datetime.now().isoformat(),
                "radio_buttons_tested": [],
                "errors": [],
                "total_radio_buttons": 0
            }
            
            # Find radio buttons
            logger.info("🔎 Finding radio buttons...")
            radios = page.query_selector_all('input[type="radio"]:not([disabled])')
            radio_results["total_radio_buttons"] = len(radios)
            
            logger.info(f"  Found {len(radios)} radio button(s)")
            
            for i, radio in enumerate(radios[:10]):  # Test first 10
                logger.info(f"\n  Testing radio button {i+1}/{min(len(radios), 10)}")
                
                try:
                    radio_id = radio.get_attribute('id') or f"radio_{i}"
                    radio_value = radio.get_attribute('value')
                    radio_name = radio.get_attribute('name')
                    is_checked = radio.is_checked()
                    
                    logger.info(f"    ID: {radio_id}")
                    logger.info(f"    Value: {radio_value}")
                    logger.info(f"    Name: {radio_name}")
                    logger.info(f"    Currently checked: {is_checked}")
                    
                    if not is_checked:
                        logger.info(f"    Clicking radio button...")
                        radio.click()
                        page.wait_for_timeout(INTERACTION_WAIT)
                        logger.info(f"    ✓ Radio button clicked")
                    else:
                        logger.info(f"    ℹ️  Radio button already selected")
                    
                    radio_results["radio_buttons_tested"].append({
                        'id': radio_id,
                        'value': radio_value,
                        'name': radio_name,
                        'was_checked': is_checked,
                        'status': 'tested'
                    })
                
                except Exception as e:
                    logger.error(f"    ✗ Error testing radio button: {str(e)}")
                    radio_results["radio_buttons_tested"].append({
                        'id': f"radio_{i}",
                        'status': 'error',
                        'error': str(e)
                    })
                    radio_results["errors"].append(f"Error testing radio button {i+1}: {str(e)}")
            
            browser.close()
            
            logger.info(f"\n✓ Radio button testing complete: {len(radio_results['radio_buttons_tested'])} tested")
            
            return json.dumps(radio_results, indent=2)
    except Exception as e:
        logger.error(f"✗ Radio button testing failed: {str(e)}")
        raise

@tool
def run_comprehensive_shiny_tests(url: str, app_name: str) -> str:
    """
    Run comprehensive tests on a Shiny application and generate a detailed HTML report.
    This orchestrates all testing phases: tabs, sliders, radio buttons, and page errors.
    
    Args:
        url: The URL of the Shiny app to test
        app_name: Name of the Shiny app (used in report filename)
    
    Returns:
        str: Path to the generated HTML report file
    """
    logger.info(f"\n{'='*60}")
    logger.info(f"🚀 STARTING COMPREHENSIVE SHINY APP TEST SUITE")
    logger.info(f"App: {app_name} | URL: {url}")
    logger.info(f"{'='*60}\n")
    
    all_test_results = []
    
    try:
        # Step 1: Discover all interactive elements
        logger.info("\n[1/5] DISCOVERING INTERACTIVE ELEMENTS...")
        discovery_result = find_all_tabs_and_sections(url)
        discovery_data = json.loads(discovery_result)
        logger.info(f"✓ Discovery complete: Found {len(discovery_data.get('tabs', []))} tabs, {len(discovery_data.get('sliders', []))} sliders")
        
        # Step 2: Test tab navigation
        logger.info("\n[2/5] TESTING TAB NAVIGATION...")
        try:
            tabs_result = test_tabs_navigation(url)
            tabs_data = json.loads(tabs_result)
            all_test_results.append(tabs_data)
            logger.info(f"✓ Tab testing complete: {len(tabs_data.get('tabs_tested', []))} tabs tested")
        except Exception as e:
            logger.error(f"✗ Tab testing failed: {str(e)}")
            all_test_results.append({"tabs_tested": [], "errors": [str(e)]})
        
        # Step 3: Test sliders
        logger.info("\n[3/5] TESTING SLIDERS...")
        try:
            sliders_result = test_sliders(url)
            sliders_data = json.loads(sliders_result)
            all_test_results.append(sliders_data)
            logger.info(f"✓ Slider testing complete: {sliders_data.get('total_sliders', 0)} sliders found, {len(sliders_data.get('sliders_tested', []))} tested")
        except Exception as e:
            logger.error(f"✗ Slider testing failed: {str(e)}")
            all_test_results.append({"sliders_tested": [], "errors": [str(e)], "total_sliders": 0})
        
        # Step 4: Test radio buttons
        logger.info("\n[4/5] TESTING RADIO BUTTONS...")
        try:
            radio_result = test_radio_buttons(url)
            radio_data = json.loads(radio_result)
            all_test_results.append(radio_data)
            logger.info(f"✓ Radio button testing complete: {len(radio_data.get('radio_buttons_tested', []))} tested")
        except Exception as e:
            logger.error(f"✗ Radio button testing failed: {str(e)}")
            all_test_results.append({"radio_buttons_tested": [], "errors": [str(e)]})
        
        # Step 5: Test main page for errors
        logger.info("\n[5/5] TESTING MAIN PAGE...")
        try:
            page_result = test_shiny_page(url, tab_name=None)
            page_data = json.loads(page_result)
            all_test_results.append(page_data)
            logger.info(f"✓ Page testing complete: Status {page_data.get('status', 'unknown')}")
        except Exception as e:
            logger.error(f"✗ Page testing failed: {str(e)}")
            all_test_results.append({"tab_tested": "Main", "status": "error", "errors": [str(e)]})
        
        # Step 6: Generate comprehensive report
        logger.info("\n[6/6] GENERATING COMPREHENSIVE REPORT...")
        results_json = json.dumps(all_test_results)
        report_path = generate_test_report(results_json, app_name)
        
        logger.info(f"\n{'='*60}")
        logger.info(f"✓ COMPREHENSIVE TEST SUITE COMPLETED SUCCESSFULLY")
        logger.info(f"Report: {report_path}")
        logger.info(f"{'='*60}\n")
        
        return report_path
        
    except Exception as e:
        logger.error(f"\n✗ COMPREHENSIVE TEST SUITE FAILED: {str(e)}")
        logger.error(f"{'='*60}\n")
        raise

def is_critical_error(error_text: str) -> bool:
    """Filter out harmless network/resource errors."""
    harmless = ["404", "favicon", "analytics", "tracking", "cors", "failed to fetch", "xhr", "typekit", "fonts.googleapis", "google"]
    return not any(p.lower() in error_text.lower() for p in harmless)

@tool
def test_shiny_page(url: str, tab_name: str = None) -> str:
    """Test a specific page or tab in a Shiny app for errors and functionality.
    
    Args:
        url: The URL of the Shiny app to test
        tab_name: Optional specific tab name to navigate to and test
    
    Returns:
        str: JSON string containing test results including errors, warnings, and element counts
    """
    logger.info(f"🧪 Testing Shiny page: {url}, Tab: {tab_name or 'Main'}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()
            
            console_messages = []
            errors = []
            
            page.on("console", lambda msg: console_messages.append({
                "type": msg.type,
                "text": msg.text
            }))
            
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            
            page.goto(url, wait_until="domcontentloaded", timeout=SHINY_LOAD_TIMEOUT)
            page.wait_for_timeout(SHINY_NAVIGATION_WAIT)
            
            test_results = {
                "timestamp": datetime.now().isoformat(),
                "url": url,
                "tab_tested": tab_name or "Main page",
                "status": "success",
                "errors": [],
                "warnings": [],
                "checks": {}
            }
            
            if tab_name:
                logger.info(f"  Attempting to click tab: {tab_name}")
                tab_selectors = [
                    f'a:has-text("{tab_name}")',
                    f'[data-value="{tab_name}"]',
                    f'a[href="#{tab_name}"]',
                ]
                
                clicked = False
                for selector in tab_selectors:
                    try:
                        element = page.query_selector(selector)
                        if element:
                            logger.info(f"  ✓ Found tab")
                            element.click()
                            page.wait_for_timeout(INTERACTION_WAIT)
                            clicked = True
                            break
                    except:
                        continue
                
                if not clicked:
                    test_results["warnings"].append(f"Could not find or click tab: {tab_name}")
                    logger.warning(f"  ✗ Could not click tab: {tab_name}")
            
            # Check for errors
            logger.info("  Checking for Shiny errors...")
            error_elements = page.query_selector_all('.shiny-output-error, .shiny-output-error-validation, .alert-danger')
            if error_elements:
                logger.warning(f"  Found {len(error_elements)} error(s)")
                for elem in error_elements[:5]:
                    try:
                        error_text = elem.inner_text()
                        test_results["errors"].append(error_text)
                    except:
                        pass
            
            # Count elements
            plots = page.query_selector_all('.shiny-plot-output, .plotly, canvas, svg')
            tables = page.query_selector_all('table, .dataTables_wrapper, .shiny-output-table')
            inputs = page.query_selector_all('input, select, textarea')
            
            test_results["checks"]["plots_found"] = len(plots)
            test_results["checks"]["tables_found"] = len(tables)
            test_results["checks"]["inputs_found"] = len(inputs)
            
            logger.info(f"  Plots: {len(plots)}, Tables: {len(tables)}, Inputs: {len(inputs)}")
            
            # Categorize console messages
            for msg in console_messages:
                if msg["type"] == "error" and is_critical_error(msg['text']):
                    test_results["errors"].append(f"Console error: {msg['text']}")
                elif msg["type"] == "warning":
                    test_results["warnings"].append(f"Console warning: {msg['text']}")
            
            test_results["errors"].extend(errors)
            
            if test_results["errors"]:
                test_results["status"] = "failed"
            elif test_results["warnings"]:
                test_results["status"] = "warning"
            
            browser.close()
            logger.info(f"✓ Test complete. Status: {test_results['status']}")
            return json.dumps(test_results, indent=2)
    except Exception as e:
        logger.error(f"✗ Page testing failed: {str(e)}")
        raise

@tool
def generate_test_report(test_results: str, app_name: str) -> str:
    """Generate a comprehensive HTML test report from test results.
    
    Args:
        test_results: JSON string containing all test results to include in the report
        app_name: Name of the Shiny app being tested
    
    Returns:
        str: Path to the generated HTML report file
    """
    logger.info(f"📄 Generating test report for: {app_name}")
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"test_report_{app_name}_{timestamp}.html"
        
        # Parse test results - handle both list and dict formats
        results_data = json.loads(test_results) if isinstance(test_results, str) else test_results
        
        # If results_data is a list, organize it by type
        tabs_results = None
        sliders_results = None
        radio_results = None
        main_page_results = None
        
        if isinstance(results_data, list):
            # Extract different test result types from the list
            for result in results_data:
                if "tabs_tested" in result:
                    tabs_results = result
                elif "sliders_tested" in result:
                    sliders_results = result
                elif "radio_buttons_tested" in result:
                    radio_results = result
                elif "tab_tested" in result:  # Main page test
                    main_page_results = result
        else:
            # If it's a dict, treat it as main page results
            main_page_results = results_data
        
        # Build HTML content
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Shiny App Test Report - {app_name}</title>
            <style>
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                body {{ 
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                    margin: 20px; 
                    background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
                    line-height: 1.6;
                }}
                .container {{ 
                    max-width: 1400px; 
                    margin: 0 auto; 
                    background: white; 
                    padding: 30px; 
                    border-radius: 10px;
                    box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                }}
                h1 {{ 
                    color: #2c3e50; 
                    border-bottom: 4px solid #3498db; 
                    padding-bottom: 15px;
                    margin-bottom: 20px;
                    font-size: 28px;
                }}
                h2 {{ 
                    color: #34495e; 
                    margin-top: 30px;
                    margin-bottom: 15px;
                    font-size: 20px;
                    border-left: 4px solid #3498db;
                    padding-left: 10px;
                }}
                .header-info {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                    gap: 15px;
                    margin-bottom: 25px;
                }}
                .info-box {{
                    background: #ecf0f1;
                    padding: 15px;
                    border-radius: 5px;
                    border-left: 4px solid #3498db;
                }}
                .info-box strong {{ color: #2c3e50; }}
                .section {{ 
                    margin: 25px 0; 
                    padding: 20px; 
                    border: 1px solid #ddd; 
                    border-radius: 8px;
                    background: #f9f9f9;
                }}
                .status-success {{ color: #27ae60; font-weight: bold; }}
                .status-failed {{ color: #e74c3c; font-weight: bold; }}
                .status-warning {{ color: #f39c12; font-weight: bold; }}
                .status-unknown {{ color: #95a5a6; font-weight: bold; }}
                .error {{ 
                    background: #fadbd8; 
                    padding: 12px; 
                    margin: 10px 0; 
                    border-left: 4px solid #e74c3c;
                    border-radius: 3px;
                }}
                .warning {{ 
                    background: #fef5e7; 
                    padding: 12px; 
                    margin: 10px 0; 
                    border-left: 4px solid #f39c12;
                    border-radius: 3px;
                }}
                .success {{ 
                    background: #d5f4e6; 
                    padding: 12px; 
                    margin: 10px 0; 
                    border-left: 4px solid #27ae60;
                    border-radius: 3px;
                }}
                table {{ 
                    width: 100%; 
                    border-collapse: collapse; 
                    margin: 15px 0;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }}
                th {{ 
                    background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
                    color: white;
                    padding: 15px;
                    text-align: left;
                    font-weight: 600;
                    border: none;
                }}
                td {{ 
                    padding: 12px 15px;
                    border-bottom: 1px solid #ecf0f1;
                }}
                tr:hover {{ background-color: #f5f5f5; }}
                tr:last-child td {{ border-bottom: none; }}
                .summary-table th {{ background: #27ae60; }}
                .tabs-table th {{ background: #3498db; }}
                .sliders-table th {{ background: #e74c3c; }}
                .radio-table th {{ background: #9b59b6; }}
                .main-page-table th {{ background: #16a085; }}
                .no-data {{
                    text-align: center;
                    padding: 20px;
                    color: #7f8c8d;
                    font-style: italic;
                }}
                .footer {{
                    margin-top: 40px;
                    padding-top: 20px;
                    border-top: 2px solid #ecf0f1;
                    text-align: center;
                    color: #7f8c8d;
                    font-size: 12px;
                }}
                .metadata {{
                    font-size: 14px;
                    color: #555;
                    margin: 5px 0;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🧪 Shiny App Test Report</h1>
                
                <div class="header-info">
                    <div class="info-box">
                        <strong>App Name:</strong>
                        <div class="metadata">{app_name}</div>
                    </div>
                    <div class="info-box">
                        <strong>Test Date:</strong>
                        <div class="metadata">{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>
                    </div>
                    <div class="info-box">
                        <strong>Overall Status:</strong>
                        <div class="metadata"><span class="status-success">✓ COMPLETED</span></div>
                    </div>
                </div>
        """
        
        # Add tabs summary
        if tabs_results:
            html_content += f"""
                <div class="section">
                    <h2>📑 Tab Navigation Results</h2>
                    <table class="tabs-table">
                        <thead>
                            <tr>
                                <th>Tab Name</th>
                                <th>Status</th>
                                <th>Error Details</th>
                            </tr>
                        </thead>
                        <tbody>
            """
            if tabs_results.get("tabs_tested"):
                for tab in tabs_results["tabs_tested"]:
                    status_class = "status-success" if tab.get("status") == "success" else "status-failed"
                    status_icon = "✓" if tab.get("status") == "success" else "✗"
                    errors = tab.get("errors") or "None"
                    html_content += f"""
                            <tr>
                                <td><strong>{tab.get('text', 'Unknown')}</strong></td>
                                <td><span class="{status_class}">{status_icon} {tab.get('status', 'unknown').upper()}</span></td>
                                <td>{errors}</td>
                            </tr>
                    """
            else:
                html_content += '<tr><td colspan="3" class="no-data">No tab data available</td></tr>'
            
            html_content += """
                        </tbody>
                    </table>
                    <p class="metadata"><strong>Total Tabs Tested:</strong> {}</p>
                </div>
            """.format(len(tabs_results.get("tabs_tested", [])))
        
        # Add sliders summary
        if sliders_results:
            html_content += f"""
                <div class="section">
                    <h2>🎚️ Slider Testing Results</h2>
                    <table class="sliders-table">
                        <thead>
                            <tr>
                                <th>Slider ID</th>
                                <th>Range</th>
                                <th>Original Value</th>
                                <th>New Value</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
            """
            if sliders_results.get("sliders_tested"):
                for slider in sliders_results["sliders_tested"]:
                    range_info = f"{slider.get('range', {}).get('min', 'N/A')} - {slider.get('range', {}).get('max', 'N/A')}"
                    status_class = "status-success" if slider.get("status") == "tested" else "status-failed"
                    status_icon = "✓" if slider.get("status") == "tested" else "✗"
                    html_content += f"""
                            <tr>
                                <td><strong>{slider.get('id', 'Unknown')}</strong></td>
                                <td>{range_info}</td>
                                <td>{slider.get('original_value', 'N/A')}</td>
                                <td><strong>{slider.get('new_value', 'N/A')}</strong></td>
                                <td><span class="{status_class}">{status_icon} {slider.get('status', 'unknown').upper()}</span></td>
                            </tr>
                    """
            else:
                html_content += '<tr><td colspan="5" class="no-data">No sliders found in the application</td></tr>'
            
            html_content += """
                        </tbody>
                    </table>
                    <p class="metadata"><strong>Total Sliders:</strong> {}</p>
                </div>
            """.format(len(sliders_results.get("sliders_tested", [])))
        
        # Add radio buttons summary
        if radio_results:
            html_content += f"""
                <div class="section">
                    <h2>🔘 Radio Button Testing Results</h2>
                    <table class="radio-table">
                        <thead>
                            <tr>
                                <th>Radio Button</th>
                                <th>Value</th>
                                <th>Name</th>
                                <th>Was Checked</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
            """
            if radio_results.get("radio_buttons_tested"):
                for radio in radio_results["radio_buttons_tested"]:
                    status_class = "status-success" if radio.get("status") == "tested" else "status-failed"
                    status_icon = "✓" if radio.get("status") == "tested" else "✗"
                    checked = "✓ Yes" if radio.get("was_checked") else "✗ No"
                    html_content += f"""
                            <tr>
                                <td><strong>{radio.get('id', 'Unknown')}</strong></td>
                                <td>{radio.get('value', 'N/A')}</td>
                                <td>{radio.get('name', 'N/A')}</td>
                                <td>{checked}</td>
                                <td><span class="{status_class}">{status_icon} {radio.get('status', 'unknown').upper()}</span></td>
                            </tr>
                    """
            else:
                html_content += '<tr><td colspan="5" class="no-data">No radio buttons found in the application</td></tr>'
            
            html_content += """
                        </tbody>
                    </table>
                    <p class="metadata"><strong>Total Radio Buttons:</strong> {}</p>
                </div>
            """.format(len(radio_results.get("radio_buttons_tested", [])))
        
        # Add main page results
        if main_page_results:
            html_content += f"""
                <div class="section">
                    <h2>📄 Main Page Testing Results</h2>
                    <table class="main-page-table">
                        <thead>
                            <tr>
                                <th>Metric</th>
                                <th>Value</th>
                            </tr>
                        </thead>
                        <tbody>
            """
            checks = main_page_results.get("checks", {})
            errors = main_page_results.get("errors", [])
            warnings = main_page_results.get("warnings", [])
            
            html_content += f"""
                            <tr>
                                <td><strong>Tab Tested</strong></td>
                                <td>{main_page_results.get('tab_tested', 'N/A')}</td>
                            </tr>
                            <tr>
                                <td><strong>Status</strong></td>
                                <td><span class="status-{main_page_results.get('status', 'unknown')}">{'✓' if main_page_results.get('status') == 'success' else '✗'} {main_page_results.get('status', 'unknown').upper()}</span></td>
                            </tr>
                            <tr>
                                <td><strong>Plots Found</strong></td>
                                <td>{checks.get('plots_found', 0)}</td>
                            </tr>
                            <tr>
                                <td><strong>Tables Found</strong></td>
                                <td>{checks.get('tables_found', 0)}</td>
                            </tr>
                            <tr>
                                <td><strong>Input Fields Found</strong></td>
                                <td>{checks.get('inputs_found', 0)}</td>
                            </tr>
                            <tr>
                                <td><strong>Errors</strong></td>
                                <td><span class="status-{'failed' if errors else 'success'}">{len(errors)} {'✓' if not errors else '✗'}</span></td>
                            </tr>
                            <tr>
                                <td><strong>Warnings</strong></td>
                                <td><span class="status-{'warning' if warnings else 'success'}">{len(warnings)}</span></td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            """
            
            if errors:
                html_content += """
                    <div class="section">
                        <h2>⚠️ Errors Detected</h2>
                """
                for error in errors:
                    html_content += f'<div class="error">{str(error)}</div>'
                html_content += "</div>"
            
            if warnings:
                html_content += """
                    <div class="section">
                        <h2>⚠️ Warnings</h2>
                """
                for warning in warnings:
                    html_content += f'<div class="warning">{str(warning)}</div>'
                html_content += "</div>"
        
        # Add footer
        html_content += """
                <div class="footer">
                    <p>Generated by Shiny App QC Agent | <strong>Report Type:</strong> Comprehensive Test Report</p>
                    <p>This report contains detailed test results for all interactive components of the Shiny application.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        logger.info(f"✓ Report generated: {filename}")
        return f"Report generated: {filename}"
    except Exception as e:
        logger.error(f"✗ Report generation failed: {str(e)}")
        raise
