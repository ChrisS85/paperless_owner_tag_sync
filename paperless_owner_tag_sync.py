#!/usr/bin/env python3
"""
Event-Driven Paperless-ngx Owner-to-Tag Sync

This script can run in multiple modes:
1. Webhook server - receives notifications from Paperless webhooks
2. Periodic sync - runs on a schedule
3. Hybrid - both webhook and periodic sync
"""

import requests
import logging
import os
import json
import time
import re
from typing import List, Dict, Optional
from flask import Flask, request, jsonify
import threading
import schedule


class PaperlessSync:
    def __init__(self, base_url: str, token: str, tag_prefix: str = "owner:", 
                 owner_tag_mapping: Dict[str, str] = None):
        """Initialize the Paperless sync client."""
        self.base_url = base_url.rstrip('/')
        self.headers = {
            'Authorization': f'Token {token}',
            'Content-Type': 'application/json'
        }
        self.tag_prefix = tag_prefix
        self.owner_tag_mapping = owner_tag_mapping or {}
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('paperless_sync.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def extract_document_id_from_url(self, url: str) -> Optional[int]:
        """
        Extract document ID from Paperless document URL.
        Example: https://paperless.example.com/documents/55/ -> 55
        """
        try:
            # Match the document ID in the URL
            match = re.search(r'/documents/(\d+)/?$', url)
            if match:
                return int(match.group(1))
            
            # Alternative pattern if the URL format is different
            match = re.search(r'document_id=(\d+)', url)
            if match:
                return int(match.group(1))
                
            self.logger.warning(f"Could not extract document ID from URL: {url}")
            return None
            
        except (ValueError, TypeError) as e:
            self.logger.error(f"Error extracting document ID from URL {url}: {e}")
            return None

    def get_users(self) -> Dict[int, str]:
        """Fetch all users from Paperless."""
        try:
            response = requests.get(f'{self.base_url}/api/users/', headers=self.headers)
            response.raise_for_status()
            
            users = {}
            for user in response.json()['results']:
                users[user['id']] = user['username']
            
            return users
        except requests.RequestException as e:
            self.logger.error(f"Failed to fetch users: {e}")
            return {}

    def get_tags(self) -> Dict[str, int]:
        """Fetch all tags from Paperless."""
        try:
            response = requests.get(f'{self.base_url}/api/tags/', headers=self.headers)
            response.raise_for_status()
            
            tags = {}
            for tag in response.json()['results']:
                tags[tag['name']] = tag['id']
            
            return tags
        except requests.RequestException as e:
            self.logger.error(f"Failed to fetch tags: {e}")
            return {}

    def create_tag(self, tag_name: str, color: str = "#007bff") -> Optional[int]:
        """Create a new tag in Paperless."""
        try:
            data = {
                'name': tag_name,
                'color': color,
                'is_inbox_tag': False
            }
            
            response = requests.post(f'{self.base_url}/api/tags/', 
                                   json=data, headers=self.headers)
            response.raise_for_status()
            
            tag_id = response.json()['id']
            self.logger.info(f"Created tag '{tag_name}' with ID {tag_id}")
            return tag_id
        except requests.RequestException as e:
            self.logger.error(f"Failed to create tag '{tag_name}': {e}")
            return None

    def get_document(self, document_id: int) -> Optional[Dict]:
        """Fetch a specific document by ID."""
        try:
            response = requests.get(f'{self.base_url}/api/documents/{document_id}/', 
                                  headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            self.logger.error(f"Failed to fetch document {document_id}: {e}")
            return None

    def get_all_documents(self) -> List[Dict]:
        """Fetch all documents from Paperless."""
        documents = []
        next_url = f'{self.base_url}/api/documents/'
        
        try:
            while next_url:
                response = requests.get(next_url, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                
                documents.extend(data['results'])
                next_url = data.get('next')
                
                # Add small delay to avoid overwhelming the API
                time.sleep(0.1)
                
            return documents
        except requests.RequestException as e:
            self.logger.error(f"Failed to fetch documents: {e}")
            return []

    def update_document_tags(self, document_id: int, tag_ids: List[int]) -> bool:
        """Update tags for a specific document."""
        try:
            data = {'tags': tag_ids}
            
            response = requests.patch(f'{self.base_url}/api/documents/{document_id}/', 
                                    json=data, headers=self.headers)
            response.raise_for_status()
            
            return True
        except requests.RequestException as e:
            self.logger.error(f"Failed to update document {document_id} tags: {e}")
            return False

    def get_owner_tag_name(self, username: str) -> str:
        """Get the tag name for a given username, using mapping or default prefix."""
        if username in self.owner_tag_mapping:
            return self.owner_tag_mapping[username]
        else:
            return f"{self.tag_prefix}{username}"

    def sync_document_owner_tag(self, document_id: int) -> bool:
        """Sync owner tag for a single document."""
        self.logger.info(f"Syncing owner tag for document {document_id}")
        
        # Get document details
        doc = self.get_document(document_id)
        if not doc:
            self.logger.error(f"Could not fetch document {document_id}")
            return False
        
        # Get users and tags
        users = self.get_users()
        tags = self.get_tags()
        
        doc_title = doc['title']
        owner_id = doc.get('owner')
        current_tag_ids = doc.get('tags', [])
        
        if not owner_id or owner_id not in users:
            self.logger.info(f"Document {document_id} has no valid owner, skipping")
            return True
        
        username = users[owner_id]
        owner_tag_name = self.get_owner_tag_name(username)
        
        # Check if owner tag exists
        if owner_tag_name not in tags:
            # If it's a custom mapped tag, don't create it - log as missing
            if username in self.owner_tag_mapping:
                self.logger.warning(f"Custom mapped tag '{owner_tag_name}' for user '{username}' does not exist")
                return False
            else:
                # Create auto-generated tag
                tag_id = self.create_tag(owner_tag_name)
                if tag_id:
                    tags[owner_tag_name] = tag_id
                else:
                    return False
        
        owner_tag_id = tags[owner_tag_name]
        
        # Check if document already has the owner tag
        if owner_tag_id in current_tag_ids:
            self.logger.info(f"Document {document_id} already has owner tag '{owner_tag_name}'")
            return True
        
        # Add owner tag to existing tags
        new_tag_ids = current_tag_ids + [owner_tag_id]
        
        # Update document
        if self.update_document_tags(document_id, new_tag_ids):
            self.logger.info(f"Added owner tag '{owner_tag_name}' to document '{doc_title}' (ID: {document_id})")
            return True
        else:
            return False

    def full_sync(self) -> Dict[str, int]:
        """Sync owner tags for all documents in Paperless."""
        self.logger.info("Starting full sync of all documents")
        
        # Get all documents, users, and tags
        documents = self.get_all_documents()
        users = self.get_users()
        tags = self.get_tags()
        
        if not documents:
            self.logger.error("No documents found or failed to fetch documents")
            return {'total': 0, 'processed': 0, 'succeeded': 0, 'failed': 0}
        
        stats = {
            'total': len(documents),
            'processed': 0,
            'succeeded': 0,
            'failed': 0
        }
        
        self.logger.info(f"Found {stats['total']} documents to process")
        
        for doc in documents:
            document_id = doc['id']
            doc_title = doc['title']
            owner_id = doc.get('owner')
            current_tag_ids = doc.get('tags', [])
            
            stats['processed'] += 1
            
            if not owner_id or owner_id not in users:
                self.logger.debug(f"Document '{doc_title}' (ID: {document_id}) has no valid owner, skipping")
                continue
            
            username = users[owner_id]
            owner_tag_name = self.get_owner_tag_name(username)
            
            # Check if owner tag exists, create if needed (for auto-generated tags only)
            if owner_tag_name not in tags:
                if username in self.owner_tag_mapping:
                    self.logger.warning(f"Custom mapped tag '{owner_tag_name}' for user '{username}' does not exist, skipping document {document_id}")
                    stats['failed'] += 1
                    continue
                else:
                    tag_id = self.create_tag(owner_tag_name)
                    if tag_id:
                        tags[owner_tag_name] = tag_id
                    else:
                        stats['failed'] += 1
                        continue
            
            owner_tag_id = tags[owner_tag_name]
            
            # Check if document already has the owner tag
            if owner_tag_id in current_tag_ids:
                self.logger.debug(f"Document '{doc_title}' (ID: {document_id}) already has owner tag '{owner_tag_name}'")
                stats['succeeded'] += 1
                continue
            
            # Add owner tag to existing tags
            new_tag_ids = current_tag_ids + [owner_tag_id]
            
            # Update document
            if self.update_document_tags(document_id, new_tag_ids):
                self.logger.info(f"Added owner tag '{owner_tag_name}' to document '{doc_title}' (ID: {document_id})")
                stats['succeeded'] += 1
            else:
                self.logger.error(f"Failed to add owner tag to document '{doc_title}' (ID: {document_id})")
                stats['failed'] += 1
            
            # Add small delay to avoid overwhelming the API
            time.sleep(0.1)
        
        self.logger.info(f"Full sync completed: {stats['succeeded']} succeeded, {stats['failed']} failed out of {stats['total']} total documents")
        return stats


class WebhookServer:
    def __init__(self, sync_client: PaperlessSync, host: str = '0.0.0.0', port: int = 5000):
        """Initialize webhook server."""
        self.sync_client = sync_client
        self.host = host
        self.port = port
        self.app = Flask(__name__)
        self.setup_routes()
        
        # Setup logging for Flask
        logging.getLogger('werkzeug').setLevel(logging.WARNING)

    def setup_routes(self):
        """Setup Flask routes for webhooks."""
        
        @self.app.route('/webhook/document', methods=['POST'])
        def document_webhook():
            try:
                data = request.get_json()
                
                if not data:
                    return jsonify({'error': 'No JSON data received'}), 400
                
                # Extract document URL from webhook payload
                document_url = data.get('url')
                if not document_url:
                    self.sync_client.logger.warning("Webhook received without URL field")
                    return jsonify({'status': 'ignored', 'message': 'No URL in payload'}), 200
                
                # Extract document ID from URL
                document_id = self.sync_client.extract_document_id_from_url(document_url)
                if not document_id:
                    self.sync_client.logger.error(f"Could not extract document ID from URL: {document_url}")
                    return jsonify({'error': 'Invalid document URL'}), 400
                
                self.sync_client.logger.info(f"Received webhook for document {document_id} (URL: {document_url})")
                
                # Small delay to ensure document is fully processed
                time.sleep(2)
                success = self.sync_client.sync_document_owner_tag(document_id)
                
                if success:
                    return jsonify({'status': 'success', 'message': 'Document processed', 'document_id': document_id}), 200
                else:
                    return jsonify({'status': 'error', 'message': 'Failed to process document', 'document_id': document_id}), 500
                    
            except Exception as e:
                self.sync_client.logger.error(f"Webhook error: {e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/health', methods=['GET'])
        def health_check():
            return jsonify({'status': 'healthy', 'service': 'paperless-sync'}), 200

    def run(self):
        """Run the webhook server."""
        self.sync_client.logger.info(f"Starting webhook server on {self.host}:{self.port}")
        self.app.run(host=self.host, port=self.port, debug=False)


def load_owner_tag_mapping(config_file: str = "owner_tag_mapping.json") -> Dict[str, str]:
    """Load owner-to-tag mapping from a JSON configuration file."""
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                mapping = json.load(f)
            print(f"Loaded owner-to-tag mapping from {config_file}: {mapping}")
            return mapping
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading mapping file {config_file}: {e}")
            return {}
    else:
        # Create example mapping file
        example_mapping = {
            "john": "John-Folder",
            "jane": "Jane-Documents", 
            "admin": "Admin-Files"
        }
        try:
            with open(config_file, 'w') as f:
                json.dump(example_mapping, f, indent=2)
            print(f"Created example mapping file at {config_file}")
        except IOError as e:
            print(f"Could not create example file: {e}")
        return {}


def main():
    # Configuration
    PAPERLESS_URL = os.getenv('PAPERLESS_URL', 'http://localhost:8000')
    PAPERLESS_TOKEN = os.getenv('PAPERLESS_TOKEN', '')
    TAG_PREFIX = os.getenv('OWNER_TAG_PREFIX', 'owner:')
    MAPPING_FILE = os.getenv('OWNER_MAPPING_FILE', 'owner_tag_mapping.json')
    
    # Mode configuration
    MODE = os.getenv('SYNC_MODE', 'webhook')  # webhook, hybrid, schedule
    WEBHOOK_HOST = os.getenv('WEBHOOK_HOST', '0.0.0.0')
    WEBHOOK_PORT = int(os.getenv('WEBHOOK_PORT', '5000'))
    SYNC_INTERVAL_HOURS = int(os.getenv('SYNC_INTERVAL_HOURS', '6'))
    
    if not PAPERLESS_TOKEN:
        print("Error: PAPERLESS_TOKEN environment variable is required")
        return
    
    # Load owner-to-tag mapping
    owner_tag_mapping = load_owner_tag_mapping(MAPPING_FILE)
    
    # Initialize sync client
    sync_client = PaperlessSync(PAPERLESS_URL, PAPERLESS_TOKEN, TAG_PREFIX, owner_tag_mapping)
    
    # Test connection
    try:
        response = requests.get(f'{PAPERLESS_URL}/api/users/', 
                              headers=sync_client.headers)
        response.raise_for_status()
        print(f"Successfully connected to Paperless at {PAPERLESS_URL}")
    except requests.RequestException as e:
        print(f"Failed to connect to Paperless: {e}")
        return
    
    print(f"Starting in {MODE} mode...")
    
    if MODE == 'webhook':
        # Run webhook server only
        webhook_server = WebhookServer(sync_client, WEBHOOK_HOST, WEBHOOK_PORT)
        webhook_server.run()
        
    elif MODE == 'hybrid':
        # Run both webhook server and periodic sync
        def run_webhook():
            webhook_server = WebhookServer(sync_client, WEBHOOK_HOST, WEBHOOK_PORT)
            webhook_server.run()
        
        def run_scheduler():
            # Periodic full sync as backup
            def full_sync():
                sync_client.logger.info("Running periodic full sync...")
                sync_client.full_sync()
            
            schedule.every(SYNC_INTERVAL_HOURS).hours.do(full_sync)
            
            while True:
                schedule.run_pending()
                time.sleep(60)
        
        # Start webhook in separate thread
        webhook_thread = threading.Thread(target=run_webhook)
        webhook_thread.daemon = True
        webhook_thread.start()
        
        # Start scheduler in main thread
        print(f"Started webhook server on {WEBHOOK_HOST}:{WEBHOOK_PORT}")
        print(f"Started periodic sync every {SYNC_INTERVAL_HOURS} hours")
        print("Press Ctrl+C to stop")
        
        try:
            run_scheduler()
        except KeyboardInterrupt:
            print("\nShutting down...")
            
    else:  # schedule mode
        def full_sync():
            sync_client.logger.info("Running scheduled full sync...")
            sync_client.full_sync()
        
        schedule.every(SYNC_INTERVAL_HOURS).hours.do(full_sync)
        
        print(f"Scheduler started - syncing every {SYNC_INTERVAL_HOURS} hours")
        print("Press Ctrl+C to stop")
        
        # Run initial sync
        full_sync()
        
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)
        except KeyboardInterrupt:
            print("\nShutting down...")


if __name__ == "__main__":
    main()