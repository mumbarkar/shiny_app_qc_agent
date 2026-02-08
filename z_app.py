from dotenv import load_dotenv
from tool_set import find_all_tabs_and_sections, generate_test_report, navigate_to_shiny_app, test_interactive_elements, test_shiny_page
from shiny_qc_agent import create_shiny_testing_agent

load_dotenv()  # Load environment variables from .env file

agent = create_shiny_testing_agent()
test_url = "https://gallery.shinyapps.io/006-tabsets/?_gl=1*16xq47f*_ga*MTE1ODQyMTc1Mi4xNzA5MzQ3MDMx*_ga_2C0WZ1JHG0*czE3NzA1Njc5NDgkbzE4JGcxJHQxNzcwNTY3OTk4JGoxMCRsMCRoMA.."
result = agent.run(f"""
    Please test the Shiny app at {test_url}:
    1. First discover all tabs and navigation elements
    """)
print(result)

"""
2. Test each major tab/section for errors
    3. Check interactive elements
    4. Generate a comprehensive test report
"""