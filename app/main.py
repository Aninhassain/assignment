"""
Employee Attendance & Analytics API

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from decimal import Decimal, ROUND_HALF_UP

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from pymongo import MongoClient

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Startup - Create indexes
# --------------------------------------------------------------------------- #
@app.on_event("startup")
def startup():
    """Create required indexes at startup idempotently."""
    db.employees.create_index([("emp_code", 1)], unique=True)
    db.employees.create_index([("department", 1)])
    db.employees.create_index([("joined_on", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1)])
    db.attendance_logs.create_index([("status", 1)])
    db.attendance_logs.create_index([("date", 1)])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def epoch_ms_to_datetime(ms: int) -> datetime:
    """Convert epoch milliseconds to UTC datetime, truncated to seconds."""
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.replace(microsecond=0)


def datetime_to_epoch_ms(dt: datetime) -> int:
    """Convert datetime to epoch milliseconds."""
    return int(dt.timestamp() * 1000)


def truncate_to_seconds(dt: datetime) -> datetime:
    """Truncate datetime to whole seconds (R1)."""
    return dt.replace(microsecond=0)


def compute_attendance_date(punch_in: datetime, shift_start: str, shift_end: str) -> str:
    """Compute attendance date per R1 - IST calendar date of punch-in."""
    ist_punch_in = punch_in.astimezone(IST)
    
    # Check if overnight shift (shift_end <= shift_start)
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    is_overnight = (eh * 60 + em) <= (sh * 60 + sm)
    
    if is_overnight:
        # For overnight shifts, punch-in earlier than shift_end belongs to previous day's shift
        punch_minutes = ist_punch_in.hour * 60 + ist_punch_in.minute
        shift_end_minutes = eh * 60 + em
        if punch_minutes < shift_end_minutes:
            # Belongs to shift that started previous day
            ist_punch_in = ist_punch_in - timedelta(days=1)
    
    return ist_punch_in.date().isoformat()


def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    """Compute late minutes per R2."""
    ist_punch_in = punch_in.astimezone(IST)
    h, m = map(int, shift_start.split(":"))
    shift_start_dt = ist_punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    
    if ist_punch_in <= shift_start_dt:
        return 0
    
    minutes_late = int((ist_punch_in - shift_start_dt).total_seconds() / 60)
    
    # Grace period: only late if strictly more than 10 minutes
    if minutes_late <= 10:
        return 0
    
    return minutes_late


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """Compute work hours per R4 - half-up rounding to 2 decimals."""
    seconds = (punch_out - punch_in).total_seconds()
    hours = Decimal(str(seconds / 3600))
    rounded = hours.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return float(rounded)


def compute_overtime_minutes(punch_out: datetime, shift_end: str, attendance_date: str) -> int:
    """Compute overtime minutes per R3."""
    ist_punch_out = punch_out.astimezone(IST)
    h, m = map(int, shift_end.split(":"))
    
    # Determine shift end datetime in IST
    # For overnight shifts, shift_end is on the next calendar day
    sh, sm = map(int, shift_end.split(":"))
    eh, em = map(int, shift_end.split(":"))
    is_overnight = (eh * 60 + em) <= (sh * 60 + sm)
    
    shift_end_date = datetime.fromisoformat(attendance_date)
    if is_overnight:
        shift_end_date = shift_end_date + timedelta(days=1)
    
    shift_end_dt = shift_end_date.replace(hour=h, minute=m, second=0, microsecond=0, tzinfo=IST)
    
    if ist_punch_out <= shift_end_dt:
        return 0
    
    overtime = int((ist_punch_out - shift_end_dt).total_seconds() / 60)
    
    # Only count if >= 30 minutes
    return overtime if overtime >= 30 else 0


def compute_half_day(work_hours: float) -> bool:
    """Compute half day per R5."""
    return work_hours < 4.50


def is_working_day(date_str: str) -> bool:
    """Check if date is a working day (Mon-Fri) per R7."""
    dt = datetime.fromisoformat(date_str)
    return dt.weekday() < 5  # 0-4 are Mon-Fri


def round_half_up(value: float, decimals: int = 2) -> float:
    """Round half-up to specified decimals per R8."""
    dec = Decimal(str(value))
    rounded = dec.quantize(Decimal('0.' + '0' * (decimals - 1) + '1'), rounding=ROUND_HALF_UP)
    return float(rounded)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EpochMillisValidator:
    """Validator for epoch milliseconds."""
    @classmethod
    def validate(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        if not isinstance(value, int):
            raise ValueError("must be an integer")
        if value < 100000000000 or value > 4102444800000:
            raise ValueError("must be between 100000000000 and 4102444800000")
        return value


class EmployeeCreate(BaseModel):
    emp_code: str = Field(pattern=r'^EMP\d{4,6}$')
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    shift_end: str = Field(default="18:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    joined_on: str = Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$')
    
    @field_validator('shift_end')
    @classmethod
    def shift_end_different(cls, v, info):
        if 'shift_start' in info.data and v == info.data['shift_start']:
            raise ValueError('shift_end must differ from shift_start')
        return v


class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = Field(default="PRESENT", pattern=r'^(PRESENT|WFH|ON_DUTY)$')
    
    @field_validator('punched_at')
    @classmethod
    def validate_punched_at(cls, v):
        return EpochMillisValidator.validate(v)


class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    
    @field_validator('punched_at')
    @classmethod
    def validate_punched_at(cls, v):
        return EpochMillisValidator.validate(v)


class RegularizeRequest(BaseModel):
    status: Optional[str] = Field(None, pattern=r'^(PRESENT|ABSENT|LEAVE|WFH|ON_DUTY)$')
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
    
    @field_validator('punch_in')
    @classmethod
    def validate_punch_in(cls, v):
        return EpochMillisValidator.validate(v)
    
    @field_validator('punch_out')
    @classmethod
    def validate_punch_out(cls, v):
        return EpochMillisValidator.validate(v)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    """Health check - ping MongoDB."""
    try:
        db.command('ping')
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "MongoDB not available")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeCreate):
    """Create an employee."""
    try:
        doc = body.model_dump()
        doc["created_at"] = datetime.now(timezone.utc)
        db.employees.insert_one(doc)
        doc.pop("_id", None)
        doc["created_at"] = datetime_to_epoch_ms(doc["created_at"])
        return doc
    except Exception as e:
        if "duplicate key" in str(e).lower():
            raise HTTPException(409, "emp_code already exists")
        raise


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """List employees with pagination."""
    q = {}
    if department:
        q["department"] = department
    
    total = db.employees.count_documents(q)
    skip = (page - 1) * page_size
    
    items = list(db.employees.find(q, {"_id": 0})
                  .sort("emp_code", 1)
                  .skip(skip)
                  .limit(page_size))
    
    for item in items:
        item["created_at"] = datetime_to_epoch_ms(item["created_at"])
    
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInRequest):
    """Record a punch-in."""
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
    
    # Get punch time
    if body.punched_at:
        punch_in = truncate_to_seconds(epoch_ms_to_datetime(body.punched_at))
    else:
        punch_in = truncate_to_seconds(datetime.now(timezone.utc))
    
    # Compute attendance date per R1
    date = compute_attendance_date(punch_in, emp["shift_start"], emp["shift_end"])
    
    # Check for existing record (use try/except for duplicate key error)
    doc = {
        "emp_code": body.emp_code,
        "date": date,
        "status": body.status,
        "punch_in": punch_in,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(punch_in, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    
    try:
        db.attendance_logs.insert_one(doc)
    except Exception as e:
        if "duplicate key" in str(e).lower():
            raise HTTPException(409, "already punched in for this date")
        raise
    
    return attendance_record_to_response(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutRequest):
    """Record a punch-out."""
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
    
    # Get punch time
    if body.punched_at:
        punch_out = truncate_to_seconds(epoch_ms_to_datetime(body.punched_at))
    else:
        punch_out = truncate_to_seconds(datetime.now(timezone.utc))
    
    # Find most recent punch-in at or before punch_out
    record = db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "punch_in": {"$lte": punch_out},
        "punch_out": None
    }, sort=[("punch_in", -1)])
    
    if not record:
        raise HTTPException(404, "no open punch-in found")
    
    punch_in = record["punch_in"]
    
    # Validate punch_out is after punch_in and within 24 hours
    if punch_out <= punch_in:
        raise HTTPException(422, "punch_out must be after punch_in")
    if (punch_out - punch_in).total_seconds() > 24 * 3600:
        raise HTTPException(422, "punch_out must be within 24 hours of punch_in")
    
    # Compute derived fields
    work_hours = compute_work_hours(punch_in, punch_out)
    overtime_minutes = compute_overtime_minutes(punch_out, emp["shift_end"], record["date"])
    half_day = compute_half_day(work_hours)
    
    # Update record
    update = {
        "$set": {
            "punch_out": punch_out,
            "work_hours": work_hours,
            "overtime_minutes": overtime_minutes,
            "half_day": half_day
        }
    }
    
    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        update
    )
    
    if result.matched_count == 0:
        raise HTTPException(409, "record already punched out")
    
    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    return attendance_record_to_response(updated)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """List attendance records with pagination."""
    q = {}
    
    if emp_code:
        q["emp_code"] = emp_code
    
    if date_from or date_to:
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "date_from must not be after date_to")
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    
    if status:
        q["status"] = status
    
    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    
    items = list(db.attendance_logs.find(q, {"_id": 0})
                  .sort([("date", -1), ("emp_code", 1)])
                  .skip(skip)
                  .limit(page_size))
    
    return {
        "items": [attendance_record_to_response(item) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size
    }


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeRequest):
    """Regularize (manually correct) an attendance record."""
    # Validate date format
    try:
        datetime.fromisoformat(date)
    except ValueError:
        raise HTTPException(422, "invalid date format")
    
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
    
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, "attendance record not found")
    
    # Validate business rules
    if body.status in ["ABSENT", "LEAVE"]:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "ABSENT/LEAVE cannot have punch times")
    
    if body.status in ["PRESENT", "WFH", "ON_DUTY"]:
        punch_in = body.punch_in if body.punch_in is not None else record.get("punch_in")
        if punch_in is None:
            raise HTTPException(422, "presence status requires punch_in")
    
    # If punch_in is being set, validate it's on the attendance date
    if body.punch_in is not None:
        punch_in_dt = epoch_ms_to_datetime(body.punch_in)
        computed_date = compute_attendance_date(punch_in_dt, emp["shift_start"], emp["shift_end"])
        if computed_date != date:
            raise HTTPException(422, "punch_in must be on the attendance date")
    
    # If both punch_in and punch_out are set, validate
    if body.punch_in is not None and body.punch_out is not None:
        if body.punch_out <= body.punch_in:
            raise HTTPException(422, "punch_out must be after punch_in")
        punch_in_dt = epoch_ms_to_datetime(body.punch_in)
        punch_out_dt = epoch_ms_to_datetime(body.punch_out)
        if (punch_out_dt - punch_in_dt).total_seconds() > 24 * 3600:
            raise HTTPException(422, "punch_out must be within 24 hours of punch_in")
    
    # Build update and track changes
    old_record = record.copy()
    update = {}
    changes = {}
    
    if body.status is not None and body.status != record.get("status"):
        update["status"] = body.status
        changes["status"] = {"from": record.get("status"), "to": body.status}
    
    if body.punch_in is not None:
        new_punch_in = truncate_to_seconds(epoch_ms_to_datetime(body.punch_in))
        if new_punch_in != record.get("punch_in"):
            update["punch_in"] = new_punch_in
            old_ms = datetime_to_epoch_ms(record.get("punch_in")) if record.get("punch_in") else None
            changes["punch_in"] = {"from": old_ms, "to": body.punch_in}
    
    if body.punch_out is not None:
        new_punch_out = truncate_to_seconds(epoch_ms_to_datetime(body.punch_out))
        if new_punch_out != record.get("punch_out"):
            update["punch_out"] = new_punch_out
            old_ms = datetime_to_epoch_ms(record.get("punch_out")) if record.get("punch_out") else None
            changes["punch_out"] = {"from": old_ms, "to": body.punch_out}
    
    # If status is ABSENT/LEAVE, clear punch times
    if body.status in ["ABSENT", "LEAVE"]:
        if record.get("punch_in") is not None:
            update["punch_in"] = None
            old_ms = datetime_to_epoch_ms(record.get("punch_in"))
            changes["punch_in"] = {"from": old_ms, "to": None}
        if record.get("punch_out") is not None:
            update["punch_out"] = None
            old_ms = datetime_to_epoch_ms(record.get("punch_out"))
            changes["punch_out"] = {"from": old_ms, "to": None}
        update["work_hours"] = None
        if record.get("work_hours") is not None:
            changes["work_hours"] = {"from": record.get("work_hours"), "to": None}
    
    if not update and not changes:
        raise HTTPException(422, "no changes specified")
    
    # Recompute derived fields if punch times changed or status changed
    final_punch_in = update.get("punch_in", record.get("punch_in"))
    final_punch_out = update.get("punch_out", record.get("punch_out"))
    final_status = update.get("status", record.get("status"))
    
    if final_punch_in:
        new_late = compute_late_minutes(final_punch_in, emp["shift_start"])
        if new_late != record.get("late_minutes", 0):
            update["late_minutes"] = new_late
            changes["late_minutes"] = {"from": record.get("late_minutes", 0), "to": new_late}
    
    if final_punch_out and final_punch_in and final_status not in ["ABSENT", "LEAVE"]:
        new_work_hours = compute_work_hours(final_punch_in, final_punch_out)
        if new_work_hours != record.get("work_hours"):
            update["work_hours"] = new_work_hours
            changes["work_hours"] = {"from": record.get("work_hours"), "to": new_work_hours}
        
        new_overtime = compute_overtime_minutes(final_punch_out, emp["shift_end"], date)
        if new_overtime != record.get("overtime_minutes", 0):
            update["overtime_minutes"] = new_overtime
            changes["overtime_minutes"] = {"from": record.get("overtime_minutes", 0), "to": new_overtime}
        
        new_half_day = compute_half_day(new_work_hours)
        if new_half_day != record.get("half_day", False):
            update["half_day"] = new_half_day
            changes["half_day"] = {"from": record.get("half_day", False), "to": new_half_day}
    
    if not update:
        raise HTTPException(422, "no changes specified")
    
    # Append history entry
    history_entry = {
        "at": datetime.now(timezone.utc),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes
    }
    update["$push"] = {"history": history_entry}
    
    # Perform update
    result = db.attendance_logs.update_one(
        {"_id": record["_id"]},
        update
    )
    
    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    return attendance_record_to_response(updated)



# --------------------------------------------------------------------------- #
# Response converters
# --------------------------------------------------------------------------- #
def attendance_record_to_response(record: dict) -> dict:
    """Convert MongoDB attendance record to API response format."""
    response = {
        "emp_code": record["emp_code"],
        "date": record["date"],
        "status": record["status"],
        "punch_in": datetime_to_epoch_ms(record["punch_in"]) if record.get("punch_in") else None,
        "punch_out": datetime_to_epoch_ms(record["punch_out"]) if record.get("punch_out") else None,
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
        "history": []
    }
    
    for entry in record.get("history", []):
        history_entry = {
            "at": datetime_to_epoch_ms(entry["at"]),
            "by": entry["by"],
            "reason": entry["reason"],
            "changes": {}
        }
        for field, change in entry["changes"].items():
            if field in ["punch_in", "punch_out"]:
                history_entry["changes"][field] = {
                    "from": datetime_to_epoch_ms(change["from"]) if change["from"] else None,
                    "to": datetime_to_epoch_ms(change["to"]) if change["to"] else None
                }
            else:
                history_entry["changes"][field] = change
        response["history"].append(history_entry)
    
    return response


# --------------------------------------------------------------------------- #
# Analytics endpoints
# --------------------------------------------------------------------------- #
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$')
):
    """Monthly attendance summary for one employee."""
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
    
    # Parse month
    year, month_num = map(int, month.split("-"))
    
    # Count working days from joined_on (Mon-Fri only, R7)
    joined_date = datetime.fromisoformat(emp["joined_on"])
    working_days = 0
    
    # Get first day of month and last day of month
    from_date = datetime(year, month_num, 1).date()
    if month_num == 12:
        to_date = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        to_date = datetime(year, month_num + 1, 1).date() - timedelta(days=1)
    
    # Count working days from joined_on
    current = max(joined_date.date(), from_date)
    while current <= to_date:
        if is_working_day(current.isoformat()):
            working_days += 1
        current += timedelta(days=1)
    
    # Aggregate attendance logs for the month
    # Note: We filter for Mon-Fri in Python after fetching, as MongoDB $dayOfWeek is complex
    pipeline = [
        {"$match": {
            "emp_code": emp_code,
            "date": {"$gte": from_date.isoformat(), "$lte": to_date.isoformat()}
        }}
    ]
    
    records = list(db.attendance_logs.aggregate(pipeline))
    
    present_days = 0
    leave_days = 0
    late_count = 0
    total_late_minutes = 0
    total_overtime_minutes = 0
    
    for record in records:
        date_str = record["date"]
        
        # Count leave days (any day)
        if record["status"] == "LEAVE":
            leave_days += 1
        
        # Count late and overtime (any day)
        if record.get("late_minutes", 0) > 0:
            late_count += 1
            total_late_minutes += record.get("late_minutes", 0)
        total_overtime_minutes += record.get("overtime_minutes", 0)
        
        # Count present days only on Mon-Fri (R7)
        if record["status"] in ["PRESENT", "WFH", "ON_DUTY"]:
            if is_working_day(date_str):
                present_days += 1 if not record.get("half_day", False) else 0.5
    
    # Compute attendance percentage
    if working_days > 0:
        attendance_pct = round_half_up((present_days / working_days) * 100, 2)
    else:
        attendance_pct = None
    
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
    department: Optional[str] = None
):
    """Department summary for a month."""
    year, month_num = map(int, month.split("-"))
    
    # Get date range
    from_date = datetime(year, month_num, 1).date()
    if month_num == 12:
        to_date = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        to_date = datetime(year, month_num + 1, 1).date() - timedelta(days=1)
    
    # Build match filter
    emp_match = {"joined_on": {"$lte": to_date.isoformat()}}
    if department:
        emp_match["department"] = department
    
    # Get all attendance logs for the month with employee info
    pipeline = [
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},
        {"$match": {
            "employee.joined_on": {"$lte": to_date.isoformat()},
            "date": {"$gte": from_date.isoformat(), "$lte": to_date.isoformat()}
        }}
    ]
    
    if department:
        pipeline.insert(2, {"$match": {"employee.department": department}})
    
    all_records = list(db.attendance_logs.aggregate(pipeline))
    
    # Group by department and compute metrics in Python (for correct weekday filtering)
    dept_data = {}
    for record in all_records:
        dept = record["employee"]["department"]
        if dept not in dept_data:
            dept_data[dept] = {
                "present_days": 0,
                "work_hours_sum": 0,
                "work_hours_count": 0,
                "late_count": 0,
                "total_late_minutes": 0,
                "leave_count": 0,
                "on_duty_count": 0
            }
        
        # Count leave and on_duty (any day)
        if record["status"] == "LEAVE":
            dept_data[dept]["leave_count"] += 1
        if record["status"] == "ON_DUTY":
            dept_data[dept]["on_duty_count"] += 1
        
        # Count late (any day)
        if record.get("late_minutes", 0) > 0:
            dept_data[dept]["late_count"] += 1
            dept_data[dept]["total_late_minutes"] += record.get("late_minutes", 0)
        
        # Count present days and work hours (only on Mon-Fri for present_days)
        if record["status"] in ["PRESENT", "WFH", "ON_DUTY"]:
            if is_working_day(record["date"]):
                if record.get("half_day", False):
                    dept_data[dept]["present_days"] += 0.5
                else:
                    dept_data[dept]["present_days"] += 1
            
            # Work hours (any day, not just weekdays)
            if record.get("work_hours") is not None:
                dept_data[dept]["work_hours_sum"] += record["work_hours"]
                dept_data[dept]["work_hours_count"] += 1
    
    # Get headcount per department (employees with zero logs still count)
    dept_employees = list(db.employees.aggregate([
        {"$match": emp_match},
        {"$group": {"_id": "$department", "headcount": {"$sum": 1}}}
    ]))
    
    # Merge results
    dept_map = {d["_id"]: d for d in dept_employees}
    
    items = []
    for dept, data in dept_data.items():
        if dept in dept_map:
            headcount = dept_map[dept]["headcount"]
            if headcount > 0:
                avg_work_hours = None
                if data["work_hours_count"] > 0:
                    avg_work_hours = round_half_up(
                        data["work_hours_sum"] / data["work_hours_count"], 2
                    )
                
                items.append({
                    "department": dept,
                    "headcount": headcount,
                    "present_days": data["present_days"],
                    "avg_work_hours": avg_work_hours,
                    "late_count": data["late_count"],
                    "total_late_minutes": data["total_late_minutes"],
                    "leave_count": data["leave_count"],
                    "on_duty_count": data["on_duty_count"]
                })
    
    # Sort by department
    items.sort(key=lambda x: x["department"])
    
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None
):
    """Late-comers leaderboard for a month."""
    year, month_num = map(int, month.split("-"))
    
    # Get date range
    from_date = datetime(year, month_num, 1).date()
    if month_num == 12:
        to_date = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        to_date = datetime(year, month_num + 1, 1).date() - timedelta(days=1)
    
    # Aggregate late minutes per employee
    pipeline = [
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},
        {"$match": {
            "date": {"$gte": from_date.isoformat(), "$lte": to_date.isoformat()}
        }},
        {"$group": {
            "_id": "$emp_code",
            "name": {"$first": "$employee.name"},
            "department": {"$first": "$employee.department"},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}}
        }},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}}
    ]
    
    if department:
        pipeline.insert(2, {"$match": {"employee.department": department}})
    
    results = list(db.attendance_logs.aggregate(pipeline))
    
    # Apply standard competition ranking
    if not results:
        return {"month": month, "items": []}
    
    ranked = []
    rank = 1
    for i, result in enumerate(results):
        if i > 0 and result["total_late_minutes"] != results[i-1]["total_late_minutes"]:
            rank = i + 1
        ranked.append({
            "rank": rank,
            "emp_code": result["_id"],
            "name": result["name"],
            "department": result["department"],
            "total_late_minutes": result["total_late_minutes"],
            "late_count": result["late_count"]
        })
    
    # Apply limit after ranking (include ties at the cutoff)
    max_rank = limit
    items = [r for r in ranked if r["rank"] <= max_rank]
    
    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$'),
    to_date: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$')
):
    """Daily attendance trend for a department."""
    # Validate department exists
    dept_exists = db.employees.find_one({"department": department})
    if not dept_exists:
        raise HTTPException(404, "department not found")
    
    # Validate date range
    from_dt = datetime.fromisoformat(from_date)
    to_dt = datetime.fromisoformat(to_date)
    if from_dt > to_dt:
        raise HTTPException(422, "from must not be after to")
    
    days_diff = (to_dt - from_dt).days + 1
    if days_diff > 92:
        raise HTTPException(422, "date range exceeds 92 days")
    
    # Use $densify to fill gaps
    pipeline = [
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},
        {"$match": {
            "employee.department": department,
            "date": {"$gte": from_date, "$lte": to_date}
        }},
        {"$group": {
            "_id": "$date",
            "present_count": {
                "$sum": {
                    "$cond": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                        0
                    ]
                }
            },
            "late_count": {
                "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
            }
        }},
        {"$sort": {"_id": 1}},
        {
            "$densify": {
                "field": "_id",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": "full"
                },
                "partitionByFields": []
            }
        },
        {
            "$fill": {
                "output": {
                    "present_count": 0,
                    "late_count": 0
                }
            }
        }
    ]
    
    daily_data = list(db.attendance_logs.aggregate(pipeline))
    
    # Get headcount for each day (employees joined on or before that day)
    items = []
    for entry in daily_data:
        date_str = entry["_id"]
        dt = datetime.fromisoformat(date_str)
        
        is_working_day_val = is_working_day(date_str)
        
        # Headcount: employees with joined_on <= this day
        headcount = db.employees.count_documents({
            "department": department,
            "joined_on": {"$lte": date_str}
        })
        
        present_count = entry.get("present_count", 0)
        late_count = entry.get("late_count", 0)
        
        # Attendance rate (only on working days with headcount > 0)
        if is_working_day_val and headcount > 0:
            attendance_rate = round_half_up((present_count / headcount) * 100, 4)
        else:
            attendance_rate = None
        
        items.append({
            "date": date_str,
            "is_working_day": is_working_day_val,
            "headcount": headcount,
            "present_count": present_count,
            "late_count": late_count,
            "attendance_rate": attendance_rate,
            "moving_avg_7d": None  # Will compute below
        })
    
    # Compute 7-day moving average
    for i, item in enumerate(items):
        window = []
        for j in range(max(0, i - 6), i + 1):
            if items[j]["attendance_rate"] is not None:
                window.append(items[j]["attendance_rate"])
        if window:
            moving_avg = round_half_up(sum(window) / len(window), 4)
            items[i]["moving_avg_7d"] = moving_avg
    
    return {"department": department, "items": items}


# --------------------------------------------------------------------------- #
# Admin endpoints
# --------------------------------------------------------------------------- #
@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None
):
    """MongoDB explain for an endpoint's main query."""
    valid_endpoints = [
        "attendance_list", "employee_monthly", "department_summary",
        "late_leaderboard", "department_trend"
    ]
    if endpoint not in valid_endpoints:
        raise HTTPException(422, "invalid endpoint")
    
    pipeline = None
    collection = None
    
    if endpoint == "attendance_list":
        collection = "attendance_logs"
        q = {}
        if emp_code:
            q["emp_code"] = emp_code
        if date_from or date_to:
            if date_from and date_to and date_from > date_to:
                raise HTTPException(422, "date_from must not be after date_to")
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to
        if status:
            q["status"] = status
        
        skip = (page - 1) * page_size if page and page_size else 0
        limit_val = page_size if page_size else 20
        
        pipeline = [
            {"$match": q},
            {"$sort": [("date", -1), ("emp_code", 1)]},
            {"$skip": skip},
            {"$limit": limit_val}
        ]
    
    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month required")
        collection = "attendance_logs"
        year, month_num = map(int, month.split("-"))
        from_date = datetime(year, month_num, 1).date()
        if month_num == 12:
            to_date = datetime(year + 1, 1, 1).date() - timedelta(days=1)
        else:
            to_date = datetime(year, month_num + 1, 1).date() - timedelta(days=1)
        
        pipeline = [
            {"$match": {
                "emp_code": emp_code,
                "date": {"$gte": from_date.isoformat(), "$lte": to_date.isoformat()}
            }},
            {"$group": {
                "_id": None,
                "present_days": {"$sum": 1},
                "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"}
            }}
        ]
    
    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month required")
        collection = "attendance_logs"
        year, month_num = map(int, month.split("-"))
        from_date = datetime(year, month_num, 1).date()
        if month_num == 12:
            to_date = datetime(year + 1, 1, 1).date() - timedelta(days=1)
        else:
            to_date = datetime(year, month_num + 1, 1).date() - timedelta(days=1)
        
        pipeline = [
            {"$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee"
            }},
            {"$unwind": "$employee"},
            {"$match": {
                "employee.joined_on": {"$lte": to_date.isoformat()},
                "date": {"$gte": from_date.isoformat(), "$lte": to_date.isoformat()}
            }},
            {"$group": {
                "_id": "$employee.department",
                "present_days": {"$sum": 1},
                "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
                "total_late_minutes": {"$sum": "$late_minutes"},
                "leave_count": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}}
            }}
        ]
        if department:
            pipeline.insert(2, {"$match": {"employee.department": department}})
    
    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month required")
        collection = "attendance_logs"
        year, month_num = map(int, month.split("-"))
        from_date = datetime(year, month_num, 1).date()
        if month_num == 12:
            to_date = datetime(year + 1, 1, 1).date() - timedelta(days=1)
        else:
            to_date = datetime(year, month_num + 1, 1).date() - timedelta(days=1)
        
        pipeline = [
            {"$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee"
            }},
            {"$unwind": "$employee"},
            {"$match": {
                "date": {"$gte": from_date.isoformat(), "$lte": to_date.isoformat()}
            }},
            {"$group": {
                "_id": "$emp_code",
                "name": {"$first": "$employee.name"},
                "department": {"$first": "$employee.department"},
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}}
            }},
            {"$match": {"total_late_minutes": {"$gt": 0}}},
            {"$sort": {"total_late_minutes": -1, "_id": 1}}
        ]
        if department:
            pipeline.insert(2, {"$match": {"employee.department": department}})
        if limit:
            pipeline.append({"$limit": limit})
    
    elif endpoint == "department_trend":
        if not from_date or not to_date:
            raise HTTPException(422, "from and to required")
        collection = "attendance_logs"
        
        pipeline = [
            {"$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee"
            }},
            {"$unwind": "$employee"},
            {"$match": {
                "employee.department": department,
                "date": {"$gte": from_date, "$lte": to_date}
            }},
            {"$group": {
                "_id": "$date",
                "present_count": {"$sum": 1},
                "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}}
            }},
            {"$sort": {"_id": 1}}
        ]
    
    explain = db[collection].aggregate(pipeline, explain=True)
    explain_result = list(explain)[0]
    
    return {
        "endpoint": endpoint,
        "collection": collection,
        "explain": explain_result
    }
