"""
core/thread_utils.py — Utilities for thread-safe execution with Streamlit context propagation.
"""


def wrap_thread_ctx(fn):
    """
    Wraps a function to attach Streamlit's ScriptRunContext to background worker threads,
    preventing 'missing ScriptRunContext!' warnings when executing parallel scans in Streamlit.
    """
    ctx = None
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        ctx = get_script_run_ctx()
    except ImportError:
        try:
            from streamlit.scriptrunner import get_script_run_ctx
            ctx = get_script_run_ctx()
        except ImportError:
            pass

    if ctx is None:
        return fn

    def wrapper(*args, **kwargs):
        try:
            from streamlit.runtime.scriptrunner import add_script_run_ctx
            add_script_run_ctx(ctx=ctx)
        except ImportError:
            try:
                from streamlit.scriptrunner import add_script_run_ctx
                add_script_run_ctx(ctx=ctx)
            except ImportError:
                pass
        except Exception:
            pass
        return fn(*args, **kwargs)

    return wrapper
