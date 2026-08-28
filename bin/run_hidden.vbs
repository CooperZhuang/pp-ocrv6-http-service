' ============================================================
'  run_hidden.vbs - Start the PP-OCRv6 HTTP OCR service with
'  NO visible console window (pure background process).
'
'  Launch via:  wscript.exe run_hidden.vbs
'  Window style 0 = hidden; python.exe runs with no visible window.
'  The inference child is spawned with CREATE_NO_WINDOW in code.
' ============================================================

Set fso = CreateObject("Scripting.FileSystemObject")
binDir = fso.GetParentFolderName(WScript.ScriptFullName)      ' this script's dir (bin\)
projectDir = fso.GetParentFolderName(binDir)                  ' project root

pythonExe = projectDir & "\.venv\Scripts\python.exe"
If Not fso.FileExists(pythonExe) Then pythonExe = "python"

script = projectDir & "\ocr_service.py"
If Not fso.FileExists(script) Then
    WScript.Echo "ERROR: ocr_service.py not found in " & projectDir
    WScript.Quit 1
End If

' window style 0 => hidden; waitOnReturn False => launch and exit (background)
Set shell = CreateObject("WScript.Shell")
shell.Run """" & pythonExe & """ """ & script & """ http", 0, False
