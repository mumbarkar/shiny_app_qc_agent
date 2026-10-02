# Shiny App QC Agent

## Demo Run

Use the project environment and verify the tests before a rehearsal:

```sh
uv sync
uv run python -m unittest discover -v
```

Run the configured early-development app in a visible Chromium window:

```sh
uv run z_app.py
```

To run the stable exploratory app instead:

```sh
uv run z_app.py --url https://rinpharma.shinyapps.io/nest_exploratory_stable/ --app-name "Teal Exploratory App"
```

The command exits successfully only when the generated HTML report records an overall `SUCCESS`. A failed report is still saved for review, but the command exits with a non-zero status. Each report includes run start/end times, elapsed time, per-tab outcomes, and embedded screenshots.

## Agent Configuration

The model-backed agent in `shiny_qc_agent.py` requires `OPENAI_API_KEY` in the environment or `.env` file. Do not commit `.env` or share its contents. The direct `z_app.py` smoke-test command does not call the language model.

## Conference Rehearsal

Run both the test suite and the exact app URL planned for the presentation during the final rehearsal. Keep the generated report available as a fallback, and verify the app connection and screenshots again on the day of the demo; live Shiny hosting and network conditions can change after a successful rehearsal.
