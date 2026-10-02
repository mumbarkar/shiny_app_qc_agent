"""
Name: Shiny App Testing Agent
Purpose: Automated testing agent for Shiny applications using smolagents and Playwright
"""

import os
import base64
from dotenv import load_dotenv
from smolagents import CodeAgent, ToolCallingAgent, tool
from playwright.sync_api import sync_playwright, Page, Browser
import json
import html
from datetime import datetime
from typing import List, Dict, Any
import time
import logging
from helper_fun import is_critical_error

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# Timeout ceilings (milliseconds). Defaults are configurable via environment
# variables; the 90s initial-navigation default is provisional based on the log.
def _timeout_setting(name: str, default: int) -> int:
    try:
        return max(1000, int(os.getenv(name, str(default))))
    except ValueError:
        logger.warning("Ignoring invalid %s; using %d ms", name, default)
        return default


SHINY_LOAD_TIMEOUT = _timeout_setting("SHINY_LOAD_TIMEOUT_MS", 90000)
SHINY_READY_TIMEOUT = _timeout_setting("SHINY_READY_TIMEOUT_MS", 60000)
TAB_RENDER_TIMEOUT = _timeout_setting("SHINY_TAB_RENDER_TIMEOUT_MS", 90000)
SCREENSHOT_TIMEOUT = _timeout_setting("SHINY_SCREENSHOT_TIMEOUT_MS", 90000)
TAB_CLICK_TIMEOUT = _timeout_setting("SHINY_ACTION_TIMEOUT_MS", 15000)
GLOBAL_RUN_TIMEOUT = _timeout_setting("SHINY_RUN_TIMEOUT_MS", 900000)
INTERACTION_WAIT = 5000  # Retained for legacy standalone tools.

TAB_SELECTOR = '[role="tab"], a[data-toggle="tab"], [data-bs-toggle="tab"]'
CONTROL_SELECTOR = 'button, input:not([type="hidden"]), select, textarea, [role="button"], [role="checkbox"], [role="radio"]'


SHINY_CONNECTION_MONITOR = """(() => {
    if (window.__qcShinyConnection) return;
    const state = window.__qcShinyConnection = {
        status: 'unknown',
        disconnectCount: 0,
        lastEventAt: performance.now(),
        jqueryAttached: false
    };
    const connected = () => {
        state.status = 'connected';
        state.lastEventAt = performance.now();
    };
    const disconnected = () => {
        state.status = 'disconnected';
        state.disconnectCount += 1;
        state.lastEventAt = performance.now();
    };
    document.addEventListener('shiny:connected', connected, true);
    document.addEventListener('shiny:disconnected', disconnected, true);
    const attachJQuery = () => {
        if (state.jqueryAttached || !window.jQuery) return;
        window.jQuery(document).on('shiny:connected', connected);
        window.jQuery(document).on('shiny:disconnected', disconnected);
        state.jqueryAttached = true;
    };
    const jqueryPoll = window.setInterval(() => {
        attachJQuery();
        if (state.jqueryAttached) window.clearInterval(jqueryPoll);
    }, 25);
    attachJQuery();
})();"""


def _install_shiny_connection_monitor(page: Page) -> None:
    page.add_init_script(SHINY_CONNECTION_MONITOR)


def _wait_for_shiny_connection(page: Page, timeout: int = SHINY_READY_TIMEOUT) -> None:
    page.wait_for_function(
        "() => window.__qcShinyConnection?.status === 'connected'",
        timeout=timeout,
    )
    if _shiny_connection_snapshot(page)["disconnected"]:
        raise RuntimeError("Shiny server disconnected during initial connection")


def _shiny_connection_snapshot(page: Page) -> Dict[str, Any]:
    return page.evaluate(
        """() => {
            const state = window.__qcShinyConnection || {};
            const bodyText = (document.body?.innerText || '').replace(/\\s+/g, ' ').toLowerCase();
            return {
                status: state.status || 'unknown',
                disconnect_count: state.disconnectCount || 0,
                disconnected: state.status === 'disconnected' ||
                    bodyText.includes('disconnected from the server')
            };
        }"""
    )


def _capture_tab_screenshot(
    page: Page,
    tab_name: str,
    panel_id: str | None,
    deadline: float,
) -> bytes:
    wait_for_settle = False
    while True:
        if wait_for_settle:
            _wait_for_tab_settle(
                page,
                timeout=_remaining_timeout_ms(deadline),
                panel_id=panel_id,
            )
        before = _shiny_connection_snapshot(page)
        if before["disconnected"]:
            wait_for_settle = True
            continue

        screenshot = page.screenshot(
            type="png",
            full_page=True,
            timeout=_remaining_timeout_ms(deadline, timeout_cap=SCREENSHOT_TIMEOUT),
        )
        after = _shiny_connection_snapshot(page)
        if after["disconnected"] or after["disconnect_count"] != before["disconnect_count"]:
            logger.warning(
                "Shiny connection changed during screenshot of tab '%s'; waiting and retrying",
                tab_name,
            )
            wait_for_settle = True
            continue
        return screenshot


def _remaining_timeout_ms(deadline: float, timeout_cap: int | None = None) -> int:
    remaining_ms = int((deadline - time.perf_counter()) * 1000)
    if remaining_ms <= 0:
        raise TimeoutError("Per-tab render deadline reached")
    return min(timeout_cap, remaining_ms) if timeout_cap is not None else remaining_ms


def _visible_tab_descriptors(page: Page, parent_panel: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    """Find root tabs or only the immediate child tabs in a selected panel."""
    return page.evaluate(
        """({selector, parentPanel}) => {
            const visible = el => {
                const style = getComputedStyle(el);
                return !!(el.getClientRects().length && style.visibility !== 'hidden' && style.display !== 'none');
            };
            const panelSelector = '.tab-pane, [role="tabpanel"]';
            let parentScope = null;
            if (parentPanel) {
                const id = parentPanel.panel_id || '';
                const href = parentPanel.href || '';
                const controls = parentPanel.controls || '';
                const panelId = controls || (href.startsWith('#') ? href.slice(1) : '');
                parentScope = (id && document.getElementById(id)) ||
                    (panelId && document.getElementById(panelId)) ||
                    (parentPanel.id && document.getElementById(parentPanel.id)) ||
                    [...document.querySelectorAll(`${panelSelector}.active, [role="tabpanel"]`)].filter(visible).at(-1) || document;
            }
            const seen = new Map();
            return [...document.querySelectorAll(selector)].filter(el => {
                if (!visible(el)) return false;
                const href = el.getAttribute('href') || '';
                const targetId = el.getAttribute('aria-controls') || (href.startsWith('#') ? href.slice(1) : '');
                const targetPanel = targetId && document.getElementById(targetId);
                const ownerPanel = targetPanel ? targetPanel.parentElement.closest(panelSelector) : el.closest(panelSelector);
                return parentPanel ? ownerPanel === parentScope : ownerPanel === null;
            }).map((el, index) => {
                const text = (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
                const descriptor = {
                    index,
                    text,
                    id: el.id || '',
                    href: el.getAttribute('href') || '',
                    value: el.getAttribute('data-value') || '',
                    controls: el.getAttribute('aria-controls') || '',
                    panel_id: el.getAttribute('aria-controls') || ((el.getAttribute('href') || '').startsWith('#') ? (el.getAttribute('href') || '').slice(1) : ''),
                    role: el.getAttribute('role') || '',
                    selector: el.hasAttribute('data-bs-toggle') ? '[data-bs-toggle="tab"]' :
                        (el.hasAttribute('data-toggle') ? 'a[data-toggle="tab"]' : '[role="tab"]')
                };
                const identity = descriptor.id || descriptor.href || descriptor.value || descriptor.controls;
                const base = [identity, text, descriptor.role].join('|');
                const occurrence = seen.get(base) || 0;
                seen.set(base, occurrence + 1);
                descriptor.key = `${base}|${occurrence}`;
                return descriptor;
            }).filter(tab => tab.text);
        }""",
        {"selector": TAB_SELECTOR, "parentPanel": parent_panel},
    )


def _visible_control_inventory(page: Page, tab: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Inspect visible control metadata and state without changing the app."""
    return page.evaluate(
        """({selector, tab}) => {
            const visible = el => {
                const style = getComputedStyle(el);
                return !!(el.getClientRects().length && style.visibility !== 'hidden' && style.display !== 'none');
            };
            const panelId = tab && tab.panel_id;
            const scope = (panelId && document.getElementById(panelId)) ||
                [...document.querySelectorAll('.tab-pane.active, [role="tabpanel"]')].filter(visible).at(-1) || document;
            const inCurrentPanel = el => scope === document || el.closest('.tab-pane, [role="tabpanel"]') === scope;
            const controls = [...scope.querySelectorAll(selector)].filter(el => visible(el) && inCurrentPanel(el));
            const counts = {};
            const details = controls.map(el => {
                const tag = el.tagName.toLowerCase();
                const nativeType = (el.getAttribute('type') || tag).toLowerCase();
                const type = el.closest('.shiny-input-slider, .irs') ? 'shiny-slider' : nativeType;
                counts[type] = (counts[type] || 0) + 1;
                const associatedLabel = el.labels && el.labels.length ? [...el.labels].map(label => label.innerText).join(' ') : '';
                const safeValue = ['password', 'file'].includes(nativeType) ? null :
                    (el.type === 'checkbox' || el.type === 'radio' ? null : (el.value ?? null));
                return {
                    tag,
                    type,
                    native_type: nativeType,
                    id: el.id || null,
                    name: el.getAttribute('name'),
                    class: typeof el.className === 'string' ? el.className : '',
                    label: (el.getAttribute('aria-label') || associatedLabel || el.getAttribute('placeholder') || el.innerText || '').trim().slice(0, 160),
                    value_present: safeValue !== null && String(safeValue).length > 0,
                    checked: (el.type === 'checkbox' || el.type === 'radio') ? !!el.checked : null,
                    selected_option_count: el.tagName === 'SELECT' ? el.selectedOptions.length : null,
                    disabled: !!el.disabled,
                    required: !!el.required,
                    valid: el.validity ? el.validity.valid : null,
                    validation_message: el.validationMessage || ''
                };
            });
            const outputs = {};
            const outputSelectors = {
                plots: '.shiny-plot-output, .plotly, .highcharts-container, canvas, svg',
                tables: 'table, .dataTables_wrapper, .shiny-output-table',
                downloads: 'a[download], .shiny-download-link',
                shiny_outputs: '.shiny-bound-output'
            };
            for (const [name, selector] of Object.entries(outputSelectors)) {
                outputs[name] = [...scope.querySelectorAll(selector)].filter(el => visible(el) && inCurrentPanel(el)).length;
            }
            const errors = [...scope.querySelectorAll('.shiny-output-error, .shiny-output-error-validation, .alert-danger')]
                .filter(el => visible(el) && inCurrentPanel(el)).map(el => (el.innerText || '').trim()).filter(Boolean);
            return {total: details.length, counts, controls: details, outputs, errors, panel_id: scope.id || null};
        }""",
        {"selector": CONTROL_SELECTOR, "tab": tab},
    )


def _wait_for_shiny_content(page: Page, timeout: int = SHINY_READY_TIMEOUT) -> None:
    """Wait for a useful Shiny DOM without depending on network-idle (WebSockets)."""
    page.locator('body').wait_for(state='attached', timeout=timeout)
    page.wait_for_function(
        """() => document.readyState !== 'loading' && (
            document.body.innerText.trim().length > 0 ||
            document.querySelectorAll('[role="tab"], a[data-toggle="tab"], [data-bs-toggle="tab"], .shiny-bound-input, input, select, button').length > 0
        )""",
        timeout=timeout,
    )


def _wait_for_root_tabs(page: Page, timeout: int = SHINY_READY_TIMEOUT) -> None:
    """Wait for visible top-level tabs, returning as soon as Shiny renders them."""
    page.wait_for_function(
        """selector => {
            const visible = el => {
                const style = getComputedStyle(el);
                return !!(el.getClientRects().length && style.visibility !== 'hidden' && style.display !== 'none');
            };
            const panelSelector = '.tab-pane, [role="tabpanel"]';
            return [...document.querySelectorAll(selector)].some(el => {
                if (!visible(el)) return false;
                const href = el.getAttribute('href') || '';
                const targetId = el.getAttribute('aria-controls') || (href.startsWith('#') ? href.slice(1) : '');
                const targetPanel = targetId && document.getElementById(targetId);
                const ownerPanel = targetPanel
                    ? targetPanel.parentElement.closest(panelSelector)
                    : el.closest(panelSelector);
                return ownerPanel === null;
            });
        }""",
        arg=TAB_SELECTOR,
        timeout=timeout,
    )


def _wait_for_tab_settle(
    page: Page,
    timeout: int = TAB_RENDER_TIMEOUT,
    panel_id: str | None = None,
) -> None:
    """Wait until the panel is active, Shiny is idle, and visible progress has ended."""
    page.wait_for_function(
        """expectedPanelId => {
            const visible = el => {
                const style = getComputedStyle(el);
                return !!(el.getClientRects().length && style.visibility !== 'hidden' &&
                    style.display !== 'none' && Number(style.opacity) > 0);
            };
            const panels = [...document.querySelectorAll('.tab-pane.active, [role="tabpanel"]')].filter(visible);
            const expected = expectedPanelId && document.getElementById(expectedPanelId);
            if (expected && (!visible(expected) || (!expected.classList.contains('active') && expected.getAttribute('aria-hidden') === 'true'))) return false;
            const scope = expected || (panels.length ? panels[panels.length - 1] : document.body);
            if (window.__qc_observed_panel !== scope) {
                if (window.__qc_panel_observer) window.__qc_panel_observer.disconnect();
                window.__qc_last_panel_mutation = performance.now();
                window.__qc_observed_panel = scope;
                window.__qc_panel_observer = new MutationObserver(() => {
                    window.__qc_last_panel_mutation = performance.now();
                });
                window.__qc_panel_observer.observe(scope, {subtree: true, childList: true, attributes: true, characterData: true});
            }

            // Shiny signals app-level server work on <html>, not necessarily
            // inside the selected tab panel. Do not block on the
            // `.recalculating` output class alone: some apps leave it attached
            // to static/hidden outputs after the visible UI is already idle.
            const outputBusy = !!scope.querySelector(
                '.shiny-bound-output.shiny-busy, [aria-busy="true"]'
            );
            const pendingVisiblePlot = [...scope.querySelectorAll('.shiny-bound-output.recalculating')]
                .filter(visible)
                .some(el => el.matches('.shiny-plot-output') || /plot|chart|graph/i.test(el.id));
            const appBusy = document.documentElement.classList.contains('shiny-busy') ||
                document.body.classList.contains('shiny-busy') || outputBusy || pendingVisiblePlot;

            // Shiny's progress UI is commonly rendered outside the tab panel.
            // Also recognize the plain-text Computing... indicator used by
            // apps/widgets that do not use Shiny's standard progress classes.
            const progressSelectors = [
                '#shiny-notification-panel .shiny-progress-notification',
                '.shiny-progress.open',
                '[role="progressbar"]',
                '[aria-busy="true"]',
                '.shinybusy', '.shinybusy-container', '.shinybusy-spinner'
            ].join(', ');
            const visibleProgress = [...document.querySelectorAll(progressSelectors)].some(visible);
            const textWalker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let textNode;
            let computingText = false;
            while ((textNode = textWalker.nextNode())) {
                const text = (textNode.nodeValue || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                if (visible(textNode.parentElement) && /^(computing|loading|please wait)(?:\\s*[.\\u2026]*)?$/.test(text)) {
                    computingText = true;
                    break;
                }
            }
            const progressBusy = visibleProgress || computingText;
            const connection = window.__qcShinyConnection;
            const bodyText = (document.body?.innerText || '').replace(/\\s+/g, ' ').toLowerCase();
            const disconnected = connection?.status === 'disconnected' ||
                bodyText.includes('disconnected from the server');
            if (appBusy || progressBusy) {
                window.__qc_last_busy_seen = performance.now();
            }

            const fontsReady = !document.fonts || document.fonts.status === 'loaded';
            const visibleImagesReady = [...scope.querySelectorAll('img')]
                .filter(visible).every(image => image.complete && image.naturalWidth > 0);
            return !appBusy && !progressBusy && !disconnected && fontsReady && visibleImagesReady &&
                performance.now() - Math.max(
                    window.__qc_last_panel_mutation || 0,
                    window.__qc_last_busy_seen || 0,
                    connection?.lastEventAt || 0
                ) >= 1500;
        }""",
        arg=panel_id,
        timeout=timeout,
    )


def _activate_tab(page: Page, tab: Dict[str, Any]) -> None:
    """Resolve and activate the intended visible tab from its stable DOM attributes."""
    activated = page.evaluate(
        """({selector, target}) => {
            const visible = el => {
                const style = getComputedStyle(el);
                return !!(el.getClientRects().length && style.visibility !== 'hidden' && style.display !== 'none');
            };
            for (const el of document.querySelectorAll(selector)) {
                if (!visible(el)) continue;
                const text = (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
                const matches = target.href
                    ? (el.getAttribute('href') || '') === target.href
                    : target.id
                        ? el.id === target.id
                        : target.controls
                            ? el.getAttribute('aria-controls') === target.controls
                            : target.value
                                ? el.getAttribute('data-value') === target.value && text === target.text
                                : text === target.text && (el.getAttribute('role') || '') === (target.role || '');
                if (matches && text === target.text) {
                    el.scrollIntoView({block: 'center', inline: 'nearest'});
                    el.click();
                    return true;
                }
            }
            return false;
        }""",
        {"selector": TAB_SELECTOR, "target": tab},
    )
    if not activated:
        raise LookupError(f"Tab {tab.get('text', 'Unknown')!r} ({tab.get('href') or tab.get('id') or tab.get('value')}) is not visible when activation was attempted")


def _is_tab_panel_active(page: Page, tab: Dict[str, Any]) -> bool:
    """Return whether a tab's content panel is currently visible and selected."""
    panel_id = tab.get("panel_id")
    if not panel_id:
        return False
    return bool(page.evaluate(
        """panelId => {
            const panel = document.getElementById(panelId);
            if (!panel) return false;
            const style = getComputedStyle(panel);
            const visible = !!(panel.getClientRects().length && style.visibility !== 'hidden' && style.display !== 'none');
            const selected = panel.classList.contains('active') || panel.getAttribute('aria-hidden') === 'false';
            return visible && selected;
        }""",
        panel_id,
    ))


def _collect_visible_shiny_errors(page: Page, tab: Dict[str, Any] | None = None) -> List[str]:
    return page.evaluate(
        """({selector, panelId}) => {
            const panel = (panelId && document.getElementById(panelId)) ||
                [...document.querySelectorAll('.tab-pane.active, [role="tabpanel"]')].filter(el => el.getClientRects().length).at(-1) || document;
            return [...panel.querySelectorAll(selector)].filter(el =>
                el.getClientRects().length && (panel === document || el.closest('.tab-pane, [role="tabpanel"]') === panel)
            )
                .map(el => (el.innerText || '').trim()).filter(Boolean);
        }""",
        {"selector": '.shiny-output-error, .shiny-output-error-validation, .alert-danger', "panelId": (tab or {}).get("panel_id")},
    )

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
            _wait_for_shiny_content(page)
            
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
            _wait_for_shiny_content(page)
            
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
            _wait_for_shiny_content(page)
            
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
            _wait_for_shiny_content(page)
            
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
            _wait_for_shiny_content(page)
            
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
    Walk every visible Shiny tab and generate an HTML report with an embedded screenshot per tab.
    The smoke test only navigates tabs, waits for each panel to settle, and captures screenshots.
    
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
    
    started_at = datetime.now()
    run_started = time.perf_counter()
    tab_results: Dict[str, Any] = {
        "smoke_test": True,
        "url": url,
        "timestamp": started_at.isoformat(),
        "start_time": started_at.isoformat(),
        "end_time": None,
        "tabs_tested": [],
        "errors": [],
        "total_tabs": 0,
        "navigation_seconds": None,
        "status": "success",
    }
    navigation_succeeded = False

    logger.info("Launching one visible Chromium window for tab-walking smoke test...")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()
            _install_shiny_connection_monitor(page)
            try:
                navigation_started = time.perf_counter()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=SHINY_LOAD_TIMEOUT)
                    _wait_for_shiny_content(page)
                    _wait_for_shiny_connection(page)
                    navigation_succeeded = True
                except Exception as exc:
                    tab_results["errors"].append(f"Initial navigation/readiness failed: {exc}")
                    tab_results["status"] = "failed"
                    logger.exception("Initial navigation/readiness failed")
                finally:
                    tab_results["navigation_seconds"] = round(time.perf_counter() - navigation_started, 2)
                    logger.info("Initial navigation/readiness elapsed: %.2fs", tab_results["navigation_seconds"])

                if not navigation_succeeded:
                    tab_results["end_time"] = datetime.now().isoformat()
                    tab_results["elapsed_seconds"] = round(time.perf_counter() - run_started, 2)
                    report_path = generate_test_report(json.dumps([tab_results]), app_name)
                    logger.error("Walkthrough failed during startup; failure report: %s", report_path)
                    return report_path

                logger.info("Discovering and walking visible tabs in the same page...")
                try:
                    _wait_for_root_tabs(page, timeout=SHINY_READY_TIMEOUT)
                except Exception as exc:
                    logger.warning("No visible root tabs appeared within %d ms: %s", SHINY_READY_TIMEOUT, exc)
                    tab_results["warnings"] = [
                        f"No visible root tabs appeared within {SHINY_READY_TIMEOUT / 1000:g} seconds: {exc}"
                    ]
                root_tabs = _visible_tab_descriptors(page)
                known_work_items = set()
                tab_results["root_tabs"] = len(root_tabs)
                tab_results["total_tabs"] = 0
                logger.info("Initially discovered %d root tab(s)", len(root_tabs))
                if not root_tabs:
                    tab_results["status"] = "warning"
                    tab_results["warnings"] = ["No visible tabs were discovered."]

                def walk_tab(tab: Dict[str, Any], ancestors: List[Dict[str, Any]]) -> None:
                    ancestry = [*ancestors, tab]
                    work_key = (tuple(item["key"] for item in ancestors), tab["key"])
                    if work_key in known_work_items:
                        return
                    known_work_items.add(work_key)
                    tab_results["total_tabs"] += 1
                    tab_name = tab["text"]
                    tab_started = time.perf_counter()
                    remaining_ms = GLOBAL_RUN_TIMEOUT - int((time.perf_counter() - run_started) * 1000)
                    if remaining_ms <= 0:
                        message = f"Global run deadline ({GLOBAL_RUN_TIMEOUT} ms) reached before tab '{tab_name}' was visited."
                        tab_results["errors"].append(message)
                        tab_results["status"] = "failed"
                        tab_results["tabs_tested"].append({
                            "text": tab_name,
                            "key": tab["key"],
                            "parent_tabs": [ancestor["text"] for ancestor in ancestors],
                            "status": "not_run",
                            "errors": message,
                        })
                        return
                    tab_timeout = min(TAB_RENDER_TIMEOUT, remaining_ms)
                    tab_deadline = min(
                        tab_started + tab_timeout / 1000,
                        run_started + GLOBAL_RUN_TIMEOUT / 1000,
                    )
                    logger.info("Starting tab %d: %s", tab_results["total_tabs"], tab_name)
                    try:
                        for ancestor in ancestors:
                            if not _is_tab_panel_active(page, ancestor):
                                _activate_tab(page, ancestor)
                                _wait_for_tab_settle(
                                    page,
                                    timeout=_remaining_timeout_ms(tab_deadline),
                                    panel_id=ancestor.get("panel_id"),
                                )
                        _activate_tab(page, tab)
                        _wait_for_tab_settle(
                            page,
                            timeout=_remaining_timeout_ms(tab_deadline),
                            panel_id=tab.get("panel_id"),
                        )
                        tab_results["tabs_tested"].append({
                            "text": tab_name,
                            "key": tab["key"],
                            "parent_tabs": [ancestor["text"] for ancestor in ancestors],
                            "status": "success",
                            "errors": None,
                            "elapsed_seconds": round(time.perf_counter() - tab_started, 2),
                        })
                        logger.info("Tab '%s' settled (%.2fs)", tab_name, time.perf_counter() - tab_started)
                    except Exception as exc:
                        try:
                            if _shiny_connection_snapshot(page)["disconnected"]:
                                exc = RuntimeError(
                                    "Shiny server disconnected while waiting for the tab to render"
                                )
                        except Exception:
                            pass
                        tab_results["tabs_tested"].append({
                            "text": tab_name,
                            "key": tab["key"],
                            "parent_tabs": [ancestor["text"] for ancestor in ancestors],
                            "status": "error",
                            "errors": str(exc),
                            "elapsed_seconds": round(time.perf_counter() - tab_started, 2),
                        })
                        tab_results["errors"].append(f"Error testing tab '{tab_name}': {exc}")
                        tab_results["status"] = "failed"
                        logger.warning("Tab '%s' walkthrough failed: %s", tab_name, exc)
                        return

                    # Screenshot capture is required for a successful smoke-test row,
                    # but a capture failure must not stop the remaining tab walk.
                    try:
                        screenshot_started = time.perf_counter()
                        screenshot_bytes = _capture_tab_screenshot(
                            page, tab_name, tab.get("panel_id"), tab_deadline
                        )
                        tab_results["tabs_tested"][-1]["screenshot_base64"] = base64.b64encode(
                            screenshot_bytes
                        ).decode("ascii")
                        logger.info(
                            "Screenshot captured for tab '%s' (%.2fs)",
                            tab_name,
                            time.perf_counter() - screenshot_started,
                        )
                    except Exception as exc:
                        row = tab_results["tabs_tested"][-1]
                        row["status"] = "screenshot_failed"
                        row["errors"] = f"Screenshot capture failed: {exc}"
                        tab_results["errors"].append(f"Tab '{tab_name}' screenshot capture failed: {exc}")
                        tab_results["status"] = "failed"
                        logger.warning("Screenshot capture failed for tab '%s': %s", tab_name, exc)
                    tab_results["tabs_tested"][-1]["elapsed_seconds"] = round(
                        time.perf_counter() - tab_started, 2
                    )

                    # Discover only immediate children of the panel just completed.
                    child_tabs = _visible_tab_descriptors(page, parent_panel=tab)
                    for child_tab in child_tabs:
                        child_key = (tuple(item["key"] for item in ancestry), child_tab["key"])
                        if child_key not in known_work_items:
                            walk_tab(child_tab, ancestry)

                for root_tab in root_tabs:
                    walk_tab(root_tab, [])

                if tab_results["status"] == "success" and any(
                    row.get("status") == "not_run" for row in tab_results["tabs_tested"]
                ):
                    tab_results["status"] = "failed"
                tab_results["end_time"] = datetime.now().isoformat()
                tab_results["elapsed_seconds"] = round(time.perf_counter() - run_started, 2)

                logger.info("Generating report; %d/%d tabs visited", len(tab_results["tabs_tested"]), tab_results["total_tabs"])
                report_path = generate_test_report(json.dumps([tab_results]), app_name)
                logger.info("Walkthrough elapsed: %.2fs", time.perf_counter() - run_started)
                logger.info("✓ Comprehensive test suite completed. Report: %s", report_path)
                return report_path
            finally:
                browser.close()
    except Exception as exc:
        logger.exception("Comprehensive Shiny test suite failed")
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
            _wait_for_shiny_content(page)
            
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

        smoke_results = next(
            (result for result in results_data if result.get("smoke_test")),
            None,
        ) if isinstance(results_data, list) else None
        if smoke_results is not None:
            tabs = smoke_results.get("tabs_tested", [])
            tab_statuses = {tab.get("status") for tab in tabs}
            if smoke_results.get("status") == "failed" or tab_statuses.intersection(
                {"error", "screenshot_failed", "not_run"}
            ):
                overall_status = "failed"
            elif smoke_results.get("status") == "warning":
                overall_status = "warning"
            else:
                overall_status = "success"
            overall_status_class = f"status-{overall_status}"
            html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Tab-Walking Smoke Test - {html.escape(app_name)}</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{ margin: 0; padding: 28px; color: #172b4d; background: #f3f6fa; font-family: Segoe UI, Arial, sans-serif; }}
        main {{ max-width: 1200px; margin: 0 auto; padding: 28px; background: white; border-radius: 14px; box-shadow: 0 10px 32px #19335418; }}
        h1 {{ margin: 0 0 8px; }} h2 {{ margin-top: 32px; }}
        .meta {{ color: #52647a; margin: 6px 0; }}
        .status-success {{ color: #16834b; font-weight: 700; }} .status-warning {{ color: #a35e00; font-weight: 700; }} .status-failed {{ color: #b42318; font-weight: 700; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
        th, td {{ padding: 11px 12px; border-bottom: 1px solid #e5eaf0; text-align: left; vertical-align: top; }}
        th {{ color: #344860; background: #f1f5f9; }}
        .card {{ margin: 20px 0; padding: 18px; border: 1px solid #e1e8f0; border-radius: 10px; background: #fbfcfe; }}
        .card img {{ display: block; width: 100%; height: auto; margin-top: 12px; border: 1px solid #d5deea; border-radius: 6px; }}
        .error {{ color: #9f1d16; white-space: pre-wrap; }} .empty {{ color: #66788a; font-style: italic; }}
        @media print {{ body {{ padding: 0; background: white; }} main {{ box-shadow: none; }} .card {{ break-inside: avoid; }} }}
    </style>
</head>
<body>
<main>
    <h1>Tab-Walking Smoke Test</h1>
    <p class="meta"><strong>App:</strong> {html.escape(app_name)}</p>
    <p class="meta"><strong>URL:</strong> {html.escape(str(smoke_results.get('url') or 'Unavailable'))}</p>
    <p class="meta"><strong>Test date:</strong> {html.escape(str(smoke_results.get('timestamp') or 'Unavailable'))}</p>
    <p class="meta"><strong>Overall status:</strong> <span class="{overall_status_class}">{overall_status.upper()}</span></p>
    <p class="meta"><strong>Tabs:</strong> {len(tabs)} visited of {smoke_results.get('total_tabs', len(tabs))} discovered; <strong>Root tabs:</strong> {smoke_results.get('root_tabs', 0)}</p>
    <p class="meta"><strong>Start Time:</strong> {html.escape(str(smoke_results.get('start_time') or smoke_results.get('timestamp') or 'Unavailable'))}</p>
    <p class="meta"><strong>End Time:</strong> {html.escape(str(smoke_results.get('end_time') or 'Unavailable'))}</p>
    <p class="meta"><strong>Total Time for Testing:</strong> {html.escape(str(smoke_results.get('elapsed_seconds', 'Unavailable')))} s</p>
    <p class="meta"><strong>Initial navigation:</strong> {html.escape(str(smoke_results.get('navigation_seconds', 'Unavailable')))} s</p>
    <h2>Tab Walk Results</h2>
    <table><thead><tr><th>Tab</th><th>Status</th><th>Elapsed (s)</th><th>Details</th></tr></thead><tbody>
"""
            if tabs:
                for tab in tabs:
                    tab_name = html.escape(str(tab.get("text") or "Unknown"))
                    tab_status = str(tab.get("status") or "unknown")
                    tab_status_class = "status-success" if tab_status == "success" else (
                        "status-warning" if tab_status == "warning" else "status-failed"
                    )
                    parent_tabs = tab.get("parent_tabs", [])
                    path = " / ".join(html.escape(str(name)) for name in [*parent_tabs, tab.get("text") or "Unknown"])
                    details = html.escape(str(tab.get("errors") or "—"))
                    html_content += (
                        f"<tr><td>{path}</td><td><span class=\"{tab_status_class}\">{html.escape(tab_status.upper())}</span></td>"
                        f"<td>{html.escape(str(tab.get('elapsed_seconds', '—')))}</td><td class=\"error\">{details}</td></tr>"
                    )
            else:
                html_content += '<tr><td colspan="4" class="empty">No tabs were visited.</td></tr>'
            html_content += "</tbody></table><h2>Tab Screenshots</h2>"
            screenshots_added = 0
            for tab in tabs:
                image_data = tab.get("screenshot_base64")
                tab_name = html.escape(str(tab.get("text") or "Unknown"))
                if image_data:
                    # The payload is generated by base64-encoding PNG bytes. Validate
                    # before embedding so report input cannot introduce markup.
                    try:
                        base64.b64decode(image_data, validate=True)
                    except (ValueError, TypeError):
                        continue
                    parent_tabs = tab.get("parent_tabs", [])
                    path = " / ".join(html.escape(str(name)) for name in [*parent_tabs, tab.get("text") or "Unknown"])
                    html_content += (
                        f'<section class="card"><h3>{path}</h3>'
                        f'<img src="data:image/png;base64,{image_data}" alt="Screenshot of {tab_name}"></section>'
                    )
                    screenshots_added += 1
            if screenshots_added == 0:
                html_content += '<p class="empty">No tab screenshots were captured.</p>'
            for error in smoke_results.get("errors", []):
                html_content += f'<p class="error">{html.escape(str(error))}</p>'
            for warning in smoke_results.get("warnings", []):
                html_content += f'<p class="meta">{html.escape(str(warning))}</p>'
            html_content += "</main></body></html>"

            with open(filename, "w", encoding="utf-8") as report_file:
                report_file.write(html_content)
            logger.info("✓ Tab-walking smoke report generated: %s", filename)
            return f"Report generated: {filename}"

        overall_status = "success"
        if main_page_results and main_page_results.get("status") == "failed":
            overall_status = "failed"
        elif main_page_results and main_page_results.get("status") == "warning":
            overall_status = "warning"
        tab_statuses = {tab.get("status") for tab in (tabs_results or {}).get("tabs_tested", [])}
        if tab_statuses.intersection({"error", "clicked_with_errors"}):
            overall_status = "failed"
        elif "warning" in tab_statuses and overall_status == "success":
            overall_status = "warning"
        status_class = {"success": "status-success", "warning": "status-warning", "failed": "status-failed"}[overall_status]
        
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
                        <div class="metadata">{html.escape(app_name)}</div>
                    </div>
                    <div class="info-box">
                        <strong>Test Date:</strong>
                        <div class="metadata">{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>
                    </div>
                    <div class="info-box">
                        <strong>Overall Status:</strong>
                        <div class="metadata"><span class="{status_class}">{overall_status.upper()}</span></div>
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
                                <th>Checklist</th>
                                <th>Elapsed (s)</th>
                                <th>Error Details</th>
                            </tr>
                        </thead>
                        <tbody>
            """
            if tabs_results.get("tabs_tested"):
                for tab in tabs_results["tabs_tested"]:
                    status_class = "status-success" if tab.get("status") == "success" else (
                        "status-warning" if tab.get("status") in {"warning", "not_run"} else "status-failed"
                    )
                    status_icon = "✓" if tab.get("status") == "success" else ("⚠" if tab.get("status") in {"warning", "not_run"} else "✗")
                    errors = html.escape(str(tab.get("errors") or "None"))
                    if tab.get("warnings"):
                        warnings = html.escape(" | ".join(tab["warnings"]))
                        errors += f"<br><strong>Warnings:</strong> {warnings}"
                    html_content += f"""
                            <tr>
                                <td><strong>{tab.get('text', 'Unknown')}</strong></td>
                                <td><span class="{status_class}">{status_icon} {tab.get('status', 'unknown').upper()}</span></td>
                                <td>{tab.get('checks_completed', 0)}/{tab.get('checks_total', 6)} checks</td>
                                <td>{tab.get('elapsed_seconds', '—')}</td>
                                <td>{errors}</td>
                            </tr>
                    """
            else:
                html_content += '<tr><td colspan="5" class="no-data">No tab data available</td></tr>'
            
            html_content += """
                        </tbody>
                    </table>
                    <p class="metadata"><strong>Discovered:</strong> {} &nbsp; <strong>Visited:</strong> {} &nbsp; <strong>Root tabs:</strong> {}</p>
                </div>
            """.format(
                tabs_results.get("total_tabs", len(tabs_results.get("tabs_tested", []))),
                sum(tab.get("status") != "not_run" for tab in tabs_results.get("tabs_tested", [])),
                tabs_results.get("root_tabs", "—"),
            )

            if tabs_results.get("controls_by_tab"):
                html_content += """
                <div class="section">
                    <h2>🧭 Per-Tab Visible Control Inventory</h2>
                    <p class="metadata">Controls were inspected in place; input values were not changed and buttons were not activated.</p>
                    <table>
                        <thead><tr><th>Tab</th><th>Visible Controls</th><th>Types</th><th>Outputs</th></tr></thead>
                        <tbody>
                """
                for tab_inventory in tabs_results["controls_by_tab"]:
                    types_summary = ", ".join(
                        f"{html.escape(str(kind))}: {count}"
                        for kind, count in tab_inventory.get("counts", {}).items()
                    ) or "None"
                    outputs_summary = ", ".join(
                        f"{html.escape(str(kind))}: {count}"
                        for kind, count in tab_inventory.get("outputs", {}).items()
                    ) or "None"
                    html_content += (
                        f"<tr><td>{html.escape(str(tab_inventory.get('tab', 'Unknown')))}</td>"
                        f"<td>{tab_inventory.get('total', 0)}</td>"
                        f"<td>{types_summary}</td><td>{outputs_summary}</td></tr>"
                    )
                html_content += "</tbody></table></div>"
                html_content += """
                <div class="section">
                    <h2>Visible Control Details</h2>
                    <table>
                        <thead><tr><th>Tab</th><th>Kind</th><th>Label</th><th>Element ID</th><th>State (redacted)</th></tr></thead>
                        <tbody>
                """
                for tab_inventory in tabs_results["controls_by_tab"]:
                    for control in tab_inventory.get("controls", []):
                        label = html.escape(str(control.get("label") or ""))
                        control_id = html.escape(str(control.get("id") or ""))
                        states = ["Disabled" if control.get("disabled") else "Enabled"]
                        if control.get("value_present") is not None:
                            states.append("Value present" if control["value_present"] else "Empty")
                        if control.get("checked") is not None:
                            states.append("Checked" if control["checked"] else "Unchecked")
                        if control.get("selected_option_count") is not None:
                            states.append(f"{control['selected_option_count']} selected")
                        if control.get("required"):
                            states.append("Required")
                        if control.get("valid") is False:
                            states.append("Invalid")
                        html_content += (
                            f"<tr><td>{html.escape(str(tab_inventory.get('tab', 'Unknown')))}</td>"
                            f"<td>{html.escape(str(control.get('type', 'unknown')))}</td>"
                            f"<td>{label or '—'}</td><td>{control_id or '—'}</td>"
                            f"<td>{html.escape(', '.join(states))}</td></tr>"
                        )
                html_content += "</tbody></table></div>"
        
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
                    status_class = "status-success" if slider.get("status") in {"tested", "inspected"} else "status-failed"
                    status_icon = "✓" if slider.get("status") in {"tested", "inspected"} else "✗"
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
                    status_class = "status-success" if radio.get("status") in {"tested", "inspected"} else "status-failed"
                    status_icon = "✓" if radio.get("status") in {"tested", "inspected"} else "✗"
                    checked = "Not changed" if radio.get("was_checked") is None else ("✓ Yes" if radio.get("was_checked") else "✗ No")
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
                                <td><strong>Page Title</strong></td>
                                <td>{html.escape(str(checks.get('page_title') or 'Unavailable'))}</td>
                            </tr>
                            <tr>
                                <td><strong>HTTP Status</strong></td>
                                <td>{html.escape(str(checks.get('http_status') or 'Unavailable'))}</td>
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
