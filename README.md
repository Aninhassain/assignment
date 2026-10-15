# Employee Attendance & Analytics API

A FastAPI-based attendance tracking system with MongoDB backend, supporting punch-in/punch-out, manual corrections with audit trails, and analytics endpoints using MongoDB aggregation pipelines.

## Features

- **Employee Management**: Create and list employees with shift schedules
- **Attendance Tracking**: Punch-in/punch-out with automatic late/overtime calculation
- **Manual Corrections**: Regularize attendance records with full audit trail
- **Analytics**: Monthly summaries, department summaries, late leaderboards, and trend analysis
- **Performance**: Optimized MongoDB indexes for fast queries on large datasets

## Business Rules Implemented

- **R1**: IST timezone handling for attendance dates, including overnight shifts
- **R2**: 10-minute grace period for late calculation
- **R3**: Overtime counted only when >= 30 minutes
- **R4**: Work hours rounded half-up to 2 decimals
- **R5**: Half-day flag when work hours < 4.50
- **R6**: Present days counted for PRESENT/WFH/ON_DUTY statuses
- **R7**: Working days are Mon-Fri, counted from joined_on
- **R8**: Half-up rounding for reported numbers
- **R9**: Headcount includes employees with zero logs
- **R10**: Pagination with page starting at 1, max page_size 100

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables (create .env file)
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db

# Load sample data (optional)
python sample_seed.py

# Run the server
uvicorn app.main:app --port 8000
```

## MongoDB Requirements

- MongoDB 6.0 or newer (tested with 7.0)
- Local Docker: `docker run -p 27017:27017 mongo:7`
- MongoDB Atlas free tier (M0) also works

## API Documentation

Once running, visit `http://localhost:8000/docs` for interactive API documentation (Swagger UI).

## Files

- `app/main.py`: Complete FastAPI application with all endpoints
- `requirements.txt`: Python dependencies
- `REVIEW.md`: Code review of the original starter
- `DECISIONS.md`: Design decisions and trade-offs
- `DATA_MODEL.md`: MongoDB schema documentation
- `openapi.yaml`: Full API contract

## Testing

The API includes an `/admin/explain/{endpoint}` endpoint to verify query plans use indexes instead of collection scans.
