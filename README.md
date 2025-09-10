# paperless_owner_tag_sync

Syncs paperless-ngx document owners to owner specific tags. Useful when automatically tagging through incoming subdirectories to support tagging when uploading documents from paperless-mobile, which only sets owners but doesn't support tags.

## Instructions
Script can run in webhook, schedule or hybrid mode. Schedule will regularly update all documents. Webhook starts a server that can receive webhooks from paperless every time a document is added. Hybrid is a combination of both.

### Environment variables
The script uses these environment variables, with defaults given below:

    PAPERLESS_URL = http://localhost:8000
    PAPERLESS_TOKEN = 
    OWNER_TAG_PREFIX = owner:
    OWNER_MAPPING_FILE = owner_tag_mapping.json

    SYNC_MODE = webhook  # webhook, hybrid, schedule
    WEBHOOK_HOST = '0.0.0.0'
    WEBHOOK_PORT = 5000
    SYNC_INTERVAL_HOURS = 6

### Owner-Tag Mappings
By default, the script adds OWNER_TAG_PREFIX in front of the owner, e.g. John --> owner:John. You can specify owner --> tag mappings in OWNER_MAPPING_FILE, which is of the form {'owner1': 'tag1', 'owner2': 'tag2}. This will override the default prefix-based mapping and require that the tags used in that file already exist in paperless.

### Webhooks
To use webhooks, you need to setup a workflow in paperless:

    Trigger: Document added
    Action: Webhook
    Webhook URL: http://localhost:5000/webhook/document (or as appropriate)
    Use parameters for webhook: Checked
    Send parameters as JSON: Checked
    Add a parameter with url / {doc_url}
    Include document: Unchecked

## Installation
Create venv in script directory:

    python -m venv .
    pip install -r requirements.txt

Adapt systemd service and environment config as needed. Copy systemd service to /etc/systemd/system/ (or to some user systemd directory if running as user). Finally:

    sudo systemctl daemon-reload
    sudo systemctl start paperless-owner-sync
    sudo systemctl enable paperless-owner-sync