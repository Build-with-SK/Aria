' Silent launcher — runs AUTORUN_ARIA.bat with no visible window at login.
' The agent windows themselves start minimized; this hides the parent shell.
CreateObject("WScript.Shell").Run _
  "cmd /c ""C:\Users\sound\Documents\trading-intelligence-system\AUTORUN_ARIA.bat""", 0, False
