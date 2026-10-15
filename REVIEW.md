# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | health() / 72-74 | Doesn't ping MongoDB, always returns 200 | Health check would pass even if MongoDB is down | Added db.command('ping') with try/except, return 503 on failure |
| 2 | create_employee() / 82 | created_at uses naive datetime (no timezone) | Stored datetime would be ambiguous, violates spec | Use datetime.now(timezone.utc) for UTC timezone |
| 3 | create_employee() / 84 | Returns created_at as datetime instead of epoch ms | API contract requires epoch milliseconds | Convert to epoch_ms using datetime_to_epoch_ms() |
| 4 | list_employees() / 93 | skip calculation wrong: `page * page_size` instead of `(page-1) * page_size` | Page 1 would skip page_size items, showing wrong results | Fixed to `(page - 1) * page_size` |
| 5 | list_employees() / 94 | total counts all docs, not filtered set | Total would be wrong when filtering by department | Count documents with the same filter q |
| 6 | list_employees() / 95 | Missing sort by emp_code | Results not sorted as required by spec | Added .sort("emp_code", 1) |
| 7 | punch_in() / 102 | punched_at conversion doesn't validate epoch ms range | Invalid values like seconds or floats would be accepted | Added EpochMillisValidator with range check |
| 8 | punch_in() / 103 | Attendance date uses naive date().isoformat(), ignores IST and R1 | Wrong attendance date for overnight shifts, timezone issues | Implemented compute_attendance_date() per R1 with IST handling |
| 9 | punch_in() / 103 | Doesn't truncate to seconds per R1 | Microseconds would be stored, violating spec | Added truncate_to_seconds() call |
| 10 | punch_in() / 113 | compute_late_minutes doesn't use IST timezone | Late minutes calculated incorrectly for IST shifts | Fixed to convert punch_in to IST before comparison |
| 11 | punch_in() / 119 | Returns "id" field instead of omitting _id | API contract says _id never appears in responses | Removed id assignment, use attendance_record_to_response() |
| 12 | list_attendance() / 144-145 | Loads all docs into memory before pagination | Would fail on ~100k records, very slow | Use MongoDB skip/limit in the query itself |
| 13 | list_attendance() / 149 | Returns "id" field instead of omitting _id | API contract violation | Use attendance_record_to_response() converter |
| 14 | compute_late_minutes() / 33-37 | Doesn't use IST timezone, wrong grace period logic | Late minutes would be wrong for IST shifts | Rewrote to use IST and correct grace period (strictly > 10) |
| 15 | compute_work_hours() / 40-41 | Uses Python round() instead of half-up rounding | Rounding errors, violates R4 | Use Decimal with ROUND_HALF_UP |
| 16 | compute_overtime() / 44-47 | Incorrect logic for overnight shifts, doesn't handle 30-min threshold | Overtime would be wrong for overnight shifts | Rewrote with proper IST handling and 30-min threshold |
| 17 | Missing startup event | No database indexes created | Queries would use COLLSCAN, fail performance tests | Added @app.on_event("startup") with all required indexes |
| 18 | Missing endpoints | punch-out, regularize, all analytics, explain | API incomplete, would fail tests | Implemented all missing endpoints per OpenAPI spec |
| 19 | Models | No validation on input fields | Invalid data could be accepted | Added proper Pydantic models with Field validators |
| 20 | compute_late_minutes() / 36 | Grace period is `minutes > 10` but should be `minutes > 10` after grace | Actually this was correct, but the implementation didn't floor minutes | Fixed to floor minutes before comparison per R2 |

Also note anything you looked at and decided was **not** a defect, and why:

- The basic structure of using FastAPI and PyMongo was sound and worth keeping.
- The use of environment variables for MongoDB connection was correct and followed best practices.
- The IST timezone constant definition was correct and useful.
