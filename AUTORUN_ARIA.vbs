' Silent launcher — runs AUTORUN_ARIA.bat with no visible window at login.
' The agent windows themselves start minimized; this hides the parent shell.
'
' The path is derived from this script's own location rather than hardcoded,
' so the shortcut keeps working if the repo moves and does not name one
' machine's user account.
Dim fso, here
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)

CreateObject("WScript.Shell").Run _
  "cmd /c """ & here & "\AUTORUN_ARIA.bat""", 0, False
