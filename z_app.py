import argparse
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from tool_set import run_comprehensive_shiny_tests

load_dotenv()

parser = argparse.ArgumentParser(description="Run a tab-by-tab Shiny smoke test and save screenshots.")
parser.add_argument(
    "--url",
    default="https://rinpharma.shinyapps.io/nest_early-dev_stable/",
    help="Shiny app URL (default: current early-development app)",
)
parser.add_argument(
    "--app-name",
    default="Teal Early Development App",
    help="Display name used in the generated report",
)
args = parser.parse_args()

print("\n" + "="*60)
print("Starting Comprehensive Shiny App QC Test")
print("="*60 + "\n")

try:
    report_result = run_comprehensive_shiny_tests(args.url, args.app_name)
    report_path = report_result.removeprefix("Report generated: ").strip()
    report_file = Path(report_path)
    if not report_file.is_file():
        raise FileNotFoundError(f"Generated report not found: {report_file}")
    report = BeautifulSoup(report_file.read_text(encoding="utf-8"), "html.parser")
    status_element = report.select_one(".status-success, .status-warning, .status-failed")
    if status_element is None:
        raise ValueError(f"Could not read overall status from report: {report_file}")
    status = status_element.get_text(strip=True).upper()
    print(f"\nReport: {report_file}\nOverall status: {status}")
    if status != "SUCCESS":
        sys.exit(1)
except Exception as e:
    print(f"\n✗ ERROR: {str(e)}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
