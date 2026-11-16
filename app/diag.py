"""卡住診斷: what every thread of the program is doing right now (stack tops), plus the relay
and queue state, as plain text for the phone / LOG."""
from __future__ import annotations

import sys
import threading
import time
import traceback


def threads_text(depth: int = 6) -> str:
    frames = sys._current_frames()
    out = []
    for t in threading.enumerate():
        f = frames.get(t.ident)
        if f is None:
            continue
        st = traceback.extract_stack(f)[-depth:]
        where = " ← ".join(f"{fr.name}({fr.filename.replace(chr(92), '/').split('/')[-1]}:{fr.lineno})" for fr in reversed(st))
        out.append(f"[{t.name}] {where}")
    return "\n".join(out)


def report() -> dict:
    import core
    lines = [f"時間 {time.strftime('%H:%M:%S')}  版本 V{core.APP_VERSION}"]
    try:
        import staging
        r = staging.root()
        lines.append(f"中繼：{r or '不使用'}  copy={staging._state.get('copy')}  jcopy={staging._state.get('jcopy')}  "
                     f"move={staging._state.get('move')}  note={staging._state.get('note')!r}  started={staging._S.get('started')}")
    except Exception as e:
        lines.append(f"中繼：讀不到（{e}）")
    try:
        from PySide6.QtWidgets import QApplication
        q = getattr(QApplication.instance(), "_fs_queue", None)
        if q:
            for j in q.jobs:
                if j.status in ("running", "paused") or (j.status == "queued" and j.message):
                    lines.append(f"#{j.id} {j.title} {j.status} {j.progress * 100:.1f}% 訊息：{j.message}")
            lines.append(f"worker 執行緒：{'活著' if getattr(q, 'thread', None) is None or q.thread.is_alive() else '已停止'}")
    except Exception as e:
        lines.append(f"佇列：讀不到（{e}）")
    return {"text": "\n".join(lines) + "\n\n執行緒：\n" + threads_text()}
