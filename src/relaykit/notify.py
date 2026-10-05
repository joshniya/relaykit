"""Notifications when something happens that you'd want to know about: the relay finished, got
blocked, failed, hit a usage limit, switched agents, reached a budget cap, or lost its login.

Channels (all optional, all best-effort, never fatal):
  desktop      a system notification (Windows toast, macOS Notification Center, Linux notify-send)
  ntfy_topic   a push to your phone via ntfy.sh (or your own ntfy server)
  webhook_url  a POST to a URL; Discord and Slack webhook URLs get their native message shape
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request

EVENTS = ("complete", "blocked", "failed", "limit", "failover", "budget", "auth", "started", "stopped")


class Notifier:
    def __init__(self, cfg: dict, relay_name: str, log=None):
        self.cfg = cfg or {}
        self.relay = relay_name
        self.log = log or (lambda *_: None)

    def enabled_for(self, event: str) -> bool:
        return event in (self.cfg.get("events") or [])

    def send(self, event: str, message: str, force: bool = False) -> None:
        if not force and not self.enabled_for(event):
            return
        title = f"relaykit · {self.relay} · {event}"
        if self.cfg.get("desktop"):
            self._desktop(title, message)
        if self.cfg.get("ntfy_topic"):
            self._ntfy(title, message, event)
        if self.cfg.get("webhook_url"):
            self._webhook(title, message, event)

    # -- channels
    def _desktop(self, title: str, message: str) -> None:
        try:
            if os.name == "nt":
                ps = (
                    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
                    "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                    "$x = $t.GetElementsByTagName('text');"
                    "$x.Item(0).AppendChild($t.CreateTextNode($env:RK_T)) | Out-Null;"
                    "$x.Item(1).AppendChild($t.CreateTextNode($env:RK_M)) | Out-Null;"
                    "$n = [Windows.UI.Notifications.ToastNotification]::new($t);"
                    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('relaykit').Show($n)"
                )
                env = dict(os.environ, RK_T=title, RK_M=message[:250])
                subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            elif sys.platform == "darwin":
                script = 'display notification (system attribute "RK_M") with title (system attribute "RK_T")'
                env = dict(os.environ, RK_T=title, RK_M=message[:250])
                subprocess.run(["osascript", "-e", script], env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=20)
            elif shutil.which("notify-send"):
                subprocess.run(["notify-send", title, message[:250]], timeout=20)
        except Exception as exc:
            self.log(f"desktop notification failed: {exc}")

    def _post(self, url: str, body: bytes, headers: dict) -> None:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()

    def _ntfy(self, title: str, message: str, event: str) -> None:
        server = (self.cfg.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
        prio = "high" if event in ("blocked", "failed", "auth", "budget") else "default"
        try:
            self._post(f"{server}/{self.cfg['ntfy_topic']}", message.encode("utf-8"),
                       {"Title": title.encode("ascii", "replace").decode("ascii"), "Priority": prio,
                        "Tags": "relaykit," + event})
        except Exception as exc:
            self.log(f"ntfy notification failed: {exc}")

    def _webhook(self, title: str, message: str, event: str) -> None:
        url = self.cfg["webhook_url"]
        if "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url:
            payload = {"content": f"**{title}**\n{message}"[:1900], "allowed_mentions": {"parse": []}}
        elif "hooks.slack.com" in url:
            payload = {"text": f"*{title}*\n{message}"}
        else:
            payload = {"source": "relaykit", "relay": self.relay, "event": event, "title": title, "message": message}
        try:
            self._post(url, json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json",
                                                                  "User-Agent": "relaykit"})
        except Exception as exc:
            self.log(f"webhook notification failed: {exc}")
