; Remove host wrappers/hooks through the bundled engine during uninstall.
!macro customUnInstall
  StrCpy $0 "$INSTDIR\resources\resources\engine\morphd-engine.exe"
  IfFileExists "$0" 0 done
  MessageBox MB_YESNO|MB_ICONQUESTION "是否同时删除 harness2-morphd 的本地数据与 Key？$\r$\nDelete local harness2-morphd data and keys as well?" IDNO keepData
  ExecWait '"$0" uninstall --all --delete-data'
  Goto done
  keepData:
  ExecWait '"$0" uninstall --all'
  done:
!macroend
