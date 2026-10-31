import os
import sys
import tempfile
import asyncio
import logging
from typing import Dict, Any

logger = logging.getLogger("uvicorn")

MIME_MAP = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}

async def run_python_sandbox(chat_id: int, code: str, purpose: str = "") -> Dict[str, Any]:
    """
    Executes Python code dynamically inside a clean, isolated execution sandbox.
    Auto-detects any generated documents (PDF, PPTX, Excel, CSV, charts, images)
    and sends them directly to the user's Telegram chat via Telegram Document API.
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
        if generated_files and chat_id and chat_id > 0:
            from main import send_file
            for fname, fpath in generated_files:
                ext = os.path.splitext(fname)[1].lower()
                mime = MIME_MAP.get(ext, "application/octet-stream")
                caption_purpose = purpose or "Generated Document"
                caption = f"📄 **{fname}**\n_{caption_purpose}_"

                with open(fpath, "rb") as gf:
                    content_bytes = gf.read()

                try:
                    await send_file(
                        chat_id=chat_id,
                        filename=fname,
                        content_bytes=content_bytes,
                        caption=caption,
                        mime_type=mime
                    )
                    files_sent.append(fname)
                    logger.info(f"Sandbox generated & sent {fname} ({len(content_bytes)} bytes) to {chat_id}")
                except Exception as send_err:
                    logger.error(f"Failed to send sandbox file {fname} to {chat_id}: {send_err}")

        if files_sent:
            return {
                "status": "success",
                "files_sent": files_sent,
                "message": f"Successfully generated and delivered {', '.join(files_sent)} to Telegram."
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
