@echo off
rem sendvoice - WeChat voice message sender (usable from any dir)
rem   sendvoice Xiaofang "D:/a.silk"
rem   sendvoice Xiaofang --text "hello"
rem NOTE: keep this file ASCII-only (cmd.exe parses .cmd with GBK codepage).
"D:\dev\wechatphone\.venv\Scripts\python.exe" "D:\dev\wechatphone\voice_msg.py" %*
