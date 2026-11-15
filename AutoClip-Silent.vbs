Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
If CreateObject("Scripting.FileSystemObject").FileExists("AutoClip.exe") Then
    WshShell.Run "AutoClip.exe", 0, False
Else
    WshShell.Run "run_autoclip.bat", 0, False
End If
Set WshShell = Nothing
