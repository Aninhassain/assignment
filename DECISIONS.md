# DECISIONS.md

1. **Indexes.** I created a unique index on (emp_code, date) for attendance_logs to prevent duplicate punch-ins per day per employee. This serves the concurrent punch-in guarantee. I also created indexes on (date, emp_code) for the attendance list query, on emp_code for employee lookups, on department for filtering, on joined_on for headcount calculations, and on status and date for analytics queries. I considered a compound index on (department, joined_on) but rejected it because department filtering and headcount queries are separate use cases.

2. **Punch-in race.** Both requests read the employee record and compute the attendance date. Both try to insert with the same (emp_code, date). MongoDB's unique index ensures exactly one insert succeeds (returns 201). The other hits a duplicate key error, which I catch and convert to a 409 response. This is atomic at the database level.

3. **Ties.** My leaderboard returns all employees whose rank is <= limit, including ties. If three employees tie for rank 10 and limit is 10, all three are returned. This follows standard competition ranking where ties share a rank and the next rank is skipped. The limit applies after ranking, not before.

4. **Headcount.** My department summary runs two separate aggregations: one on attendance_logs (for metrics) and one on employees (for headcount). The employees aggregation counts all employees with joined_on <= month-end, regardless of whether they have logs. I then merge the results, ensuring departments with zero logs still show their headcount.

5. **One thing you would change.** I would add a caching layer (Redis) for the analytics endpoints. Monthly summaries and trend data don't change frequently, so caching for 5-15 minutes would dramatically reduce database load for 100x traffic without affecting data freshness significantly.
