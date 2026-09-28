# Scheduler deployment model

Status date: 2026-09-28.

| Piece | Status |
| --- | --- |
| One `AutomationRunner` loop inside the API process | **IMPLEMENTED** |
| Exclusive file lock so extra workers on one machine do not tick | **IMPLEMENTED** |
| `TriggerScheduler` left unstarted | **IMPLEMENTED** |
| Database uniqueness as the only duplicate guard | **NOT_IMPLEMENTED** (and not used as the guard) |
| A lock across more than one host | **NOT_IMPLEMENTED** |

## What the process does today

`Dockerfile` starts `uvicorn main:app` with no `--workers` flag, so one container runs one worker. `docker-compose.yml` sets `SCHEDULER_ENABLED` to true. `create_app` starts `scheduler_loop` from the FastAPI lifespan only when that flag is true.

Each worker process runs the lifespan. Two workers would each start a loop. A unique row in `scheduled_jobs` does not stop both loops from ticking: both can read the same due work before either commits.

## Chosen strategy

`scheduler/leader.py` `SchedulerLeader` takes an exclusive, non-blocking lock on `temp_dir/scheduler.leader.lock` and holds it until shutdown.

- Windows uses `msvcrt.locking`.
- Other platforms use `fcntl.flock` with `LOCK_EX | LOCK_NB`.

The worker that gets the lock starts `scheduler_loop`. A worker that does not get the lock logs a warning and does not tick. The lock is released when the lifespan ends.

This is one lock per machine, held for the life of the process. It is not a unique database constraint.

## Supported deployment

Run one API container, or one process that holds the lock, on a host. The image's default command is one worker. If you raise `--workers` inside that container, only one worker runs the scheduler.

## Not supported yet

Two hosts, two containers, or two machines that do not share this lock file can both tick. A distributed or database advisory lock for that layout is **NOT_IMPLEMENTED**. Do not treat a unique job row as that lock.

`agent_platform.triggers.TriggerScheduler` is not started. Starting it would be a second loop.

## Tenant

**IMPLEMENTED**: the scheduler still uses the studio's user boundary. A user id is the tenant for campaigns, posts, and tokens.

**NOT_IMPLEMENTED**: an organization that owns many users. The future shape, if a product requirement appears, is Organization → users → agents. This deployment does not add that model.
