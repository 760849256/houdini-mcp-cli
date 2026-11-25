# Queryable host tasks

Daily context / inspect / edit / execute / look / job remain the six entry points.
Edit and expensive reads execute through one bounded queue in the selected Houdini.
Only the host main thread calls GUI scene APIs. Health and task queries remain available while it waits.

## Submit and query

`houdini_edit`, `houdini_inspect`, `houdini_execute` and `houdini_look` accept `job_id`,
`wait_ms` (0–30000) and `queue_timeout_ms` (1–120000, default 15000).
Short tasks can finish in the submission response. Otherwise use
`houdini_job(job_id=original_id)` or `action=list` / `action=cancel`.
Never create a new ID to retry an uncertain write. Same ID and request return the original receipt;
the same ID with changed content is rejected. A completed look queried through job also attaches its available image.

States: queued, running, succeeded, failed, partial, cancelled, expired, outcome_unknown.
Queued cancellation and expiry prevent execution. Running Python/cook cannot be force-killed;
Python can call `bridge_checkpoint()` cooperatively. Cancelling after effects may report partial.
Closing the MCP client stops waiting and leaves accepted host tasks queryable. A service restart
cancels lost queued payloads and marks interrupted running receipts outcome_unknown. It never replays them.

SQLite receipts live under the private runtime's tasks directory, one database per instance.
Admission, running, first effects, cancellation and completion are committed durably.
Progress is cached and checkpointed at most every 100 ms, plus the last step. It may lag after a crash.
The latest 128 full results are retained; older IDs and request digests remain reserved, up to 50000 receipts.
At capacity new admission stops. Python source and original scene payloads are not persisted in the journal.
Results can contain requested scene data or printed output. A disk failure blocks further admission;
a response available only in memory is explicitly marked. Receipts do not provide exactly-once execution across a host crash.

## Guarded batches

The host resolves targets, validates, reviews, runs in one Undo group, and verifies affected state.
This is not an atomic transaction. A failed batch may have applied earlier steps.
Repeated parameter writes retain every requested setter and its immediate readback; final values
are checked against the live scene. Only trusted observations from that executing task can prove superseded writes.

Absolute paths avoid an extra context request. Selection/network aliases use the host's cheap
cached identity token and are rejected if it differs on execution. All queued tasks check HIP epoch;
writes also check the permission epoch. Stop/reload refuses while a host task is running.

Inspect returns `target_ids`. Supply these as `expected_nodes={absolute_path: session_id}`
to reject deleted/replaced targets, and `expected_parms={absolute_path: {parm_name: old_value}}`
to require a known parameter state before any batch write. These checks are optional explicit contracts;
path-only requests do not detect a previously replaced unselected node. Existing context tokens remain supported.

Inspect accepts `fields` and `parm_names`; node field filtering happens before unrequested parameter/child reads.
Context is a cheap main-thread snapshot sampled every 500 ms and after ordinary tasks.
Its `context_cache` includes age, sample time and identity revision even with field projection.
Snapshots older than one second or from a previous HIP epoch fall back to a queued fresh read. They never authorize writes;
the batch always checks current scene state on the main thread.
Full advanced interfaces remain available. CLI MCP defaults to compact text plus full `structuredContent`.
Use `--response-profile full` or `BLIB_HOU_RESPONSE_PROFILE=full` for clients requiring all results in text.
The Python adapter constructor retains full text by default for embedding compatibility.
This changes wire bytes; model context savings depend on the client's handling of structured content.

## Screenshots and recovery

Captures use the private runtime's screenshots directory. Temporary network navigation and framing
restore on success and error. Viewport camera, view, resolution and timeline restoration remain guarded.
Pending capture, unusable preview and completed edit are separate states. Black/unreadable viewport
captures are errors; completion alone is not visual acceptance.

For diagnostics use `--diagnostics`; for historical read-only evidence provide the original
`session_id` to `houdini_job`. Session credentials are never returned in task handles or diagnostic events.
The legacy CLI stops on a pending task and reports its ID rather than treating it as completed.
