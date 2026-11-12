import os
import sys
import tempfile
import asyncio
import logging
from typing import Dict, Any, Optional

from core.file_dispatch import MIME_MAP, dispatch_generated_file

logger = logging.getLogger("uvicorn")

async def run_python_sandbox(
    chat_id: int,
    code: str,
    purpose: str = "",
    delivery_chat_id: Optional[int] = None,
    channel: str = "telegram",
) -> Dict[str, Any]:
    """
    Executes Python code dynamically inside a clean, isolated execution sandbox.
    Auto-detects any generated documents (PDF, PPTX, Excel, CSV, DOCX, charts, images)
    and delivers them via channel-aware artifact dispatch (Telegram sendDocument or App URL).
    """
    if not code or not code.strip():
        return {"status": "error", "message": "No code provided to execute."}

    temp_dir = tempfile.mkdtemp(prefix=f"ai_sandbox_{chat_id}_")
    script_path = os.path.join(temp_dir, "script.py")

    with open(script_path, "w", encoding="utf-8") as f:
        f.write(code)

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=temp_dir
        )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=25.0)
            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {"status": "error", "message": "Code execution timed out after 25 seconds."}

        # Check for generated files in sandbox dir
        generated_files = []
        for fname in os.listdir(temp_dir):
            if fname == "script.py":
                continue
            fpath = os.path.join(temp_dir, fname)
            if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                generated_files.append((fname, fpath))

        files_sent = []
        attachments = []
        delivery_errors = []
        if generated_files:
            for fname, fpath in generated_files:
                ext = os.path.splitext(fname)[1].lower()
                mime = MIME_MAP.get(ext, "application/octet-stream")
                caption_purpose = purpose or "Generated Document"
                caption = f"{fname}\n{caption_purpose}"

                with open(fpath, "rb") as gf:
                    content_bytes = gf.read()

                dispatch_res = await dispatch_generated_file(
                    chat_id=chat_id,
                    origin_chat_id=delivery_chat_id or chat_id,
                    filename=fname,
                    content_bytes=content_bytes,
                    caption=caption,
                    mime_type=mime,
                    channel=channel,
                )
                if dispatch_res.get("delivered"):
                    files_sent.append(fname)
                    attachments.append({
                        k: dispatch_res[k]
                        for k in ("artifact_id", "filename", "mime_type", "bytes", "download_url", "delivery_channel")
                        if k in dispatch_res
                    })
                else:
                    delivery_errors.append(dispatch_res.get("user_message") or dispatch_res.get("message") or fname)

        if files_sent:
            return {
                "status": "success",
                "delivered": True,
                "files_sent": files_sent,
                "filename": files_sent[0],
                "attachments": attachments,
                "message": f"Successfully generated and delivered {', '.join(files_sent)}."
            }

        if delivery_errors:
            return {
                "status": "error",
                "error_type": "delivery_failed",
                "delivered": False,
                "user_message": delivery_errors[0],
                "message": "; ".join(delivery_errors),
            }

        if proc.returncode != 0:
            return {
                "status": "error",
                "returncode": proc.returncode,
                "stdout": stdout.strip(),
                "stderr": stderr.strip()[:300],
                "message": f"Execution finished with error: {stderr.strip()[:200]}"
            }

        res = {
            "status": "success",
            "stdout": stdout.strip(),
        }
        if stdout.strip():
            res["result"] = stdout.strip()
        else:
            res["message"] = "Code executed successfully (no stdout/files)."

        return res

    except Exception as exc:
        return {"status": "error", "message": f"Sandbox execution exception: {str(exc)}"}
    finally:
        # Clean up temporary directory and files
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
