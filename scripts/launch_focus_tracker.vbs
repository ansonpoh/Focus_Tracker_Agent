Option Explicit

Dim shell, fso, scriptDir, repoRoot, pythonwPath, mainPath, command

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
repoRoot = fso.GetParentFolderName(scriptDir)
pythonwPath = fso.BuildPath(repoRoot, "venv\Scripts\pythonw.exe")
mainPath = fso.BuildPath(repoRoot, "main.py")

If Not fso.FileExists(pythonwPath) Then
    WScript.Echo "Missing Python runtime: " & pythonwPath
    WScript.Quit 1
End If

If Not fso.FileExists(mainPath) Then
    WScript.Echo "Missing entrypoint: " & mainPath
    WScript.Quit 1
End If

command = """" & pythonwPath & """ """ & mainPath & """"
shell.CurrentDirectory = repoRoot
shell.Run command, 0, False
