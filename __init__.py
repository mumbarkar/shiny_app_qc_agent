"""
Shiny App QC Agent
Automated testing agent for Shiny applications using smolagents and Playwright
"""

from .tool_set import (
    navigate_to_shiny_app,
    find_all_tabs_and_sections,
    test_shiny_page,
    test_interactive_elements,
    generate_test_report,
)

from .shiny_qc_agent import create_shiny_testing_agent

__version__ = "0.1.0"
__all__ = [
    "navigate_to_shiny_app",
    "find_all_tabs_and_sections",
    "test_shiny_page",
    "test_interactive_elements",
    "generate_test_report",
    "create_shiny_testing_agent",
]