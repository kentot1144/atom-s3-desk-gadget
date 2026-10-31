' AtomS3 desk hub - silent launcher (no console window at all)
' Double-click this file instead of start_hub.bat.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
hubPy = scriptDir & "\hub.py"

candidates = Array( _
    sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"), _
    "C:\Program Files\Python312\pythonw.exe", _
    "C:\Python312\pythonw.exe" _
)

pyw = ""
For Each c In candidates
    If fso.FileExists(c) Then
        pyw = c
        Exit For
    End If
Next

If pyw = "" Then
    MsgBox "pythonw.exe not found. Install Python 3 first.", 16, "AtomS3 Hub"
    WScript.Quit 1
End If

sh.CurrentDirectory = scriptDir
sh.Run """" & pyw & """ """ & hubPy & """", 0, False
