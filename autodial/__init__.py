"""autodial package: WeChat voice-call automation via UI automation.

Design (decoupled module):
- calibrate.py : one-time calibration wizard, records click targets into
                 data/autodial_calib.json (+ optional button template image)
- dialer.py    : WeChatDialer - find/activate WeChat window, search contact,
                 open chat, click voice-call button (template match first,
                 coordinate fallback), batch dial with task injection
- CLI          : python -m autodial.cli calibrate | dial | batch | windows
- API          : autodial_app.py (Flask, :8767)

Task injection: before each call, dialer writes data/current_task.json;
bridge.py picks it up and injects "who am I calling + task" into the
Realtime instructions via session.update.
"""
