# PoolGuard — AI Pool Surveillance Dashboard
Django web application for receiving YOLOv8 detection events and alerting pool managers.

---

## Setup

### 1. Install dependencies
```bash
pip install django djangorestframework pillow
```

### 2. Run migrations
```bash
python manage.py migrate
```

### 3. Start the server
```bash
python manage.py runserver
```

Open http://127.0.0.1:8000 in your browser.

---

## API Endpoints

### POST `/api/ingest/`
Called by your YOLOv8 detection module when a threat is detected.

**Request body (JSON):**
```json
{
  "object_class": "trash",       // trash | food | animal | bottle | littering
  "confidence": 0.87,            // float 0.0–1.0
  "image_path": "path/to/img",   // optional
  "location_note": "Pool Deck"   // optional
}
```

**Example (Python, from your YOLO module):**
```python
import requests

requests.post("http://127.0.0.1:8000/api/ingest/", json={
    "object_class": "animal",
    "confidence": 0.92,
    "image_path": "/captures/frame_001.jpg",
    "location_note": "North Perimeter"
})
```

### GET `/api/notifications/poll/?since_id=0`
Returns unread notifications since a given ID (used by dashboard for live updates).

### POST `/api/events/<id>/acknowledge/`
Mark an event as cleared by the pool manager.

### GET `/api/history/?page=1`
Paginated list of all detection events.

### GET `/api/stats/`
Aggregated stats for charts and counters.

---

## Dashboard Features

| Page       | Features                                          |
|------------|---------------------------------------------------|
| Overview   | Stats counters, recent alerts, class bar chart    |
| Live Feed  | Camera stream placeholder, last detection status  |
| Alerts     | All active alerts, acknowledge/clear buttons      |
| History    | Full paginated table of all events                |
| Analytics  | 7-day trend chart, severity donut, class breakdown|

- **In-app notifications**: Toast popups appear on every new detection
- **Sound alerts**: Audio beep pattern plays (high/medium/low pitch based on severity)
- **Auto-polling**: Dashboard polls `/api/notifications/poll/` every 3 seconds
- **Demo bar**: Simulate detections directly from the Overview page for testing

---

## Severity Mapping

| Class      | Severity |
|------------|----------|
| animal     | High     |
| littering  | High     |
| trash      | Medium   |
| food       | Medium   |
| bottle     | Low      |

---

## Project Structure
```
pool_guardian/
├── core/               # Django project settings & URLs
├── dashboard/          # Main app
│   ├── models.py       # DetectionEvent, Notification
│   ├── views.py        # Dashboard view + REST API
│   └── urls.py
├── templates/
│   └── dashboard/
│       └── index.html  # Full front-end
├── media/              # Captured images stored here
├── pool_guardian.db    # SQLite database
└── manage.py
```
