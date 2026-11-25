"""Per-request text fallback policy; structured evidence is always retained."""
from contextvars import ContextVar
from functools import wraps

profile = ContextVar("bridge_response_profile", default="full")


def scoped_response(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        token = profile.set(self.response_profile)
        try:
            return method(self, *args, **kwargs)
        finally:
            profile.reset(token)
    return wrapped


def compact(response):
    result = response.get("result") or {}
    summary = {k: response[k] for k in ("ok", "pending", "error") if k in response}
    if isinstance(result, dict):
        keys = ("status", "job_id", "session_id", "control_code", "hip_name", "generation", "verified", "ran", "step_count",
                "failed_step", "summary", "context_token", "current_network", "selection",
                "resolved_targets", "target_ids", "context_cache", "execution_started", "next_step", "image_status",
                "data", "data_error", "changed_paths", "applied_steps", "truncated", "preview_of")
        summary["result"] = {k: result[k] for k in keys if k in result}
        receipt = result.get("response")
        terminal = (receipt.get("result") or {}) if isinstance(receipt, dict) else {}
        preview = result.get("preview") or terminal.get("preview")
        if isinstance(preview, dict):
            summary["result"]["preview"] = {key: preview[key] for key in ("status", "error", "delivery_status") if key in preview}
        if "verified" in terminal:
            summary["result"]["verified"] = terminal["verified"]
        for key, value in result.items():
            if value is None or isinstance(value, (bool, int, float)):
                summary["result"].setdefault(key, value)
    summary["details"] = "Full result and verification evidence are in structuredContent. Use --response-profile full for a complete text fallback."
    return summary
