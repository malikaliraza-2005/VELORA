-- AD-38 (the AD-36 finding): the monitor can tell when the worker has stopped.
--
-- worker_heartbeats (0013) is global and holds no tenant data: a worker's name, when it
-- started and when it was last seen. The monitor reads it and alerts once the last sign of
-- life is too old, even when there is no queued work to show a growing queue age.

GRANT SELECT ON worker_heartbeats TO velora_monitor;
