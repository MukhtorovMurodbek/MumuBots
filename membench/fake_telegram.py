"""A fake Telegram Bot API: enough for five bots to start, poll, and answer commands.
POST /inject {"token":..., "updates":[...]} queues updates for one bot."""
import json, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

lock = threading.Condition()
queues: dict = {}
next_id = [1]
msg_id = [1000]
calls: dict = {}

def bot_user(token):
    bid = int(token.split(":")[0])
    return {"id": bid, "is_bot": True, "first_name": f"Bot{bid}", "username": f"fake{bid}_bot",
            "can_join_groups": True, "can_read_all_group_messages": False, "supports_inline_queries": False}

def message(token, params):
    msg_id[0] += 1
    chat_id = params.get("chat_id", 1)
    try: chat_id = int(chat_id)
    except Exception: chat_id = 1
    return {"message_id": msg_id[0], "date": int(time.time()), "chat": {"id": chat_id, "type": "private", "first_name": "U"},
            "from": bot_user(token), "text": str(params.get("text", ""))[:100]}

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        ctype = self.headers.get("Content-Type", "")
        if self.path == "/inject":
            body = json.loads(raw)
            with lock:
                q = queues.setdefault(body["token"], [])
                for u in body["updates"]:
                    u["update_id"] = next_id[0]; next_id[0] += 1; q.append(u)
                lock.notify_all()
            return self.reply({"ok": True, "result": True})
        parts = self.path.strip("/").split("/")
        if len(parts) < 2 or not parts[0].startswith("bot"):
            return self.reply({"ok": False, "error_code": 404, "description": "Not Found"}, 404)
        token, method = parts[0][3:], parts[1]
        params = {}
        if "json" in ctype and raw:
            params = json.loads(raw)
        elif raw and "multipart" not in ctype:
            params = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
        calls[method] = calls.get(method, 0) + 1
        if method == "getMe":
            return self.reply({"ok": True, "result": bot_user(token)})
        if method == "getUpdates":
            timeout = float(params.get("timeout") or 0)
            offset = int(params.get("offset") or 0)
            deadline = time.time() + min(timeout, 10)
            with lock:
                while True:
                    q = queues.setdefault(token, [])
                    q[:] = [u for u in q if u["update_id"] >= offset]
                    if q or time.time() >= deadline:
                        out = q[:100]
                        break
                    lock.wait(timeout=max(0.05, deadline - time.time()))
            return self.reply({"ok": True, "result": out})
        if method in ("sendMessage", "editMessageText", "sendPhoto", "sendDocument", "sendVideo", "sendSticker",
                      "copyMessage", "forwardMessage", "sendAnimation", "sendAudio", "sendVoice"):
            return self.reply({"ok": True, "result": message(token, params) if method != "copyMessage" else {"message_id": msg_id[0]}})
        if method in ("getMyCommands", "getChatAdministrators"):
            return self.reply({"ok": True, "result": []})
        if method == "getChat":
            return self.reply({"ok": True, "result": {"id": int(params.get("chat_id", 1) or 1), "type": "private"}})
        if method == "getWebhookInfo":
            return self.reply({"ok": True, "result": {"url": "", "has_custom_certificate": False, "pending_update_count": 0}})
        return self.reply({"ok": True, "result": True})
    def reply(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == "/calls":
            return self.reply(calls)
        if self.path == "/pending":
            with lock:
                return self.reply({"pending": sum(len(q) for q in queues.values())})
        return self.reply({"ok": False}, 404)

ThreadingHTTPServer.daemon_threads = True
ThreadingHTTPServer.request_queue_size = 512
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8099), H).serve_forever()
