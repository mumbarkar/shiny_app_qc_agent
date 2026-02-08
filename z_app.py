from dotenv import load_dotenv
from tool_set import run_comprehensive_shiny_tests

load_dotenv()

# Test the comprehensive test suite
test_url = "https://gallery.shinyapps.io/006-tabsets/"
app_name = "006-tabsets"

print("\n" + "="*60)
print("Starting Comprehensive Shiny App QC Test")
print("="*60 + "\n")

try:
    report_path = run_comprehensive_shiny_tests(test_url, app_name)
    print(f"\n✓ SUCCESS: Report generated at {report_path}")
except Exception as e:
    print(f"\n✗ ERROR: {str(e)}")
    import traceback
    traceback.print_exc()
