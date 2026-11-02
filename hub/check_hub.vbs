' AtomS3 hub watchdog - completely silent, no console window.
' Pings the hub and starts it again if it is not answering.
' Run it from Task Scheduler every 1-2 minutes, or at logon.
Option Explicit

Dim sh, fso, dir, http, ok

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
ok = False

On Error Resume Next
Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
http.setTimeouts 1500, 1500, 1500, 2000
http.open "GET", "http://127.0.0.1:8765/ping", False
http.send
If Err.Number = 0 Then
    If http.status = 200 Then ok = True
End If
Err.Clear
On Error GoTo 0

If Not ok Then
    sh.CurrentDirectory = dir
    sh.Run "wscript.exe """ & dir & "\start_hub_silent.vbs""", 0, False
End If
