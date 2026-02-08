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
from tool_set import (
    navigate_to_shiny_app,
    find_all_tabs_and_sections,
    test_tabs_navigation,
    test_shiny_page,
    test_sliders,
    test_radio_buttons,
    generate_test_report,
    run_comprehensive_shiny_tests
)

# Load environment variables
load_dotenv()

# Initialize the agent with tools
def create_shiny_testing_agent():
    """Create and return a Shiny app testing agent"""
    from smolagents import LiteLLMModel
    
    # Use LiteLLM for better compatibility
    model = LiteLLMModel(
        model_id="gpt-4o-mini",
        api_key=os.getenv("OPENAI_API_KEY")
    )
    
    tools = [
        navigate_to_shiny_app,
        find_all_tabs_and_sections,
        test_tabs_navigation,
        test_shiny_page,
        test_sliders,
        test_radio_buttons,
        generate_test_report,
        run_comprehensive_shiny_tests
    ]
    
    agent = ToolCallingAgent(
        tools=tools,
        model=model,
        max_steps=5
    )
    
    return agent


if __name__ == "__main__":
    # Example usage
    agent = create_shiny_testing_agent()
    
    test_url = "https://vnijs.shinyapps.io/radiant/?SSUID=a5f80319cb"
    
    # Run comprehensive test
    result = agent.run(f"""
    Please test the Shiny app at {test_url}:
    1. First discover all tabs and navigation elements
    2. Test each major tab/section for errors
    3. Check interactive elements
    4. Generate a comprehensive test report
    """)
    
    print(result)