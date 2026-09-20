"""Track real task history — saved to a JSON file next to the app."""
import json
import os
from datetime import datetime
import sys

def _history_path():
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base, 'task_history.json')

def load_history():
    try:
        with open(_history_path()) as f:
            return json.load(f)
    except:
        return []

def save_task(task_type: str, name: str, status: str):
    """Add a task to history. task_type: Upload/Scrape/Audit/Convert"""
    history = load_history()
    history.insert(0, {
        "type": task_type,
        "name": name,
        "status": status,
        "date": datetime.now().strftime("%b %d, %Y"),
    })
    history = history[:20]  # keep last 20
    try:
        with open(_history_path(), 'w') as f:
            json.dump(history, f, indent=2)
    except:
        pass
