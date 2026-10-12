# LifeFlow: Blood Donation Website with AI Demand Prediction

A responsive blood donation website built with Flask, SQLite, scikit-learn and vanilla JavaScript.

## Features

- **Donor registration** with validation, search and filter by blood group or city
- **90-day donation eligibility** check for every donor
- **AI demand forecasting** (Random Forest) for the next 24 hours, 7 days and 30 days
- **Smart inventory engine**: stock per blood group, expiry tracking (42-day shelf life), shortage and excess detection
- **Optimization engine**: replenishment, donor request and redistribution recommendations
- **Blood compatibility finder**: shows which donors can give to a patient's blood group
- **Hospital requests** with automatic FIFO allocation (earliest expiry is used first)
- **Responsive design** for mobile, tablet and desktop, with dark mode
- Pipeline diagram section that matches the system design

## Project structure

```
blood-donation-ai/
├── app.py              # Flask routes and API
├── database.py         # SQLite schema, seed data, shared constants
├── ai_model.py         # Forecasting, inventory engine, optimization engine
├── requirements.txt
├── templates/
│   └── index.html      # Single-page responsive UI
└── static/
    ├── css/style.css   # Responsive styles, light and dark themes
    ├── js/app.js       # Frontend logic and charts
    └── img/pipeline.jpg
```

## How to run in VS Code

### 1. Open the project
1. Unzip `blood-donation-ai.zip`.
2. In VS Code choose **File → Open Folder** and select the `blood-donation-ai` folder.

### 2. Create a virtual environment
Open the terminal (**Terminal → New Terminal**) and run:

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

If VS Code asks *"Select interpreter"*, choose the one inside `venv`.

### 3. Install the requirements
```bash
pip install -r requirements.txt
```

### 4. Run the app
```bash
python app.py
```

Open **http://127.0.0.1:5000** in your browser.

On the first run the app creates `blood_donation.db` and fills it with demo data (donors, stock and 180 days of usage history), so the dashboard and AI forecast work immediately.

To reset the demo data, stop the server, delete `blood_donation.db`, and run `python app.py` again.

## API reference

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/stats` | Dashboard counters |
| GET | `/api/donors?group=&q=` | List and search donors |
| POST | `/api/donors` | Register a donor |
| DELETE | `/api/donors/<id>` | Remove a donor |
| GET | `/api/compatible/<group>` | Eligible donors for a patient's blood group |
| GET | `/api/inventory` | Stock per group and batch list |
| POST | `/api/donations` | Record collected units |
| GET | `/api/requests` | Recent hospital requests |
| POST | `/api/requests` | Submit a hospital request (auto-allocates stock) |
| GET | `/api/forecast/<group>` | 24h / 7d / 30d demand forecast |
| GET | `/api/recommendations` | Replenishment, donor, redistribution and expiry actions |

## Notes on the AI model

- **Preprocessing:** daily usage is grouped by blood group, and missing days are filled with zero.
- **Features:** the previous day, 7-day lag, 7- and 14-day averages, 14-day standard deviation, weekday and month.
- **Forecasting:** a Random Forest is trained per blood group. The 30-day forecast is recursive, so each day's prediction feeds the next.
- **Accuracy:** the last 14 days are held out to report an average error (shown on the dashboard).
- **Replacing the demo data:** the model reads from the `usage_history` table. Insert real historical records there, or change `load_daily_usage()` in `ai_model.py` to read from a CSV file or a hospital system.

## Troubleshooting

- **`ModuleNotFoundError`**: make sure the virtual environment is active and run `pip install -r requirements.txt` again.
- **Port in use**: change `port=5000` in `app.py` to another port, such as `5001`.
- **Charts not showing**: Chart.js loads from a CDN, so an internet connection is needed.
- **Pipeline image missing**: the page still shows the text steps. Put `pipeline.jpg` in `static/img/`.

## Medical disclaimer

This project is a demonstration for educational use. Real blood bank systems must follow national blood safety regulations, and any clinical decision must be made by qualified medical staff.
