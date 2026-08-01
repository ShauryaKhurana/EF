from ingestion import ingest
from extraction import extract_status_items
from synthesis import synthesize_briefing
from output import output_briefing


def run_pipeline(slack_channel: str, google_doc_id: str | None = None) -> str:
    normalized_messages = ingest()
    status_items = extract_status_items(normalized_messages)
    briefing = synthesize_briefing(status_items)
    doc_id = output_briefing(briefing, slack_channel, google_doc_id)
    return doc_id


if __name__ == "__main__":
    print("Running COO Agent orchestration pipeline...")
    result_doc_id = run_pipeline(slack_channel="#general")
    print(f"Pipeline complete. Google Doc ID: {result_doc_id}")
