# PoolGuard 🏊 — AI-Powered Swimming Pool Safety Monitor

PoolGuard is a real-time pool safety and contamination monitoring system. It uses a custom-trained YOLOv26 computer vision model to detect hazards in a swimming pool — trash, food, animals, bottles, and other foreign objects — and immediately alerts the pool manager through a live dashboard.

---

## 🌐 Live Application

The dashboard is deployed and publicly accessible at:

**[https://pool-guard.onrender.com](https://pool-guard.onrender.com)**

---

## 🏗️ System Architecture

PoolGuard is made up of two components that work together:

```
┌─────────────────────────────┐         ┌──────────────────────────────────┐
│   Stream Server (Local)     │         │   Django Dashboard (Railway)      │
│                             │         │                                   │
│  Camera → YOLOv26 Inference  │──POST──▶│  Ingest API → PostgreSQL DB       │
│  Flask + OpenCV             │         │  Dashboard → Chart.js Graphs      │
│  (stream_server.py)         │         │  Email Alerts → Manager           │
└─────────────────────────────┘         └──────────────────────────────────┘
```

1. **Stream Server** runs locally (at the pool location). It reads the camera feed, runs YOLOv26 inference on every frame, and sends detection events to the Django backend via HTTP POST.
2. **Django Dashboard** receives detections, stores them in PostgreSQL, serves the manager dashboard, and sends email alerts on each detected hazard.

---

## ✨ Features

### Real-Time Hazard Detection
- Detects pool contaminants: **trash, food, animals, bottles, and littering**
- Powered by a custom YOLOv26 model (`best.pt`) trained specifically on pool hazard images
- Confidence threshold filtering to reduce false positives

### Live Manager Dashboard
- Real-time overview of all active and recent detections
- Detection history table with timestamps, object class, and confidence score
- Interactive charts (Chart.js) showing detection frequency over time
- Visual alert indicators when a hazard is detected

### Email Alerts
- Instant email notification sent to the pool manager on each new detection
- Includes the object class, confidence score, and timestamp

### Detection History & Reports
- Full log of all detection events stored in the database
- Filter and review past incidents by date or object type

---

## 🚀 Getting Started

### Prerequisites

- Python 3.12+
- Git
- A webcam or IP camera (for the stream server)
- (Optional) A Cloudflare Tunnel or ngrok account for HTTPS bridging

---

### 1. Clone the Repository

```bash
git clone https://github.com/MugishaJoshua/POOL-SAFE.git
cd POOL-SAFE
```

---

### 2. Set Up the Django Backend (Local Development)

```bash
# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Linux/macOS
venv\Scripts\activate           # Windows (Git Bash)

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment variables
cp .env.example .env
# Edit .env with your database credentials and email settings

# Apply database migrations
python manage.py migrate

# Create a superuser (admin account)
python manage.py createsuperuser

# Run the development server
python manage.py runserver
```

The dashboard will be available at **http://127.0.0.1:8000**

---

### 3. Configure Environment Variables

Create a `.env` file in the project root with the following:

```env
SECRET_KEY=your-django-secret-key
DEBUG=False

# Database (PostgreSQL)
DATABASE_URL=postgresql://user:password@host:port/dbname

# Email alerts
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
ALERT_EMAIL=manager@example.com

# CSRF (for production)
CSRF_TRUSTED_ORIGINS=https://your-domain.up.railway.app
```

---

### 4. Run the Stream Server (Camera + YOLO)

The stream server runs separately on the machine connected to the pool camera.

```bash
# From the project root, with your venv active:
python stream_server.py
```

By default, it reads from your local webcam. To use an IP camera, update the camera URL inside `stream_server.py`.

> **Note for HTTPS deployments:** If your Django backend is served over HTTPS (e.g., on Railway), the stream server must also be reachable via HTTPS. Use **Cloudflare Tunnel** or **ngrok** to expose the stream server securely.

```bash
# Example with Cloudflare Tunnel
cloudflared tunnel --url http://localhost:5000
```

---

## 🖥️ Dashboard Walkthrough

### Accessing the Dashboard
Navigate to the live URL or your local server and log in with your manager credentials.

### Main Dashboard (`/`)
- **Detection Feed** — Live table of the most recent detections with object class, confidence, and time
- **Alert Banner** — Highlights when an active hazard is detected
- **Charts** — Detection frequency over the last 24 hours, broken down by object class

### Detection History (`/history/`)
- Full paginated log of all past detections
- Filter by date range or object class

### Admin Panel (`/admin/`)
- Manage user accounts and permissions
- View and edit detection records directly
- Access via Django's built-in admin interface

---

## 🤖 AI Model

| Metric          | Value  |
|-----------------|--------|
| Architecture    | YOLOv26 |
| Precision       | ~93%   |
| Recall          | ~92%   |
| mAP@50          | ~93%   |
| mAP@50-95       | ~84%   |
| Training Tool   | Google Colab (T4 GPU) |
| Dataset Source  | Roboflow |

**Detected Classes:** `trash`, `food`, `animal`, `bottle`, `littering`

---

## 🗄️ Tech Stack

| Layer        | Technology                        |
|--------------|-----------------------------------|
| Backend      | Django, Django REST Framework     |
| Database     | PostgreSQL (hosted on Railway)    |
| ML/CV        | YOLOv26 (Ultralytics), OpenCV      |
| Stream Server| Flask                             |
| Frontend     | Vanilla JS, Chart.js              |
| Deployment   | Render                            |
| Tunnel       | Cloudflare Tunnel / ngrok         |

---

## 📁 Project Structure

```
POOL-SAFE/
├── dashboard/              # Django app — views, models, URLs
│   ├── models.py           # Detection model (stores events)
│   ├── views.py            # Dashboard views + ingest API + email alerts
│   └── templates/          # HTML templates
├── pool_safe/              # Django project settings
├── stream_server.py        # Flask stream server (camera + YOLO inference)
├── best.pt                 # Trained YOLOv26 model weights
├── requirements.txt
├── Dockerfile              # Railway deployment
└── manage.py
```

---

## ☁️ Deployment (Render)

The production backend is deployed on [Render](https://render.com). Key deployment notes:

- Uses a **Dockerfile** for reliable OpenCV support
- Requires `libgl1` system package for OpenCV
- Migrations run automatically before gunicorn starts
- `CSRF_TRUSTED_ORIGINS` must include your Render domain (e.g. `https://pool-guard.onrender.com`)

---

## 📬 Contact

Built by **Joshua Mugisha** as a final-year academic project.  
GitHub: [@MugishaJoshua](https://github.com/MugishaJoshua)