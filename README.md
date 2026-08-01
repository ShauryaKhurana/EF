# COO Agent

COO Agent is a hackathon prototype that ingests Slack and Gmail messages, extracts structured status updates using an LLM, synthesizes an executive briefing, and posts the result back to Slack. The project is designed as a modular Python pipeline for rapid experimentation with conversational data and summary generation.

## Setup

1. Create a Python virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your credentials.
4. Run the orchestrator:
   ```bash
   python orchestrator.py
   ```

## Files

- `ingestion.py`: load Slack and Gmail messages and normalize them.
- `extraction.py`: call an LLM to convert raw messages into structured status items.
- `synthesis.py`: produce a COO briefing from structured status items.
- `output.py`: post the briefing to Slack or write it to Google Docs.
- `orchestrator.py`: chain the pipeline end-to-end.
- `schema.py`: shared data model and JSON schema for status items.
